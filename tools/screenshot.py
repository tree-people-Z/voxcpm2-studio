"""开发用：把主窗口渲染成 PNG，便于检查界面效果。

用法：python tools/screenshot.py [输出路径] [--mode design|clone|ultimate] [--ref 参考音频.wav]

clone / ultimate 模式若给了 --ref，会填好参考音频（不加载模型，只读音频时长）；
ultimate 模式还会把参考音频文本填成该文件的文件名（逐字转录的占位）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VOXCPM_NO_AUTOLOAD"] = "1"
os.environ["VOXCPM_NO_SAVE"] = "1"  # 截图时不要真的加载 2B 模型

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from voxcpm_studio import icons, theme  # noqa: E402
from voxcpm_studio.config import AppConfig  # noqa: E402

icons.set_theme(AppConfig.load().theme)  # 需在导入 mainwindow 之前

from voxcpm_studio.mainwindow import MainWindow  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shot.png"
    mode = "design"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]

    ref = ""
    if "--ref" in sys.argv:
        ref = sys.argv[sys.argv.index("--ref") + 1]

    app = QApplication(sys.argv)
    app.setStyleSheet(theme.qss())
    win = MainWindow()
    win.mode_ctrl.set_current(mode)
    win.text_edit.setPlainText(
        "VoxCPM2 是一个无需离散 token 的语音合成系统，直接生成连续语音表征。"
        "它支持三十种语言，48kHz 高保真输出。"
    )
    # 演示「标签自由组合」：挑 2–3 个维度，描述会自动拼进上面的输入框
    demo = {
        "design": [("性别年龄", "年轻女性"), ("音色", "温柔甜美"), ("情绪", "欢快带笑意")],
        "clone": [("情绪", "轻快带笑"), ("语速", "语速偏快"), ("语气", "朋友聊天")],
        "ultimate": [],
    }.get(mode, [])
    for dim, tag in demo:
        btn = win._tag_buttons.get(dim)
        if btn is not None:
            btn._choose(tag)

    if ref:
        win.ref_edit.setText(ref)
    if mode == "ultimate" and ref:
        win.prompt_text_edit.setPlainText(Path(ref).stem)
    win.show()

    def shoot() -> None:
        win.grab().save(str(out))
        print(f"saved {out}")
        app.quit()

    QTimer.singleShot(1200, shoot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
