"""Local configuration loader for BMSON2PM.

Machine-specific paths live in ``backend/config.toml`` (gitignored); copy
``config.toml.example`` to seed it. Environment variables always take priority
over the file, so CI and ad-hoc overrides keep working without a config file.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG_PATH = _BACKEND_ROOT / "config.toml"

_cache: dict[str, Any] | None = None


def _raw_config() -> dict[str, Any]:
    global _cache
    if _cache is None:
        path = Path(os.getenv("BMSON2PM_CONFIG") or _DEFAULT_CONFIG_PATH)
        if path.is_file():
            with path.open("rb") as stream:
                _cache = tomllib.load(stream)
            return _cache
        _cache = {}
    return _cache


def pm3_root(root_id: str) -> Path | None:
    """Resolve a PM3 trusted-root path from env var, then config file.

    Returns ``None`` when the root is not configured, so callers can report it
    as unavailable instead of resolving to an arbitrary working directory.
    """
    env_value = os.getenv(f"BMSON2PM_PM3_{root_id.upper()}_ROOT")
    if env_value:
        return Path(env_value).expanduser()
    file_value = _raw_config().get("pm3", {}).get(f"{root_id}_root")
    if file_value:
        return Path(file_value).expanduser()
    return None
