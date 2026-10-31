"""VoxCPM2 Studio 启动入口：python main.py"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 `python main.py` 在任意工作目录下都能导入包
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    # 降低 CUDA 显存碎片，8GB 显卡更稳
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    # 主题必须在导入主窗口之前确定：窗口里的图标与颜色在导入时绑定
    from voxcpm_studio import icons, theme
    from voxcpm_studio.config import AppConfig

    cfg = AppConfig.load()
    icons.set_theme(getattr(cfg, "theme", "light"))

    from PyQt6.QtGui import QFont, QIcon
    from PyQt6.QtWidgets import QApplication

    from voxcpm_studio.icons import logo_pixmap
    from voxcpm_studio.mainwindow import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VoxCPM2 Studio")
    app.setApplicationDisplayName("VoxCPM2 Studio")
    app.setWindowIcon(QIcon(logo_pixmap(128)))
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(theme.qss())

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
