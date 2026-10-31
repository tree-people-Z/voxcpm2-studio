"""本地 HTTP API：让其它程序（尤其是 AI agent）直接调用这台机器上的 TTS。

设计取舍：
- 只用标准库 ``http.server``，不引入新依赖；
- 与界面共用同一个引擎单例，所以模型只加载一次、显存不会被占两份；
- 同一时刻只允许一路生成（GPU 只有一块），串行化用一把锁；界面正在生成时
  返回 409 而不是排队，避免让人/机互相堵住；
- 生成可中断：每个请求有自己的取消事件，``DELETE /jobs/{id}`` 即可停。

默认只监听 127.0.0.1。若设置里填了 token，则所有请求需带
``X-API-Key: <token>``。
"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import numpy as np

from . import __version__
from .engine import GenerationCancelled, GenerationParams, TTSEngine, save_wav
from .presets import CLONE_TAGS, DESIGN_TAGS, QUICK_COMBOS, compose

# 官方推荐的取值区间（voxcpm CLI 的校验器）
PARAM_RANGES = {
    "cfg_value": [1.0, 3.0],
    "inference_timesteps": [4, 30],
    "max_len": [256, 8192],
}

MODES = ("design", "clone", "ultimate")


# ==================================================================== #
# 任务
# ==================================================================== #
@dataclass
class Job:
    id: str
    text: str
    mode: str
    created: float = field(default_factory=time.time)
    status: str = "queued"          # queued / running / done / error / canceled
    progress: dict[str, Any] = field(default_factory=dict)
    path: str | None = None
    error: str | None = None
    error_status: int = 500
    audio_seconds: float = 0.0
    elapsed: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "cancel_requested": self.cancel.is_set(),
            "mode": self.mode,
            "text_preview": self.text[:60],
            "progress": self.progress,
            "path": self.path,
            "audio_seconds": round(self.audio_seconds, 2),
            "elapsed": round(self.elapsed, 2),
            "error": self.error,
        }


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.payload = {"ok": False, "error": code, "message": message, **extra}


# ==================================================================== #
# 服务
# ==================================================================== #
class ApiServer:
    """后台线程里的 HTTP 服务；start() / stop() 可以反复调用。"""

    def __init__(
        self,
        engine: TTSEngine,
        host: str = "127.0.0.1",
        port: int = 8765,
        token: str = "",
        output_dir: str | Path = "outputs",
        log=None,
        is_busy: Callable[[], bool] | None = None,
    ) -> None:
        self.engine = engine
        self.host = host
        self.port = int(port)
        self.token = token or ""
        self.output_dir = Path(output_dir)
        self.log = log or (lambda _m: None)
        # 界面自己也可能在跑生成：必须共享这一个"忙"判断，否则两边会抢 GPU
        self.is_busy = is_busy or (lambda: False)

        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._gen_lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()

    # ------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def busy(self) -> bool:
        return self._gen_lock.locked() or bool(self.is_busy())

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> str:
        if self._httpd is not None:
            return self.base_url()
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.log(f"本地 API 已启动：{self.base_url()}（文档：GET /help）")
        return self.base_url()

    def stop(self) -> None:
        if self._httpd is None:
            return
        for job in list(self._jobs.values()):
            job.cancel.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
        self.log("本地 API 已停止")

    # ------------------------------------------------------------ #
    # 任务执行
    # ------------------------------------------------------------ #
    def submit(self, payload: dict[str, Any], background: bool) -> Job:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ApiError(400, "missing_text", "text 不能为空")
        mode = str(payload.get("mode") or "design")
        if mode not in MODES:
            raise ApiError(400, "bad_mode", f"mode 必须是 {'/'.join(MODES)}")

        job = Job(id=uuid.uuid4().hex[:12], text=text, mode=mode)
        with self._jobs_lock:
            self._jobs[job.id] = job
            # 只保留最近 50 个任务记录
            for old in sorted(self._jobs.values(), key=lambda j: j.created)[:-50]:
                self._jobs.pop(old.id, None)

        if background:
            threading.Thread(target=self._run_job, args=(job, payload), daemon=True).start()
        else:
            self._run_job(job, payload)
        return job

    def get_job(self, job_id: str) -> Job:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(404, "unknown_job", "没有这个任务 id")
        return job

    def cancel_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        job.cancel.set()
        if job.status in ("queued", "running"):
            # 非流式生成没法打断模型的那一次前向，只能等当前分段结束
            job.status = "canceling"
        return job

    def _run_job(self, job: Job, payload: dict[str, Any]) -> None:
        try:
            if not self.engine.is_loaded:
                raise ApiError(
                    503,
                    "model_not_loaded",
                    "模型尚未加载：请先在 VoxCPM2 Studio 界面里点「加载模型」",
                )
            if self.is_busy():
                raise ApiError(
                    409, "busy", "界面正在生成，请等它结束或先点「停止合成」"
                )
            if not self._gen_lock.acquire(timeout=0.2):
                raise ApiError(
                    409,
                    "busy",
                    "已有生成任务在进行，请稍后重试或先取消当前任务",
                )
            try:
                job.status = "running"
                self._generate(job, payload)
            finally:
                self._gen_lock.release()
        except ApiError as exc:
            job.status = "error"
            job.error = exc.payload.get("message", str(exc))
            job.error_status = exc.status
            job.error_payload = exc.payload  # type: ignore[attr-defined]
        except GenerationCancelled as exc:
            job.status = "canceled"
            job.audio_seconds = exc.produced_seconds
            job.progress["canceled_at_seconds"] = round(exc.produced_seconds, 2)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.done.set()

    def _generate(self, job: Job, payload: dict[str, Any]) -> None:
        mode = job.mode
        params = _params_from(payload, self.engine)

        control = payload.get("control")
        if not control and isinstance(payload.get("tags"), dict):
            order = list((DESIGN_TAGS if mode == "design" else CLONE_TAGS))
            control = compose({k: str(v) for k, v in payload["tags"].items() if v}, order)
        control = (str(control).strip() or None) if control else None

        reference = payload.get("reference_audio")
        reference_path = _materialize_audio(reference, self.output_dir) if reference else None
        prompt_text = (payload.get("prompt_text") or "").strip() or None

        if mode in ("clone", "ultimate") and not reference_path:
            raise ApiError(400, "missing_reference", f"{mode} 模式需要 reference_audio")
        if mode == "ultimate" and not prompt_text:
            raise ApiError(400, "missing_prompt_text", "ultimate 模式需要 prompt_text（逐字转录）")
        if mode == "ultimate":
            control = None

        t0 = time.time()
        result = self.engine.generate(
            text=job.text,
            control=control,
            prompt_wav=reference_path if mode == "ultimate" else None,
            prompt_text=prompt_text if mode == "ultimate" else None,
            reference_wav=reference_path if mode in ("clone", "ultimate") else None,
            params=params,
            should_stop=job.cancel.is_set,
            on_progress=lambda seg, total, secs: job.progress.update(
                {"segment": seg, "segments": total, "audio_seconds": round(secs, 2)}
            ),
        )
        job.audio_seconds = result.audio_seconds
        job.elapsed = time.time() - t0

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out = self.output_dir / f"api_{mode}_{stamp}_{job.id}.wav"
        save_wav(out, result.wav, result.sample_rate)
        job.path = str(out)
        job.status = "done"

    # ------------------------------------------------------------ #
    # 序列化辅助
    # ------------------------------------------------------------ #
    def wav_bytes(self, job: Job) -> bytes:
        if not job.path:
            raise ApiError(404, "no_audio", "该任务还没有音频")
        return Path(job.path).read_bytes()

    def health(self) -> dict[str, Any]:
        eng = self.engine
        return {
            "ok": True,
            "service": "voxcpm2-studio",
            "version": __version__,
            "model_loaded": eng.is_loaded,
            "device": getattr(eng, "device", None),
            "sample_rate": getattr(eng, "sample_rate", None),
            "vram": eng.vram_info(),
            "busy": self.busy,
            "queue": sum(1 for j in self._jobs.values() if j.status in ("queued", "running")),
        }


def _params_from(payload: dict[str, Any], engine: TTSEngine) -> GenerationParams:
    raw = payload.get("params") or {}
    if not isinstance(raw, dict):
        raise ApiError(400, "bad_params", "params 必须是对象")
    p = GenerationParams()
    if payload.get("stream"):
        # 官方：流式下 retry_badcase 会被强制关闭；换来的是取消能在下一个音频块生效
        p.retry_badcase = False
    for key in ("cfg_value", "inference_timesteps", "max_len", "min_len"):
        if key in raw:
            try:
                value = float(raw[key]) if key == "cfg_value" else int(raw[key])
            except (TypeError, ValueError):
                raise ApiError(400, "bad_params", f"{key} 不是数字") from None
            lo, hi = PARAM_RANGES.get(key, [0, 10**9])
            if not lo <= value <= hi:
                raise ApiError(
                    400, "bad_params", f"{key} 超出范围，官方推荐 {lo}–{hi}", field=key
                )
            setattr(p, key, value)
    for key in ("normalize", "denoise", "retry_badcase"):
        if key in raw:
            setattr(p, key, bool(raw[key]))
    if "split_chars" in payload:
        p.split_chars = int(payload["split_chars"] or 0)
    return p


def _materialize_audio(spec: Any, output_dir: Path) -> str:
    """reference_audio 支持三种写法：本地路径 / data URL(base64) / 纯 base64。"""
    if isinstance(spec, dict):
        if "path" in spec:
            spec = spec["path"]
        elif "base64" in spec:
            spec = spec["base64"]
        else:
            raise ApiError(400, "bad_reference", "reference_audio 需要 path 或 base64")
    text = str(spec).strip()
    if not text:
        raise ApiError(400, "bad_reference", "reference_audio 为空")

    if text.startswith("data:") or (len(text) > 512 and "://" not in text and "/" not in text[:64]):
        b64 = text.split(",", 1)[-1] if text.startswith("data:") else text
        try:
            raw = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            raise ApiError(400, "bad_reference", "reference_audio 的 base64 解析失败") from None
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"_ref_{uuid.uuid4().hex[:8]}.wav"
        path.write_bytes(raw)
        return str(path)

    path = Path(text)
    if not path.exists():
        raise ApiError(400, "bad_reference", f"参考音频不存在：{path}")
    return str(path)


# ==================================================================== #
# HTTP 处理
# ==================================================================== #
def _make_handler(server: ApiServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"VoxCPM2Studio/{__version__}"

        # ---------------- 基础 ---------------- #
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return  # 不往 stderr 刷访问日志

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, exc: ApiError) -> None:
            self._json(exc.status, exc.payload)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > 200 * 1024 * 1024:
                raise ApiError(413, "too_large", "请求体过大（上限 200MB）")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                raise ApiError(400, "bad_json", "请求体不是合法 JSON") from None
            if not isinstance(data, dict):
                raise ApiError(400, "bad_json", "请求体必须是 JSON 对象")
            return data

        def _auth_ok(self) -> bool:
            if not server.token:
                return True
            return self.headers.get("X-API-Key", "") == server.token

        # ---------------- 路由 ---------------- #
        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, b"", "text/plain")

        def do_GET(self) -> None:  # noqa: N802
            try:
                if not self._auth_ok():
                    raise ApiError(401, "unauthorized", "需要 X-API-Key")
                self._route_get()
            except ApiError as exc:
                self._error(exc)

        def do_POST(self) -> None:  # noqa: N802
            try:
                if not self._auth_ok():
                    raise ApiError(401, "unauthorized", "需要 X-API-Key")
                self._route_post()
            except ApiError as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": "internal", "message": str(exc)})

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                if not self._auth_ok():
                    raise ApiError(401, "unauthorized", "需要 X-API-Key")
                parts = [p for p in urlparse(self.path).path.split("/") if p]
                if len(parts) == 2 and parts[0] == "jobs":
                    job = server.cancel_job(parts[1])
                    self._json(200, {"ok": True, "job": job.public()})
                    return
                raise ApiError(404, "not_found", "没有这个接口")
            except ApiError as exc:
                self._error(exc)

        def _route_get(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/":
                path = "/help"
            if path == "/help":
                self._json(200, {"ok": True, "endpoints": ENDPOINTS})
            elif path == "/health":
                body = server.health()
                self._json(200 if body["model_loaded"] else 503, body)
            elif path == "/presets":
                self._json(
                    200,
                    {
                        "ok": True,
                        "design": DESIGN_TAGS,
                        "clone": CLONE_TAGS,
                        "quick_combos": QUICK_COMBOS,
                        "note": "description = compose(tags) 按维度顺序用「，」串联",
                    },
                )
            elif path == "/params":
                defaults = GenerationParams()
                self._json(
                    200,
                    {
                        "ok": True,
                        "recommended": PARAM_RANGES,
                        "defaults": {
                            "cfg_value": defaults.cfg_value,
                            "inference_timesteps": defaults.inference_timesteps,
                            "max_len": defaults.max_len,
                            "min_len": defaults.min_len,
                            "normalize": defaults.normalize,
                            "denoise": defaults.denoise,
                            "retry_badcase": defaults.retry_badcase,
                        },
                    },
                )
            elif path == "/jobs":
                with server._jobs_lock:
                    items = sorted(server._jobs.values(), key=lambda j: j.created, reverse=True)
                self._json(200, {"ok": True, "jobs": [j.public() for j in items]})
            elif path.startswith("/jobs/"):
                job = server.get_job(path.split("/")[2])
                payload = {"ok": True, "job": job.public()}
                if job.status == "error":
                    payload["job"]["error_payload"] = getattr(job, "error_payload", None)
                self._json(200, payload)
            elif path.startswith("/audio/"):
                job = server.get_job(path.split("/")[2])
                data = server.wav_bytes(job)
                self._send(
                    200,
                    data,
                    "audio/wav",
                    {"Content-Disposition": f'attachment; filename="{Path(job.path).name}"'},
                )
            else:
                raise ApiError(404, "not_found", "没有这个接口，GET /help 查看列表")

        def _route_post(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/synthesize":
                payload = self._body()
                background = bool(payload.get("async"))
                job = server.submit(payload, background=background)
                if background:
                    self._json(202, {"ok": True, "job": job.public(),
                                     "poll": f"/jobs/{job.id}"})
                    return
                self._respond_job(job, payload)
            elif path == "/cancel":
                payload = self._body()
                job = server.cancel_job(str(payload.get("id") or ""))
                self._json(200, {"ok": True, "job": job.public()})
            else:
                raise ApiError(404, "not_found", "没有这个接口，GET /help 查看列表")

        def _respond_job(self, job: Job, payload: dict[str, Any]) -> None:
            if job.status == "error":
                extra = getattr(job, "error_payload", None)
                if extra:
                    self._json(job.error_status, extra)   # 保留 409/400/503 等语义
                else:
                    self._json(500, {"ok": False, "error": "generation_failed",
                                     "message": job.error})
                return
            if job.status == "canceled":
                self._json(409, {"ok": False, "error": "canceled",
                                 "job": job.public()})
                return
            fmt = str(payload.get("response_format") or payload.get("format") or "json")
            if fmt == "wav":
                data = server.wav_bytes(job)
                self._send(200, data, "audio/wav",
                           {"X-Job-Id": job.id, "X-Audio-Seconds": f"{job.audio_seconds:.2f}"})
            elif fmt == "base64":
                data = server.wav_bytes(job)
                self._json(200, {
                    "ok": True,
                    "job": job.public(),
                    "audio_base64": base64.b64encode(data).decode("ascii"),
                })
            else:
                self._json(200, {"ok": True, "job": job.public(),
                                 "audio_url": f"/audio/{job.id}"})

    return Handler


ENDPOINTS = {
    "GET /help": "接口列表",
    "GET /health": "模型/显存/忙闲状态；模型未加载返回 503",
    "GET /presets": "标签维度库（design / clone）与常用组合",
    "GET /params": "参数默认值与官方推荐区间",
    "POST /synthesize": (
        "合成。JSON: text*, mode(design|clone|ultimate), control 或 tags{dim:tag}, "
        "reference_audio(路径/base64), prompt_text, params{cfg_value,inference_timesteps,"
        "max_len,normalize,denoise,retry_badcase}, split_chars, stream(bool), "
        "response_format(json|wav|base64), async"
    ),
    "GET /jobs": "任务列表（最近 50 个）",
    "GET /jobs/{id}": "单个任务状态与进度",
    "GET /audio/{id}": "下载该任务的 wav",
    "DELETE /jobs/{id}": "取消任务",
    "POST /cancel": "取消任务（body: {id}）",
}


# ------------------------------------------------------------------ #
def write_wav_bytes(wav: np.ndarray, sample_rate: int) -> bytes:
    """内存里生成 wav 字节（测试和 MCP 用）。"""
    buf = io.BytesIO()
    import soundfile as sf

    sf.write(buf, np.asarray(wav, dtype=np.float32), sample_rate, format="WAV")
    return buf.getvalue()
