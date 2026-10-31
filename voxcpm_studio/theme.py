"""主题系统 —— VoxCPM2 Studio。

方向来自 OpenDesign 的 `elevenlabs` 设计系统（AI 语音产品：暖白画布、极细描边、
近黑药丸主按钮、300 字重标题、等宽数字），并针对桌面工具做了落地裁剪：

- 只保留一个容器层级：卡片是唯一的面，卡片里不再套描边盒子
- 深度只用 1px 极细线（无阴影），层次来自字号、字重与留白
- 主色即「墨色」：浅色下近黑、深色下近白；彩色只出现在状态点与波形上
- 药丸（24px，等价于全圆）只给主按钮、分段选择器与预设标签；卡片 16px、输入框 10px
- 文字三级全部 ≥ 4.5:1（浅色/深色两套分别校验）
"""

from __future__ import annotations

# 8px 基准间距：4 / 8 / 12 / 16 / 20 / 24 / 32
SPACE = 8
# 圆角阶梯：输入框 10 / 卡片 16 / 药丸 999
RADIUS_INPUT = 10
RADIUS_CARD = 16
# Qt QSS 的圆角一旦超过控件高度的一半就会判为无效、退化成直角，
# 所以「药丸」必须按控件高度分别给值（下面这些值 = 各自高度的一半）
RADIUS_PILL = 15      # 普通按钮 / 分段页签（高约 31–33）
RADIUS_CTA = 22       # 主按钮（高 44）
RADIUS_CHIP = 13      # 预设标签（高 26）
RADIUS_META = 10      # 指标小胶囊（高 20）

THEMES: dict[str, dict[str, str]] = {
    # 暖白（默认）：ElevenLabs 式 achromatic + 暖石头色
    "light": {
        "canvas": "#f5f2ef",
        "surface": "#ffffff",
        "surface2": "#faf8f6",
        "line": "#e6e4e0",
        "lineSoft": "#efece8",
        "text": "#0a0a0a",
        "text2": "#4e4e4e",
        "text3": "#736d65",   # 在暖白画布上 4.59:1 / 白卡上 5.12:1
        "cta": "#111111",
        "ctaHover": "#2a2a2a",
        "ctaText": "#ffffff",
        "segBar": "#eae6e0",
        "segActiveBg": "#ffffff",
        "segActiveText": "#0a0a0a",
        "inputBg": "#ffffff",
        "inputLine": "#e0ddd8",
        "chipBg": "#f7f5f2",
        "chipLine": "#e6e4e0",
        "focus": "#111111",
        "selBg": "#e8e2da",
        "waveA": "#e9a13b",
        "waveB": "#d3573f",
        "ok": "#1c7c4a",
        "warn": "#9a6a12",
        "err": "#b32d2d",
    },
    # 深色：电影感近黑，同一个结构、同一套尺度
    "dark": {
        "canvas": "#0b0b0c",
        "surface": "#141415",
        "surface2": "#0f0f10",
        "line": "#26262a",
        "lineSoft": "#1d1d20",
        "text": "#f6f4f1",
        "text2": "#a8a29a",
        "text3": "#8b857c",
        "cta": "#f5f2ef",
        "ctaHover": "#ffffff",
        "ctaText": "#0a0a0a",
        "segBar": "#1a1a1c",
        "segActiveBg": "#2a2a2e",
        "segActiveText": "#ffffff",
        "inputBg": "#0f0f10",
        "inputLine": "#2a2a2e",
        "chipBg": "#191919",
        "chipLine": "#2a2a2e",
        "focus": "#f5f2ef",
        "selBg": "#2c2c31",
        "waveA": "#f0b04a",
        "waveB": "#e0603f",
        "ok": "#4ade80",
        "warn": "#fbbf24",
        "err": "#f87171",
    },
}

DEFAULT_THEME = "light"
_current = DEFAULT_THEME


def set_theme(name: str) -> str:
    """设置全局主题（影响 tokens()、图标颜色与波形配色）。"""
    global _current
    _current = name if name in THEMES else DEFAULT_THEME
    return _current


def theme_name() -> str:
    return _current


def tokens() -> dict[str, str]:
    return THEMES[_current]


def qss(name: str | None = None) -> str:
    t = THEMES[name] if name in THEMES else tokens()
    return f"""
* {{ font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif; }}

QWidget {{ background: transparent; color: {t['text']}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {t['canvas']}; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {t['text3']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; border: none; height: 0; width: 0;
}}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 3px; min-width: 36px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['text3']}; }}

/* ---------------- 卡片：唯一的面 ---------------- */
QFrame#card {{
    background: {t['surface']};
    border: 1px solid {t['line']};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#cardTight {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: {RADIUS_INPUT}px; }}
QFrame#hairline {{ background: {t['lineSoft']}; border: none; max-height: 1px; min-height: 1px; }}

QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 20px; font-weight: 400; color: {t['text']}; }}
QLabel#subtitle {{ color: {t['text3']}; font-size: 11px; }}
QLabel#cardTitle {{ color: {t['text2']}; font-size: 12px; font-weight: 600; }}
QLabel#fieldLabel {{ color: {t['text2']}; font-size: 12px; }}
QLabel#hint {{ color: {t['text3']}; font-size: 11px; }}
QLabel#valueChip {{
    background: {t['chipBg']}; border: 1px solid {t['chipLine']}; border-radius: {RADIUS_META}px;
    color: {t['text2']}; font-size: 11px; font-weight: 500; padding: 2px 8px;
}}
QLabel#metaChip {{
    background: transparent; border: 1px solid {t['line']}; border-radius: {RADIUS_META}px;
    color: {t['text3']}; font-size: 11px; padding: 1px 8px;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}
QLabel#metaText {{
    background: transparent; border: none; color: {t['text3']}; font-size: 11px;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}
QLabel#logo {{ background: transparent; border: none; }}

/* 状态：小圆点 + 文字 */
QLabel#statusChip {{
    background: transparent; border: none; padding: 0 2px;
    color: {t['text3']}; font-size: 11px; font-weight: 500;
}}
QLabel#statusChip[state="ok"] {{ color: {t['ok']}; }}
QLabel#statusChip[state="busy"] {{ color: {t['warn']}; }}
QLabel#statusChip[state="err"] {{ color: {t['err']}; }}

/* ---------------- 输入控件 ---------------- */
QPlainTextEdit, QTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {t['inputBg']};
    border: 1px solid {t['inputLine']};
    border-radius: {RADIUS_INPUT}px;
    padding: 9px 11px;
    color: {t['text']};
    selection-background-color: {t['selBg']};
    selection-color: {t['text']};
}}
QPlainTextEdit:hover, QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {t['text3']};
}}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {t['focus']};
}}

/* 数值框：读作「数字」，不读作输入框 */
QSpinBox#valueSpin, QDoubleSpinBox#valueSpin {{
    background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 2px 4px; color: {t['text']}; font-size: 12px; font-weight: 600;
    font-family: "Cascadia Mono", "Consolas", monospace;
}}
QSpinBox#valueSpin:hover, QDoubleSpinBox#valueSpin:hover {{
    background: {t['chipBg']}; border-color: {t['line']};
}}
QSpinBox#valueSpin:focus, QDoubleSpinBox#valueSpin:focus {{
    background: {t['chipBg']}; border-color: {t['focus']};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {t['text3']}; width: 0; height: 0; margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px;
    selection-background-color: {t['chipBg']}; selection-color: {t['text']}; outline: none; padding: 4px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 0; height: 0; border: none; background: transparent; image: none;
}}

/* ---------------- 按钮 ---------------- */
QPushButton {{
    background: {t['surface']};
    border: 1px solid {t['line']};
    border-radius: {RADIUS_PILL}px;
    padding: 7px 14px;
    color: {t['text2']};
}}
QPushButton:hover {{ background: {t['chipBg']}; color: {t['text']}; border-color: {t['text3']}; }}
QPushButton:pressed {{ background: {t['lineSoft']}; padding-top: 8px; padding-bottom: 6px; }}
QPushButton:focus {{ border-color: {t['focus']}; }}
QPushButton:disabled {{ color: {t['text3']}; background: transparent; border-color: {t['line']}; }}

QPushButton#ghost {{
    background: transparent; border: 1px solid {t['line']}; color: {t['text2']};
}}
QPushButton#ghost:hover {{ background: {t['chipBg']}; color: {t['text']}; }}
QPushButton#ghost:pressed {{ background: {t['lineSoft']}; padding-top: 8px; padding-bottom: 6px; }}
QPushButton#ghost:checked {{ background: {t['cta']}; border-color: {t['cta']}; color: {t['ctaText']}; }}

/* 主按钮：药丸 + 墨色，全屏唯一的高对比元素 */
QPushButton#primary {{
    background: {t['cta']};
    border: none; color: {t['ctaText']}; font-size: 14px; font-weight: 600;
    padding: 13px 18px; border-radius: {RADIUS_CTA}px;
}}
QPushButton#primary:hover {{ background: {t['ctaHover']}; }}
QPushButton#primary:pressed {{ background: {t['ctaHover']}; padding-top: 14px; padding-bottom: 12px; }}
QPushButton#primary:focus {{ background: {t['ctaHover']}; }}
QPushButton#primary:disabled {{ background: {t['lineSoft']}; color: {t['text3']}; }}

/* 分段控件：药丸底 + 白色浮起片 */
QWidget#segmentBar {{
    background: {t['segBar']}; border: none; border-radius: {RADIUS_PILL}px;
}}
QPushButton#segment {{
    /* 始终保留 1px 边框：选中时只换颜色，避免页签高度跳动 */
    background: transparent; border: 1px solid transparent; border-radius: {RADIUS_PILL}px;
    padding: 8px 6px; color: {t['text3']}; font-size: 12px; font-weight: 600;
}}
QPushButton#segment:hover {{ color: {t['text']}; }}
QPushButton#segment:checked {{
    background: {t['segActiveBg']}; color: {t['segActiveText']};
    border: 1px solid {t['line']};
}}

/* 预设标签 */
QPushButton#chip {{
    background: {t['chipBg']}; border: 1px solid {t['chipLine']}; border-radius: {RADIUS_CHIP}px;
    padding: 5px 11px; color: {t['text2']}; font-size: 11px;
}}
QPushButton#chip:hover {{ border-color: {t['text3']}; color: {t['text']}; }}
/* 已选中的标签用墨色实心，一眼能看出当前组合 */
QPushButton#chip[active="on"] {{ background: {t['cta']}; border-color: {t['cta']}; color: {t['ctaText']}; }}
QPushButton#chip[active="on"]:hover {{ background: {t['ctaHover']}; border-color: {t['ctaHover']}; color: {t['ctaText']}; }}
QPushButton#chip:pressed {{ background: {t['lineSoft']}; }}

QCheckBox {{ spacing: 7px; color: {t['text2']}; font-size: 12px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {t['text3']}; background: transparent; }}
QCheckBox::indicator:hover {{ border-color: {t['text']}; }}
QCheckBox::indicator:checked {{ background: {t['cta']}; border-color: {t['cta']}; }}

/* ---------------- 滑块 ---------------- */
QSlider {{ min-height: 20px; }}
QSlider::groove:horizontal {{ height: 3px; background: {t['line']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['text']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    background: {t['surface']}; border: 1.5px solid {t['text']};
}}
QSlider::handle:horizontal:hover {{ background: {t['text']}; }}

/* ---------------- 进度条 ---------------- */
QProgressBar {{ background: {t['lineSoft']}; border: none; border-radius: 2px; height: 3px; }}
QProgressBar::chunk {{ background: {t['text']}; border-radius: 2px; }}

/* ---------------- 列表 ---------------- */
QListWidget {{ background: transparent; border: none; padding: 0; }}
QListWidget::item {{ padding: 9px 6px; border-bottom: 1px solid {t['lineSoft']}; color: {t['text2']}; }}
QListWidget::item:hover {{ color: {t['text']}; background: {t['chipBg']}; }}
QListWidget::item:selected {{ background: {t['chipBg']}; color: {t['text']}; }}

QSplitter::handle {{ background: transparent; width: 16px; }}

QStatusBar {{ background: {t['canvas']}; color: {t['text3']}; border-top: 1px solid {t['lineSoft']}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']};
    padding: 6px 9px; border-radius: 8px;
}}
QMenu {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 6px; color: {t['text2']}; }}
QMenu::item:selected {{ background: {t['chipBg']}; color: {t['text']}; }}

QDockWidget {{ color: {t['text3']}; font-size: 11px; font-weight: 500; }}
QDockWidget::title {{
    background: {t['surface2']}; padding: 6px 12px; border-top: 1px solid {t['line']};
    border-bottom: 1px solid {t['line']}; text-align: left;
}}
QPlainTextEdit#log {{
    background: {t['surface2']}; border: none; border-radius: 0;
    color: {t['text3']}; font-family: "Cascadia Mono", "Consolas", monospace; font-size: 11px;
    padding: 8px 12px; selection-background-color: {t['selBg']}; selection-color: {t['text']};
}}

QDialog QLabel {{ color: {t['text2']}; }}
QDialogButtonBox QPushButton {{ min-width: 78px; }}
"""


QSS = qss(DEFAULT_THEME)
