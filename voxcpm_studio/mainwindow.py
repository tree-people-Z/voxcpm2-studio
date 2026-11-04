"""VoxCPM2 Studio 主窗口。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .config import MODES, AppConfig
from .engine import GenerationParams, audio_duration, get_engine, save_wav
from .icons import (
    COLOR_ACCENT,
    COLOR_DIM,
    COLOR_MUTED,
    COLOR_TEXT,
    COLOR_WHITE,
    logo_pixmap,
    svg_icon,
)
from .model_setup import ModelSetupDialog
from .presets import CLONE_TAGS, DESIGN_TAGS, QUICK_COMBOS, compose
from .widgets import (
    DropLineEdit,
    TagButton,
    HistoryList,
    SegmentedControl,
    TextEdit,
    WaveformWidget,
    elide_middle,
)
from .workers import GenerateWorker, LoadModelWorker


# ==================================================================== #
class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(600)
        self.cfg = cfg

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.model_edit = DropLineEdit(cfg.model_dir)
        b1 = QPushButton("浏览…")
        b1.setObjectName("ghost")
        b1.clicked.connect(self._pick_model)
        row1 = QHBoxLayout()
        row1.addWidget(self.model_edit, 1)
        row1.addWidget(b1)
        w1 = QWidget()
        w1.setLayout(row1)
        form.addRow("模型目录", w1)

        self.device_combo = QComboBox()
        self.device_combo.addItems(["auto", "cuda", "cpu"])
        self.device_combo.setCurrentText(cfg.device)
        form.addRow("运行设备", self.device_combo)

        self.denoiser_check = QCheckBox("启用 ZipEnhancer 降噪模型（参考音频降噪，需额外下载）")
        self.denoiser_check.setChecked(cfg.use_denoiser)
        form.addRow("", self.denoiser_check)

        self.optimize_check = QCheckBox("torch.compile 加速（需 CUDA + triton，Windows 通常自动跳过）")
        self.optimize_check.setToolTip(
            "PyTorch 的 JIT 编译器：把逐算子执行合成融合 kernel。官方需要 triton，"
            "Windows 官方不发 triton wheel，缺少时会在加载日志里说明并自动跳过，不会报错。"
        )
        self.optimize_check.setChecked(cfg.optimize)
        form.addRow("", self.optimize_check)

        self.out_edit = DropLineEdit(cfg.output_dir)
        b2 = QPushButton("浏览…")
        b2.setObjectName("ghost")
        b2.clicked.connect(self._pick_out)
        row2 = QHBoxLayout()
        row2.addWidget(self.out_edit, 1)
        row2.addWidget(b2)
        w2 = QWidget()
        w2.setLayout(row2)
        form.addRow("输出目录", w2)

        self.autosave_check = QCheckBox("生成完成后自动保存 WAV")
        self.autosave_check.setChecked(cfg.auto_save)
        form.addRow("", self.autosave_check)

        self.api_check = QCheckBox("启用本地 API（供其它程序 / AI 调用）")
        self.api_check.setChecked(getattr(cfg, "api_enabled", False))
        self.api_check.setToolTip("默认只监听 127.0.0.1，接口说明见 GET /help")
        self.api_port = QSpinBox()
        self.api_port.setRange(1024, 65535)
        self.api_port.setValue(getattr(cfg, "api_port", 7810))
        self.api_port.setFixedWidth(96)
        self.api_port.setToolTip("端口被占用时换一个（1024–65535）")
        api_row = QHBoxLayout()
        api_row.addWidget(self.api_check, 1)
        api_row.addWidget(QLabel("端口"))
        api_row.addWidget(self.api_port)
        w_api = QWidget()
        w_api.setLayout(api_row)
        form.addRow("本地 API", w_api)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色 · 暖白工作台", "light")
        self.theme_combo.addItem("深色 · 影院黑", "dark")
        idx = self.theme_combo.findData(getattr(cfg, "theme", "light"))
        self.theme_combo.setCurrentIndex(max(idx, 0))
        self.theme_combo.setToolTip("切换后需重启软件生效")
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo, 1)
        theme_hint = QLabel("重启后生效")
        theme_hint.setObjectName("hint")
        theme_row.addWidget(theme_hint)
        w3 = QWidget()
        w3.setLayout(theme_row)
        form.addRow("界面外观", w3)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(16)
        title = QLabel("设置")
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _pick_model(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择 VoxCPM2 模型目录", self.model_edit.text())
        if d:
            self.model_edit.setText(d)

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def apply_to(self, cfg: AppConfig) -> bool:
        reload_needed = (
            cfg.model_dir != self.model_edit.text().strip()
            or cfg.device != self.device_combo.currentText()
            or cfg.use_denoiser != self.denoiser_check.isChecked()
            or cfg.optimize != self.optimize_check.isChecked()
        )
        cfg.model_dir = self.model_edit.text().strip()
        cfg.device = self.device_combo.currentText()
        cfg.use_denoiser = self.denoiser_check.isChecked()
        cfg.optimize = self.optimize_check.isChecked()
        cfg.output_dir = self.out_edit.text().strip()
        cfg.auto_save = self.autosave_check.isChecked()
        cfg.theme = self.theme_combo.currentData() or "light"
        cfg.api_enabled = self.api_check.isChecked()
        cfg.api_port = int(self.api_port.value())
        return reload_needed


# ==================================================================== #
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = AppConfig.load()
        self.engine = get_engine()
        self.setWindowTitle("VoxCPM2 Studio")
        self.setWindowIcon(QIcon(logo_pixmap(64)))
        self.resize(1360, 880)
        self.setMinimumSize(960, 600)   # 1366×768 笔记本也能放下；更小时两列各自滚动

        self._buf: list[np.ndarray] = []
        self._last_result = None
        self._last_wav_path: Path | None = None
        self._playing_path: Path | None = None
        self._gen_worker: GenerateWorker | None = None
        self._load_worker: LoadModelWorker | None = None
        self._gen_t0 = 0.0
        self._history: list[Path] = []
        self.api_server = None

        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.9)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.playbackStateChanged.connect(self._on_playback_state)

        self._build_ui()
        self._restore_state()

        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._on_tick)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.start_generation)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self.start_generation)
        QShortcut(QKeySequence("Ctrl+Space"), self, self.toggle_play)
        QShortcut(QKeySequence("Ctrl+L"), self, self.btn_log.toggle)

        QTimer.singleShot(0, self._sync_page_min_height)
        QTimer.singleShot(300, self._startup)

    # ================================================================ #
    # 布局骨架
    # ================================================================ #
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(12)
        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 45)
        splitter.setStretchFactor(1, 55)
        splitter.setSizes([610, 740])
        root.addWidget(splitter, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        dock = QDockWidget("运行日志", self)
        dock.setWidget(self.log_view)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.log_dock = dock
        self.resizeDocks([dock], [96], Qt.Orientation.Vertical)
        # 日志默认收起：空面板不占版面，出错时自动弹出
        dock.hide()
        self.btn_log.toggled.connect(dock.setVisible)
        dock.visibilityChanged.connect(self.btn_log.setChecked)

        self.statusBar().showMessage("就绪")

    def _make_card(self, title: str | None = None, icon: str | None = None):
        """返回 (卡片, 内容布局, 标题栏布局)。"""
        card = QFrame()
        card.setObjectName("card")
        # 卡片按内容定高：多余空间留给「待合成文本」与列尾，窗口变矮时才出现滚动条
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        body = QVBoxLayout(card)
        body.setContentsMargins(18, 15, 18, 17)
        body.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(7)
        if title:
            if icon:
                ico = QLabel()
                ico.setPixmap(svg_icon(icon, COLOR_DIM).pixmap(13, 13))
                head.addWidget(ico)
            lbl = QLabel(title)
            lbl.setObjectName("cardTitle")
            head.addWidget(lbl)
            head.addStretch(1)
            body.addLayout(head)
        return card, body, head

    # ---------------------------------------------------------------- #
    def _build_header(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(logo_pixmap(34))
        logo.setFixedSize(34, 34)
        logo.setToolTip("VoxCPM2 Studio")
        lay.addWidget(logo)

        box = QVBoxLayout()
        box.setSpacing(1)
        title = QLabel("VoxCPM2 Studio")
        title.setObjectName("title")
        tf = QFont(title.font())
        title.setFont(tf)
        sub = QLabel("Tokenizer-Free TTS · 30 语言 · 声音设计 / 克隆 · 48kHz")
        sub.setObjectName("subtitle")
        box.addWidget(title)
        box.addWidget(sub)
        lay.addLayout(box)
        lay.addStretch(1)

        self.pill_model = QLabel("模型未加载")
        self.pill_model.setObjectName("statusChip")
        self.pill_model.setProperty("state", "idle")
        lay.addWidget(self.pill_model)

        self.chip_device = QLabel("设备 —")
        self.chip_device.setObjectName("metaText")
        lay.addWidget(self.chip_device)

        self.chip_vram = QLabel("显存 —")
        self.chip_vram.setObjectName("metaText")
        lay.addWidget(self.chip_vram)

        self.btn_load = QPushButton("加载模型")
        self.btn_load.setObjectName("ghost")
        self.btn_load.setIcon(svg_icon("refresh", COLOR_TEXT))
        self.btn_load.setIconSize(QSize(15, 15))
        self.btn_load.clicked.connect(self.load_model)
        lay.addWidget(self.btn_load)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setIcon(svg_icon("settings", COLOR_TEXT))
        self.btn_settings.setIconSize(QSize(15, 15))
        self.btn_settings.clicked.connect(self.open_settings)
        lay.addWidget(self.btn_settings)

        self.btn_log = QPushButton("日志")
        self.btn_log.setObjectName("ghost")
        self.btn_log.setCheckable(True)
        self.btn_log.setIcon(svg_icon("terminal", COLOR_MUTED))
        self.btn_log.setIconSize(QSize(15, 15))
        self.btn_log.setToolTip("展开 / 收起运行日志（Ctrl+L）")
        lay.addWidget(self.btn_log)

        return w

    # ---------------------------------------------------------------- #
    def _build_left(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 4, 6)
        lay.setSpacing(12)

        # ① 生成模式
        card, body, _ = self._make_card("生成模式", "layers")
        self.mode_ctrl = SegmentedControl(
            [(key, label.split("（")[0]) for key, label in MODES.items()]
        )
        self.mode_ctrl.changed.connect(lambda _k: self._on_mode_changed())
        body.addWidget(self.mode_ctrl)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("hint")
        self.mode_hint.setWordWrap(True)
        body.addWidget(self.mode_hint)
        lay.addWidget(card)

        # ② 文本
        card, body, head = self._make_card("待合成文本", "type")
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.lbl_chars = QLabel("0 字")
        self.lbl_chars.setObjectName("metaChip")
        head.addWidget(self.lbl_chars)

        self.text_edit = TextEdit()
        self.text_edit.setPlaceholderText(
            "输入要合成的文本，支持中 / 英 / 日 / 韩等 30 种语言，无需指定语种…\n\n"
            "标点即韵律：句号=较长停顿，逗号=短停顿，省略号=迟疑。\n"
            "方言请直接写方言用词，例如：伙計，唔該一個A餐，凍奶茶少甜！"
        )
        self.text_edit.setMinimumHeight(96)
        self.text_edit.textChanged.connect(self._update_char_count)
        body.addWidget(self.text_edit, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.split_check = QCheckBox("长文本分段合成")
        self.split_check.setToolTip("长文本容易掉速 / 爆音 / 爆显存，按句切分后拼接更稳定")
        self.split_check.toggled.connect(self._on_split_toggled)
        row.addWidget(self.split_check)
        self.split_spin = QSpinBox()
        self.split_spin.setRange(30, 500)
        self.split_spin.setValue(120)
        self.split_spin.setSuffix(" 字/段")
        self.split_spin.setEnabled(False)
        self.split_spin.setFixedWidth(104)
        self.split_spin.setToolTip("可直接输入，或用滚轮调整")
        row.addWidget(self.split_spin)
        row.addStretch(1)
        body.addLayout(row)
        lay.addWidget(card, 1)

        # ③ 声音描述（按维度挑标签自由组合）
        card, body, head = self._make_card("声音描述 · 风格控制", "palette")
        self.btn_quick = QPushButton("常用组合")
        self.btn_quick.setObjectName("ghost")
        self.btn_quick.setIcon(svg_icon("sparkles", COLOR_MUTED))
        self.btn_quick.setIconSize(QSize(12, 12))
        self.btn_quick.setFixedHeight(26)
        head.addWidget(self.btn_quick)

        self.btn_clear_control = QPushButton("清空")
        self.btn_clear_control.setObjectName("ghost")
        self.btn_clear_control.setIcon(svg_icon("x", COLOR_MUTED))
        self.btn_clear_control.setIconSize(QSize(12, 12))
        self.btn_clear_control.setFixedHeight(26)
        self.btn_clear_control.clicked.connect(self._clear_control)
        head.addWidget(self.btn_clear_control)

        self.control_combo = QComboBox()
        self.control_combo.setEditable(True)
        self.control_combo.setCurrentText("")
        self.control_combo.lineEdit().setPlaceholderText("留空则随机音色；也可以直接手写描述")
        self.control_combo.setToolTip("最终会以「(描述)正文」的形式前置到文本")
        self.control_combo.lineEdit().textEdited.connect(self._on_control_edited)
        body.addWidget(self.control_combo)

        self._control_auto = False   # 描述框里的内容是否由标签/常用组合生成
        self._tag_selected: dict[str, str] = {}
        self._tag_buttons: dict[str, TagButton] = {}
        self._tag_order: list[str] = []
        self.tag_grid = QGridLayout()
        self.tag_grid.setHorizontalSpacing(6)
        self.tag_grid.setVerticalSpacing(6)
        body.addLayout(self.tag_grid)

        self.lbl_control_hint = QLabel()
        self.lbl_control_hint.setObjectName("hint")
        self.lbl_control_hint.setWordWrap(True)
        body.addWidget(self.lbl_control_hint)
        self.g_control = card
        lay.addWidget(card)

        # ④ 参考音频
        card, body, _ = self._make_card("参考音频", "mic")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.ref_edit = DropLineEdit()
        self.ref_edit.textChanged.connect(self._on_ref_changed)
        row.addWidget(self.ref_edit, 1)
        b = QPushButton("浏览…")
        b.setObjectName("ghost")
        b.clicked.connect(self._pick_ref)
        row.addWidget(b)
        b2 = QPushButton("试听")
        b2.setObjectName("ghost")
        b2.setIcon(svg_icon("headphones", COLOR_TEXT))
        b2.setIconSize(QSize(14, 14))
        b2.clicked.connect(self._preview_ref)
        row.addWidget(b2)
        body.addLayout(row)

        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self.lbl_ref_info = QLabel("未选择参考音频 · 建议 5–30 秒干净人声")
        self.lbl_ref_info.setObjectName("hint")
        info_row.addWidget(self.lbl_ref_info, 1)
        self.denoise_check = QCheckBox("降噪参考音频")
        self.denoise_check.setToolTip("需先在「设置」中启用 ZipEnhancer 模型")
        info_row.addWidget(self.denoise_check)
        body.addLayout(info_row)

        self.lbl_prompt_text = QLabel("参考音频文本（极限克隆需逐字准确）")
        self.lbl_prompt_text.setObjectName("fieldLabel")
        self.prompt_text_edit = TextEdit()
        self.prompt_text_edit.setPlaceholderText("参考音频里说的那句话，逐字转录…")
        self.prompt_text_edit.setMaximumHeight(58)
        body.addWidget(self.lbl_prompt_text)
        body.addWidget(self.prompt_text_edit)
        self.g_ref = card
        lay.addWidget(card)

        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_scroll = scroll
        return scroll

    # ---------------------------------------------------------------- #
    def _build_right(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 0, 0, 6)
        lay.setSpacing(12)

        # 参数
        card, body, _ = self._make_card("生成参数", "sliders")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        grid.addWidget(self._field_label("CFG 强度"), 0, 0)
        self.cfg_slider = QSlider(Qt.Orientation.Horizontal)
        self.cfg_slider.setRange(10, 30)
        self.cfg_slider.setValue(int(self.cfg.cfg_value * 10))
        self.cfg_spin = QDoubleSpinBox()
        self.cfg_spin.setObjectName("valueSpin")
        self.cfg_spin.setRange(1.0, 3.0)
        self.cfg_spin.setSingleStep(0.1)
        self.cfg_spin.setDecimals(1)
        self.cfg_spin.setValue(self.cfg.cfg_value)
        self.cfg_spin.setFixedWidth(66)
        self.cfg_spin.setToolTip("可直接输入，或用滚轮调整")
        self.cfg_slider.valueChanged.connect(lambda v: self.cfg_spin.setValue(v / 10))
        self.cfg_spin.valueChanged.connect(lambda v: self.cfg_slider.setValue(int(v * 10)))
        grid.addWidget(self.cfg_slider, 0, 1)
        grid.addWidget(self.cfg_spin, 0, 2)

        grid.addWidget(self._field_label("扩散步数"), 1, 0)
        self.step_slider = QSlider(Qt.Orientation.Horizontal)
        self.step_slider.setRange(4, 30)
        self.step_slider.setValue(self.cfg.inference_timesteps)
        self.step_spin = QSpinBox()
        self.step_spin.setObjectName("valueSpin")
        self.step_spin.setRange(4, 30)
        self.step_spin.setValue(self.cfg.inference_timesteps)
        self.step_spin.setFixedWidth(66)
        self.step_spin.setToolTip("可直接输入，或用滚轮调整")
        self.step_slider.valueChanged.connect(self.step_spin.setValue)
        self.step_spin.valueChanged.connect(self.step_slider.setValue)
        grid.addWidget(self.step_slider, 1, 1)
        grid.addWidget(self.step_spin, 1, 2)

        grid.addWidget(self._field_label("最大长度"), 2, 0)
        self.maxlen_slider = QSlider(Qt.Orientation.Horizontal)
        self.maxlen_slider.setRange(256, 8192)
        self.maxlen_slider.setSingleStep(256)
        self.maxlen_slider.setPageStep(1024)
        self.maxlen_slider.setValue(self.cfg.max_len)
        self.maxlen_slider.setToolTip(
            "每次推理的最大 token 数。4096 足够；更长的文本请在左侧勾选「长文本分段合成」"
        )
        self.maxlen_spin = QSpinBox()
        self.maxlen_spin.setObjectName("valueSpin")
        self.maxlen_spin.setRange(256, 8192)
        self.maxlen_spin.setSingleStep(256)
        self.maxlen_spin.setValue(self.cfg.max_len)
        self.maxlen_spin.setFixedWidth(66)
        self.maxlen_spin.setToolTip("可直接输入，或用滚轮调整")
        self.maxlen_slider.valueChanged.connect(self.maxlen_spin.setValue)
        self.maxlen_spin.valueChanged.connect(self.maxlen_slider.setValue)
        grid.addWidget(self.maxlen_slider, 2, 1)
        grid.addWidget(self.maxlen_spin, 2, 2)
        grid.setColumnStretch(1, 1)

        body.addLayout(grid)


        line = QFrame()
        line.setObjectName("hairline")
        body.addWidget(line)

        opts = QHBoxLayout()
        opts.setSpacing(18)
        self.norm_check = QCheckBox("文本正则化")
        self.norm_check.setToolTip("自动展开数字、日期、单位")
        self.norm_check.setChecked(self.cfg.normalize)
        self.retry_check = QCheckBox("异常重试")
        self.retry_check.setToolTip(
            "勾选后改为一次性生成：官方在流式模式下会强制关闭重试，"
            "只有非流式调用才会真的重试（最多 3 次）。代价是波形要等这句生成完才出现。"
        )
        self.retry_check.setChecked(self.cfg.retry_badcase)
        self.retry_check.toggled.connect(self._update_gen_hint)
        opts.addWidget(self.norm_check)
        opts.addWidget(self.retry_check)
        opts.addStretch(1)
        body.addLayout(opts)
        lay.addWidget(card)

        # 生成
        self.btn_generate = QPushButton("开始合成")
        self.btn_generate.setObjectName("primary")
        self.btn_generate.setMinimumHeight(50)
        self.btn_generate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_generate.clicked.connect(self.start_generation)
        lay.addWidget(self.btn_generate)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.lbl_gen_status = QLabel()
        self._update_gen_hint()
        self.lbl_gen_status.setObjectName("hint")
        lay.addWidget(self.lbl_gen_status)

        # 结果
        card, body, head = self._make_card("合成结果", "wave")
        self.chip_dur = QLabel("时长 —")
        self.chip_dur.setObjectName("metaChip")
        self.chip_cost = QLabel("耗时 —")
        self.chip_cost.setObjectName("metaChip")
        self.chip_rtf = QLabel("RTF —")
        self.chip_rtf.setObjectName("metaChip")
        for c in (self.chip_dur, self.chip_cost, self.chip_rtf):
            head.addWidget(c)

        self.wave = WaveformWidget()
        self._wave_fade = QGraphicsOpacityEffect(self.wave)
        self._wave_fade.setOpacity(1.0)
        self.wave.setGraphicsEffect(self._wave_fade)
        self._wave_anim = QPropertyAnimation(self._wave_fade, b"opacity", self)
        self._wave_anim.setDuration(180)
        self._wave_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        body.addWidget(self.wave)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_play = QPushButton("播放")
        self.btn_play.setIcon(svg_icon("play", COLOR_TEXT))
        self.btn_play.setIconSize(QSize(14, 14))
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_save = QPushButton("另存为…")
        self.btn_save.setIcon(svg_icon("save", COLOR_TEXT))
        self.btn_save.setIconSize(QSize(14, 14))
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_as)
        self.btn_folder = QPushButton("打开输出目录")
        self.btn_folder.setObjectName("ghost")
        self.btn_folder.setIcon(svg_icon("folder", COLOR_MUTED))
        self.btn_folder.setIconSize(QSize(14, 14))
        self.btn_folder.clicked.connect(self.open_output_dir)
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_folder)
        row.addStretch(1)
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("hint")
        row.addWidget(self.lbl_info)
        body.addLayout(row)
        lay.addWidget(card)

        # 历史
        card, body, head = self._make_card("历史记录", "clock")
        # 历史列表吃掉右列剩余高度，底部不留死白
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.history_list = HistoryList()
        self.history_list.setMinimumHeight(92)
        self.history_list.itemDoubleClicked.connect(self._history_play)
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._history_menu)
        body.addWidget(self.history_list)
        lay.addWidget(card, 1)

        # 右侧同样套滚动区：窗口变矮时不会挤压、重叠
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_scroll = scroll
        return scroll

    # ---------------------------------------------------------------- #
    def _sync_page_min_height(self) -> None:
        """把「内容需要的高度」写成页面最小高度。

        Qt 默认在视口不足时会把卡片压到最小尺寸（内容贴边、被裁），
        这里显式声明最小高度，窗口变矮时改为出现滚动条。
        """
        for scroll in (getattr(self, "left_scroll", None), getattr(self, "right_scroll", None)):
            if scroll is None:
                continue
            page = scroll.widget()
            lay = page.layout()
            if lay is None:
                continue
            # 用布局「最小高度」而不是首选高度：足够放下卡片内容，又不会
            # 因为差一两个像素就常驻一条滚动条
            need = lay.minimumSize().height()
            if getattr(scroll, "_min_height", None) != need:
                scroll._min_height = need  # 防止滚动条出现后再次触发 resize 造成循环
                page.setMinimumHeight(need)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_page_min_height()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    # ================================================================ #
    # 状态 / 配置
    # ================================================================ #
    def _restore_state(self) -> None:
        self.mode_ctrl.set_current(self.cfg.last_mode)
        self.control_combo.setCurrentText(self.cfg.last_control or "")
        self._on_mode_changed()
        self._update_char_count()
        self._update_vram()

    def _save_state(self) -> None:
        # 截图 / 自检脚本会设这个变量：不要用临时窗口状态覆盖用户的 config.json
        if os.environ.get("VOXCPM_NO_SAVE"):
            return
        self.cfg.cfg_value = self.cfg_spin.value()
        self.cfg.inference_timesteps = self.step_spin.value()
        self.cfg.max_len = self.maxlen_spin.value()
        self.cfg.normalize = self.norm_check.isChecked()
        self.cfg.retry_badcase = self.retry_check.isChecked()
        self.cfg.denoise = self.denoise_check.isChecked()
        self.cfg.last_mode = self.mode_ctrl.current_key()
        self.cfg.last_control = self.control_combo.currentText()
        if self.ref_edit.text().strip():
            self.cfg.last_reference_dir = str(Path(self.ref_edit.text()).parent)
        try:
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            self.log(f"保存配置失败：{exc}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_state()
        self.player.stop()
        if self.api_server is not None:
            self.api_server.stop()
            self.api_server = None
        if self._gen_worker is not None and self._gen_worker.isRunning():
            self._gen_worker.cancel()          # 先请求取消，正常情况下 1 块内退出
            self._gen_worker.requestInterruption()
        for w in (self._gen_worker, self._load_worker):
            if w is not None and w.isRunning():
                w.wait(8000)
        super().closeEvent(event)

    def _update_gen_hint(self) -> None:
        """状态行随「异常重试」开关变化：流式 vs 一次性生成。"""
        if getattr(self, "lbl_gen_status", None) is None:
            return
        if getattr(self, "retry_check", None) is not None and self.retry_check.isChecked():
            self.lbl_gen_status.setText("等待生成  ·  一次性生成（重试生效）  ·  Ctrl+Enter 快速合成")
        else:
            self.lbl_gen_status.setText("等待生成  ·  流式生成（边生成边出波形）  ·  Ctrl+Enter 快速合成")

    def log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {msg}")
        self.statusBar().showMessage(msg, 6000)
        # 出错时把日志面板推出来，平时不占版面。
        # 注意「异常重试」是正常提示，不能当成错误触发弹面板
        looks_bad = any(k in msg for k in ("失败", "错误", "Traceback", "Error", "无法"))
        if looks_bad and "异常重试" not in msg and not self.log_dock.isVisible():
            self.log_dock.show()

    def _set_pill(self, text: str, state: str = "idle") -> None:
        self.pill_model.setText(f"●  {text}")
        self.pill_model.setProperty("state", state)
        self.pill_model.style().unpolish(self.pill_model)
        self.pill_model.style().polish(self.pill_model)

    # ================================================================ #
    # 本地 API
    # ================================================================ #
    def _sync_api(self) -> None:
        """按配置启动 / 停止本地 HTTP API（设置保存后即时生效）。"""
        from .api import ApiServer

        want = bool(getattr(self.cfg, "api_enabled", False)) and not os.environ.get(
            "VOXCPM_NO_API"
        )
        if not want:
            if self.api_server is not None:
                self.api_server.stop()
                self.api_server = None
            return

        port = int(getattr(self.cfg, "api_port", 7810))
        if self.api_server is not None and self.api_server.running:
            if self.api_server.port == port:
                return
            self.api_server.stop()
            self.api_server = None

        server = ApiServer(
            self.engine,
            port=port,
            token=getattr(self.cfg, "api_token", ""),
            output_dir=self.cfg.output_dir,
            log=self.log,
            is_busy=lambda: bool(
                self._gen_worker is not None and self._gen_worker.isRunning()
            ),
        )
        try:
            server.start()
        except OSError as exc:
            self.log(
                f"本地 API 启动失败（端口 {port}）：{exc}\n"
                f"  端口已被占用 —— 大概率是 start-api.bat 起的无界面服务还在跑。\n"
                f"  两者同时使用会各加载一份模型（约 7.9 GB × 2），请先关掉其中一个。"
            )
            return
        self.api_server = server

    # ================================================================ #
    # 模型加载
    # ================================================================ #
    def _startup(self) -> None:
        self._sync_api()
        if os.environ.get("VOXCPM_NO_AUTOLOAD"):
            self.log("已跳过自动加载（VOXCPM_NO_AUTOLOAD）")
            return
        if self.engine.is_loaded:
            self._on_model_ready()
            return
        if self.cfg.model_ready:
            self.load_model()
        else:
            self._offer_model_download()

    def _offer_model_download(self) -> None:
        """模型缺失时的入口：下载 / 指定本地目录 / 稍后。"""
        self._set_pill("模型未下载", "err")
        self.lbl_gen_status.setText("模型未就绪 · 首次使用需要下载约 4.6 GB 权重")
        dlg = ModelSetupDialog(self.cfg.model_dir, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.model_dir:
            self.log("已跳过模型准备；需要时点右上角「加载模型」重新打开下载窗口")
            return
        if Path(dlg.model_dir) != Path(self.cfg.model_dir):
            self.cfg.model_dir = dlg.model_dir
            self.cfg.save()
        self.log(f"模型目录：{self.cfg.model_dir}")
        self.load_model()

    def load_model(self) -> None:
        if self._load_worker and self._load_worker.isRunning():
            return
        if not self.cfg.model_ready:
            self._offer_model_download()
            return
        self._set_pill("模型加载中…", "busy")
        self.btn_load.setEnabled(False)
        self.btn_generate.setEnabled(False)
        self.lbl_gen_status.setText("正在加载模型（首次约 30 秒，含预热）…")
        self._load_worker = LoadModelWorker(self.engine, self.cfg, self)
        self._load_worker.log.connect(self.log)
        self._load_worker.ok.connect(self._on_model_ready)
        self._load_worker.fail.connect(self._on_model_failed)
        self._load_worker.start()

    def _on_model_ready(self) -> None:
        self.btn_load.setEnabled(True)
        self.btn_generate.setEnabled(True)
        self._set_pill("模型就绪", "ok")
        self.chip_device.setText(f"设备 {self.engine.torch_device}")
        self.lbl_gen_status.setText(f"就绪 · {self.engine.sample_rate // 1000}kHz · Ctrl+Enter 快速合成")
        self._update_vram()
        self.log(f"模型加载完成：device={self.engine.torch_device}")

    def _on_model_failed(self, err: str) -> None:
        self.btn_load.setEnabled(True)
        self._set_pill("加载失败", "err")
        self.log(err)
        self.lbl_gen_status.setText("模型加载失败，详见运行日志")
        last = err.strip().splitlines()[-1] if err.strip() else ""
        QMessageBox.critical(
            self,
            "模型加载失败",
            "加载失败，常见原因：\n"
            "· 显存不足（VoxCPM2 需要约 8GB 显存）→ 在「设置」中把设备改为 cpu\n"
            "· torch / transformers 版本不匹配\n\n"
            f"详情：{last}",
        )

    def _update_vram(self) -> None:
        info = self.engine.vram_info()
        if info:
            used, total = info
            self.chip_vram.setText(f"显存 {used:.1f}/{total:.1f} GB")
        else:
            self.chip_vram.setText("显存 —")

    # ================================================================ #
    # 交互
    # ================================================================ #
    # ---------------- 声音描述标签 ---------------- #
    def _on_control_edited(self, _text: str) -> None:
        """手写描述后，标签/组合不再覆盖它。"""
        self._control_auto = False

    def _rebuild_tags(self, mode: str) -> None:
        """按模式重建标签按钮：设计模式给音色维度，克隆模式只给表达方式。"""
        # 上一模式由标签拼出来的描述在新模式下不适用（维度不同），直接清掉；
        # 用户手写的内容则保留，不擅自删改
        if getattr(self, "_control_auto", False):
            self.control_combo.setCurrentText("")
            self._control_auto = False
        groups = DESIGN_TAGS if mode == "design" else CLONE_TAGS
        while self.tag_grid.count():
            item = self.tag_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)   # 立刻消失，否则会和新建的标签叠在一起
                w.deleteLater()
        self._tag_buttons.clear()
        self._tag_selected.clear()
        self._tag_order = list(groups)
        for i, (dim, tags) in enumerate(groups.items()):
            btn = TagButton(dim, tags)
            btn.picked.connect(self._on_tag_picked)
            self.tag_grid.addWidget(btn, i // 3, i % 3)
            self._tag_buttons[dim] = btn
        for col in range(4):
            self.tag_grid.setColumnStretch(col, 1 if col == 3 else 0)
        self._relayout_tags()

        menu = QMenu(self)
        for combo in QUICK_COMBOS["设计" if mode == "design" else "克隆"]:
            act = menu.addAction(combo)
            act.triggered.connect(lambda _=False, t=combo: self._apply_combo(t))
        self.btn_quick.setMenu(menu)

        if mode == "design":
            self.lbl_control_hint.setText(
                "每个维度挑一个标签即可自由组合，结果会写进上面的描述框；也可以直接手写。"
                "描述以括号前置：(描述)正文。"
            )
        else:
            self.lbl_control_hint.setText(
                "克隆模式下音色由参考音频决定，这里只影响情绪 / 语速 / 语气，不会改变音色。"
            )

    def _on_tag_picked(self, dimension: str, tag: str) -> None:
        if tag:
            self._tag_selected[dimension] = tag
        else:
            self._tag_selected.pop(dimension, None)
        self.control_combo.setCurrentText(compose(self._tag_selected, self._tag_order))
        self._control_auto = True
        self._relayout_tags()

    def _relayout_tags(self) -> None:
        """标签文字变长后重算列宽，避免被裁切或相互压住。

        Qt 只在事件循环里惰性重排，光靠 singleShot(activate) 往往会算在
        旧几何上；这里先同步 invalidate + activate 走一遍，再补一次延迟重排。
        """
        self._force_tag_layout()
        QTimer.singleShot(25, self._force_tag_layout)

    def _force_tag_layout(self) -> None:
        self.tag_grid.invalidate()
        self.tag_grid.activate()
        outer = self.g_control.layout()
        if outer is not None:
            outer.invalidate()
            outer.activate()

    def _apply_combo(self, text: str) -> None:
        """应用「常用组合」：写入描述框，并清掉手动挑选的标签。"""
        for btn in self._tag_buttons.values():
            btn.clear()
        self._tag_selected.clear()
        self.control_combo.setCurrentText(text)
        self._control_auto = True

    def _clear_control(self) -> None:
        self._tag_selected.clear()
        for btn in self._tag_buttons.values():
            btn.clear()
        self.control_combo.setCurrentText("")
        self._control_auto = False

    def _on_mode_changed(self) -> None:
        mode = self.mode_ctrl.current_key()
        is_design = mode == "design"
        is_ultimate = mode == "ultimate"

        self.g_control.setVisible(not is_ultimate)
        self.g_ref.setVisible(not is_design)
        self._rebuild_tags("design" if is_design else "clone")
        self.lbl_prompt_text.setVisible(is_ultimate)
        self.prompt_text_edit.setVisible(is_ultimate)

        if is_design:
            self.mode_hint.setText("无需参考音频，用一句描述凭空创造一个音色。")
        elif mode == "clone":
            self.mode_hint.setText("用参考音频克隆音色，描述只微调情绪与语速，不改变音色。")
        else:
            self.mode_hint.setText("参考音频 + 逐字文本，相似度最高；此模式下描述不生效。")

        self._update_char_count()

    def _on_split_toggled(self, on: bool) -> None:
        self.split_spin.setEnabled(on)

    def _update_char_count(self) -> None:
        text = self.text_edit.toPlainText()
        extra = " · 极限克隆" if self.mode_ctrl.current_key() == "ultimate" else ""
        self.lbl_chars.setText(f"{len(text)} 字{extra}")

    def _pick_ref(self) -> None:
        start = self.cfg.last_reference_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考音频", start, "音频文件 (*.wav *.mp3 *.flac *.m4a *.ogg *.opus)"
        )
        if path:
            self.ref_edit.setText(path)

    def _on_ref_changed(self, path: str) -> None:
        path = path.strip().strip('"')
        if not path or not Path(path).exists():
            self.lbl_ref_info.setText("未选择参考音频 · 建议 5–30 秒干净人声")
            return
        try:
            dur = audio_duration(path)
        except Exception:
            self.lbl_ref_info.setText("无法读取该音频，请确认格式（wav / mp3 / flac…）")
            return
        warn = ""
        if dur < 3:
            warn = "  · 偏短，建议 5–30 秒"
        elif dur > 40:
            warn = "  · 偏长，建议截取 5–30 秒"
        # 文件名可能很长：中间省略，避免把「降噪参考音频」挤到角落
        name = elide_middle(Path(path).name, 26)
        self.lbl_ref_info.setText(f"时长 {dur:.2f}s · {name}{warn}")
        self.lbl_ref_info.setToolTip(path)

    def _preview_ref(self) -> None:
        path = self.ref_edit.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.information(self, "提示", "请先选择参考音频")
            return
        self._play_file(Path(path), toggle=True)

    # ---------------------------------------------------------------- #
    def _params(self) -> GenerationParams:
        return GenerationParams(
            cfg_value=self.cfg_spin.value(),
            inference_timesteps=self.step_spin.value(),
            max_len=self.maxlen_spin.value(),
            min_len=2,
            normalize=self.norm_check.isChecked(),
            denoise=self.denoise_check.isChecked(),
            retry_badcase=self.retry_check.isChecked(),
            split_chars=self.split_spin.value() if self.split_check.isChecked() else 0,
        )

    def start_generation(self) -> None:
        if self._gen_worker and self._gen_worker.isRunning():
            self._cancel_generation()
            return
        if not self.engine.is_loaded:
            QMessageBox.warning(self, "模型未加载", "请先加载模型。")
            return

        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "提示", "请输入要合成的文本。")
            return

        mode = self.mode_ctrl.current_key()
        ref = self.ref_edit.text().strip() or None
        prompt_text = None
        reference_wav = None
        control = self.control_combo.currentText().strip() or None

        if mode in ("clone", "ultimate"):
            if not ref or not Path(ref).exists():
                QMessageBox.information(self, "提示", "该模式需要先选择参考音频。")
                return
            reference_wav = ref
        if mode == "ultimate":
            prompt_text = self.prompt_text_edit.toPlainText().strip()
            if not prompt_text:
                QMessageBox.information(
                    self,
                    "提示",
                    "极限克隆需要参考音频的逐字文本；\n若没有文本，请改用「声音克隆」模式。",
                )
                return
            control = None

        self._buf = []
        self._last_wav_path = None
        self._last_result = None
        self.wave.set_audio(None, 1)
        self.btn_play.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.lbl_info.setText("")
        self.chip_dur.setText("时长 —")
        self.chip_cost.setText("耗时 —")
        self.chip_rtf.setText("RTF —")
        self.player.stop()

        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("停止合成")
        self.btn_generate.setToolTip("正在生成，点击可中途停止")
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self._gen_t0 = time.time()
        self._tick.start()
        self.lbl_gen_status.setText("正在合成…")

        self._save_state()
        self._gen_worker = GenerateWorker(
            self.engine,
            text,
            control,
            ref if mode == "ultimate" else None,
            prompt_text,
            reference_wav,
            self._params(),
            self,
        )
        self._gen_worker.chunk.connect(self._on_chunk)
        self._gen_worker.progress.connect(self._on_progress)
        self._gen_worker.done.connect(self._on_done)
        self._gen_worker.canceled.connect(self._on_canceled)
        self._gen_worker.fail.connect(self._on_fail)
        self._gen_worker.log.connect(self.log)
        self._gen_worker.start()

    def _on_chunk(self, chunk: np.ndarray) -> None:
        self._buf.append(np.asarray(chunk, dtype=np.float32).reshape(-1))

    def _on_tick(self) -> None:
        elapsed = time.time() - self._gen_t0
        audio = sum(len(c) for c in self._buf) / max(self.engine.sample_rate, 1)
        self.lbl_gen_status.setText(f"正在合成 · 已用 {elapsed:.1f}s · 已生成 {audio:.1f}s 音频")
        if self._buf:
            self.wave.set_audio(np.concatenate(self._buf), self.engine.sample_rate)
        self._update_vram()

    def _cancel_generation(self) -> None:
        if self._gen_worker is None or not self._gen_worker.isRunning():
            return
        self._gen_worker.cancel()
        self._gen_worker.requestInterruption()
        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("正在停止…")
        self.lbl_gen_status.setText("正在停止（当前分段结束后生效）…")

    def _on_progress(self, seg: int, total: int, seconds: float) -> None:
        if total > 1:
            self.lbl_gen_status.setText(
                f"生成中 · 第 {seg}/{total} 段 · 已生成 {seconds:.1f}s · Ctrl+Enter 可停止"
            )
        else:
            self.lbl_gen_status.setText(
                f"生成中 · 已生成 {seconds:.1f}s · Ctrl+Enter 可停止"
            )

    def _on_canceled(self, produced: float) -> None:
        self._reset_generate_button()
        self.progress.setVisible(False)
        self._tick.stop()
        self._buf = []
        self.wave.set_audio(None, 1)
        self.lbl_gen_status.setText(f"已取消（丢弃 {produced:.1f}s 音频）")
        self.log(f"生成已取消，丢弃 {produced:.1f}s 音频")

    def _reset_generate_button(self) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("开始合成")
        self.btn_generate.setToolTip("Ctrl+Enter")

    def _on_done(self, result) -> None:
        self._tick.stop()
        self.progress.setVisible(False)
        self._reset_generate_button()

        wav = result.wav
        self.wave.set_audio(wav, result.sample_rate)
        self._animate_wave_in()
        self.chip_dur.setText(f"时长 {result.audio_seconds:.2f}s")
        self.chip_cost.setText(f"耗时 {result.elapsed:.1f}s")
        self.chip_rtf.setText(f"RTF {result.rtf:.2f}")
        self.lbl_info.setText(f"{result.segments} 段" if result.segments > 1 else "")
        self.lbl_gen_status.setText(
            f"完成 · 音频 {result.audio_seconds:.2f}s · 耗时 {result.elapsed:.1f}s · RTF {result.rtf:.2f}"
        )
        self.log(
            f"合成完成：{result.audio_seconds:.2f}s 音频 / {result.elapsed:.1f}s 耗时 "
            f"(RTF {result.rtf:.2f})"
        )

        if self.cfg.auto_save:
            path = self._auto_save_path()
            try:
                save_wav(path, wav, result.sample_rate)
                self._last_wav_path = path
                self._add_history(path, result)
                self.log(f"已保存：{path}")
            except Exception as exc:  # noqa: BLE001
                self.log(f"保存失败：{exc}")

        self.btn_play.setEnabled(True)
        self.btn_save.setEnabled(True)
        self._last_result = result

    def _animate_wave_in(self) -> None:
        self._wave_anim.stop()
        self._wave_fade.setOpacity(0.35)
        self._wave_anim.setStartValue(0.35)
        self._wave_anim.setEndValue(1.0)
        self._wave_anim.start()

    def _on_fail(self, err: str) -> None:
        self._tick.stop()
        self.progress.setVisible(False)
        self._reset_generate_button()
        self.lbl_gen_status.setText("合成失败，详见运行日志")
        self.log(err)
        last = err.strip().splitlines()[-1] if err.strip() else "未知错误"
        QMessageBox.critical(self, "合成失败", last)

    # ---------------------------------------------------------------- #
    def _auto_save_path(self) -> Path:
        out = Path(self.cfg.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        mode = self.mode_ctrl.current_key() or "tts"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return out / f"voxcpm2_{mode}_{stamp}.wav"

    def save_as(self) -> None:
        if self._last_result is None:
            return
        start = str(Path(self.cfg.output_dir) / "voxcpm2_output.wav")
        path, _ = QFileDialog.getSaveFileName(self, "保存音频", start, "WAV (*.wav);;FLAC (*.flac)")
        if not path:
            return
        try:
            save_wav(path, self._last_result.wav, self._last_result.sample_rate)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._last_wav_path = Path(path)
        self._add_history(Path(path), self._last_result)
        self.log(f"已另存为：{path}")

    def open_output_dir(self) -> None:
        out = Path(self.cfg.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

    # ---------------------------------------------------------------- #
    def _add_history(self, path: Path, result=None) -> None:
        if path in self._history:
            return
        self._history.insert(0, path)
        desc = path.name + (f"   ·   {result.audio_seconds:.1f}s" if result is not None else "")
        item = QListWidgetItem(desc)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        text = self.text_edit.toPlainText().strip().replace("\n", " ")
        item.setToolTip(f"{path}\n{elide_middle(text, 80)}")
        self.history_list.insertItem(0, item)
        while self.history_list.count() > 100:
            self.history_list.takeItem(self.history_list.count() - 1)

    def _history_play(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if not path.exists():
            QMessageBox.information(self, "文件不存在", str(path))
            return
        self._play_file(path, toggle=True)

    def _history_menu(self, pos) -> None:
        item = self.history_list.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        act_play = menu.addAction("播放")
        act_reveal = menu.addAction("在资源管理器中显示")
        act_remove = menu.addAction("从列表移除")
        act = menu.exec(self.history_list.mapToGlobal(pos))
        if act is act_play:
            self._play_file(path, toggle=True)
        elif act is act_reveal:
            self._reveal(path)
        elif act is act_remove:
            self.history_list.takeItem(self.history_list.row(item))

    @staticmethod
    def _reveal(path: Path) -> None:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ---------------------------------------------------------------- #
    def _play_file(self, path: Path, toggle: bool = False) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if toggle and self._playing_path == path and playing:
            self.player.pause()
            return
        self._playing_path = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def toggle_play(self) -> None:
        if self._last_wav_path and self._last_wav_path.exists():
            self._play_file(self._last_wav_path, toggle=True)
        elif self._last_result is not None:
            tmp = Path(self.cfg.output_dir) / "_preview.wav"
            save_wav(tmp, self._last_result.wav, self._last_result.sample_rate)
            self._last_wav_path = tmp
            self._play_file(tmp, toggle=True)

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("暂停" if playing else "播放")
        self.btn_play.setIcon(svg_icon("pause" if playing else "play", COLOR_TEXT))

    # ---------------------------------------------------------------- #
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            reload_needed = dlg.apply_to(self.cfg)
            self.cfg.save()
            self.log("设置已保存")
            self._sync_api()
            if reload_needed:
                if self.engine.is_loaded:
                    self.engine.unload()
                self._set_pill("模型未加载", "idle")
                self.chip_device.setText("设备 —")
                self.lbl_gen_status.setText("设置已更新，请重新加载模型")
