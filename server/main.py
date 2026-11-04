"""VoxCPM2 独立服务入口：不启动 PyQt，只跑本地 HTTP API（无界面）。

双击 start-api.bat 就是它。被 Tree 聚合时也是同一个进程，只是端口由 Tree 分配。
入口都不是给人看的：浏览器打开只能拿到 JSON（列表在 GET /help）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))

from voxcpm_studio.api import ApiServer
from voxcpm_studio.config import AppConfig
from voxcpm_studio.engine import TTSEngine

MANIFEST = json.loads((PKG / "manifest.json").read_text(encoding="utf-8")) \
    if (PKG / "manifest.json").exists() else {}
OUTPUT_DIR = PKG / "outputs"


IDLE_UNLOAD_SEC = int(os.environ.get("VOXCPM_IDLE_UNLOAD_SEC") or 300)


def idle_unload_loop(engine, server) -> None:
    """空闲超过阈值就卸载模型，把显存让给别的程序。

    不依赖 Tree —— 单独用 start-api.bat 跑也会生效。
    """
    if IDLE_UNLOAD_SEC <= 0:
        return
    interval = min(60, max(10, IDLE_UNLOAD_SEC // 5))
    while True:
        time.sleep(interval)
        if not engine.is_loaded or server.busy:
            continue
        last = 0.0
        for job in list(server._jobs.values()):
            last = max(last, job.ended or job.started or job.created)
        if not last or time.time() - last < IDLE_UNLOAD_SEC:
            continue
        try:
            engine.unload()
            print(f"[model] 空闲 {IDLE_UNLOAD_SEC}s，已卸载模型", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[model] 卸载失败：{exc}", flush=True)


def pick_port(cli_port: int | None) -> int:
    if cli_port:
        return cli_port
    if os.environ.get("TREE_PORT"):
        return int(os.environ["TREE_PORT"])
    return int(MANIFEST.get("port") or 7810)


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        if probe.connect_ex((host, port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    parser = argparse.ArgumentParser(prog="voxcpm2-server")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    port = pick_port(args.port)
    url = f"http://{args.host}:{port}/"
    if not port_free(port, args.host):
        print(f"VoxCPM2 已经在运行：{url}（端口 {port} 被占用）")
        return 0

    cfg = AppConfig.load()
    engine = TTSEngine()
    server = ApiServer(engine, host=args.host, port=port,
                       token=getattr(cfg, "api_token", "") or "",
                       output_dir=OUTPUT_DIR,
                       log=lambda m: print(f"[api] {m}", flush=True))

    _load_lock = threading.Lock()

    def ensure_loaded() -> None:
        """第一次提交任务时自动加载模型，用户不用先去点「加载模型」。"""
        with _load_lock:
            _load_once()


    def _load_once() -> None:
        if engine.is_loaded:
            return
        model_dir = Path(str(getattr(cfg, "model_dir", "")) or (PKG / "models" / "VoxCPM2"))
        if not model_dir.exists():
            raise RuntimeError(
                f"模型目录不存在：{model_dir}\n"
                f"  先跑 download_model.bat，或在界面里下载模型后再用 API。"
            )
        print(f"[model] 加载 {model_dir} …", flush=True)
        engine.load(str(model_dir), device=getattr(cfg, "device", "auto"),
                    use_denoiser=bool(getattr(cfg, "use_denoiser", False)),
                    optimize=bool(getattr(cfg, "optimize", False)),
                    log=lambda m: print(f"[model] {m}", flush=True))
        print("[model] 就绪", flush=True)

    server.ensure_loaded = ensure_loaded
    server.start()
    threading.Thread(target=idle_unload_loop, args=(engine, server),
                     name="idle-unload", daemon=True).start()

    print(f"VoxCPM2 {MANIFEST.get('version', '')}  →  {url}")
    print(f"  产物：{OUTPUT_DIR}")
    print(f"  接口清单：{url}help   状态：{url}api/health")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
