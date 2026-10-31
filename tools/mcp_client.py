"""极简 MCP stdio 客户端：本会话尚未热加载 MCP 工具时，用它直接调 OpenDesign 工具。

用法:
  python tools/mcp_client.py tools
  python tools/mcp_client.py call <tool> '<json-args>'
  python tools/mcp_client.py seq '<tool>' '<json>' '<tool>' '<json>' ...
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

CLI = (
    Path(os.environ["LOCALAPPDATA"])
    / "Programs"
    / "Open Design"
    / "resources"
    / "app"
    / "prebundled"
    / "daemon"
    / "daemon-cli.mjs"
)
DAEMON_URL = os.environ.get("OD_DAEMON_URL", "")  # 留空则让 od mcp 自动发现运行时


class McpSession:
    def __init__(self) -> None:
        cmd = ["node", str(CLI), "mcp"]
        if DAEMON_URL:
            cmd += ["--daemon-url", DAEMON_URL]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._q: queue.Queue[dict] = queue.Queue()
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pi-manual", "version": "1.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # ---------------------------------------------------------- #
    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                pass

    def _drain_err(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            sys.stderr.write("[od] " + line)

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 180.0) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            msg = self._q.get(timeout=timeout)
            if msg.get("id") == rid:
                return msg

    def tools(self) -> list[dict]:
        return self.request("tools/list").get("result", {}).get("tools", [])

    def call(self, name: str, args: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": args})

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass


def _text_of(resp: dict) -> str:
    res = resp.get("result", resp)
    if "content" in res:
        parts = []
        for c in res["content"]:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(res, ensure_ascii=False, indent=2)


def main(argv: list[str]) -> int:
    sess = McpSession()
    try:
        if len(argv) >= 2 and argv[1] == "resources":
            for r in sess.request("resources/list").get("result", {}).get("resources", []):
                print(f"- {r.get('uri')}  {r.get('name','')}  {(r.get('description') or '')[:60]}")
            return 0
        if len(argv) >= 2 and argv[1] == "read":
            print(_text_of(sess.request("resources/read", {"uri": argv[2]})))
            return 0
        if len(argv) >= 2 and argv[1] == "tools":
            for t in sess.tools():
                print(f"- {t['name']}: {(t.get('description') or '').strip().splitlines()[0][:110]}")
            return 0
        if len(argv) >= 3 and argv[1] == "call":
            args = json.loads(argv[3]) if len(argv) > 3 else {}
            print(_text_of(sess.call(argv[2], args)))
            return 0
        if len(argv) >= 2 and argv[1] == "seq":
            pairs = argv[2:]
            for i in range(0, len(pairs), 2):
                name = pairs[i]
                args = json.loads(pairs[i + 1]) if i + 1 < len(pairs) else {}
                print(f"\n===== {name} =====")
                print(_text_of(sess.call(name, args)))
            return 0
        print(__doc__)
        return 2
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
