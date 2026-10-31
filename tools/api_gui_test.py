"""验证界面内的本地 API 开关：开 → /health 可用 → 关窗 → 端口释放。"""

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os

os.environ["VOXCPM_NO_AUTOLOAD"] = "1"   # 不加载模型，只验证服务生命周期
os.environ["VOXCPM_NO_SAVE"] = "1"

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from voxcpm_studio import icons, theme
from voxcpm_studio.config import AppConfig

cfg = AppConfig.load()
icons.set_theme(cfg.theme)
from voxcpm_studio.mainwindow import MainWindow

app = QApplication(sys.argv)
app.setStyleSheet(theme.qss())

cfg.api_enabled = True
cfg.api_port = 8797

win = MainWindow()
win.cfg.api_enabled = True
win.cfg.api_port = 8797
win.show()


def probe() -> None:
    win._sync_api()
    time.sleep(0.4)
    app.processEvents()
    try:
        with urllib.request.urlopen("http://127.0.0.1:8797/help", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        print("[1] /help ok, endpoints:", len(body["endpoints"]), flush=True)
        with urllib.request.urlopen("http://127.0.0.1:8797/health", timeout=3) as resp:
            print("[2] /health", resp.status, flush=True)
    except urllib.error.HTTPError as exc:
        print("[2] /health", exc.code, "（模型未加载，符合预期）", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[!] 请求失败:", exc, flush=True)

    print("[3] 关闭窗口…", flush=True)
    win.close()
    time.sleep(0.6)
    try:
        urllib.request.urlopen("http://127.0.0.1:8797/help", timeout=2)
        print("[4] 端口仍在监听 —— 失败", flush=True)
    except Exception:
        print("[4] 端口已释放 —— 通过", flush=True)
    app.quit()


QTimer.singleShot(800, probe)
app.exec()
