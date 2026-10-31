"""OpenDesign MCP 桥接器：自动发现本地 daemon 端口后启动 od mcp（stdio 透传）。

用途：把 OpenDesign 的 MCP 服务器接进任意 MCP 客户端，而不用写死端口。
daemon 端口是每次启动随机分配的，本脚本从 daemon-health.json 拿到 pid，
再从 netstat 反查监听端口，最后用 /api/health 验证。

用法（通常由 MCP 客户端配置调用）：
    python tools/od_mcp_bridge.py
环境变量：
    OD_DAEMON_URL   指定后跳过自动发现
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

APP_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Open Design"
CLI = APP_ROOT / "resources" / "app" / "prebundled" / "daemon" / "daemon-cli.mjs"
HEALTH_GLOB = "Open Design/namespaces/*/data/diagnostics/daemon-health.json"


def _health_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def _daemon_pids() -> list[int]:
    appdata = Path(os.environ.get("APPDATA", ""))
    pids: list[int] = []
    for path in appdata.glob(HEALTH_GLOB):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("state") == "running" and isinstance(data.get("pid"), int):
            pids.append(int(data["pid"]))
    return pids


def _ports_for_pids(pids: list[int]) -> list[int]:
    if not pids:
        return []
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        return []
    ports: list[int] = []
    for line in out.splitlines():
        m = re.match(r"\s*TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
        if not m:
            continue
        port, pid = int(m.group(1)), int(m.group(2))
        if pid in pids:
            ports.append(port)
    return ports


def resolve_daemon_url() -> str | None:
    explicit = os.environ.get("OD_DAEMON_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    candidates = [f"http://127.0.0.1:{p}" for p in _ports_for_pids(_daemon_pids())]
    candidates += ["http://127.0.0.1:7456"]
    for url in candidates:
        if _health_ok(url):
            return url
    return None


def main() -> int:
    if not CLI.exists():
        print(f"找不到 OpenDesign CLI：{CLI}", file=sys.stderr)
        return 1
    url = resolve_daemon_url()
    if url is None:
        print("未发现正在运行的 OpenDesign daemon（请先打开 Open Design 桌面端）", file=sys.stderr)
        return 1
    print(f"[od-bridge] daemon = {url}", file=sys.stderr)
    # 不能用 execvp：Windows 上带空格的路径不会被正确加引号
    cmd = ["node", str(CLI), "mcp", "--daemon-url", url]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
