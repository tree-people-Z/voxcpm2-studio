"""自定义控件：波形显示、分段控件、标签按钮、拖放输入框、应用图标。"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QAction, QBrush, QColor, QFont, QLinearGradient, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtCore import QSize
from .theme import tokens

from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)

RADIUS = 12.0


class WaveformWidget(QWidget):
    """带刻度与空状态的波形视图（按像素列取峰峰值）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._peaks: tuple[np.ndarray, np.ndarray] | None = None
        self._duration = 0.0
        self._placeholder = "还没有音频"
        self._sub_placeholder = "填写文本后点击「开始合成」"
        t = tokens()
        self._color = QColor(t["waveA"])
        self._color2 = QColor(t["waveB"])

    # ------------------------------------------------------------ #
    def set_audio(self, wav: np.ndarray | None, sample_rate: int) -> None:
        if wav is None or len(wav) == 0:
            self._peaks = None
            self._duration = 0.0
        else:
            wav = np.asarray(wav, dtype=np.float32).reshape(-1)
            self._duration = len(wav) / max(sample_rate, 1)
            self._peaks = self._compute_peaks(wav, max(self.width() - 2, 120))
        self.update()

    @staticmethod
    def _compute_peaks(wav: np.ndarray, columns: int) -> tuple[np.ndarray, np.ndarray]:
        n = len(wav)
        if n == 0:
            return np.zeros(columns, dtype=np.float32), np.zeros(columns, dtype=np.float32)
        columns = max(1, min(columns, n))
        step = max(1, n // columns)
        usable = (n // step) * step
        seg = wav[:usable].reshape(-1, step)
        mins = seg.min(axis=1).astype(np.float32)
        maxs = seg.max(axis=1).astype(np.float32)
        if len(mins) > columns:  # 取最后 columns 列，保持时间轴对齐
            mins = mins[-columns:]
            maxs = maxs[-columns:]
        peak = max(float(np.abs(mins).max()), float(np.abs(maxs).max()), 1e-6)
        return mins / peak, maxs / peak

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update()

    # ------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        clip = QPainterPath()
        clip.addRoundedRect(rect, RADIUS, RADIUS)
        p.setClipPath(clip)

        # 背景（自上而下微渐变）
        bg = QLinearGradient(
            QPointF(float(rect.left()), float(rect.top())),
            QPointF(float(rect.left()), float(rect.bottom())),
        )
        t = tokens()
        bg.setColorAt(0.0, QColor(t["surface2"]))
        bg.setColorAt(1.0, QColor(t["canvas"]))
        p.fillRect(rect, QBrush(bg))

        mid = rect.center().y()
        ruler_h = 20.0
        body_bottom = rect.bottom() - ruler_h

        if self._peaks is None:
            self._paint_empty(p, rect)
            p.end()
            return

        # 水平网格线：只在有音频时出现，空状态保持干净
        grid = QColor(t["line"])
        grid.setAlpha(170)
        p.setPen(QPen(grid, 1))
        for f in (0.25, 0.5, 0.75):
            y = float(rect.top() + (body_bottom - rect.top()) * f)
            p.drawLine(QPointF(float(rect.left() + 8), y), QPointF(float(rect.right() - 8), y))

        mins, maxs = self._peaks
        half = (body_bottom - rect.top()) / 2 - 7
        grad = QLinearGradient(
            QPointF(float(rect.left()), float(rect.top())),
            QPointF(float(rect.right()), float(rect.top())),
        )
        grad.setColorAt(0.0, self._color)
        grad.setColorAt(1.0, self._color2)
        p.setPen(QPen(QBrush(grad), 1.4))
        left = float(rect.left() + 1)
        for i in range(len(mins)):
            x = left + i + 0.5
            y1 = float(mid - float(maxs[i]) * half)
            y2 = float(mid - float(mins[i]) * half)
            if abs(y2 - y1) < 1.2:
                y2 = y1 + 1.2
            p.drawLine(QPointF(x, y1), QPointF(x, y2))

        self._paint_ruler(p, rect, ruler_h, body_bottom)
        p.end()

    # ------------------------------------------------------------ #
    def _paint_empty(self, p: QPainter, rect: QRectF) -> None:
        inner = rect.adjusted(8, 8, -8, -8)

        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor(tokens()["text2"])))
        title_rect = QRectF(
            float(inner.left()), float(inner.center().y() - 20), float(inner.width()), 20.0
        )
        p.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), self._placeholder)

        f2 = QFont(self.font())
        f2.setPointSize(9)
        p.setFont(f2)
        p.setPen(QPen(QColor(tokens()["text3"])))
        sub_rect = QRectF(
            float(inner.left()), float(inner.center().y() + 2), float(inner.width()), 18.0
        )
        p.drawText(sub_rect, int(Qt.AlignmentFlag.AlignCenter), self._sub_placeholder)

    def _paint_ruler(self, p: QPainter, rect: QRectF, ruler_h: float, body_bottom: float) -> None:
        ruler = QColor(tokens()["line"])
        ruler.setAlpha(200)
        p.setPen(QPen(ruler, 1))
        p.drawLine(
            QPointF(float(rect.left() + 8), float(body_bottom)),
            QPointF(float(rect.right() - 8), float(body_bottom)),
        )

        f = QFont(self.font())
        f.setPointSize(8)
        f.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        p.setFont(f)
        fm = p.fontMetrics()
        dur = max(self._duration, 0.01)
        steps = 5
        span = float(rect.width() - 16)
        for i in range(steps + 1):
            frac = i / steps
            x = float(rect.left() + 8 + span * frac)
            p.setPen(QPen(QColor(tokens()["text3"]), 1))
            p.drawLine(QPointF(x, float(body_bottom)), QPointF(x, float(body_bottom + 4)))
            label = f"{dur * frac:.1f}s"
            tw = int(fm.horizontalAdvance(label))
            lx = min(max(x, float(rect.left() + tw / 2 + 2)), float(rect.right() - tw / 2 - 2))
            p.setPen(QPen(QColor(tokens()["text3"])))
            p.drawText(
                QRectF(float(lx - tw / 2 - 2), float(body_bottom + 4), float(tw + 4), ruler_h - 4),
                int(Qt.AlignmentFlag.AlignCenter),
                label,
            )


class HistoryList(QListWidget):
    """历史记录列表：空状态自己画提示，避免显示一块无意义的空白。"""

    EMPTY_TITLE = "还没有生成记录"
    EMPTY_HINT = "合成完成的音频会自动出现在这里，双击播放"

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.count():
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.viewport().rect())

        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor(tokens()["text2"])))
        p.drawText(
            QRectF(r.left(), r.center().y() - 22, r.width(), 20),
            int(Qt.AlignmentFlag.AlignCenter),
            self.EMPTY_TITLE,
        )

        f2 = QFont(self.font())
        f2.setPointSize(9)
        p.setFont(f2)
        p.setPen(QPen(QColor(tokens()["text3"])))
        p.drawText(
            QRectF(r.left(), r.center().y(), r.width(), 18),
            int(Qt.AlignmentFlag.AlignCenter),
            self.EMPTY_HINT,
        )
        p.end()


class SegmentedControl(QWidget):
    """iOS 风格分段选择器。"""

    changed = pyqtSignal(str)

    def __init__(self, items: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segmentBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, label in items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("segment")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._group.addButton(btn)
            lay.addWidget(btn)
            self._buttons[key] = btn
            btn.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        if self._buttons:
            next(iter(self._buttons.values())).setChecked(True)

    def current_key(self) -> str:
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return next(iter(self._buttons), "")

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
            self.changed.emit(key)


class TextEdit(QPlainTextEdit):
    """sizeHint 收紧的文本框：避免把父级滚动区域的最小高度撑大。"""

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 110)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, 92)


class TagButton(QPushButton):
    """维度标签按钮：点开挑一个标签，选中的会显示在按钮上。

    每个按钮只管一个维度（性别年龄 / 音色 / 情绪…），多个按钮组合成完整描述。
    """

    picked = pyqtSignal(str, str)  # (维度, 标签；空字符串表示取消本维度)

    def __init__(self, dimension: str, tags: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dimension = dimension
        self.value = ""
        self.setObjectName("chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"选一个「{dimension}」标签，多个维度会自动拼成一句描述")

        self._menu = QMenu(self)
        none_act = self._menu.addAction("不指定")
        none_act.triggered.connect(lambda: self._choose(""))
        self._menu.addSeparator()
        self._actions: dict[str, QAction] = {}
        for tag in tags:
            act = self._menu.addAction(tag)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, t=tag: self._choose(t))
            self._actions[tag] = act
        self.setMenu(self._menu)
        self._sync()

    # ------------------------------------------------------------ #
    def _choose(self, tag: str) -> None:
        self.value = tag
        for name, act in self._actions.items():
            act.setChecked(name == tag)
        self._sync()
        self.picked.emit(self.dimension, tag)

    def clear(self) -> None:
        if self.value:
            self._choose("")

    def _sync(self) -> None:
        if self.value:
            self.setText(f"{self.dimension} · {self.value}")
            self.setProperty("active", "on")
        else:
            self.setText(f"{self.dimension}  ▾")
            self.setProperty("active", "off")
        self.updateGeometry()
        self.style().unpolish(self)
        self.style().polish(self)


class DropLineEdit(QLineEdit):
    """支持拖放音频文件的路径输入框。"""

    fileDropped = pyqtSignal(str)
    AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac"}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setPlaceholderText("拖入音频文件，或点击右侧「浏览…」")

    @classmethod
    def _first_audio(cls, mime) -> str | None:
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if any(path.lower().endswith(ext) for ext in cls.AUDIO_EXT):
                return path
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._first_audio(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._first_audio(event.mimeData())
        if path:
            self.setText(path)
            self.fileDropped.emit(path)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


def elide_middle(text: str, limit: int = 48) -> str:
    if len(text) <= limit:
        return text
    keep = (limit - 3) // 2
    return text[:keep] + "…" + text[-keep:]


