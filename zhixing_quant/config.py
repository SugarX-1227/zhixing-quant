"""Configuration loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load YAML settings for the daily brick scanner.

    Args:
        path: YAML config path.

    Returns:
        Parsed configuration dictionary.

    Rule source:
        User-confirmed first-version settings in this workspace.
    """
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

