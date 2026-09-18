"""配置加载。

加载时会做校验——本仓库栽在「配置改了不生效」上的次数比任何算法错误都多，
所以这里宁可启动时报错，也不要静默走默认值。详见 config_schema.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

# 校验结果留一份，界面可以显示出来（告警不阻断启动，但必须让人看见）
LAST_WARNINGS: list = []


def load_config(path: Path = DEFAULT_CONFIG_PATH,
                validate: bool = True) -> Dict[str, Any]:
    """加载 YAML 配置。

    Args:
        path: 配置文件路径。
        validate: 是否校验。写错的枚举值、打错的键名、重复的段落在这里
            会被抓出来；枚举写错直接抛异常，未知键只告警（存进
            LAST_WARNINGS，界面负责显示）。

    Returns:
        配置字典。

    Raises:
        DuplicateKeyError: YAML 里有重复键（会静默覆盖，必须拦）。
        ValueError: 枚举值非法或必填项缺失。
    """
    from zhixing_quant.config_schema import StrictLoader
    from zhixing_quant.config_schema import validate as _validate

    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.load(f, Loader=StrictLoader)

    if validate and isinstance(cfg, dict):
        result = _validate(cfg)
        global LAST_WARNINGS
        LAST_WARNINGS = list(result.warnings)
        if not result.ok:
            raise ValueError(
                f"配置校验未通过（{path}）：\n" + result.report()
                + "\n\n这些写法不会让程序崩溃，只会让配置静默失效——"
                  "所以这里直接拦下来。"
            )
    return cfg


def config_warnings() -> list:
    """上次加载配置时产生的告警，供界面显示。"""
    return list(LAST_WARNINGS)
