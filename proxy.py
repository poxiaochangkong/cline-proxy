#!/usr/bin/env python3
"""
Cline Proxy - A local OpenAI-Compatible Gateway for Cline.

Eliminates the pain of:
  - Cline not exposing parameters like top_p in the GUI.
  - Having to manually re-enter API keys, URLs, and parameters
    every time you switch between providers/models.

Usage:
    python proxy.py                    # Uses config.yaml
    python proxy.py --config my.yaml   # Custom config
    python proxy.py --port 9000        # Override port
"""

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config_manager import ConfigError, load_config
from logger_setup import setup_logging
from port_manager import find_free_port
from proxy_handler import handle_chat_completions

HERE = Path(__file__).parent.resolve()
DEFAULT_CONFIG = HERE / "config.yaml"

app = FastAPI(title="Cline Proxy", version="1.0.0")


def create_app(config_path: str, port: int):
    """Initialize the app with config and return the FastAPI instance."""
    config = load_config(config_path)
    logger = setup_logging(config.logging, str(HERE))

    # Validate api_key resolution at startup
    for pname in config.providers_raw:
        try:
            config.get_provider(pname)
        except ConfigError as e:
            logger.error("Provider '%s': %s", pname, e)
            sys.exit(1)

    # Store on app state
    app.state.config = config
    app.state.logger = logger
    app.state.port = port

    # Banner
    logger.info("=" * 50)
    logger.info("Cline Proxy starting...")
    logger.info("Config: %s", config_path)
    default = config.default_provider
    logger.info("Default provider: %s", default if default else "(none)")
    logger.info("Model routing entries: %d", len(config.model_routing))
    logger.info("Provider count: %d", len(config.providers_raw))
    logger.info("Configuration validation passed.")
    logger.info("=" * 50)
    logger.info("Cline Proxy is ready!")
    logger.info("Set Base URL in Cline to:")
    logger.info("  http://localhost:%d/v1", port)
    logger.info("=" * 50)

    return app


# ---- Routes ----


@app.get("/health")
async def health():
    """Health check endpoint."""
    port = getattr(app.state, "port", None)
    return {"status": "ok", "port": port}


@app.get("/v1/models")
async def list_models():
    """Return all configured models as an OpenAI-compatible model list."""
    config = app.state.config
    data = []
    for model_name, provider_name in config.model_routing.items():
        data.append(
            {
                "id": model_name,
                "object": "model",
                "owned_by": provider_name,
            }
        )
    # Include models from default_provider that are not in routing table
    default = config.default_provider
    if default:
        provider = config.get_provider(default)
        if provider:
            for model_name in provider["models"]:
                if model_name not in config.model_routing:
                    data.append(
                        {
                            "id": model_name,
                            "object": "model",
                            "owned_by": default,
                        }
                    )
    # Deduplicate by id
    seen = set()
    unique = []
    for item in data:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    return {"object": "list", "data": unique}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Main proxy endpoint - intercept, override, forward, passthrough."""
    return await handle_chat_completions(
        request, app.state.config, app.state.logger
    )


# ---- CLI entry ----

def main():
    parser = argparse.ArgumentParser(
        description="Cline Proxy - Local OpenAI-Compatible Gateway"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG),
        help=f"Config file path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override port (overrides config.yaml value)",
    )
    args = parser.parse_args()

    config_path = args.config

    # Check config exists
    if not os.path.exists(config_path):
        example_path = HERE / "config.example.yaml"
        if config_path == str(DEFAULT_CONFIG) and example_path.exists():
            print(
                f"[ERROR] config.yaml not found.\n"
                f"        Copy config.example.yaml to config.yaml and edit it:\n"
                f"        copy {example_path} {config_path}"
            )
        else:
            print(f"[ERROR] Config file not found: {config_path}")
        sys.exit(1)

    # Determine preferred port
    if args.port is not None:
        preferred_port = args.port
    else:
        try:
            tmp_config = load_config(config_path)
            preferred_port = tmp_config.port
        except ConfigError as e:
            print(f"[CONFIG ERROR] {e}")
            sys.exit(1)

    # Find a free port
    port = find_free_port(preferred_port)

    # Initialize the app
    try:
        create_app(config_path, port)
    except ConfigError as e:
        print(f"[CONFIG ERROR] {e}")
        sys.exit(1)

    # Run uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()