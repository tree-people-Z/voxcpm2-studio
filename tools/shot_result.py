"""生成真实音频后截图，检查结果区/波形/历史记录。"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import os
os.environ["VOXCPM_NO_SAVE"] = "1"

from voxcpm_studio import icons, theme
from voxcpm_studio.config import AppConfig

icons.set_theme(AppConfig.load().theme)

from voxcpm_studio.mainwindow import MainWindow

app = QApplication(sys.argv); app.setStyleSheet(theme.qss())
win = MainWindow(); win.show()
state = {"phase": "load"}
def tick():
    if state["phase"] == "load":
        if win.engine.is_loaded and win.btn_generate.isEnabled():
            win.control_combo.setCurrentText("年轻女性，温柔甜美")
            win.text_edit.setPlainText("你好，我是 VoxCPM2 语音合成工作站，支持三十种语言与四十八千赫兹高保真输出。")
            win.start_generation(); state["phase"] = "gen"
    elif state["phase"] == "gen":
        if win.btn_generate.isEnabled() and win._last_result is not None:
            state["phase"] = "shot"
            QTimer.singleShot(600, shoot)
def shoot():
    win.grab().save(str(ROOT / "shot_result.png"))
    print("saved shot_result.png", flush=True)
    win.close(); app.quit()
t = QTimer(); t.timeout.connect(tick); t.start(400)
app.exec()
