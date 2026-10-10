"""
Local mirror of a udata portal's catalog export.

udata portals can publish their own catalog as a dataset of CSV files (one per
model: dataset, resource, organization...), regenerated on a schedule. This
module keeps a local copy of those files: one catalog request tells which
exports changed, and only those are downloaded again.

Run it with `python -m helpers.catalog_mirror` (e.g. from a daily timer).
"""

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from helpers import datagouv_api_client, env_config
from helpers.local_tabular_client import format_size, iter_chunks
from helpers.logging import MAIN_LOGGER_NAME
from helpers.ssrf import ssrf_async_session

logger = logging.getLogger(MAIN_LOGGER_NAME)

STATE_FILE = "state.json"
# Safety net against a runaway download; the largest export on dados.gov.pt is ~40 MB
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024

_EXPORT_TITLE = re.compile(r"^export-([a-z_]+)-\d{8}-\d{6}\.csv$")
_MODEL_NAME = re.compile(r"^[a-z_]+$")


class CatalogMirrorError(Exception):
    """Raised when the catalog export cannot be mirrored."""


@dataclass
class SyncReport:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed


def dataset_id() -> str | None:
    """ID or slug of the portal's catalog export dataset, from UDATA_CATALOG_DATASET."""
    return os.getenv("UDATA_CATALOG_DATASET", "").strip() or None


def catalog_dir() -> Path:
    """
    Directory of the mirror, from UDATA_CATALOG_DIR.

    Defaults to $XDG_DATA_HOME/datagouv-mcp/catalog/<portal host>
    (~/.local/share/... if unset).
    """
    raw = os.getenv("UDATA_CATALOG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    base = os.getenv("XDG_DATA_HOME", "").strip() or "~/.local/share"
    host = urlsplit(env_config.get_base_url("site")).hostname or "portal"
    return Path(base).expanduser() / "datagouv-mcp" / "catalog" / host


def export_model(resource: dict[str, Any]) -> str | None:
    """Which model a resource of the catalog dataset exports (dataset, resource...)."""
    model = str((resource.get("extras") or {}).get("csv-export:model") or "")
    if _MODEL_NAME.match(model):
        return model
    match = _EXPORT_TITLE.match(str(resource.get("title") or ""))
    return match.group(1) if match else None


def iter_dataset_rows(path: Path) -> Iterator[dict[str, str]]:
    """Rows of a mirrored dataset export, without the archived datasets."""
    csv.field_size_limit(1 << 30)
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source, delimiter=";"):
            if row.get("id") and (row.get("archived") or "False") == "False":
                yield row


def read_state(directory: Path) -> dict[str, Any]:
    try:
        state = json.loads((directory / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(directory: Path, state: dict[str, Any]) -> None:
    temporary = directory / f"{STATE_FILE}.tmp"
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, directory / STATE_FILE)


def _fingerprint(resource: dict[str, Any]) -> dict[str, Any]:
    checksum = resource.get("checksum")
    return {
        "url": resource.get("url"),
        "last_modified": resource.get("last_modified"),
        "filesize": resource.get("filesize"),
        "checksum": checksum if isinstance(checksum, dict) else None,
    }


def _hasher(checksum: dict[str, Any] | None) -> Any | None:
    if not checksum or not checksum.get("value"):
        return None
    try:
        return hashlib.new(str(checksum.get("type") or ""))
    except (ValueError, TypeError):
        return None


async def _download(url: str, target: Path, expected: dict[str, Any]) -> int:
    """Stream a file to `target`, replacing it only once fully received and verified."""
    part = target.with_name(f"{target.name}.part")
    digest = _hasher(expected["checksum"])
    received = 0
    session = ssrf_async_session()
    try:
        resp = await session.get(url, timeout=120.0, allow_redirects=True, stream=True)
        resp.raise_for_status()
        with part.open("wb") as output:
            async for chunk in iter_chunks(resp):
                received += len(chunk)
                if received > MAX_FILE_BYTES:
                    raise CatalogMirrorError(
                        f"larger than {format_size(MAX_FILE_BYTES)}"
                    )
                if digest is not None:
                    digest.update(chunk)
                output.write(chunk)
        filesize = expected["filesize"]
        if isinstance(filesize, int) and filesize != received:
            raise CatalogMirrorError(
                f"received {received} bytes, the catalog says {filesize}"
            )
        if digest is not None and digest.hexdigest() != expected["checksum"]["value"]:
            raise CatalogMirrorError("checksum does not match the catalog")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
        await session.close()
    return received


async def sync(force: bool = False) -> SyncReport:
    """
    Bring the local mirror up to date: one catalog request, then one download
    per export that changed since the last run (all of them with `force`).

    Raises:
        CatalogMirrorError: not configured, or the dataset holds no export.
    """
    dataset = dataset_id()
    if not dataset:
        raise CatalogMirrorError(
            "UDATA_CATALOG_DATASET is not set (ID or slug of the portal's catalog "
            "export dataset)."
        )
    directory = catalog_dir()
    directory.mkdir(parents=True, exist_ok=True)

    details = await datagouv_api_client.get_dataset_details(dataset)
    exports: dict[str, dict[str, Any]] = {}
    for resource in details.get("resources") or []:
        model = export_model(resource)
        if model and resource.get("url"):
            exports[model] = resource
    if not exports:
        raise CatalogMirrorError(
            f"Dataset '{dataset}' on {env_config.get_portal_name()} has no catalog "
            "export files."
        )

    state = read_state(directory)
    files: dict[str, Any] = state.get("files") or {}
    report = SyncReport()
    for model, resource in sorted(exports.items()):
        target = directory / f"{model}.csv"
        fingerprint = _fingerprint(resource)
        known = files.get(model) or {}
        current = {key: known.get(key) for key in fingerprint}
        if not force and target.is_file() and current == fingerprint:
            report.unchanged.append(model)
            continue
        try:
            size = await _download(str(resource["url"]), target, fingerprint)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Catalog mirror: {model} failed: {e}")
            report.failed[model] = str(e)
            continue
        logger.info(f"Catalog mirror: {model} updated ({format_size(size)})")
        files[model] = {**fingerprint, "size": size, "fetched_at": time.time()}
        report.updated.append(model)

    _write_state(
        directory,
        {
            "portal": env_config.get_portal_name(),
            "dataset": dataset,
            "checked_at": time.time(),
            "files": files,
        },
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mirror a udata portal's catalog export (CSV files) locally."
    )
    parser.add_argument(
        "--force", action="store_true", help="download every file again"
    )
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(sync(force=args.force))
    except Exception as e:  # noqa: BLE001
        print(f"Catalog mirror failed: {e}", file=sys.stderr)
        return 1
    print(f"Catalog mirror: {catalog_dir()}")
    print(f"  updated: {', '.join(report.updated) or 'none'}")
    print(f"  unchanged: {', '.join(report.unchanged) or 'none'}")
    for model, reason in report.failed.items():
        print(f"  failed: {model} ({reason})", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
