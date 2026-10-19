"""Verify local GScloud ASTER GDEM ZIPs without touching browser or service state.

Run with the project Python. Optional imagecodecs is loaded only from the
sibling _terrain_audit_deps directory; no runtime environment changes occur.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import zipfile
import zlib

APP_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = Path(__file__).resolve().parent / "_terrain_audit_deps"
if LOCAL_DEPS.is_dir():
    sys.path.insert(0, str(LOCAL_DEPS))

import numpy as np
import tifffile

TILE_RE = re.compile(r"^ASTGTMV003_([NS])(\d{2})([EW])(\d{3})\.zip$", re.I)
PUBLIC_SOURCE = "https://www.gscloud.cn/sources/index?pid=302"
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: object) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def checked_members(z: zipfile.ZipFile, destination: Path) -> list[zipfile.ZipInfo]:
    members = z.infolist()
    if not members or sum(m.file_size for m in members) > MAX_TOTAL_BYTES:
        raise ValueError("Empty archive or uncompressed total exceeds safety limit")
    seen = set()
    for member in members:
        name = member.filename.replace("\\", "/")
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or ":" in name
                or stat.S_ISLNK(member.external_attr >> 16)):
            raise ValueError(f"Unsafe archive member: {member.filename}")
        target = (destination / Path(*relative.parts)).resolve()
        if not target.is_relative_to(destination):
            raise ValueError(f"Archive member escapes extraction directory: {member.filename}")
        if name.casefold() in seen:
            raise ValueError(f"Duplicate archive member: {member.filename}")
        seen.add(name.casefold())
        if member.file_size > MAX_MEMBER_BYTES:
            raise ValueError(f"Archive member exceeds safety limit: {member.filename}")
    return members


def existing_crc(path: Path) -> int:
    crc = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def safe_extract(z: zipfile.ZipFile, members: list[zipfile.ZipInfo], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for member in members:
        target = (destination / Path(*PurePosixPath(member.filename.replace("\\", "/")).parts)).resolve()
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            if target.stat().st_size != member.file_size or existing_crc(target) != member.CRC:
                raise FileExistsError(f"Existing extracted file differs; refusing overwrite: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".extracting")
        if temp.exists():
            raise FileExistsError(f"Unfinished extraction exists: {temp}")
        with z.open(member) as source, temp.open("xb") as output:
            shutil.copyfileobj(source, output)
        if temp.stat().st_size != member.file_size or existing_crc(temp) != member.CRC:
            raise ValueError(f"Extracted CRC mismatch: {temp}")
        os.replace(temp, target)


def raster_metadata(path: Path, bounds: list[int], decode: bool) -> dict:
    with tifffile.TiffFile(path) as image:
        page = image.pages[0]
        geo = image.geotiff_metadata or {}
        selected = {}
        for name in ("ModelPixelScaleTag", "ModelTiepointTag", "GeoKeyDirectoryTag",
                     "GeoAsciiParamsTag", "GDAL_NODATA", "GDAL_METADATA", "Software",
                     "ImageDescription", "DateTime"):
            if name in page.tags:
                selected[name] = page.tags[name].value
        result = {
            "path": str(path), "name": path.name, "size_bytes": path.stat().st_size,
            "sha256": sha256(path), "page_count_including_overviews": len(image.pages),
            "shape": list(page.shape), "dtype": str(page.dtype),
            "compression": page.compression.name, "geotiff_tags": geo,
            "raw_selected_tags": selected, "nominal_tile_bounds_wsen": bounds,
            "nodata_tag_present": "GDAL_NODATA" in selected,
            "vertical_crs_tag_present": any(str(k).startswith("Vertical") for k in geo),
        }
        scale, tie = geo.get("ModelPixelScale"), geo.get("ModelTiepoint")
        if scale and tie and len(page.shape) == 2:
            height, width = page.shape
            left, top = tie[3] - tie[0] * scale[0], tie[4] + tie[1] * scale[1]
            result["pixel_size_degrees"] = list(scale[:2])
            if geo.get("GTRasterTypeGeoKey") == 1:
                result["georeferenced_outer_bounds_wsen"] = [left, top - height * scale[1], left + width * scale[0], top]
                result["pixel_center_bounds_wsen"] = [left + scale[0] / 2, top - (height - .5) * scale[1], left + (width - .5) * scale[0], top - scale[1] / 2]
        if not decode:
            result["pixel_decode_check"] = {"status": "not_requested"}
        else:
            try:
                values = page.asarray(maxworkers=1)
                height, width = values.shape
                valid = np.isfinite(values)
                nodata = selected.get("GDAL_NODATA")
                if nodata is not None:
                    try:
                        valid &= values != float(str(nodata).strip("\x00 "))
                    except ValueError:
                        result["nodata_parse_warning"] = str(nodata)
                usable = values[valid]
                points = [(0, 0), (height // 2, width // 2), (height - 1, width - 1)]
                result["pixel_decode_check"] = {
                    "status": "passed", "scope": "entire primary raster of this tile only",
                    "sample_pixels": [{"row": y, "col": x, "value": values[y, x].item()} for y, x in points],
                    "statistics_without_inferred_nodata": {
                        "pixel_count": values.size, "valid_per_explicit_metadata_count": int(valid.sum()),
                        "min": usable.min().item() if usable.size else None,
                        "max": usable.max().item() if usable.size else None,
                        "negative_pixel_count": int((usable < 0).sum()),
                        "zero_pixel_count": int((usable == 0).sum()),
                    },
                }
                del values, valid, usable
            except Exception as exc:
                result["pixel_decode_check"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        return result


def audit_archive(archive: Path, root: Path, decode: bool, source_page: str) -> dict:
    match = TILE_RE.fullmatch(archive.name)
    if not match:
        raise ValueError("Unexpected tile filename")
    ns, latitude, ew, longitude = match.groups()
    south = int(latitude) * (1 if ns.upper() == "N" else -1)
    west = int(longitude) * (1 if ew.upper() == "E" else -1)
    tile = f"{ns.upper()}{latitude}{ew.upper()}{longitude}"
    destination = (root / "original" / tile).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Extraction directory escapes terrain root")
    before = archive.stat()
    record = {
        "dataset": "ASTER Global Digital Elevation Model Version 3 (GScloud GDEMV3 30M)",
        "tile_id": tile, "source_page": source_page,
        "acquisition_route": "User-authenticated GScloud browser download; original ZIP copied locally after download completion",
        "verified_at": now(), "archive": {"path": str(archive), "name": archive.name,
            "size_bytes": before.st_size, "sha256": sha256(archive)},
    }
    with zipfile.ZipFile(archive) as package:
        members = checked_members(package, destination)
        bad_member = package.testzip()
        if bad_member is not None:
            raise ValueError(f"ZIP CRC mismatch: {bad_member}")
        record["archive"].update({"zip_crc_verified": True, "member_paths_validated": True,
            "members": [{"name": m.filename, "size_bytes": m.file_size, "crc32": f"{m.CRC:08x}"} for m in members]})
        safe_extract(package, members, destination)
    after = archive.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Archive changed during validation")
    expected = [destination / f"ASTGTMV003_{tile}_{suffix}.tif" for suffix in ("dem", "num")]
    if not all(path.is_file() for path in expected):
        raise ValueError("Expected DEM and NUM TIFF files are not both present")
    record["extracted_directory"] = str(destination)
    record["rasters"] = [raster_metadata(path, [west, south, west + 1, south + 1], decode) for path in expected]
    decode_failed = any(r["pixel_decode_check"]["status"] == "failed" for r in record["rasters"])
    record["status"] = "pixel_decode_failed" if decode_failed else "verified"
    record["processing_status"] = "downloaded_crc_verified_safely_extracted; no mosaic, reprojection, or administrative clipping"
    record["limitations"] = [
        "Vertical reference and nodata are not inferred when absent from source metadata; compare tide and terrain only after authoritative datum checks.",
        "ASTER GDEM surface elevations do not establish local engineering accuracy or a validated inundation model.",
        "NUM is a separate support/quality layer, not a second elevation layer; its exact interpretation requires product documentation.",
    ]
    write_json(root / f"{tile}_metadata.json", record)
    return record


def load_catalog_observations(path: Path | None) -> dict:
    """Merge explicit browser findings separately from local-file validation."""
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"no_matching_row", "download_requested", "matching_row_found", "not_checked"}
    observations = {}
    for row in document.get("observations", []):
        tile = row.get("tile_id", "")
        status = row.get("catalog_status", "")
        if not re.fullmatch(r"N\d{2}E\d{3}", tile) or status not in allowed:
            raise ValueError(f"Invalid explicit catalog observation: {row!r}")
        observations[tile] = row
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terrain-root", type=Path, default=APP_ROOT / "data/spatial_runtime/raw/terrain")
    parser.add_argument("--tile", action="append", help="Audit specified tile(s), e.g. N29E122; default audits all completed local ZIPs")
    parser.add_argument("--metadata-only", action="store_true", help="Skip decompression of primary TIFF pixel data")
    parser.add_argument("--manifest-only", action="store_true", help="Refresh the summary from existing per-tile audit records without decoding or extracting again")
    parser.add_argument("--source-page", default=PUBLIC_SOURCE, help="Public product page; never pass credential-bearing URLs")
    parser.add_argument("--catalog-status", type=Path, help="Optional explicit browser observations JSON; defaults to terrain-root/catalog_status.json when present")
    args = parser.parse_args()
    root = args.terrain_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    default_catalog = root / "catalog_status.json"
    catalog_path = args.catalog_status.resolve() if args.catalog_status else (default_catalog if default_catalog.is_file() else None)
    catalog = load_catalog_observations(catalog_path)
    wanted = {tile.upper() for tile in args.tile} if args.tile else None
    records = []
    failures = []
    archives = [] if args.manifest_only else sorted(root.glob("ASTGTMV003_*.zip"))
    for archive in archives:
        tile = archive.stem.removeprefix("ASTGTMV003_").upper()
        if wanted and tile not in wanted:
            continue
        try:
            record = audit_archive(archive, root, not args.metadata_only, args.source_page)
            records.append(record)
            print(f"{tile}: {record['status']}", flush=True)
        except Exception as exc:
            failures.append({"tile_id": tile, "archive_path": str(archive), "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            print(f"{tile}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    # Keep previously audited tiles when a newly completed batch is selected.
    by_tile = {}
    for metadata in sorted(root.glob("N??E???_metadata.json")):
        try:
            previous = json.loads(metadata.read_text(encoding="utf-8"))
            if previous.get("tile_id"):
                by_tile[previous["tile_id"]] = previous
        except (ValueError, OSError):
            pass
    by_tile.update({r["tile_id"]: r for r in records})
    for failure in failures:
        by_tile[failure["tile_id"]] = failure
    expected = [f"N{lat:02d}E{lon:03d}" for lat in range(27, 32) for lon in range(118, 124)]
    manifest = {
        "dataset": "GScloud GDEMV3 30M / ASTER GDEM V3", "updated_at": now(),
        "source_page": args.source_page, "requested_search_envelope_wsen": [118, 27, 124, 32],
        "expected_candidate_tiles": expected,
        "coverage_note": "Envelope candidates are not a statement that every offshore tile exists in the provider catalog. No local ZIP is not equivalent to no data.",
        "catalog_status_source_file": str(catalog_path) if catalog_path else None,
        "candidate_status": [{"tile_id": tile,
            "status": by_tile.get(tile, {}).get("status", "not_downloaded_or_not_yet_checked"),
            "catalog_status": catalog.get(tile, {}).get("catalog_status", "not_checked"),
            "catalog_observation": catalog.get(tile)} for tile in expected],
        "summary": {"audited_tiles": len(by_tile), "verified_tiles": sum(r.get("status") == "verified" for r in by_tile.values()),
                    "total_archive_bytes": sum(r.get("archive", {}).get("size_bytes", 0) for r in by_tile.values()),
                    "catalog_available_and_download_requested_tiles": sum(catalog.get(tile, {}).get("catalog_status") == "download_requested" for tile in expected),
                    "catalog_no_matching_row_tiles": sum(catalog.get(tile, {}).get("catalog_status") == "no_matching_row" for tile in expected),
                    "requested_downloads_pending_local_verification": sum(catalog.get(tile, {}).get("catalog_status") == "download_requested" and by_tile.get(tile, {}).get("status") != "verified" for tile in expected),
                    "original_tiff_count": sum(len(r.get("rasters", [])) for r in by_tile.values()),
                    "original_tiff_bytes": sum(t.get("size_bytes", 0) for r in by_tile.values() for t in r.get("rasters", []))},
        "tiles": [by_tile[key] for key in sorted(by_tile)],
        "dependencies": {"python": sys.version.split()[0], "numpy": np.__version__, "tifffile": tifffile.__version__,
                         "isolated_imagecodecs_directory": str(LOCAL_DEPS)},
    }
    write_json(root / "download_manifest.json", manifest)
    return 1 if failures or any(r["status"] != "verified" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
