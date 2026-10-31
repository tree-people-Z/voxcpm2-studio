"""下载 VoxCPM2 权重到 models/VoxCPM2（走 ModelScope，国内网络友好）。"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_ID = "OpenBMB/VoxCPM2"
TARGET = Path(__file__).resolve().parent / "models" / "VoxCPM2"


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    print(f"下载 {MODEL_ID} -> {TARGET}（约 5GB，支持断点续传）")
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("请先安装 modelscope：pip install modelscope")
        return 1
    path = snapshot_download(MODEL_ID, local_dir=str(TARGET))
    print(f"完成：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
