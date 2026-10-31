"""TTS MCP 服务器（stdio）：把 VoxCPM2 Studio 的本地 API 包装成 MCP 工具。

给 Claude Code / Codex / pi 这类 agent 直接调用：
    python tools/tts_mcp_server.py                # 默认连 http://127.0.0.1:8765
    TTS_API_URL=http://127.0.0.1:8800 python tools/tts_mcp_server.py
    TTS_API_KEY=xxx python tools/tts_mcp_server.py

只依赖标准库：自己实现最小 JSON-RPC 2.0 over stdio（MCP 的 stdio 传输就是
一行一个 JSON，不需要 Content-Length 头）。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_URL = os.environ.get("TTS_API_URL", "http://127.0.0.1:8765").rstrip("/")
API_KEY = os.environ.get("TTS_API_KEY", "")
PROTOCOL = "2024-11-05"
SERVER_INFO = {"name": "voxcpm2-studio", "version": "1.0.0"}


# ------------------------------------------------------------------ #
# HTTP
# ------------------------------------------------------------------ #
def _request(path: str, payload: dict | None = None, method: str = "GET", timeout: int = 600):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if API_KEY:
        req.add_header("X-API-Key", API_KEY)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8"))
        return f"{body.get('error')}: {body.get('message')}"
    except Exception:  # noqa: BLE001
        return f"HTTP {exc.code}"


# ------------------------------------------------------------------ #
# 工具实现
# ------------------------------------------------------------------ #
def tool_health(_args: dict) -> dict:
    try:
        return _request("/health")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "detail": _http_error_message(exc)}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "detail": f"连不上 {API_URL}：{exc}",
            "hint": "先在 VoxCPM2 Studio 里「设置 → 本地 API」打开开关",
        }


def tool_presets(_args: dict) -> dict:
    return _request("/presets")


def tool_params(_args: dict) -> dict:
    return _request("/params")


def tool_synthesize(args: dict) -> dict:
    text = (args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text 不能为空"}
    payload: dict[str, Any] = {
        "text": text,
        "mode": args.get("mode") or "design",
        "response_format": "json",
    }
    if args.get("control"):
        payload["control"] = args["control"]
    if args.get("tags"):
        payload["tags"] = args["tags"]
    if args.get("reference_audio"):
        payload["reference_audio"] = args["reference_audio"]
    if args.get("prompt_text"):
        payload["prompt_text"] = args["prompt_text"]
    if args.get("params"):
        payload["params"] = args["params"]
    if args.get("split_chars"):
        payload["split_chars"] = int(args["split_chars"])
    if args.get("stream"):
        payload["stream"] = True
    try:
        return _request("/synthesize", payload, "POST", timeout=1800)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "detail": _http_error_message(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"调用失败：{exc}"}


def tool_cancel(args: dict) -> dict:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return {"ok": False, "error": "job_id 不能为空"}
    return _request(f"/jobs/{job_id}", method="DELETE")


# ------------------------------------------------------------------ #
# MCP 描述
# ------------------------------------------------------------------ #
TOOLS = [
    {
        "name": "tts_health",
        "description": "检查本机 VoxCPM2 Studio 是否在运行、模型是否已加载、显存占用与忙闲状态。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "tts_presets",
        "description": (
            "列出可用的声音描述标签：design（性别年龄/音色/情绪/语速/场景/口音）与 "
            "clone（情绪/语速/语气）。用 tts_synthesize 时把这些标签放进 tags 参数，"
            "服务端会按维度顺序拼成一句描述。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "tts_params",
        "description": "返回生成参数的默认值与官方推荐区间（CFG 1.0–3.0、扩散步数 4–30）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "tts_synthesize",
        "description": (
            "把文本合成为语音（本机 GPU，48kHz）。三种模式：design 用文字描述凭空造音色；"
            "clone 用 5–30 秒参考音频克隆；ultimate 需要参考音频 + 其逐字文本，相似度最高。"
            "返回保存好的 wav 路径、时长、耗时与 RTF。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要合成的文本（必填）"},
                "mode": {
                    "type": "string",
                    "enum": ["design", "clone", "ultimate"],
                    "description": "默认 design",
                },
                "control": {
                    "type": "string",
                    "description": "声音描述，会以「(描述)文本」前置；例如：年轻女性，温柔甜美",
                },
                "tags": {
                    "type": "object",
                    "description": "按维度给标签，例如 {\"情绪\":\"轻快带笑\",\"语速\":\"语速偏快\"}",
                    "additionalProperties": {"type": "string"},
                },
                "reference_audio": {
                    "type": "string",
                    "description": "参考音频：本地绝对路径，或 data:audio/wav;base64,...",
                },
                "prompt_text": {
                    "type": "string",
                    "description": "参考音频的逐字转录（ultimate 模式必填）",
                },
                "params": {
                    "type": "object",
                    "description": "cfg_value / inference_timesteps / max_len / normalize / denoise / retry_badcase",
                },
                "split_chars": {
                    "type": "integer",
                    "description": "长文本按句切分的每段字数（0=不分段），建议 80–150",
                },
                "stream": {
                    "type": "boolean",
                    "description": "true 时边生成边返回（取消更快），但官方会关闭异常重试",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "tts_cancel",
        "description": "取消一个正在进行的合成任务（需要 job_id）。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]

HANDLERS = {
    "tts_health": tool_health,
    "tts_presets": tool_presets,
    "tts_params": tool_params,
    "tts_synthesize": tool_synthesize,
    "tts_cancel": tool_cancel,
}


# ------------------------------------------------------------------ #
# JSON-RPC
# ------------------------------------------------------------------ #
def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _err(msg_id, -32602, f"未知工具：{name}")
        try:
            result = handler(args)
        except Exception as exc:  # noqa: BLE001
            return _ok(msg_id, {
                "content": [{"type": "text", "text": f"调用失败：{exc}"}],
                "isError": True,
            })
        return _ok(msg_id, {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "isError": bool(isinstance(result, dict) and result.get("ok") is False),
        })
    if method in ("resources/list", "prompts/list"):
        return _ok(msg_id, {"resources": []} if method == "resources/list" else {"prompts": []})
    if msg_id is not None:
        return _err(msg_id, -32601, f"未实现的方法：{method}")
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
