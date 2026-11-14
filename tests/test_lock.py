"""單一執行程序鎖定測試。"""

from __future__ import annotations

from pathlib import Path
import pytest

from src.lock import ProcessLockError, acquire_single_instance_lock


def test_single_instance_lock_prevents_concurrent_runs(tmp_path: Path):
    lock_file = tmp_path / "worker.lock"
    with acquire_single_instance_lock(lock_file):
        assert lock_file.exists()
        with pytest.raises(ProcessLockError):
            with acquire_single_instance_lock(lock_file):
                pass
    assert not lock_file.exists()
