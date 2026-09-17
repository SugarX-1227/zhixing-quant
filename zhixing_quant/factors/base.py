"""因子定义与注册表。

为什么需要这一层
----------------

在此之前「选股」只有两步：战法给出一个布尔信号，然后按**成交额降序**截断。
成交额只是流动性代理，和这只票接下来会不会涨没有任何关系。命中 50 只
只能买 5 只时，用成交额挑等于随机挑。

因子层把「排序」这件事变成可以量化、可以检验的东西：

    战法信号（布尔，决定**能不能买**）
        ×
    因子打分（连续值，决定**先买哪只**）

一个因子就是一个函数：给一只标的的日线，返回逐日的因子值。
横截面打分（cross_section.py）把同一天全市场的因子值标准化后合成总分，
有效性检验（evaluate.py）算 IC / ICIR / 分层收益，回答「这个因子到底有没有用」。

时点正确性
----------

`compute(df)` 只能读 df 里**当前行及之前**的数据。任何 `shift(-n)`、
`rolling(...).mean().shift(-1)`、或者拿全样本统计量做标准化的写法都是未来函数，
会让 IC 好看得不真实。本模块的标准化一律在**单个横截面内**做，
不跨日期，就是为了堵住这条路。

唯一允许看未来的是 `evaluate.forward_returns`——那是被解释变量，
用于研究因子有没有用，绝不能进信号。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd


@dataclass(frozen=True)
class Factor:
    """一个因子。

    Attributes:
        name: 英文标识，也是面板里的列名。
        label: 中文名，界面显示用。
        category: 分类（动量 / 趋势 / 量能 / 波动 / 位置 / 形态）。
        compute: (df) -> Series，输入单只标的的日线，输出逐日因子值。
            长度和索引必须与 df 一致。
        direction: 1 表示值越大越好，-1 表示越小越好。横截面标准化时
            会乘上这个方向，使得合成总分永远是「越大越好」。
        needs: 依赖的指标流水线步骤（见 indicators/pipeline.PIPELINES）。
            缺列时 compute 应该返回全 NaN 而不是抛异常。
        help: 一句话说明这个因子在赌什么。
    """

    name: str
    label: str
    category: str
    compute: Callable[[pd.DataFrame], pd.Series]
    direction: int = 1
    needs: Tuple[str, ...] = ()
    help: str = ""


FACTOR_REGISTRY: Dict[str, Factor] = {}


def register_factor(factor: Factor) -> Factor:
    """注册一个因子。重名直接覆盖，方便在 notebook 里迭代。"""
    FACTOR_REGISTRY[factor.name] = factor
    return factor


def get_factor(name: str) -> Optional[Factor]:
    return FACTOR_REGISTRY.get(name)


def list_factors(category: Optional[str] = None) -> List[Factor]:
    """列出已注册因子，可按分类过滤。"""
    items = sorted(FACTOR_REGISTRY.values(), key=lambda f: (f.category, f.name))
    return [f for f in items if category is None or f.category == category]


def categories() -> List[str]:
    return sorted({f.category for f in FACTOR_REGISTRY.values()})


def required_steps(names: Sequence[str]) -> List[str]:
    """这批因子一共需要哪些指标流水线步骤（去重、保序）。

    和 backtest/exits.required_steps 同样的用意：不把依赖并进流水线，
    因子拿不到列就会静默返回 NaN，界面上看不出「这个因子没在工作」。
    """
    steps: List[str] = []
    for name in names:
        f = FACTOR_REGISTRY.get(name)
        if f is None:
            continue
        for step in f.needs:
            if step not in steps:
                steps.append(step)
    return steps


def missing_columns(df: pd.DataFrame, names: Sequence[str]) -> Dict[str, List[str]]:
    """哪些因子因为缺列而算不出来。给界面报告用。"""
    out: Dict[str, List[str]] = {}
    for name in names:
        f = FACTOR_REGISTRY.get(name)
        if f is None:
            out[name] = ["未注册的因子"]
            continue
        series = f.compute(df)
        if series is None or series.isna().all():
            out[name] = list(f.needs) or ["计算结果全为 NaN"]
    return out


def _col(df: pd.DataFrame, name: str) -> Optional[pd.Series]:
    """取一列并转数值，缺列返回 None——因子据此决定是否降级为 NaN。"""
    if name not in df.columns:
        return None
    return pd.to_numeric(df[name], errors="coerce")


def _nan_like(df: pd.DataFrame) -> pd.Series:
    return pd.Series(float("nan"), index=df.index)
