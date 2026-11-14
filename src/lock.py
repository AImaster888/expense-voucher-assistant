"""單一執行程序互斥鎖，避免多程序同時寫入 Google Sheets 造成競態問題。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ProcessLockError(RuntimeError):
    """當已有另一個處理程序正在執行時拋出。"""


@contextmanager
def acquire_single_instance_lock(lock_path: Path) -> Iterator[None]:
    """使用原子檔案建立（O_CREAT | O_EXCL）確保同一時間只有一個 Worker 執行。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    try:
        fd = os.open(str(lock_path), flags)
    except FileExistsError as exc:
        existing_pid = ""
        try:
            existing_pid = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise ProcessLockError(
            f"已有另一個執行程序正在運行（鎖定檔：{lock_path}，資訊：{existing_pid}）。"
            "為避免 Google Sheets 寫入競態，已中止本次重複啟動。"
        ) from exc

    try:
        os.write(fd, f"pid={os.getpid()}".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
