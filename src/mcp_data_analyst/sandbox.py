"""Filesystem sandbox: what may be read from the data dir and written to the output dir."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from .errors import SecurityError

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".csv", ".tsv", ".parquet", ".xlsx", ".sqlite", ".sqlite3", ".db"}
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def is_within(path: Path, root: Path) -> bool:
    """True if ``path`` (after resolving symlinks and ``..``) lies inside ``root``."""
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def iter_data_files(data_dir: Path, max_size_bytes: int) -> Iterator[Path]:
    """Yield supported files under ``data_dir`` that are safe to register.

    Files are skipped (with a warning) when they resolve outside the sandbox, e.g. a
    symlink pointing to ``/etc``, or when they exceed the configured size limit.
    """
    if not data_dir.is_dir():
        raise SecurityError(f"Data directory does not exist or is not a directory: {data_dir}")
    root = data_dir.resolve()
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES or not path.is_file():
            continue
        if not is_within(path, root):
            log.warning("skipping file that resolves outside the data dir", extra={"path": str(path)})
            continue
        size = path.stat().st_size
        if size > max_size_bytes:
            log.warning("skipping file over size limit", extra={"path": str(path), "bytes": size})
            continue
        yield path


def safe_output_path(output_dir: Path, filename: str, allowed_suffixes: set[str]) -> Path:
    """Return ``output_dir/filename`` after strictly validating ``filename``.

    Only a bare file name is accepted: no directories, no ``..``, no absolute paths,
    and the suffix must be one of ``allowed_suffixes``.
    """
    if not _SAFE_FILENAME.fullmatch(filename) or ".." in filename:
        raise SecurityError(
            f"Invalid file name {filename!r}: use letters, digits, '_', '-' and '.' only, without directories."
        )
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        raise SecurityError(
            f"File extension {suffix or '(none)'!r} not allowed; expected one of {sorted(allowed_suffixes)}."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filename
    if not is_within(target, output_dir):
        raise SecurityError(f"Refusing to write outside the output directory: {filename!r}")
    if target.is_symlink():
        raise SecurityError(f"Refusing to overwrite a symlink: {filename!r}")
    return target
