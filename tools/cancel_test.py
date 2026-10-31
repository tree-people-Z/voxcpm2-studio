"""GUI 级验证：生成 → 中途取消 → 再生成一次成功。运行后自动退出。"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["VOXCPM_NO_SAVE"] = "1"   # 不要用测试窗口的状态覆盖用户的 config.json

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from voxcpm_studio import icons, theme
from voxcpm_studio.config import AppConfig

icons.set_theme(AppConfig.load().theme)

from voxcpm_studio.mainwindow import MainWindow

app = QApplication(sys.argv)
app.setStyleSheet(theme.qss())
win = MainWindow()
win.show()

state = {"phase": "load"}


def tick() -> None:
    if state["phase"] == "load":
        if win.engine.is_loaded and win.btn_generate.isEnabled():
            win.text_edit.setPlainText(
                "这是取消功能的界面级测试。我们会让它生成一会儿，然后点停止合成。"
                "理论上点下去之后，程序应该在一秒内回到可以再次点击的状态。"
                "然后再跑一次短文本，确认模型没有被这次取消弄坏。"
            )
            win.split_check.setChecked(False)
            print("[t] 开始生成（长文本）", flush=True)
            win.start_generation()
            state["phase"] = "cancel"
            QTimer.singleShot(2500, do_cancel)
        return


def do_cancel() -> None:
    print("[t] 点击停止，按钮文案 =", win.btn_generate.text(), flush=True)
    win.start_generation()          # 生成中调用 = 取消
    print("[t] 取消后立即状态:", win.btn_generate.text(), win.btn_generate.isEnabled(), flush=True)
    state["phase"] = "wait_cancel"
    QTimer.singleShot(300, poll_cancel)


def poll_cancel() -> None:
    if win.btn_generate.text() == "开始合成" and win.btn_generate.isEnabled():
        print("[t] 已回到可生成状态:", win.lbl_gen_status.text(), flush=True)
        print("[t] 波形是否清空:", win.wave._peaks is None, "缓冲长度:", len(win._buf), flush=True)
        win.text_edit.setPlainText("取消之后的第二次生成。")
        print("[t] 再次生成…", flush=True)
        win.start_generation()
        state["phase"] = "second"
        QTimer.singleShot(300, poll_second)
    else:
        QTimer.singleShot(300, poll_cancel)


def poll_second() -> None:
    if win.btn_generate.text() == "开始合成" and win._last_result is not None:
        r = win._last_result
        print(f"[t] 第二次成功: {r.audio_seconds:.2f}s / RTF {r.rtf:.2f}", flush=True)
        print("[t] 通过", flush=True)
        win.close()
        app.quit()
    else:
        QTimer.singleShot(300, poll_second)


timer = QTimer()
timer.timeout.connect(tick)
timer.start(400)
app.exec()
