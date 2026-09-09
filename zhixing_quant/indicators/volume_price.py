"""Volume-price indicators: double volume, sky volume, floor volume, pullback."""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import ma, ref


def add_volume_price(df: pd.DataFrame, vol_ma_window: int = 5) -> pd.DataFrame:
    """Append volume-price indicator columns.

    Args:
        df: Daily OHLCV DataFrame.
        vol_ma_window: Moving average window for volume baseline.

    Returns:
        Copy of df with volume-price columns.

    Rule source:
        通达信行情指标与选股指标(1).md 第 3.3 节:
        - 倍量: vol / prev_vol >= 2.0
        - 天量: 连续两日倍量
        - 地量: vol == 20 日最低
        - 缩量: vol < MA(vol,5) * 0.6 AND vol < prev_vol
        - 有效缩量: is_low_vol AND 收盘价在 5 日均线之上
    """
    out = df.copy()
    vol = pd.to_numeric(out["vol"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")

    vol_prev = ref(vol, 1)
    vol_ratio = vol / vol_prev.replace(0, float("nan"))
    vol_ma = ma(vol, vol_ma_window)
    vol_ma20 = ma(vol, 20)
    close_ma5 = ma(close, 5)

    out["vol_ratio_prev"] = vol_ratio
    out["is_double_vol"] = vol_ratio >= 2.0
    # Keep the shifted boolean dtype explicit; fillna on the object result
    # emits a pandas downcasting warning on recent versions.
    out["is_sky_vol"] = out["is_double_vol"] & out["is_double_vol"].shift(
        1, fill_value=False
    )
    out["is_low_vol"] = (vol < vol_ma * 0.6) & (vol < vol_prev)
    out["is_floor_vol"] = vol == ma(vol, 20).rolling(20, min_periods=1).min()
    out["is_flat_vol"] = (vol_ratio - 1).abs() < 0.05
    out["pullback_low_vol"] = out["is_low_vol"] & (close > close_ma5)

    return out
