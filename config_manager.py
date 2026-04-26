"""
Configuration manager - load, validate, and resolve api_key references.

Supports:
- YAML config loading with validation
- Three api_key resolution methods (plain text, env var, file reference)
- Dict-based config access (similar to SimpleNamespace)
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class ConfigError(Exception):
    """Configuration-related errors."""


class Config:
    """Immutable config wrapper providing typed access to parsed config."""

    def __init__(self, data: dict, workdir: str):
        self._data = data
        self._workdir = workdir

    @property
    def port(self) -> int:
        return self._data.get("port", 0)

    @property
    def default_provider(self) -> Optional[str]:
        return self._data.get("default_provider")

    @property
    def logging(self) -> dict:
        return self._data.get("logging", {})

    @property
    def model_routing(self) -> Dict[str, str]:
        return self._data.get("model_routing", {})

    @property
    def providers_raw(self) -> dict:
        return self._data.get("providers", {})

    def get_provider(self, name: str) -> Optional[dict]:
        """Get resolved provider config (api_key already substituted)."""
        raw = self.providers_raw.get(name)
        if raw is None:
            return None
        return {
            "base_url": raw["base_url"].rstrip("/"),
            "api_key": self._resolve_api_key(raw["api_key"]),
            "allowed_params": set(raw.get("allowed_params", [])),
            "models": raw.get("models", {}),
        }

    def _resolve_api_key(self, raw: str) -> str:
        """Resolve api_key from plain text, env var, or file reference."""
        if not isinstance(raw, str):
            raise ConfigError("api_key must be a string")

        # Environment variable: ${VAR_NAME}
        env_match = re.match(r"^\$\{(\w+)\}$", raw)
        if env_match:
            var_name = env_match.group(1)
            value = os.environ.get(var_name)
            if not value:
                raise ConfigError(
                    f"Environment variable '{var_name}' is not set "
                    f"(referenced in api_key for a provider)"
                )
            return value

        # File reference: @file:./path/to/key
        file_match = re.match(r"^@file:(.+)$", raw)
        if file_match:
            file_path = Path(self._workdir) / file_match.group(1)
            if not file_path.exists():
                raise ConfigError(
                    f"api_key file not found: {file_path.resolve()}"
                )
            return file_path.read_text(encoding="utf-8").strip()

        # Plain text
        return raw


def load_config(config_path: str) -> Config:
    """
    Load and validate config.yaml.

    Returns a Config instance with api_keys resolved.
    Raises ConfigError on any validation failure.
    """
    path = Path(config_path).resolve()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a YAML mapping")

    workdir = str(path.parent)

    # --- validation ---

    # Validate model_routing
    routing = data.get("model_routing", {})
    providers_raw = data.get("providers", {})
    if not isinstance(routing, dict):
        raise ConfigError("'model_routing' must be a mapping")

    for model_name, provider_name in routing.items():
        if provider_name not in providers_raw:
            raise ConfigError(
                f"Model '{model_name}' routes to provider "
                f"'{provider_name}', but that provider is not defined "
                f"in 'providers' section"
            )

    # Validate providers
    if not isinstance(providers_raw, dict):
        raise ConfigError("'providers' must be a mapping")

    for pname, pcfg in providers_raw.items():
        if not isinstance(pcfg, dict):
            raise ConfigError(f"Provider '{pname}' must be a mapping")
        if "base_url" not in pcfg:
            raise ConfigError(f"Provider '{pname}' is missing 'base_url'")
        if "api_key" not in pcfg:
            raise ConfigError(f"Provider '{pname}' is missing 'api_key'")

        # Validate models sub-config
        models = pcfg.get("models", {})
        if not isinstance(models, dict):
            raise ConfigError(f"Provider '{pname}'.models must be a mapping")

    # Validate logging
    logging_cfg = data.get("logging", {})
    if not isinstance(logging_cfg, dict):
        raise ConfigError("'logging' must be a mapping")

    # Validate port
    port = data.get("port", 0)
    if not isinstance(port, int) or port < 0 or port > 65535:
        raise ConfigError("'port' must be an integer between 0 and 65535")

    return Config(data, workdir)
