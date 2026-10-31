"""端到端自测：真实加载模型 → 语音设计合成 → 声音克隆合成。

用法：python tools/e2e_test.py
产出：outputs/ 下的 wav 文件与 e2e.log 日志
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from voxcpm_studio.mainwindow import MainWindow  # noqa: E402
from voxcpm_studio.theme import QSS  # noqa: E402

T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    log("窗口已创建")

    state = {"phase": "load", "design_wav": None}

    def tick() -> None:
        if time.time() - T0 > 600:
            log("超时，退出")
            app.exit(2)
            return

        if state["phase"] == "load":
            if win.engine.is_loaded and win.btn_generate.isEnabled():
                log(f"模型已加载：device={win.engine.torch_device} vram={win.engine.vram_info()}")
                win.mode_ctrl.set_current("design")
                win.control_combo.setCurrentText("年轻女性，温柔甜美")
                win.text_edit.setPlainText("你好，我是 VoxCPM2，正在做一次端到端测试。")
                log("开始语音设计合成…")
                win.start_generation()
                state["phase"] = "design"
            elif "加载失败" in win.pill_model.text():
                log("模型加载失败：" + win.log_view.toPlainText()[-800:])
                app.exit(3)
            return

        if state["phase"] == "design":
            if win.btn_generate.isEnabled() and win._last_result is not None:
                res = win._last_result
                log(
                    f"语音设计完成：{res.audio_seconds:.2f}s 音频 / {res.elapsed:.1f}s / "
                    f"RTF {res.rtf:.2f} -> {win._last_wav_path}"
                )
                state["design_wav"] = win._last_wav_path
                # 用生成结果当参考音频，测试克隆
                win.mode_ctrl.set_current("clone")
                win.ref_edit.setText(str(win._last_wav_path))
                win.control_combo.setCurrentText("")
                win.text_edit.setPlainText("这是克隆模式的测试，音色应该与参考音频一致。")
                log("开始声音克隆合成…")
                win.start_generation()
                state["phase"] = "clone"
            return

        if state["phase"] == "clone":
            if win.btn_generate.isEnabled() and win._last_result is not None:
                res = win._last_result
                log(
                    f"声音克隆完成：{res.audio_seconds:.2f}s 音频 / {res.elapsed:.1f}s / "
                    f"RTF {res.rtf:.2f} -> {win._last_wav_path}"
                )
                log(f"历史记录条数：{win.history_list.count()}")
                win.close()
                log("全部通过")
                app.exit(0)
            return

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(400)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
