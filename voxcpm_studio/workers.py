"""后台线程：模型加载、语音合成。"""

from __future__ import annotations

import traceback

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from .config import AppConfig
from .engine import (GenerationCancelled, GenerationParams, GenerationResult,
                    TTSEngine)


class LoadModelWorker(QThread):
    ok = pyqtSignal()
    fail = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, engine: TTSEngine, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.cfg = cfg

    def run(self) -> None:
        try:
            self.engine.load(
                self.cfg.model_dir,
                device=self.cfg.device,
                use_denoiser=self.cfg.use_denoiser,
                optimize=self.cfg.optimize,
                log=self.log.emit,
            )
        except Exception:
            self.fail.emit(traceback.format_exc(limit=6))
        else:
            self.ok.emit()


class GenerateWorker(QThread):
    chunk = pyqtSignal(object)          # np.ndarray
    done = pyqtSignal(object)           # GenerationResult
    canceled = pyqtSignal(float)        # 已生成的秒数
    progress = pyqtSignal(int, int, float)   # 段序号 / 总段数 / 已生成秒数
    fail = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(
        self,
        engine: TTSEngine,
        text: str,
        control: str | None,
        prompt_wav: str | None,
        prompt_text: str | None,
        reference_wav: str | None,
        params: GenerationParams,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.text = text
        self.control = control
        self.prompt_wav = prompt_wav
        self.prompt_text = prompt_text
        self.reference_wav = reference_wav
        self.params = params
        self._cancel = False

    def cancel(self) -> None:
        """请求取消：引擎在下一个音频块边界停下（非流式则等当前分段跑完）。"""
        self._cancel = True

    def run(self) -> None:
        try:
            result: GenerationResult = self.engine.generate(
                text=self.text,
                control=self.control,
                prompt_wav=self.prompt_wav,
                prompt_text=self.prompt_text,
                reference_wav=self.reference_wav,
                params=self.params,
                on_chunk=lambda c: self.chunk.emit(np.asarray(c)),
                log=self.log.emit,
                should_stop=lambda: self._cancel or self.isInterruptionRequested(),
                on_progress=lambda seg, total, secs: self.progress.emit(seg, total, secs),
            )
        except GenerationCancelled as exc:
            self.canceled.emit(exc.produced_seconds)
        except Exception:
            self.fail.emit(traceback.format_exc(limit=8))
        else:
            self.done.emit(result)
