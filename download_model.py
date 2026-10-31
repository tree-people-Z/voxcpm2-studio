"""命令行下载 VoxCPM2 权重到 models/VoxCPM2（支持断点续传，可 Ctrl+C 中断）。

和软件首次启动时的下载走同一套实现（`voxcpm_studio/downloader.py`），
只依赖标准库，不需要额外装 modelscope。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from voxcpm_studio import downloader  # noqa: E402

TARGET = ROOT / "models" / "VoxCPM2"


def _fmt(n: float) -> str:
    return f"{n / 1024**3:.2f} GB"


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    print(f"下载 {downloader.MODEL_ID} → {TARGET}（约 4.6 GB，支持断点续传）")
    last = [0.0]

    def on_progress(done: int, total: int, speed: float) -> None:
        now = time.time()
        if now - last[0] < 0.5 and done < total:
            return
        last[0] = now
        pct = done / total * 100 if total else 0
        eta = ""
        if speed > 1024:
            left = (total - done) / speed
            eta = f" 剩余 {int(left // 60):02d}:{int(left % 60):02d}"
        sys.stdout.write(f"\r{pct:5.1f}%  {_fmt(done)} / {_fmt(total)}  "
                         f"{speed / 1024**2:5.1f} MB/s{eta}   ")
        sys.stdout.flush()

    try:
        downloader.download_model(TARGET, on_progress=on_progress, log=lambda m: None)
    except KeyboardInterrupt:
        print("\n已中断；再次运行会从已下载的位置继续。")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\n下载失败：{exc}")
        return 1
    print(f"\n完成：{TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
