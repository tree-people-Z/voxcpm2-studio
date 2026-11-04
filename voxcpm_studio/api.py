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
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

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
KNOWN_PARAMS = ("cfg_value", "inference_timesteps", "max_len", "min_len",
                "normalize", "denoise", "retry_badcase", "split_chars")

# ---- API 规范层（见 Tree/docs/module-api-spec.md）---- #
API_VERSION = 1
MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 上传上限，防止把磁盘写满


def _pkg_root() -> Path:
    """仓库根目录；打包后变成 _internal（ops.json 由 spec 作为数据文件带进去）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


PKG_ROOT = _pkg_root()
STATE_MAP = {
    "queued": "queued",
    "running": "running",
    "canceling": "canceling",
    "done": "succeeded",
    "error": "failed",
    "canceled": "canceled",
}


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
    # ---- 规范层字段 ----
    op: str = "audio.tts"
    pct: float = 0.0
    stage: str = "排队中"
    detail: str = ""
    started: float | None = None
    ended: float | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    gen_params: GenerationParams | None = None
    error_payload: dict[str, Any] | None = None

    def error_code(self) -> str:
        """失败任务要报出真实原因（bad_params / missing_reference …），不能一律 internal。"""
        err = (self.error_payload or {}).get("error")
        if isinstance(err, dict):
            return str(err.get("code") or "internal")
        if err:
            return str(err)
        return "internal"

    def api(self) -> dict[str, Any]:
        """规范层任务形状：state / progress{pct,stage,detail} / outputs。"""
        outputs = {"audio": Path(self.path).name} if self.path else None
        error = None
        if self.status == "error":
            error = {"code": self.error_code(), "message": self.error or "生成失败"}
        elif self.status == "canceled":
            error = {"code": "canceled", "message": "任务已取消"}
        return {
            "id": self.id,
            "op": self.op,
            "state": STATE_MAP.get(self.status, self.status),
            "progress": {
                "pct": round(float(self.pct or 0.0), 4),
                "stage": self.stage,
                "detail": self.detail,
            },
            "inputs": self.inputs,
            "params": self.params,
            "outputs": outputs,
            "error": error,
            "created_at": self.created,
            "started_at": self.started,
            "ended_at": self.ended,
        }

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
        # 规范层的错误形状：{ok:false, error:{code, message, field}}
        # 顶层仍然保留 message，老客户端读 body["message"] 不会断。
        err: dict[str, Any] = {"code": code, "message": message}
        field = extra.pop("field", None)
        if field:
            err["field"] = field
        self.payload = {"ok": False, "error": err, "message": message, **extra}


# ==================================================================== #
# 服务
# ==================================================================== #
class ApiServer:
    """后台线程里的 HTTP 服务；start() / stop() 可以反复调用。"""

    def __init__(
        self,
        engine: TTSEngine,
        host: str = "127.0.0.1",
        port: int = 7810,
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
        # 独立运行时由 server/main.py 设置：模型没加载就先自动加载
        self.ensure_loaded: Callable[[], None] | None = None
        self.upload_dir = Path(output_dir).parent / "work" / "uploads"

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
            raise ApiError(400, "missing_text", "文本不能为空", field="text")
        mode = str(payload.get("mode") or "design")
        if mode not in MODES:
            raise ApiError(400, "bad_mode", f"mode 必须是 {'/'.join(MODES)}", field="mode")
        # 参数在提交时就校验：越界 / 拼错立刻 400，不要变成一条失败任务
        gen_params = _params_from(payload, self.engine)

        job = Job(id=uuid.uuid4().hex[:12], text=text, mode=mode)
        job.gen_params = gen_params
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
            if self.is_busy():
                raise ApiError(
                    409, "busy", "界面正在生成，请等它结束或先点「停止合成」"
                )
            # 单 GPU：后来的任务排队等锁，而不是直接拒绝。
            # 等待期间每 0.2s 检查一次取消，排队中的任务也能被取消。
            while not self._gen_lock.acquire(timeout=0.2):
                if job.cancel.is_set():
                    raise GenerationCancelled(0.0)
                job.stage = "排队中"
            try:
                # 加载模型必须在锁内：否则并发提交会同时加载三份，直接撑爆显存。
                if not self.engine.is_loaded:
                    if self.ensure_loaded is not None:
                        job.stage = "加载模型"
                        self.ensure_loaded()
                    if not self.engine.is_loaded:
                        raise ApiError(
                            503, "model_missing",
                            "模型尚未加载：独立运行时请检查 models/VoxCPM2 是否存在",
                        )
                job.status = "running"
                job.started = time.time()
                job.stage = "加载模型" if not self.engine.is_loaded else "开始合成"
                self._generate(job, payload)
            finally:
                self._gen_lock.release()
        except ApiError as exc:
            job.status = "error"
            job.error = exc.payload.get("message", str(exc))
            job.error_status = exc.status
            job.error_payload = exc.payload
        except GenerationCancelled as exc:
            job.status = "canceled"
            job.audio_seconds = exc.produced_seconds
            job.progress["canceled_at_seconds"] = round(exc.produced_seconds, 2)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.ended = time.time()
            job.done.set()

    def _generate(self, job: Job, payload: dict[str, Any]) -> None:
        mode = job.mode
        params = job.gen_params or _params_from(payload, self.engine)

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
            on_progress=lambda seg, total, secs: _on_progress(job, seg, total, secs),
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
    def ops(self) -> list[dict[str, Any]]:
        """能力声明来自 ops.json（唯一真源，界面和 Tree 都读它）。"""
        path = PKG_ROOT / "ops.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))["ops"]
        except Exception:  # noqa: BLE001
            return []

    def output_name(self, job: Job) -> str | None:
        return Path(job.path).name if job.path else None

    def list_outputs(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.output_dir.exists():
            return rows
        for p in self.output_dir.iterdir():
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rows.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                         "kind": "audio" if p.suffix.lower() in (".wav", ".mp3", ".flac") else "file"})
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return rows

    def cancel_all(self) -> int:
        n = 0
        for job in list(self._jobs.values()):
            if job.status in ("queued", "running"):
                try:
                    self.cancel_job(job.id)
                    n += 1
                except ApiError:
                    pass
        return n

    def unload(self) -> dict[str, Any]:
        # 必须拿到生成锁再卸：只查 busy 存在竞态窗口，任务可能刚好在检查之后启动，
        # 模型被卸掉后正在跑的那次生成会直接崩在 CUDA 上。
        if not self._gen_lock.acquire(timeout=0.2):
            raise ApiError(429, "busy", "还有任务在跑，不能卸载")
        try:
            if self.is_busy():
                raise ApiError(429, "busy", "界面正在生成，不能卸载")
            before = _vram_used_mb()
            try:
                self.engine.unload()
            except Exception:  # noqa: BLE001
                pass
            after = _vram_used_mb()
        finally:
            self._gen_lock.release()
        return {"freed_mb": max(0, int((before or 0) - (after or 0)))}

    def api_state(self) -> dict[str, Any]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)[:50]
        return {
            "ok": True,
            "api_version": API_VERSION,
            "version": __version__,
            "ops": self.ops(),
            "jobs": [j.api() for j in jobs],
            "outputs": self.list_outputs()[:50],
            "output_dir": str(self.output_dir),
            "health": self.health(),
        }

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


def _on_progress(job: Job, segment: int, total: int, seconds: float) -> None:
    """把引擎的（第几段/共几段/已生成秒数）翻成规范层的 pct/stage/detail。

    两条硬规则：pct 只增不减；单段时 pct 停住，靠 stage/detail 给反馈。
    """
    total = max(1, int(total))
    pct = 0.05 + 0.9 * ((max(1, int(segment)) - 1) / total)
    job.pct = max(job.pct, min(0.95, pct))
    job.stage = f"第 {segment}/{total} 段"
    job.detail = f"已生成 {seconds:.1f}s"
    job.progress.update({"segment": segment, "segments": total,
                         "audio_seconds": round(seconds, 2)})


def _vram_used_mb() -> int:
    try:
        import torch
        if torch.cuda.is_available():
            return int(torch.cuda.memory_reserved() / 1024 / 1024)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _params_from(payload: dict[str, Any], engine: TTSEngine) -> GenerationParams:
    raw = payload.get("params") or {}
    if not isinstance(raw, dict):
        raise ApiError(400, "bad_params", "params 必须是对象")
    unknown = set(raw) - set(KNOWN_PARAMS)
    if unknown:
        first = sorted(unknown)[0]
        raise ApiError(400, "bad_params", f"不接受参数：{', '.join(sorted(unknown))}",
                       field=first)
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
    elif "split_chars" in raw:
        p.split_chars = int(raw["split_chars"] or 0)
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

        # ---------------- /api 规范层 ---------------- #
        def _is_api(self) -> bool:
            return urlparse(self.path).path.startswith("/api/")

        def _api(self, method: str) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/api"
            segs = [unquote(s) for s in path.split("/") if s]     # ["api", ...]
            try:
                if method == "GET":
                    self._api_get(segs)
                elif method == "POST":
                    self._api_post(segs)
                elif method == "DELETE":
                    self._api_delete(segs)
                else:
                    raise ApiError(405, "method_not_allowed", f"不支持 {method}")
            except ApiError as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False,
                                 "error": {"code": "internal", "message": str(exc)}})

        def _api_get(self, segs: list[str]) -> None:
            if segs == ["api", "health"]:
                body = server.health()
                body.update({"module": "voxcpm2-studio", "api_version": API_VERSION,
                             "version": __version__,
                             "state": "ready" if body.get("model_loaded") else "starting",
                             "gpu": _gpu_info(server)})
                self._json(200, body)
                return
            if segs == ["api", "ops"]:
                self._json(200, {"ok": True, "ops": server.ops()})
                return
            if segs == ["api", "state"]:
                self._json(200, server.api_state())
                return
            if segs == ["api", "jobs"]:
                with server._jobs_lock:
                    items = sorted(server._jobs.values(), key=lambda j: j.created, reverse=True)
                self._json(200, {"ok": True, "jobs": [j.api() for j in items]})
                return
            if len(segs) == 3 and segs[:2] == ["api", "jobs"]:
                self._json(200, {"ok": True, "job": server.get_job(segs[2]).api()})
                return
            if segs == ["api", "outputs"]:
                self._json(200, {"ok": True, "outputs": server.list_outputs()})
                return
            if len(segs) >= 3 and segs[:2] == ["api", "outputs"]:
                self._send_output("/".join(segs[2:]))
                return
            raise ApiError(404, "not_found", f"没有这个接口：{'/'.join(segs)}")

        def _api_post(self, segs: list[str]) -> None:
            if segs == ["api", "jobs"]:
                body = self._body()
                op_id = str(body.get("op") or "audio.tts")
                if op_id != "audio.tts":
                    raise ApiError(404, "unknown_op", f"没有这个能力：{op_id}")

                params = dict(body.get("params") or {})
                inputs = dict(body.get("inputs") or {})
                # 拼错的参数必须报错，不能静默丢掉（否则用户以为生效了）
                allowed = set(KNOWN_PARAMS) | {"text", "mode", "control", "prompt_text"}
                unknown = set(params) - allowed
                if unknown:
                    first = sorted(unknown)[0]
                    raise ApiError(400, "bad_params",
                                   f"不接受参数：{', '.join(sorted(unknown))}", field=first)
                mode = str(params.get("mode") or "design")
                if mode not in MODES:
                    raise ApiError(400, "bad_param", f"mode 必须是 {'/'.join(MODES)}",
                                   field="mode")
                reference = inputs.get("reference")
                if mode in ("clone", "ultimate") and not reference:
                    raise ApiError(400, "missing_input",
                                   f"{mode} 模式需要参考音频", field="reference")
                if mode == "ultimate" and not str(params.get("prompt_text") or "").strip():
                    raise ApiError(400, "bad_param", "极限克隆需要参考音频的逐字文本",
                                   field="prompt_text")

                payload = {
                    "text": params.get("text") or "",
                    "mode": mode,
                    "control": params.get("control") or "",
                    "prompt_text": params.get("prompt_text") or "",
                    "params": {k: v for k, v in params.items()
                               if k in ("cfg_value", "inference_timesteps", "max_len",
                                        "min_len", "normalize", "denoise",
                                        "retry_badcase", "split_chars")},
                }
                if reference:
                    payload["reference_audio"] = {"path": str(reference)}

                job = server.submit(payload, background=True)
                job.inputs = {k: str(v) for k, v in inputs.items()}
                job.params = dict(params)
                self._json(202, {"ok": True, "job": job.api()})
                return

            if segs == ["api", "upload"]:
                query = urlparse(self.path).query
                name = ""
                for part in query.split("&"):
                    if part.startswith("name="):
                        name = unquote(part[5:])
                if not name:
                    raise ApiError(400, "bad_param", "缺少 name 参数", field="name")
                safe = Path(name).name or "upload.wav"
                slot = server.upload_dir / f"{int(time.time() * 1000)}"
                slot.mkdir(parents=True, exist_ok=True)
                target = slot / safe
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise ApiError(400, "bad_json", "Content-Length 不是数字") from None
                if length <= 0:
                    raise ApiError(400, "bad_json", "请求体为空")
                if length > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "too_large",
                                   f"上传超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
                remaining = length
                with target.open("wb") as fh:
                    while remaining > 0:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        fh.write(chunk)
                        remaining -= len(chunk)
                self._json(200, {"ok": True, "token": str(target),
                                 "path": str(target), "name": safe})
                return

            if segs == ["api", "unload"]:
                self._json(200, {"ok": True, **server.unload()})
                return
            if segs == ["api", "cancel-all"]:
                self._json(200, {"ok": True, "canceled": server.cancel_all()})
                return
            if segs == ["api", "shutdown"]:
                self._json(200, {"ok": True})
                threading.Thread(target=server.stop, daemon=True).start()
                return
            raise ApiError(404, "not_found", f"没有这个接口：{'/'.join(segs)}")

        def _api_delete(self, segs: list[str]) -> None:
            if len(segs) == 3 and segs[:2] == ["api", "jobs"]:
                job = server.cancel_job(segs[2])
                self._json(200, {"ok": True, "job": job.api()})
                return
            raise ApiError(404, "not_found", f"没有这个接口：{'/'.join(segs)}")

        def _send_output(self, name: str) -> None:
            target = (server.output_dir / name).resolve()
            try:
                target.relative_to(server.output_dir.resolve())
            except ValueError:
                raise ApiError(404, "missing_output", "没有这个产物") from None
            if not target.is_file():
                raise ApiError(404, "missing_output", "没有这个产物")

            size = target.stat().st_size
            ctype = "audio/wav" if target.suffix.lower() == ".wav" else "application/octet-stream"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            partial = False
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                try:
                    start = int(a) if a else 0
                    end = int(b) if b else size - 1
                except ValueError:
                    start, end = 0, size - 1
                # 越界 / 反着写的 Range 一律夹到合法区间，否则会算出负数长度
                start = max(0, min(start, size - 1))
                end = max(start, min(end, size - 1))
                partial = True

            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with target.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        # ---------------- 路由 ---------------- #
        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, b"", "text/plain")

        def do_GET(self) -> None:  # noqa: N802
            try:
                if not self._auth_ok():
                    raise ApiError(401, "unauthorized", "需要 X-API-Key")
                if self._is_api():
                    self._api("GET")
                    return
                self._route_get()
            except ApiError as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False,
                                 "error": {"code": "internal", "message": str(exc)}})

        def do_POST(self) -> None:  # noqa: N802
            try:
                if not self._auth_ok():
                    raise ApiError(401, "unauthorized", "需要 X-API-Key")
                if self._is_api():
                    self._api("POST")
                    return
                self._route_post()
            except ApiError as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False,
                                 "error": {"code": "internal", "message": str(exc)}})

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                if not self._auth_ok():
                    raise ApiError(401, "unauthorized", "需要 X-API-Key")
                if self._is_api():
                    self._api("DELETE")
                    return
                parts = [p for p in urlparse(self.path).path.split("/") if p]
                if len(parts) == 2 and parts[0] == "jobs":
                    job = server.cancel_job(parts[1])
                    self._json(200, {"ok": True, "job": job.public()})
                    return
                raise ApiError(404, "not_found", "没有这个接口")
            except ApiError as exc:
                self._error(exc)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False,
                                 "error": {"code": "internal", "message": str(exc)}})

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
                    self._json(500, {"ok": False,
                                     "error": {"code": "generation_failed",
                                               "message": job.error}})
                return
            if job.status == "canceled":
                self._json(409, {"ok": False,
                                 "error": {"code": "canceled", "message": "任务已取消"},
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


def _gpu_info(server: ApiServer) -> dict[str, Any]:
    info: dict[str, Any] = {"name": None, "vram_total_mb": None, "vram_used_mb": None}
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            info["name"] = torch.cuda.get_device_name(0)
            info["vram_total_mb"] = int(total / 1024 / 1024)
            info["vram_used_mb"] = int((total - free) / 1024 / 1024)
    except Exception:  # noqa: BLE001
        pass
    return info


ENDPOINTS = {
    "GET /api/health": "就绪/忙闲/显存（规范层）",
    "GET /api/ops": "能力声明（来自 ops.json）",
    "GET /api/state": "一次拿全：ops + 任务 + 产物",
    "POST /api/jobs": "提交合成任务 {op, params, inputs} → 202",
    "GET /api/jobs/{id}": "任务状态与进度",
    "DELETE /api/jobs/{id}": "取消任务",
    "GET /api/outputs/{name}": "产物下载（支持 Range）",
    "POST /api/upload?name=x.wav": "上传文件（原始字节）",
    "POST /api/unload": "释放显存",
    "POST /api/cancel-all": "取消所有任务",
    "POST /api/shutdown": "停止服务",
    "GET /help": "接口列表（旧）",
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
