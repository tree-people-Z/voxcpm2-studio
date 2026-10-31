"""本地 API 自检：真的起一个服务、真的合成一句、验证任务/取消/文件。

用法：python tools/api_test.py [--port 8799] [--keep]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voxcpm_studio.api import ApiServer  # noqa: E402
from voxcpm_studio.engine import TTSEngine  # noqa: E402

PORT = 8799 if "--port" not in sys.argv else int(sys.argv[sys.argv.index("--port") + 1])
BASE = f"http://127.0.0.1:{PORT}"


def call(path: str, payload: dict | None = None, method: str = "GET", raw: bool = False):
    url = BASE + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            if raw:
                return resp.status, body, dict(resp.headers)
            return resp.status, json.loads(body.decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body.decode("utf-8")), dict(exc.headers)
        except Exception:  # noqa: BLE001
            return exc.code, {"raw": body[:200]}, dict(exc.headers)


def main() -> int:
    engine = TTSEngine()
    print("[1] 未加载模型时 /health 应为 503 …", flush=True)
    gui_busy = {"on": False}
    server = ApiServer(
        engine,
        port=PORT,
        output_dir=ROOT / "outputs",
        log=lambda m: print("   ", m, flush=True),
        is_busy=lambda: gui_busy["on"],
    )
    server.start()
    ok = True
    try:
        status, body, _ = call("/health")
        print(f"    -> {status} model_loaded={body.get('model_loaded')}", flush=True)
        ok &= status == 503

        print("[2] 加载模型 …", flush=True)
        engine.load(str(ROOT / "models" / "VoxCPM2"), device="cuda", log=lambda _m: None, warmup=True)
        status, health, _ = call("/health")
        print(f"    -> {status} {health.get('device')} vram={health.get('vram')}", flush=True)
        ok &= status == 200 and health["model_loaded"]

        print("[3] /presets /params …", flush=True)
        s1, presets, _ = call("/presets")
        s2, params, _ = call("/params")
        print(f"    -> {s1}/{s2} 设计维度={len(presets['design'])} 推荐区间={params['recommended']}", flush=True)
        ok &= s1 == 200 and s2 == 200

        print("[4] 同步合成（标签组合 + json）…", flush=True)
        t0 = time.time()
        status, out, _ = call(
            "/synthesize",
            {
                "text": "你好，我是通过本地接口调用的语音合成。",
                "mode": "design",
                "tags": {"性别年龄": "年轻女性", "音色": "温柔甜美", "情绪": "轻快带笑"},
                "params": {"cfg_value": 2.0, "inference_timesteps": 8},
                "response_format": "json",
            },
            "POST",
        )
        job = out.get("job", {})
        print(f"    -> {status} {job.get('status')} 音频={job.get('audio_seconds')}s "
              f"耗时={time.time()-t0:.1f}s 文件={job.get('path')}", flush=True)
        ok &= status == 200 and job.get("status") == "done" and Path(job["path"]).exists()

        print("[5] 下载 wav …", flush=True)
        status, data, headers = call(f"/audio/{job['id']}", raw=True)
        print(f"    -> {status} {len(data)} bytes {headers.get('Content-Type')}", flush=True)
        ok &= status == 200 and data[:4] == b"RIFF"

        print("[6] 参数越界应 400 …", flush=True)
        status, out, _ = call(
            "/synthesize",
            {"text": "测试", "params": {"cfg_value": 9.0}},
            "POST",
        )
        print(f"    -> {status} {out.get('error')} {out.get('message')}", flush=True)
        ok &= status == 400

        print("[7] 异步任务 + 取消 …", flush=True)
        long_text = "这是异步取消测试。" * 40
        status, out, _ = call(
            "/synthesize",
            {"text": long_text, "mode": "design", "async": True, "split_chars": 60},
            "POST",
        )
        jid = out["job"]["id"]
        print(f"    -> 202 job={jid}", flush=True)
        time.sleep(2.0)
        status, out, _ = call(f"/jobs/{jid}", method="DELETE")
        print(f"    -> 取消 {status} {out.get('job', {}).get('status')}", flush=True)
        # 非流式：取消要等当前分段跑完，这里最多等 20s
        for _ in range(40):
            time.sleep(0.5)
            status, out, _ = call(f"/jobs/{jid}")
            if out["job"]["status"] in ("canceled", "done", "error"):
                break
        print(f"    -> 最终状态 {out['job']['status']} 已生成 {out['job']['audio_seconds']}s", flush=True)
        ok &= out["job"]["status"] == "canceled"

        print("[7b] 流式任务取消（应 3 秒内停）…", flush=True)
        status, out, _ = call(
            "/synthesize",
            {"text": long_text, "mode": "design", "async": True, "stream": True},
            "POST",
        )
        jid2 = out["job"]["id"]
        time.sleep(2.0)
        t1 = time.time()
        call(f"/jobs/{jid2}", method="DELETE")
        for _ in range(20):
            time.sleep(0.25)
            status, out = call(f"/jobs/{jid2}")[:2]
            if out["job"]["status"] in ("canceled", "done", "error"):
                break
        print(f"    -> {out['job']['status']} 停用 {time.time()-t1:.1f}s 已生成 {out['job']['audio_seconds']}s",
              flush=True)
        ok &= out["job"]["status"] == "canceled"

        print("[7c] 界面占用时应 409 …", flush=True)
        gui_busy["on"] = True
        status, out, _ = call("/synthesize", {"text": "测试"}, "POST")
        gui_busy["on"] = False
        print(f"    -> {status} {out.get('error')} {out.get('message')}", flush=True)
        ok &= status == 409

        print("[8] 未知接口 404 …", flush=True)
        status, out, _ = call("/nope")
        ok &= status == 404
        print(f"    -> {status} {out.get('error')}", flush=True)

    finally:
        server.stop()

    print("\n结果:", "全部通过" if ok else "有失败项", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
