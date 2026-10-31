"""MCP 自检：起本地 API → 用子进程跑 MCP 服务器 → 走一遍 initialize/list/call。

用法：python tools/mcp_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voxcpm_studio.api import ApiServer  # noqa: E402
from voxcpm_studio.engine import TTSEngine  # noqa: E402

PORT = 8798


class McpClient:
    def __init__(self) -> None:
        # 必须继承完整环境：Windows 上只给 PATH 会让子进程的 Winsock 起不来
        env = dict(os.environ)
        env["TTS_API_URL"] = f"http://127.0.0.1:{PORT}"
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "tts_mcp_server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise RuntimeError(f"MCP 服务器没有响应：{err[:400]}")
        return json.loads(line)

    def call(self, name: str, args: dict) -> dict:
        resp = self.request("tools/call", {"name": name, "arguments": args})
        return json.loads(resp["result"]["content"][0]["text"])

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


def main() -> int:
    engine = TTSEngine()
    engine.load(str(ROOT / "models" / "VoxCPM2"), device="cuda", log=lambda _m: None, warmup=True)
    server = ApiServer(engine, port=PORT, output_dir=ROOT / "outputs", log=lambda m: print("   ", m))
    server.start()
    ok = True
    client = None
    try:
        client = McpClient()
        init = client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        print("[1] initialize ->", init["result"]["serverInfo"], flush=True)

        tools = client.request("tools/list")["result"]["tools"]
        print("[2] tools:", ", ".join(t["name"] for t in tools), flush=True)
        ok &= {"tts_health", "tts_synthesize", "tts_presets"} <= {t["name"] for t in tools}

        health = client.call("tts_health", {})
        print("[3] tts_health ->", json.dumps(health, ensure_ascii=False)[:300], flush=True)
        ok &= health.get("model_loaded") is True

        presets = client.call("tts_presets", {})
        print("[4] tts_presets -> 设计维度", list(presets.get("design", {})), flush=True)
        ok &= "design" in presets

        out = client.call("tts_synthesize", {
            "text": "这是通过 MCP 工具调用合成的语音。",
            "mode": "design",
            "tags": {"性别年龄": "成熟男性", "音色": "低沉磁性", "语速": "语速偏慢"},
            "params": {"inference_timesteps": 8},
        })
        job = out.get("job", {})
        print("[5] tts_synthesize ->", job.get("status"), job.get("audio_seconds"), "s",
              Path(job.get("path", "")).name, flush=True)
        ok &= job.get("status") == "done" and Path(job["path"]).exists()

        err = client.call("tts_synthesize", {"text": "x", "mode": "clone"})
        print("[6] 缺少参考音频 ->", err.get("detail", err.get("error")), flush=True)
        ok &= err.get("ok") is False

        bad = client.call("tts_synthesize", {"text": "", "mode": "design"})
        print("[7] 空文本 ->", bad.get("error"), flush=True)
        ok &= bad.get("ok") is False
    finally:
        if client is not None:
            client.close()
        server.stop()

    print("\n结果:", "全部通过" if ok else "有失败项", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
