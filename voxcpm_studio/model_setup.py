"""首次启动的模型下载对话框（可断点续传、可取消、有真实进度）。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                             QLineEdit, QProgressBar, QPushButton,
                             QVBoxLayout, QWidget)

from . import downloader
from .config import DEFAULT_MODEL_DIR


def _gb(n: float) -> str:
    return f"{n / 1024**3:.2f} GB"


def _mbps(bps: float) -> str:
    return f"{bps / 1024**2:.1f} MB/s"


class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int, int, float)   # 已下载字节 / 总字节 / 速度
    done = pyqtSignal(str)
    fail = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, dest: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.dest = str(dest)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            path = downloader.download_model(
                self.dest,
                on_progress=lambda d, t, s: self.progress.emit(d, t, s),
                should_stop=lambda: self._cancel,
                log=self.log.emit,
            )
        except downloader.DownloadCancelled:
            self.log.emit("下载已取消（已下载的部分会保留，下次接着下）")
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(str(path))


class ModelSetupDialog(QDialog):
    """让用户选择：立即下载 / 指定本地目录 / 稍后。"""

    def __init__(self, model_dir: str | Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("首次使用：准备模型")
        self.setMinimumWidth(560)
        self.model_dir: str | None = None
        self.worker: ModelDownloadWorker | None = None
        self._total = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)

        title = QLabel("需要 VoxCPM2 模型权重")
        title.setObjectName("title")
        lay.addWidget(title)

        desc = QLabel(
            "首次使用要下载约 4.6 GB 的权重（OpenBMB/VoxCPM2，Apache-2.0）。\n"
            "支持断点续传：中途关掉，下次会从已下载的位置继续。"
        )
        desc.setObjectName("hint")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.dir_edit = QLineEdit(str(model_dir or DEFAULT_MODEL_DIR))
        self.dir_edit.setReadOnly(True)
        row.addWidget(self.dir_edit, 1)
        self.btn_pick = QPushButton("换个目录…")
        self.btn_pick.setObjectName("ghost")
        self.btn_pick.clicked.connect(self._pick_dir)
        row.addWidget(self.btn_pick)
        lay.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("hint")
        self.lbl_status.setVisible(False)
        lay.addWidget(self.lbl_status)

        self.lbl_log = QLabel("")
        self.lbl_log.setObjectName("hint")
        self.lbl_log.setWordWrap(True)
        self.lbl_log.setVisible(False)
        lay.addWidget(self.lbl_log)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_local = QPushButton("我有本地模型…")
        self.btn_local.setObjectName("ghost")
        self.btn_local.clicked.connect(self._use_local)
        buttons.addWidget(self.btn_local)

        self.btn_later = QPushButton("稍后再说")
        self.btn_later.setObjectName("ghost")
        self.btn_later.clicked.connect(self.reject)
        buttons.addWidget(self.btn_later)
        buttons.addStretch(1)

        self.btn_start = QPushButton("立即下载")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumWidth(150)
        self.btn_start.clicked.connect(self._start_or_cancel)
        buttons.addWidget(self.btn_start)
        lay.addLayout(buttons)

    # ------------------------------------------------------------ #
    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择模型存放目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _use_local(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择已有的 VoxCPM2 模型目录", self.dir_edit.text())
        if not d:
            return
        path = Path(d)
        if not (path / "config.json").exists() or not any(path.glob("*.safetensors")):
            self.lbl_log.setVisible(True)
            self.lbl_log.setText("这个目录里没有 config.json + *.safetensors，看起来不是模型目录。")
            return
        self.model_dir = str(path)
        self.accept()

    # ------------------------------------------------------------ #
    def _start_or_cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.btn_start.setEnabled(False)
            self.btn_start.setText("正在停止…")
            return

        dest = Path(self.dir_edit.text().strip() or DEFAULT_MODEL_DIR)
        dest.mkdir(parents=True, exist_ok=True)
        if not downloader.ensure_space(dest, 5 * 1024**3):
            self.lbl_log.setVisible(True)
            self.lbl_log.setText("磁盘剩余空间不足 6 GB，换个目录或清理磁盘后再试。")
            return

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.lbl_status.setVisible(True)
        self.lbl_status.setText("正在获取文件列表…")
        self.lbl_log.setVisible(True)
        self.lbl_log.setText("")
        self.btn_pick.setEnabled(False)
        self.btn_local.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.btn_start.setText("取消下载")

        self.worker = ModelDownloadWorker(dest, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._on_log)
        self.worker.fail.connect(self._on_fail)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_progress(self, done: int, total: int, speed: float) -> None:
        self._total = total
        if total > 0:
            self.progress.setValue(int(done / total * 1000))
            left = ""
            if speed > 1024:
                eta = (total - done) / speed
                left = f" · 剩余约 {int(eta // 60)}:{int(eta % 60):02d}"
            self.lbl_status.setText(
                f"已下载 {_gb(done)} / {_gb(total)} · {_mbps(speed)}{left}"
            )

    def _on_log(self, msg: str) -> None:
        self.lbl_log.setText(msg)

    def _on_fail(self, err: str) -> None:
        self.lbl_status.setText("下载失败")
        self.lbl_log.setText(f"{err}\n可再点一次「立即下载」继续（已下载部分会保留）。")
        self._reset_buttons()

    def _on_done(self, path: str) -> None:
        self.progress.setValue(1000)
        self.lbl_status.setText(f"下载完成：{path}")
        self.model_dir = path
        self.accept()

    def _reset_buttons(self) -> None:
        self.btn_pick.setEnabled(True)
        self.btn_local.setEnabled(True)
        self.btn_later.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_start.setText("立即下载")

    # ------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        super().closeEvent(event)
