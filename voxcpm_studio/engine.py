"""VoxCPM2 推理引擎封装（与界面无关，可单独在脚本 / 线程里使用）。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator

import numpy as np

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]
ProgressFn = Callable[[int, int, float], None]   # (当前段, 总段数, 已生成秒数)


class GenerationCancelled(RuntimeError):
    """用户中途取消了生成。"""

    def __init__(self, produced_seconds: float = 0.0) -> None:
        super().__init__("生成已取消")
        self.produced_seconds = produced_seconds


def _noop(_: str) -> None:
    pass


@dataclass
class GenerationParams:
    # 默认值 = 官方推荐区间上界（1.0–3.0 / 4–30），质量优先；追求速度可降到 2.0 / 10
    cfg_value: float = 3.0
    inference_timesteps: int = 30
    max_len: int = 4096
    min_len: int = 2
    normalize: bool = False
    denoise: bool = False
    retry_badcase: bool = True
    split_chars: int = 0  # >0 时长文本按句子切成该长度以内的片段，逐段合成后拼接


@dataclass
class GenerationResult:
    wav: np.ndarray
    sample_rate: int
    elapsed: float
    audio_seconds: float
    segments: int = 1

    @property
    def rtf(self) -> float:
        return self.elapsed / self.audio_seconds if self.audio_seconds > 0 else 0.0


_SENT_SPLIT = re.compile(r"(?<=[。！？；…!?;\n])")


def split_text(text: str, max_chars: int) -> list[str]:
    """按句末标点切分，并合并为不超过 max_chars 的片段。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    for sent in _SENT_SPLIT.split(text):
        sent = sent.strip()
        if not sent:
            continue
        while len(sent) > max_chars:
            cut = sent.rfind("，", 0, max_chars)
            if cut < max_chars // 2:
                cut = sent.rfind(",", 0, max_chars)
            if cut < max_chars // 2:
                cut = max_chars
            pieces.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if sent:
            pieces.append(sent)

    out: list[str] = []
    buf = ""
    for p in pieces:
        if not buf:
            buf = p
        elif len(buf) + len(p) <= max_chars:
            buf += p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out or [text]


class TTSEngine:
    """惰性加载 VoxCPM2，提供流式 / 一次性合成。"""

    def __init__(self) -> None:
        self._model = None
        self.model_dir: str | None = None
        self.device: str | None = None
        self._sample_rate = 48000
        self._log: LogFn | None = None

    # ------------------------------------------------------------ #
    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def torch_device(self) -> str:
        return self.device or "?"

    def vram_info(self) -> tuple[float, float] | None:
        """返回 (已用GB, 总GB)，不可用时返回 None。"""
        try:
            import torch

            if not torch.cuda.is_available():
                return None
            free, total = torch.cuda.mem_get_info()
            used = (total - free) / 1024**3
            return used, total / 1024**3
        except Exception:
            return None

    # ------------------------------------------------------------ #
    @staticmethod
    def resolve_optimize(
        want: bool, device: str, log: LogFn = _noop
    ) -> tuple[bool, str]:
        """决定是否真的开启 torch.compile，并给出人能看懂的结论。

        官方 ``optimize()`` 需要 triton（Windows 官方不发 triton wheel），
        不满足时会静默退化成 eager，还会白跑一次官方 warmup —— 所以这里提前判断。
        返回 ``(是否开启, 给人看的原因)``。
        """
        if not want:
            return False, "已关闭"

        target = (device or "auto").lower()
        if target == "cpu":
            return False, "torch.compile 需要 CUDA，当前设备是 CPU，已跳过"

        if target == "auto":
            try:
                import torch

                if not torch.cuda.is_available():
                    return False, "torch.compile 需要 CUDA，未检测到可用显卡，已跳过"
            except Exception:  # noqa: BLE001
                return False, "torch.compile 需要 CUDA，无法检测设备，已跳过"

        try:
            import triton  # noqa: F401
        except ImportError:
            return False, (
                "torch.compile 已跳过：缺少 triton。"
                "Windows 需先装 triton-windows（第三方移植，需与 torch 版本匹配）"
            )

        return True, "已启用 torch.compile（首次生成会多花几十秒编译）"

    # ------------------------------------------------------------ #
    def load(
        self,
        model_dir: str,
        device: str = "auto",
        use_denoiser: bool = False,
        optimize: bool = False,
        log: LogFn = _noop,
        warmup: bool = True,
    ) -> None:
        model_path = Path(model_dir)
        if not (model_path / "config.json").exists():
            raise FileNotFoundError(
                f"模型目录无效：{model_path}\n请先下载模型（见 README）或在设置中指定正确路径。"
            )

        from voxcpm import VoxCPM

        log(f"加载模型：{model_path}")
        dev = None if device in ("auto", "", None) else device
        use_compile, compile_note = self.resolve_optimize(optimize, device, log)
        log(f"加速设置：{compile_note}")
        t0 = time.time()
        self._model = VoxCPM.from_pretrained(
            hf_model_id=str(model_path),
            load_denoiser=use_denoiser,
            optimize=use_compile,
            local_files_only=True,
            device=dev,
        )
        self.model_dir = str(model_path)
        self.device = getattr(self._model.tts_model, "device", dev or "auto")
        self._sample_rate = int(
            getattr(self._model.tts_model, "sample_rate", 48000) or 48000
        )
        log(f"模型就绪（{time.time() - t0:.1f}s），设备 {self.device}，采样率 {self._sample_rate}Hz")
        if warmup:
            # 首次推理要编译 CUDA kernel，先预热一次，避免用户第一次点击等待过久
            log("预热中…")
            t1 = time.time()
            try:
                for _ in self.generate_stream(
                    "你好。", params=GenerationParams(inference_timesteps=4, retry_badcase=False)
                ):
                    pass
                log(f"预热完成（{time.time() - t1:.1f}s）")
            except Exception as exc:  # noqa: BLE001
                log(f"预热失败（不影响使用）：{exc}")

    def unload(self) -> None:
        self._model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # ------------------------------------------------------------ #
    @staticmethod
    def build_text(text: str, control: str | None) -> str:
        """VoxCPM2 用 ``(声音描述)正文`` 的形式进行声音设计 / 可控克隆。"""
        control = (control or "").strip()
        return f"({control}){text}" if control else text

    def _kwargs(
        self,
        text: str,
        control: str | None,
        prompt_wav: str | None,
        prompt_text: str | None,
        reference_wav: str | None,
        p: GenerationParams,
    ) -> dict:
        return dict(
            text=self.build_text(text, control),
            prompt_wav_path=prompt_wav or None,
            prompt_text=(prompt_text or None) if prompt_wav else None,
            reference_wav_path=reference_wav or None,
            cfg_value=float(p.cfg_value),
            inference_timesteps=int(p.inference_timesteps),
            min_len=int(p.min_len),
            max_len=int(p.max_len),
            normalize=bool(p.normalize),
            denoise=bool(p.denoise),
            retry_badcase=bool(p.retry_badcase),
        )

    def generate_stream(
        self,
        text: str,
        control: str | None = None,
        prompt_wav: str | None = None,
        prompt_text: str | None = None,
        reference_wav: str | None = None,
        params: GenerationParams | None = None,
        stream: bool | None = None,
        should_stop: StopFn | None = None,
        on_segment: Callable[[int, int], None] | None = None,
    ) -> Generator[np.ndarray, None, None]:
        """逐块产出 float32 单声道波形（采样率见 sample_rate）。

        官方限制：流式生成时 ``retry_badcase`` 会被强制关闭
       （voxcpm2.py: "Retry on bad cases is not supported in streaming mode"）。
        所以勾了「异常重试」就自动改走一次性生成，让重试真正生效；
        代价是波形要等这句生成完才一次性出现。
        """
        if self._model is None:
            raise RuntimeError("模型尚未加载")
        if not text.strip():
            raise ValueError("文本不能为空")
        p = params or GenerationParams()
        streaming = (not p.retry_badcase) if stream is None else bool(stream)
        if not streaming and self._log:
            self._log("非流式生成（异常重试生效，波形在结束时出现）")
        segments = split_text(text.strip(), p.split_chars)
        produced = 0.0
        for i, seg in enumerate(segments):
            if should_stop is not None and should_stop():
                raise GenerationCancelled(produced)
            if len(segments) > 1 and self._log:
                self._log(f"分段 {i + 1}/{len(segments)}（{len(seg)} 字）")
            if on_segment is not None:
                on_segment(i + 1, len(segments))
            kwargs = self._kwargs(seg, control, prompt_wav, prompt_text, reference_wav, p)
            if streaming:
                for chunk in self._model.generate_streaming(**kwargs):
                    # 每块都查一次：取消的响应延迟 ≈ 一块音频的生成时间（约 0.15s）
                    produced += len(chunk) / max(self.sample_rate, 1)
                    if should_stop is not None and should_stop():
                        raise GenerationCancelled(produced)
                    yield chunk
            else:
                # 一次性生成：retry_badcase 才会真正生效，代价是取消要等这一段跑完
                wav = np.asarray(self._model.generate(**kwargs), dtype=np.float32).reshape(-1)
                produced += len(wav) / max(self.sample_rate, 1)
                if should_stop is not None and should_stop():
                    raise GenerationCancelled(produced)
                yield wav

    def generate(
        self,
        text: str,
        control: str | None = None,
        prompt_wav: str | None = None,
        prompt_text: str | None = None,
        reference_wav: str | None = None,
        params: GenerationParams | None = None,
        on_chunk: Callable[[np.ndarray], None] | None = None,
        log: LogFn | None = None,
        should_stop: StopFn | None = None,
        on_progress: ProgressFn | None = None,
    ) -> GenerationResult:
        t0 = time.time()
        self._log = log
        chunks: list[np.ndarray] = []
        state = {"seg": 1, "total": 1}

        def _seg(i: int, total: int) -> None:
            state["seg"] = i
            state["total"] = total

        produced = 0.0
        for chunk in self.generate_stream(
            text=text,
            control=control,
            prompt_wav=prompt_wav,
            prompt_text=prompt_text,
            reference_wav=reference_wav,
            params=params,
            should_stop=should_stop,
            on_segment=_seg,
        ):
            chunks.append(chunk)
            produced += len(chunk) / max(self.sample_rate, 1)
            if on_chunk is not None:
                on_chunk(chunk)
            if on_progress is not None:
                on_progress(state["seg"], state["total"], produced)
        wav = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        elapsed = time.time() - t0
        seg_count = len(split_text(text.strip(), (params or GenerationParams()).split_chars))
        return GenerationResult(
            wav=np.asarray(wav, dtype=np.float32).reshape(-1),
            sample_rate=self.sample_rate,
            elapsed=elapsed,
            audio_seconds=len(wav) / max(self.sample_rate, 1),
            segments=seg_count,
        )


def save_wav(path: str | Path, wav: np.ndarray, sample_rate: int) -> Path:
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(wav, dtype=np.float32), sample_rate)
    return path


def audio_duration(path: str | Path) -> float:
    """读取音频时长（秒）。"""
    import soundfile as sf

    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate or 1)


_ENGINE: TTSEngine | None = None


def get_engine() -> TTSEngine:
    """进程内单例，供界面各页面共享同一份显存。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = TTSEngine()
    return _ENGINE
