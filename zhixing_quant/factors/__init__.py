"""因子层：把「选哪只」从按成交额排序变成可量化、可检验的打分。

    base.py          因子定义与注册表
    library.py       内置因子库 + 预设权重组合
    cross_section.py 横截面去极值/标准化/加权合成
    evaluate.py      IC / ICIR / 分层收益（唯一允许读未来数据的模块）
"""

from zhixing_quant.factors.base import (Factor, categories, get_factor,
                                        list_factors, register_factor,
                                        required_steps)
from zhixing_quant.factors import library as _library   # noqa: F401  触发注册

__all__ = ["Factor", "register_factor", "get_factor", "list_factors",
           "categories", "required_steps"]
