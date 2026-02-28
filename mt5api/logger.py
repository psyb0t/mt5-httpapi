import logging
import os
import time

from mt5api.config import FULL_LOG, IDENTITY, LOG_DIR

_FMT = f"[%(asctime)s] [api:{IDENTITY}] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _LockedFileHandler(logging.FileHandler):
    """FileHandler that uses mkdir/rmdir as a cross-process mutex."""

    def __init__(self, filename, **kwargs):
        self._lock_dir = filename + ".lock"
        super().__init__(filename, **kwargs)

    def _acquire(self):
        for _ in range(200):
            try:
                os.mkdir(self._lock_dir)
                return
            except OSError:
                time.sleep(0.01)

    def _release(self):
        try:
            os.rmdir(self._lock_dir)
        except OSError:
            pass

    def emit(self, record):
        try:
            msg = self.format(record) + self.terminator
            self._acquire()
            try:
                self.stream.write(msg)
                self.stream.flush()
            finally:
                self._release()
        except Exception:
            self.handleError(record)


log = logging.getLogger("mt5api")
log.setLevel(logging.INFO)

_formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

# stdout — captured by start.bat into per-API log files
_stdout = logging.StreamHandler()
_stdout.setFormatter(_formatter)
log.addHandler(_stdout)

# full.log — shared across all APIs and bat scripts, mkdir-locked per write
os.makedirs(LOG_DIR, exist_ok=True)
_file = _LockedFileHandler(FULL_LOG, encoding="utf-8")
_file.setFormatter(_formatter)
log.addHandler(_file)
