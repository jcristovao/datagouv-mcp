"""
On-disk cache for the files downloaded by the local query fallback.

Each URL gets a data file and a JSON sidecar (where it came from, when it was
fetched and last checked). The data file's modification time records the last
access: once the cache grows past its size limit, the files that have gone
longest without being accessed are deleted first.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from helpers.logging import MAIN_LOGGER_NAME

logger = logging.getLogger(MAIN_LOGGER_NAME)

DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_REVALIDATE_SECONDS = 24 * 60 * 60

_DATA_SUFFIX = ".data"
_META_SUFFIX = ".json"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def cache_dir() -> Path:
    """
    Directory of the cache, from LOCAL_QUERY_CACHE_DIR.

    Defaults to $XDG_CACHE_HOME/datagouv-mcp/resources (~/.cache/... if unset).
    """
    raw = os.getenv("LOCAL_QUERY_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    base = os.getenv("XDG_CACHE_HOME", "").strip() or "~/.cache"
    return Path(base).expanduser() / "datagouv-mcp" / "resources"


def max_bytes() -> int:
    """
    Total size the cache may reach, from LOCAL_QUERY_CACHE_MAX_BYTES.

    Defaults to 1 GB. 0 disables the on-disk cache.
    """
    return _env_int("LOCAL_QUERY_CACHE_MAX_BYTES", DEFAULT_MAX_BYTES)


def revalidate_seconds() -> int:
    """
    How long a cached file is used before checking the source again, from
    LOCAL_QUERY_CACHE_REVALIDATE_SECONDS. Defaults to 24 hours.
    """
    return _env_int("LOCAL_QUERY_CACHE_REVALIDATE_SECONDS", DEFAULT_REVALIDATE_SECONDS)


def is_enabled() -> bool:
    return max_bytes() > 0


def _paths(url: str) -> tuple[Path, Path]:
    stem = hashlib.sha256(url.encode("utf-8")).hexdigest()
    directory = cache_dir()
    return directory / f"{stem}{_DATA_SUFFIX}", directory / f"{stem}{_META_SUFFIX}"


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def lookup(url: str) -> dict[str, Any] | None:
    """Return the sidecar metadata of a cached URL, or None if it is not cached."""
    data_path, meta_path = _paths(url)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("url") != url or not data_path.is_file():
        return None
    return meta


def touch(url: str) -> None:
    """Record an access, so the file counts as recently used."""
    try:
        os.utime(_paths(url)[0])
    except OSError:
        pass


def read(url: str) -> bytes | None:
    """Return the cached content of a URL and record the access."""
    data_path = _paths(url)[0]
    try:
        content = data_path.read_bytes()
    except OSError:
        return None
    touch(url)
    return content


def mark_checked(url: str) -> None:
    """Record that the source confirmed the cached copy is still current."""
    meta = lookup(url)
    if meta is None:
        return
    meta["checked_at"] = time.time()
    try:
        _write_atomic(_paths(url)[1], json.dumps(meta).encode("utf-8"))
    except OSError as e:
        logger.warning(f"File cache: could not update {url}: {e}")


def remove(url: str) -> None:
    for path in _paths(url):
        path.unlink(missing_ok=True)


def store(url: str, content: bytes, **meta: Any) -> None:
    """
    Save a downloaded file, then delete least recently accessed files until
    the cache fits its size limit. A failure to write is logged, not raised:
    the caller already has the content.
    """
    limit = max_bytes()
    if len(content) > limit:
        return
    data_path, meta_path = _paths(url)
    now = time.time()
    sidecar = {
        **meta,
        "url": url,
        "size": len(content),
        "fetched_at": now,
        "checked_at": now,
    }
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(data_path, content)
        _write_atomic(meta_path, json.dumps(sidecar).encode("utf-8"))
    except OSError as e:
        logger.warning(f"File cache: could not store {url}: {e}")
        return
    evict(keep=data_path)


def evict(keep: Path | None = None) -> list[Path]:
    """
    Delete the least recently accessed files until the cache fits its limit.

    Returns the deleted data files. `keep` is never deleted.
    """
    limit = max_bytes()
    entries: list[tuple[float, int, Path]] = []
    try:
        candidates = list(cache_dir().glob(f"*{_DATA_SUFFIX}"))
    except OSError:
        return []
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))

    total = sum(size for _, size, _ in entries)
    deleted: list[Path] = []
    for _, size, path in sorted(entries, key=lambda entry: entry[0]):
        if total <= limit:
            break
        if path == keep:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(_META_SUFFIX).unlink(missing_ok=True)
        total -= size
        deleted.append(path)
    if deleted:
        logger.info(f"File cache: deleted {len(deleted)} least recently used file(s)")
    return deleted
