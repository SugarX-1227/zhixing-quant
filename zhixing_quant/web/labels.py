"""界面上显示的中文文案。业务层返回的是英文代号，翻译只在这里做一次。"""

from __future__ import annotations

# DefenseEngine 返回的是英文代号，界面上不该出现 stop_loss_hit 这种东西
EXIT_REASON_TEXT = {
    "take_profit_hit": "触发止盈位",
    "stop_loss_hit": "触发止损位",
    "sell_signal": "S 系列卖点触发",
    "distribution": "识别到主力出货形态",
    "macd_death": "MACD 死叉加速",
    "white_break": "跌破白线",
    "yellow_break": "跌破黄线",
    "volume_stagnation": "放量滞涨",
    "dual_line_break_white": "跌破白线且次日未收回",
    "yoga_pants_stop": "触发止损位",
    "single_needle_break_tip": "跌破针尖",
    "b1_break_yellow": "跌破知行多空线",
}

PRIORITY_LABEL = {
    1: "P1 止盈", 2: "P2 止损", 3: "P3 卖点", 4: "P4 出货",
    5: "P5 MACD", 6: "P6 白线", 7: "P7 黄线", 8: "P8 滞涨",
}

BOOKS = {"swing": "波段账户", "scalp": "超短账户"}

REGIME_TEXT = {"BULL": "多头", "NEUTRAL": "中性", "BEAR": "空头"}

# 核心仓：多头区间闲置现金放进哪个指数（ETF 代理）
CORE_INDEXES = {"sh000905": "中证500", "sh000852": "中证1000",
                "sh000300": "沪深300", "sz399006": "创业板指"}


def exit_reason(code: str) -> str:
    return EXIT_REASON_TEXT.get(str(code), str(code))
