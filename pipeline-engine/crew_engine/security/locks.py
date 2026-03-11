import os
import time
from contextlib import contextmanager

@contextmanager
def file_lock(lock_path: str, timeout_s: int = 10, poll_s: float = 0.1):
    """
    Atomic create (O_EXCL) ile basit dosya kilidi.
    Fail-closed: timeout olursa TimeoutError.
    """
    start = time.time()
    fd = None

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            if time.time() - start > timeout_s:
                raise TimeoutError(f"Lock timeout: {lock_path}")
            time.sleep(poll_s)

    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
            os.unlink(lock_path)
        except FileNotFoundError:
            pass
