"""Atomic local artifact replacement, without shared staging file names."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".staging",
            delete=False,
        ) as handle:
            staging = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
        staging = None
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
