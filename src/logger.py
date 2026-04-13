"""Structured file logging with stdout mirroring."""

import sys
from datetime import datetime
from pathlib import Path


class TeeWriter:
    """Writes to both the original stdout and a log file."""

    def __init__(self, original_stdout, log_file_handle):
        self.original = original_stdout
        self.log_file = log_file_handle

    def write(self, text):
        self.original.write(text)
        self.log_file.write(text)
        self.log_file.flush()

    def flush(self):
        self.original.flush()
        self.log_file.flush()

    @property
    def encoding(self):
        return self.original.encoding

    def fileno(self):
        return self.original.fileno()

    def isatty(self):
        return self.original.isatty()


def setup_logging(log_dir: Path) -> Path:
    """Set up logging to both stdout and a timestamped log file.

    Replaces sys.stdout with a TeeWriter that mirrors all print() output
    to a log file under ``log_dir``.

    Returns the path to the created log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"run_{timestamp}.log"
    log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115

    sys.stdout = TeeWriter(sys.stdout, log_handle)
    sys.stderr = TeeWriter(sys.stderr, log_handle)

    return log_path
