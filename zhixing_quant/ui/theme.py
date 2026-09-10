"""设计令牌 + Streamlit CSS 注入 + Plotly 主题（浅色）。

用法（st.set_page_config 之后立刻调用一次）：

    from zhixing_quant.ui.theme import inject_css
    inject_css()

配色说明：

浅色不是把深色反过来。红绿在白底上比在黑底上更亮更跳，直接沿用深色版的
#F0483E / #14A87C 会刺眼且互相打架，所以两个语义色都往深里压了一档，
保证在白底上的对比度达标，同时红仍然比绿"热"。

底色用 #FAFAF8 而不是纯白：纯白配大面积表格会有眩光，长时间看很累。
面板用纯白 #FFFFFF 反而比底色亮，形成层次，不需要靠阴影。

强调色 #8F6708 是知行多空线那个黄的深色版本。原来深色版用的 #F2B01E
在白底上只有 1.83:1，做文字和边框完全不可用；#8F6708 达到 4.89:1，
白字按钮 5.11:1，都过 WCAG AA。但画在 K 线图上仍然用亮版 #E8A317——
线条是图形不是文字，要的是跳出来而不是可读性。所以 SIGNAL 和
SIGNAL_LINE 是两个值，不要合并。

弱文字用 #646E7B（4.95:1）。第一版试的 #8A93A0 只有 2.97:1，
在白底上小字几乎看不清。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 令牌
# ---------------------------------------------------------------------------

BG = "#FAFAF8"          # 页面底色，微暖灰，避免纯白眩光
SURFACE = "#FFFFFF"     # 面板，比底色亮，靠明度差分层
SURFACE_2 = "#F3F4F1"   # 选中态 / 次级面板
LINE = "#DFE1DC"        # 描边
LINE_SOFT = "#EBEDE8"   # 发丝分隔线

# 中国股市：红涨绿跌。浅色版比深色版压深一档，保证白底对比度。
UP = "#C62828"
DOWN = "#00796B"
UP_DIM = "#C6282814"
DOWN_DIM = "#00796B14"

# 强调 = 知行多空线。文字/边框用深版，图表线条用亮版。
SIGNAL = "#8F6708"
SIGNAL_LINE = "#E8A317"
SIGNAL_DIM = "#8F670814"

TEXT = "#1C1F23"
TEXT_2 = "#5A6470"
TEXT_3 = "#646E7B"

MA_FAST = "#1E5FBF"

FONT_STACK = (
    'Inter, "Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", '
    '"Hiragino Sans GB", sans-serif'
)
RADIUS = "6px"


# ---------------------------------------------------------------------------
# Plotly
# ---------------------------------------------------------------------------

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=FONT_STACK, size=12, color=TEXT_2),
    margin=dict(l=8, r=56, t=28, b=8),
    xaxis=dict(showgrid=False, zeroline=False, linecolor=LINE,
               tickfont=dict(size=10.5)),
    yaxis=dict(showgrid=True, gridcolor=LINE_SOFT, gridwidth=1, zeroline=False,
               side="right",                 # A 股软件惯例：价格刻度在右
               tickfont=dict(size=10.5)),
    hoverlabel=dict(bgcolor=SURFACE, bordercolor=LINE,
                    font=dict(family=FONT_STACK, size=12, color=TEXT)),
    legend=dict(orientation="h", y=1.06, x=0, font=dict(size=11.5),
                bgcolor="rgba(0,0,0,0)"),
    dragmode="pan",
)


def candlestick_colors() -> dict:
    """阳线红色空心、阴线绿色实心，A 股软件惯例。

    Plotly 默认是绿涨红跌，必须覆盖，否则涨跌颜色是反的。
    """
    return dict(
        increasing=dict(line=dict(color=UP, width=1.1), fillcolor="rgba(0,0,0,0)"),
        decreasing=dict(line=dict(color=DOWN, width=1.1), fillcolor=DOWN),
    )


def volume_colors(open_series, close_series) -> list:
    return [UP if c >= o else DOWN for o, c in zip(open_series, close_series)]


def apply_layout(fig, height: int = 640, title: str = ""):
    fig.update_layout(**PLOTLY_LAYOUT, height=height, title=title,
                      xaxis_rangeslider_visible=False, showlegend=True)
    fig.update_xaxes(type="category", nticks=12)
    return fig


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = f"""
<style>
:root{{
  --bg:{BG}; --surface:{SURFACE}; --surface-2:{SURFACE_2};
  --line:{LINE}; --line-soft:{LINE_SOFT};
  --up:{UP}; --down:{DOWN}; --up-dim:{UP_DIM}; --down-dim:{DOWN_DIM};
  --signal:{SIGNAL}; --signal-line:{SIGNAL_LINE}; --signal-dim:{SIGNAL_DIM};
  --text:{TEXT}; --text-2:{TEXT_2}; --text-3:{TEXT_3};
  --r:{RADIUS};
}}

html, body, .stApp, [class*="css"] {{
  font-family:{FONT_STACK};
  font-variant-numeric:tabular-nums; font-feature-settings:"tnum";
}}
.stApp {{ background:var(--bg); color:var(--text); }}
.block-container {{ padding:26px 32px 60px; max-width:1400px; }}
#MainMenu, footer, header {{ visibility:hidden; }}

section[data-testid="stSidebar"] {{
  background:var(--surface); border-right:1px solid var(--line-soft);
  width:210px !important;
}}
section[data-testid="stSidebar"] .block-container {{ padding:20px 14px; }}

h1 {{ font-size:21px !important; font-weight:600 !important; color:var(--text); }}
h2 {{ font-size:15px !important; font-weight:600 !important; margin:0 0 10px !important; }}
h3 {{ font-size:14px !important; font-weight:600 !important; }}

.stButton > button {{
  height:32px; padding:0 14px; border-radius:var(--r);
  background:var(--surface); border:1px solid var(--line); color:var(--text);
  font-size:13px; transition:border-color .12s, background .12s;
}}
.stButton > button:hover {{ border-color:var(--text-3); background:var(--surface-2); }}
.stButton > button[kind="primary"] {{
  background:var(--signal); border-color:var(--signal); color:#fff; font-weight:600;
}}
.stButton > button[kind="primary"]:hover {{ filter:brightness(1.1); }}
.stButton > button:focus-visible {{ outline:2px solid var(--signal); outline-offset:2px; }}

.stSelectbox div[data-baseweb="select"] > div,
.stTextInput input, .stDateInput input, .stNumberInput input {{
  background:var(--surface) !important; border-color:var(--line) !important;
  color:var(--text) !important; font-size:13px !important;
  border-radius:var(--r) !important;
}}

[data-testid="stDataFrame"] {{ border:1px solid var(--line-soft); border-radius:var(--r); }}
[data-testid="stDataFrame"] [role="columnheader"] {{
  background:var(--bg) !important; color:var(--text-3) !important;
  font-size:11.5px !important; font-weight:500 !important;
}}
[data-testid="stDataFrame"] [role="gridcell"] {{
  font-size:13.5px !important; border-color:var(--line-soft) !important;
}}

[data-testid="stMetric"] {{
  background:var(--surface); padding:14px 16px; border-radius:0;
  box-shadow:0 0 0 1px var(--line-soft);
}}
[data-testid="stMetricLabel"] {{ font-size:11.5px !important; color:var(--text-3) !important; }}
[data-testid="stMetricValue"] {{ font-size:22px !important; font-weight:600 !important; }}

.stAlert {{ border-radius:var(--r); border:none; font-size:13px; }}
.stProgress > div > div > div > div {{ background:var(--signal); }}
hr {{ border-color:var(--line-soft); margin:22px 0; }}
.stPlotlyChart {{
  background:var(--surface); border:1px solid var(--line-soft);
  border-radius:var(--r); padding:12px 8px 4px;
}}


/* ---- 侧栏导航 ----
   原型里导航是自定义 HTML，Streamlit 的 radio 带圆圈、间距大，长得完全不一样。
   改用 button 做导航，这里把它还原成原型的样子：
   未选中 = 透明底 + 次要文字色；选中 = 淡黄底 + 黄字 + 左侧黄条。      */
section[data-testid="stSidebar"] .stButton > button {{
  height:auto; min-height:34px; padding:7px 11px;
  background:transparent; border:none; border-left:2px solid transparent;
  border-radius:var(--r); color:var(--text-2);
  font-size:13.5px; font-weight:400; text-align:left;
  justify-content:flex-start; box-shadow:none;
}}
section[data-testid="stSidebar"] .stButton > button:hover {{
  background:var(--surface-2); color:var(--text); border-left-color:transparent;
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {{
  background:var(--signal-dim); color:var(--signal);
  border-left:2px solid var(--signal); font-weight:600;
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {{
  background:var(--signal-dim); filter:none;
}}
section[data-testid="stSidebar"] .stButton > button p {{
  font-size:13.5px; margin:0;
}}
/* 账户切换：紧凑，不要抢导航的注意力 */
section[data-testid="stSidebar"] [data-testid="stSegmentedControl"] button {{
  font-size:12.5px; padding:3px 10px;
}}

/* 参数面板的 tab 收窄一点，四个 tab 不该占掉半屏 */
.stTabs [data-baseweb="tab"] {{ padding:6px 14px; font-size:13px; }}
.stTabs [data-baseweb="tab-list"] {{ gap:2px; }}

/* 表格选中行用强调色，配合左表右图的交互 */
[data-testid="stDataFrame"] [role="row"][aria-selected="true"] {{
  background:var(--signal-dim) !important;
}}

@media (prefers-reduced-motion: reduce) {{
  * {{ transition:none !important; animation:none !important; }}
}}
</style>
"""


def inject_css() -> None:
    import streamlit as st
    st.markdown(CSS, unsafe_allow_html=True)
