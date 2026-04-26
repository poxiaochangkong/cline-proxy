"""
Core proxy logic - request interception, parameter overrides, and forwarding.

Design principles:
  1. model field is NEVER modified - passed through as-is.
  2. Parameter overrides are selective (only config-specified fields).
  3. Each provider has an allowed_params whitelist to filter unknown params.
  4. Stream responses are byte-level passthrough - no SSE parsing.
  5. All errors are returned as valid SSE error events.
"""

import json
import logging
from typing import AsyncGenerator, Optional

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("cline-proxy")

# Core parameters that every provider MUST support.
# These are always passed through, regardless of allowed_params.
CORE_PARAMS = {
    "model",
    "messages",
    "stream",
    "max_tokens",
    "tools",
    "tool_choice",
    "response_format",
    "stop",
    "n",
}

# Timeouts
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
NORMAL_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)


def _sse_error(message: str) -> bytes:
    """Build a valid SSE error event that Cline can consume."""
    payload = json.dumps(
        {"error": {"message": message, "type": "proxy_error"}},
        ensure_ascii=False,
    )
    return f"data: {payload}\n\n".encode("utf-8")


async def _forward_stream(
    url: str, headers: dict, body: dict
) -> AsyncGenerator[bytes, None]:
    """
    Forward a streaming request and passthrough chunks byte-by-byte.

    On errors, yield a valid SSE error event followed by [DONE].
    """
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST", url, json=body, headers=headers, timeout=STREAM_TIMEOUT
            ) as response:
                if response.status_code != 200:
                    error_text = (await response.aread()).decode(
                        "utf-8", errors="replace"
                    )
                    logger.error(
                        "Upstream returned HTTP %d: %s",
                        response.status_code,
                        error_text[:500],
                    )
                    yield _sse_error(
                        f"Upstream returned HTTP {response.status_code}"
                    )
                    yield b"data: [DONE]\n\n"
                    return

                async for chunk in response.aiter_bytes():
                    yield chunk

        except httpx.ConnectError as e:
            logger.error("Connection failed: %s", e)
            yield _sse_error(f"Cannot connect to upstream: {e}")
            yield b"data: [DONE]\n\n"

        except httpx.ReadTimeout:
            logger.error("Stream read timeout")
            yield _sse_error("Stream read timeout - upstream did not respond in time")
            yield b"data: [DONE]\n\n"

        except Exception as e:
            logger.error("Unexpected stream error: %s", e)
            yield _sse_error(f"Proxy error: {e}")
            yield b"data: [DONE]\n\n"


async def _forward_normal(url: str, headers: dict, body: dict) -> JSONResponse:
    """Forward a non-streaming request and return the JSON response."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url, json=body, headers=headers, timeout=NORMAL_TIMEOUT
            )
            return JSONResponse(
                content=response.json(),
                status_code=response.status_code,
                headers={
                    "Content-Type": "application/json",
                },
            )
        except httpx.ConnectError as e:
            logger.error("Connection failed: %s", e)
            return JSONResponse(
                status_code=502,
                content={"error": {"message": f"Cannot connect to upstream: {e}"}},
            )
        except httpx.TimeoutException:
            logger.error("Request timeout")
            return JSONResponse(
                status_code=504,
                content={"error": {"message": "Upstream did not respond in time"}},
            )
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return JSONResponse(
                status_code=500,
                content={"error": {"message": f"Proxy error: {e}"}},
            )


def _sanitize_and_override(
    body: dict, provider_cfg: dict, model_cfg: Optional[dict]
) -> dict:
    """
    Filter and override parameters safely.

    1. Keep CORE_PARAMS unconditionally.
    2. Keep only allowed_params from the rest (provider whitelist).
    3. Apply model_cfg overrides on allowed params only.

    Never introduces parameters that the provider might reject.
    """
    allowed = CORE_PARAMS | provider_cfg.get("allowed_params", set())

    # Step 1: filter
    sanitized = {}
    for key, value in body.items():
        if key in allowed:
            sanitized[key] = value
        else:
            logger.debug("Filtered out parameter '%s' (not in whitelist)", key)

    # Step 2: override
    if model_cfg:
        for key, value in model_cfg.items():
            if key in allowed:
                sanitized[key] = value
                logger.info("Override %s: %s", key, value)
            else:
                logger.warning(
                    "Config tries to override '%s' but it's not in allowed_params",
                    key,
                )

    return sanitized


async def handle_chat_completions(
    request: Request, config, logger: logging.Logger
):
    """
    Main handler for POST /v1/chat/completions.

    1. Resolve provider from model_routing.
    2. Filter + override parameters.
    3. Forward the request.
    4. Passthrough the response.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.error("Invalid JSON body: %s", e)
        return JSONResponse(
            status_code=400,
            content={
                "error": {"message": f"Invalid JSON in request body: {e}"}
            },
        )

    model_name = body.get("model", "")
    if not model_name:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Missing 'model' in request body"}},
        )

    # --- Resolve provider ---
    routing = config.model_routing
    provider_name = routing.get(model_name)

    if provider_name is None:
        # Fallback to default_provider if set
        default = config.default_provider
        if default:
            logger.info(
                "Model '%s' not in routing table, using default_provider '%s'",
                model_name,
                default,
            )
            provider_name = default
        else:
            logger.warning("Unknown model: '%s' (no default_provider set)", model_name)
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "message": (
                            f"Model '{model_name}' is not configured. "
                            "Add it to 'model_routing' in config.yaml."
                        )
                    }
                },
            )

    provider_cfg = config.get_provider(provider_name, strict=False)
    if provider_cfg is None:
        logger.error(
            "Provider '%s' not found in configuration", provider_name
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"Provider '{provider_name}' is missing from config"
                }
            },
        )

    if provider_cfg["api_key"] is None:
        logger.warning(
            "Request to unavailable provider '%s' (api_key not configured)",
            provider_name,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": (
                        f"Provider '{provider_name}' is not available. "
                        f"Configure its api_key in config.yaml and restart."
                    )
                }
            },
        )

    # --- Model override config ---
    model_cfg = provider_cfg["models"].get(model_name)

    # --- Sanitize and override ---
    original_params = {
        k: body.get(k)
        for k in ("temperature", "top_p", "top_k",
                  "frequency_penalty", "presence_penalty", "seed")
        if k in body
    }

    body = _sanitize_and_override(body, provider_cfg, model_cfg)

    # Log changes
    for key, original in original_params.items():
        if key in body and body[key] != original:
            logger.info(
                "  Override '%s': %s → %s", key, original, body[key]
            )

    # --- Forward ---
    target_url = f"{provider_cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider_cfg['api_key']}",
        "Content-Type": "application/json",
    }

    is_stream = body.get("stream", False)
    logger.info(
        "→ %s | provider=%s model=%s stream=%s",
        target_url, provider_name, model_name, is_stream,
    )

    if is_stream:
        return StreamingResponse(
            _forward_stream(target_url, headers, body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await _forward_normal(target_url, headers, body)
