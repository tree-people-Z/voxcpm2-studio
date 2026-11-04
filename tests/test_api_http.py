"""HTTP 层的真起服务测试（假引擎，不需要 GPU / 模型）。

和 test_core.py 的分工：那边测纯函数，这边真的 listen 一个端口、走 http.client，
把 /api 规范层的路由、错误形状、任务状态机、产物下载都过一遍。
"""

from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voxcpm_studio.api import ApiServer  # noqa: E402
from voxcpm_studio.engine import GenerationCancelled, GenerationResult  # noqa: E402


class StubEngine:
    """只实现 ApiServer 真正用到的那几个属性的假引擎。"""

    def __init__(self) -> None:
        self.is_loaded = True
        self.device = "cpu"
        self.sample_rate = 48000
        self.model_dir: str | None = None
        self.calls = 0

    def vram_info(self):  # health / /api/health 会读
        return (0.0, 0.0)

    def unload(self) -> None:
        self.is_loaded = False

    def generate(self, text, control, prompt_wav, prompt_text, reference_wav,
                 params, should_stop, on_progress) -> GenerationResult:
        self.calls += 1
        on_progress(1, 1, 0.5)
        if should_stop():
            raise GenerationCancelled(0.0)
        return GenerationResult(
            wav=np.zeros(4800, dtype=np.float32),
            sample_rate=48000,
            elapsed=0.05,
            audio_seconds=0.1,
        )


@pytest.fixture()
def api(tmp_path):
    server = ApiServer(StubEngine(), host="127.0.0.1", port=0,
                       output_dir=tmp_path / "outputs")
    server.start()
    port = server._httpd.server_address[1]   # port=0 → 真实端口只有绑定后才知道
    try:
        yield server, port
    finally:
        server.stop()


def call(port: int, method: str, path: str, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"} if payload else {}
    hdrs.update(headers or {})
    try:
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            data = raw
        return resp.status, data, dict(resp.getheaders())
    finally:
        conn.close()


def wait_done(port: int, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        status, body, _ = call(port, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        job = body["job"]
        if job["state"] in ("succeeded", "failed", "canceled"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"任务没在 {timeout}s 内结束：{job}")


# ------------------------------------------------------------------ #
# 规范层：状态 / 能力 / 形状
# ------------------------------------------------------------------ #
def test_api_health_shape(api):
    _, port = api
    status, body, _ = call(port, "GET", "/api/health")
    assert status == 200
    assert body["module"] == "voxcpm2-studio"
    assert body["api_version"] == 1
    assert body["state"] == "ready"
    assert set(body["gpu"]) == {"name", "vram_total_mb", "vram_used_mb"}


def test_api_state_includes_ops_from_json(api):
    _, port = api
    status, body, _ = call(port, "GET", "/api/state")
    assert status == 200
    assert body["ok"] is True
    assert body["ops"], "ops.json 应该被读到（能力声明唯一真源）"
    op_ids = {o.get("id") for o in body["ops"]}
    assert "audio.tts" in op_ids
    assert {"jobs", "outputs", "output_dir", "health"} <= set(body)


def test_unknown_api_endpoint_is_404_with_dict_error(api):
    _, port = api
    status, body, _ = call(port, "GET", "/api/nope")
    assert status == 404
    assert body["ok"] is False
    assert isinstance(body["error"], dict)
    assert body["error"]["code"] == "not_found"


# ------------------------------------------------------------------ #
# 任务：提交 → 轮询 → 下载
# ------------------------------------------------------------------ #
def test_submit_job_roundtrip_and_download(api):
    _, port = api
    status, body, _ = call(port, "POST", "/api/jobs", {
        "op": "audio.tts",
        "params": {"text": "你好", "mode": "design", "inference_timesteps": 8},
    })
    assert status == 202
    job = body["job"]
    assert job["state"] in ("queued", "running")

    job = wait_done(port, job["id"])
    assert job["state"] == "succeeded"
    assert job["error"] is None
    assert job["outputs"]["audio"].endswith(".wav")
    assert job["params"]["mode"] == "design"

    status, data, headers = call(port, "GET", f"/api/outputs/{job['outputs']['audio']}")
    assert status == 200
    assert headers["Content-Type"] == "audio/wav"
    assert data[:4] == b"RIFF"


def test_outputs_support_range_requests(api):
    _, port = api
    _, body, _ = call(port, "POST", "/api/jobs",
                      {"params": {"text": "你好", "mode": "design"}})
    job = wait_done(port, body["job"]["id"])
    name = job["outputs"]["audio"]

    status, data, headers = call(port, "GET", f"/api/outputs/{name}",
                                 headers={"Range": "bytes=0-3"})
    assert status == 206
    assert data[:4] == b"RIFF"
    assert headers["Content-Range"].startswith("bytes 0-3/")

    # 越界 / 反着写的 Range 不能算出负数长度
    status, data, _ = call(port, "GET", f"/api/outputs/{name}",
                           headers={"Range": "bytes=999-0"})
    assert status == 206
    assert len(data) == 1


def test_outputs_reject_path_traversal(api):
    _, port = api
    status, body, _ = call(port, "GET", "/api/outputs/..%2F..%2Fconfig.json")
    assert status == 404
    assert body["error"]["code"] == "missing_output"


# ------------------------------------------------------------------ #
# 校验：错就当场 400，别变成一条失败任务
# ------------------------------------------------------------------ #
def test_unknown_param_is_rejected_with_field(api):
    _, port = api
    status, body, _ = call(port, "POST", "/api/jobs", {
        "params": {"text": "你好", "cfgg_value": 2.0},   # 拼错
    })
    assert status == 400
    assert body["error"]["code"] == "bad_params"
    assert body["error"]["field"] == "cfgg_value"
    assert body["message"]                       # 老客户端读顶层 message 不能断


def test_clone_mode_requires_reference(api):
    _, port = api
    status, body, _ = call(port, "POST", "/api/jobs",
                           {"params": {"text": "你好", "mode": "clone"}})
    assert status == 400
    assert body["error"]["code"] == "missing_input"
    assert body["error"]["field"] == "reference"


def test_unknown_op_is_404(api):
    _, port = api
    status, body, _ = call(port, "POST", "/api/jobs",
                           {"op": "video.make", "params": {"text": "你好"}})
    assert status == 404
    assert body["error"]["code"] == "unknown_op"


# ------------------------------------------------------------------ #
# 失败任务要报真实原因（不是一律 internal）
# ------------------------------------------------------------------ #
def test_failed_job_reports_real_error_code(api):
    _, port = api
    status, body, _ = call(port, "POST", "/api/jobs", {
        "params": {"text": "你好", "mode": "clone"},
        "inputs": {"reference": "/nonexistent/ref.wav"},
    })
    assert status == 202
    job = wait_done(port, body["job"]["id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == "bad_reference"   # 不是 "internal"
    assert "nonexistent" in job["error"]["message"]      # Windows 下分隔符会变成 \


# ------------------------------------------------------------------ #
# 显存释放不能和正在跑的任务抢锁
# ------------------------------------------------------------------ #
def test_unload_refuses_while_generation_lock_held(api):
    server, port = api
    server._gen_lock.acquire()          # 模拟有任务正在生成
    try:
        status, body, _ = call(port, "POST", "/api/unload")
        assert status == 429
        assert body["error"]["code"] == "busy"
        assert server.engine.is_loaded is True   # 没被卸掉
    finally:
        server._gen_lock.release()


def test_unload_when_idle(api):
    server, port = api
    status, body, _ = call(port, "POST", "/api/unload")
    assert status == 200
    assert body["ok"] is True
    assert server.engine.is_loaded is False
