"""SVG 图标加载与着色（统一 1.8px 描边线性图标，24×24 网格）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from . import theme

ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"

# 语义色（由 set_theme 按当前主题刷新；需在创建窗口之前调用）
COLOR_TEXT = "#0a0a0a"
COLOR_MUTED = "#777169"
COLOR_DIM = "#777169"
COLOR_WHITE = "#ffffff"      # 主按钮（墨色/反色）上的图标色
COLOR_ACCENT = "#0a0a0a"
COLOR_OK = "#1c7c4a"
COLOR_WARN = "#9a6a12"
COLOR_ERR = "#b32d2d"

# logo 是自绘的：方底 + V，颜色跟随主题
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
    '<rect width="48" height="48" rx="13" fill="{bg}"/>'
    '<path d="M13.5 16h4.6l5.9 13.4L29.9 16h4.6l-8.3 18h-4.4z" fill="{fg}"/>'
    "</svg>"
)


def set_theme(name: str) -> str:
    """切换主题并刷新模块级语义色（主题变更后需要重启窗口）。"""
    global COLOR_TEXT, COLOR_MUTED, COLOR_DIM, COLOR_WHITE, COLOR_ACCENT
    global COLOR_OK, COLOR_WARN, COLOR_ERR
    name = theme.set_theme(name)
    t = theme.tokens()
    COLOR_TEXT = t["text"]
    COLOR_MUTED = t["text3"]
    COLOR_DIM = t["text3"]
    COLOR_WHITE = t["ctaText"]
    COLOR_ACCENT = t["text"]
    COLOR_OK = t["ok"]
    COLOR_WARN = t["warn"]
    COLOR_ERR = t["err"]
    svg_icon.cache_clear()
    raw_icon.cache_clear()
    return name

_RENDER_SIZES = (16, 20, 24, 32, 48)


def icon_path(name: str) -> Path:
    return ICON_DIR / f"{name}.svg"


@lru_cache(maxsize=256)
def svg_icon(name: str, color: str = COLOR_TEXT) -> QIcon:
    """把 SVG 渲染成指定颜色的多尺寸 QIcon（保留透明通道）。"""
    renderer = QSvgRenderer(str(icon_path(name)))
    icon = QIcon()
    tint = QColor(color)
    for size in _RENDER_SIZES:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(p, QRectF(0, 0, size, size))
        if name != "logo":  # logo 自带渐变，不做单色化
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), tint)
        p.end()
        icon.addPixmap(pm)
    return icon


@lru_cache(maxsize=16)
def raw_icon(name: str) -> QIcon:
    """保留原始配色的图标（用于 logo）。"""
    return QIcon(str(icon_path(name)))


def logo_pixmap(size: int = 34, bg: str | None = None, fg: str | None = None) -> QPixmap:
    """主题自适应的 logo：墨色方块 + 反色 V。"""
    from PyQt6.QtCore import QByteArray

    t = theme.tokens()
    svg = _LOGO_SVG.format(bg=bg or t["cta"], fg=fg or t["ctaText"])
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm
