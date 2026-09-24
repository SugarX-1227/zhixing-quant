"""把 pandas / numpy / dataclass 结果转成可以直接 json.dumps 的结构。

Starlette 的 JSONResponse 用 allow_nan=False，任何一个 NaN 漏过去整个请求就 500。
所以所有出口都走 jsonable()，NaN / inf 一律变 None，前端显示成「—」。
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def scalar(v: Any) -> Any:
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.strftime("%Y-%m-%d")
    return v


def jsonable(obj: Any) -> Any:
    if isinstance(obj, pd.DataFrame):
        return frame(obj)
    if isinstance(obj, pd.Series):
        return series(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    return scalar(obj)


def frame(df: pd.DataFrame, index: bool = False) -> dict:
    """表格：{"columns": [...], "rows": [[...], ...]}。

    Args:
        index: 索引有意义（分层标签、卖出原因之类）时带上，作为第一列。
    """
    if df is None:
        return {"columns": [], "rows": []}
    if index:
        df = df.reset_index()
    return {
        "columns": [str(c) for c in df.columns],
        "rows": [[scalar(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


def series(s: pd.Series) -> dict:
    """曲线：{"x": [日期...], "y": [值...]}。"""
    if s is None:
        return {"x": [], "y": []}
    return {"x": [scalar(i) for i in s.index], "y": [scalar(v) for v in s.values]}


def records(df: pd.DataFrame) -> list:
    if df is None or df.empty:
        return []
    cols = [str(c) for c in df.columns]
    return [dict(zip(cols, (scalar(v) for v in row)))
            for row in df.itertuples(index=False, name=None)]
