"""从 ModelScope 下载 VoxCPM2 权重（断点续传 / 可取消 / 真实进度）。

为什么不用 ``modelscope.snapshot_download``：
- 它是黑盒，没有取消入口，进度只能看它自己往 stderr 刷的 tqdm；
- 我们要在首次启动的对话框里显示「已下载 1.2 / 5.0 GB · 12 MB/s · 剩余 4 分钟」，
  还要能随时停下，所以直接用 ModelScope 的仓库文件 API + Range 请求自己拉。
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MODEL_ID = "OpenBMB/VoxCPM2"
FILES_API = "https://www.modelscope.cn/api/v1/models/{model_id}/repo/files?Revision={rev}"
FILE_API = "https://www.modelscope.cn/api/v1/models/{model_id}/repo?Revision={rev}&FilePath={path}"
REVISION = "master"
CHUNK = 1 << 20          # 1 MiB
UA = "VoxCPM2Studio/1.0 (+https://github.com/)"

ProgressFn = Callable[[int, int, float], None]   # (已下载字节, 总字节, 速度 B/s)
LogFn = Callable[[str], None]


@dataclass
class RemoteFile:
    path: str
    size: int


class DownloadCancelled(RuntimeError):
    pass


def list_remote_files(model_id: str = MODEL_ID, revision: str = REVISION) -> list[RemoteFile]:
    """列出仓库里需要下载的文件（跳过 .gitattributes 之类的仓库元数据）。"""
    url = FILES_API.format(model_id=model_id, rev=revision)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("Code") != 200:
        raise RuntimeError(f"ModelScope 返回异常：{data.get('Code')} {data.get('Message')}")
    files = []
    for item in data["Data"]["Files"]:
        name = item.get("Path") or item.get("Name") or ""
        if not name or name.startswith(".") or item.get("Type") != "blob":
            continue
        files.append(RemoteFile(path=name, size=int(item.get("Size") or 0)))
    if not files:
        raise RuntimeError("远端文件列表为空")
    return files


def total_size(files: list[RemoteFile]) -> int:
    return sum(f.size for f in files)


def local_size(dest: Path, files: list[RemoteFile]) -> int:
    got = 0
    for f in files:
        p = dest / f.path
        if p.exists():
            got += min(p.stat().st_size, f.size)
    return got


def _download_one(
    dest: Path,
    remote: RemoteFile,
    model_id: str,
    should_stop: Callable[[], bool],
    on_bytes: Callable[[int], None],
) -> None:
    target = dest / remote.path
    target.parent.mkdir(parents=True, exist_ok=True)
    have = target.stat().st_size if target.exists() else 0
    if have >= remote.size:
        return
    url = FILE_API.format(model_id=model_id, rev=REVISION, path=urllib.parse.quote(remote.path))
    headers = {"User-Agent": UA}
    mode = "wb"
    if have:
        headers["Range"] = f"bytes={have}-"
        mode = "ab"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp, open(target, mode) as fh:
        while True:
            if should_stop():
                raise DownloadCancelled()
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            fh.write(chunk)
            on_bytes(len(chunk))


def download_model(
    dest: str | Path,
    model_id: str = MODEL_ID,
    on_progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    log: LogFn | None = None,
) -> Path:
    """把权重下载到 dest，返回 dest。已存在的文件按大小跳过（可断点续传）。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stop = should_stop or (lambda: False)
    say = log or (lambda _m: None)

    files = list_remote_files(model_id)
    total = total_size(files)
    done = local_size(dest, files)
    say(f"共 {len(files)} 个文件，{total / 1024**3:.2f} GB，已存在 {done / 1024**3:.2f} GB")

    last = [time.time(), done]
    speed = [0.0]
    done_nonlocal = [done]

    def bump(n: int) -> None:
        done_nonlocal[0] += n
        now = time.time()
        if now - last[0] >= 0.25:
            speed[0] = (done_nonlocal[0] - last[1]) / (now - last[0])
            last[0], last[1] = now, done_nonlocal[0]
            if on_progress:
                on_progress(done_nonlocal[0], total, speed[0])

    if on_progress:
        on_progress(done, total, 0.0)

    for remote in files:
        if stop():
            raise DownloadCancelled()
        before = done_nonlocal[0]
        _download_one(dest, remote, model_id, stop, bump)
        got = done_nonlocal[0] - before
        if got:
            say(f"{remote.path} 完成（{got / 1024**2:.0f} MB）")

    if on_progress:
        on_progress(done_nonlocal[0], total, speed[0])
    say("下载完成")
    return dest


def ensure_space(dest: Path, need_bytes: int) -> bool:
    """粗略检查磁盘剩余空间（留 1GB 余量）。"""
    try:
        free = shutil.disk_usage(str(dest if dest.exists() else dest.parent)).free
    except Exception:  # noqa: BLE001
        return True
    return free > need_bytes + (1 << 30)
