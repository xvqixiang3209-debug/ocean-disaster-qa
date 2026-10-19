"""Read documented source pixels; do not infer inundation or evacuation counts.

Population remains on its original Albers grid. A geographic selection is
applied to source pixel centres, without warping/resampling population counts.
The source's D_unknown datum is retained and exposed as a positional limit.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

DEPENDENCY_ERROR = None
try:
    import numpy as np
    from pyproj import CRS, Transformer
    import rasterio
    from rasterio.windows import Window, from_bounds
except (ImportError, OSError) as exc:
    # Spatial data is optional for the existing QA service. Importing the
    # service on an unprepared deployment must not take its other routes down.
    DEPENDENCY_ERROR = "空间查询依赖尚未安装：" + str(getattr(exc, "name", None) or "rasterio/pyproj")

DEFAULT_ENVELOPE = (118.0, 27.0, 124.0, 32.0)
SCHEMA = 1
ASTER_GUIDE = "https://www.earthdata.nasa.gov/s3fs-public/2025-04/ASTGTM_User_Guide_V3.pdf"
ASTER_CATALOG = "https://doi.org/10.5067/ASTER/ASTGTM.003"
ASTER_VERTICAL_PROVENANCE = {
    "product_declared_vertical_datum": "EGM96",
    "source_url": ASTER_CATALOG,
    "guide_url": ASTER_GUIDE,
    "local_tiff_tag_verified": False,
    "basis": "NASA Earthdata ASTER GDEM V003 catalog and V3 guide declare WGS84/EGM96; local TIFF has no vertical CRS tag.",
    "source_checked_date": "2026-10-17",
    "source_check_method": "Official NASA catalog and PDF indexed text; direct page/PDF fetch returned HTTP403.",
}
LIMITATIONS = [
    "覆盖118°—124°E、27°—32°N的矩形包络，包含浙江周边地区；尚未按省界裁剪。",
    "ASTER约30米地形为EGM96高程，未转换为1985国家高程基准；不能与潮位直接比较得出精确淹没。",
    "DEM中的海面零值不代表该点陆地、滩涂可通行或海底水深。",
    "人口为2020年模型估计常住人口，按原100米像元人数统计；不是当前游客、受灾人数或实际转移人数。",
    "人口原始Albers投影使用Krassovsky 1942椭球、D_unknown基准；转换精度未知，不能用作精确地籍定位。",
    "人口矩形统计采用源像元中心归属，不拆分边界像元，不对人数栅格重采样。",
]


def _base(root):
    root = Path(root).resolve()
    if (root / "raw").is_dir() or root.name == "spatial_runtime":
        return root
    return root / "data" / "spatial_runtime"


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bbox(west, south, east, north):
    values = tuple(float(v) for v in (west, south, east, north))
    if not all(math.isfinite(v) for v in values):
        raise ValueError("经纬度必须为有限数值")
    w, s, e, n = values
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("矩形范围须满足西经度<东经度、南纬度<北纬度，且在合法经纬度范围内")
    return values


def _source_crs(source, metadata=None):
    """Prefer the exact saved source WKT over assigning an EPSG by resemblance."""
    if metadata:
        citation = metadata.get("geotiff_metadata", {}).get("PCSCitationGeoKey", "")
        if "ESRI PE String = " in citation:
            return CRS.from_wkt(citation.split("ESRI PE String = ", 1)[1].strip().rstrip("|"))
    if source.crs:
        return CRS.from_wkt(source.crs.to_wkt())
    raise ValueError("人口栅格没有可信坐标系；不能猜测EPSG")


def _transformers(crs):
    # Ballpark is explicitly disclosed: the original D_unknown source supplies
    # no datum shift. PROJ's inverse projection does not establish that shift.
    return (Transformer.from_crs("EPSG:4326", crs, always_xy=True),
            Transformer.from_crs(crs, "EPSG:4326", always_xy=True))


def _source_window(source, bbox, transformer, padding=1):
    w, s, e, n = bbox
    # Densified sides enclose the curved Albers image of the WGS84 rectangle.
    t = np.linspace(0.0, 1.0, 257)
    lons = np.concatenate((w + (e-w)*t, np.full_like(t, e), e - (e-w)*t, np.full_like(t, w)))
    lats = np.concatenate((np.full_like(t, s), s + (n-s)*t, np.full_like(t, n), n - (n-s)*t))
    x, y = transformer.transform(lons, lats)
    raw = from_bounds(float(np.min(x)), float(np.min(y)), float(np.max(x)), float(np.max(y)), source.transform)
    left = max(0, math.floor(raw.col_off) - padding)
    top = max(0, math.floor(raw.row_off) - padding)
    right = min(source.width, math.ceil(raw.col_off + raw.width) + padding)
    bottom = min(source.height, math.ceil(raw.row_off + raw.height) + padding)
    if right <= left or bottom <= top:
        return None
    return Window(left, top, right-left, bottom-top)


def _windows(window, rows=128):
    for offset in range(0, int(window.height), rows):
        yield Window(int(window.col_off), int(window.row_off)+offset,
                     int(window.width), min(rows, int(window.height)-offset))


def _population_stats(source, window, bbox=None, inverse=None):
    total = valid_count = nodata_count = inside_count = 0
    minimum = maximum = None
    for block in _windows(window):
        values = source.read(1, window=block, masked=True)
        mask = ~np.ma.getmaskarray(values)
        # Retain all source values except explicit nodata; negative person
        # counts are rejected rather than silently interpreted as zero.
        data = values.data
        if bbox is not None:
            rows = np.arange(int(block.row_off), int(block.row_off + block.height), dtype=np.float64) + .5
            cols = np.arange(int(block.col_off), int(block.col_off + block.width), dtype=np.float64) + .5
            x = source.transform.c + cols * source.transform.a
            y = source.transform.f + rows * source.transform.e
            xx, yy = np.meshgrid(x, y)
            lons, lats = inverse.transform(xx, yy)
            w, s, e, n = bbox
            inside = (lons >= w) & (lons < e) & (lats >= s) & (lats < n)
        else:
            inside = np.ones(data.shape, dtype=bool)
        inside_count += int(inside.sum())
        nodata_count += int((inside & ~mask).sum())
        selected = data[inside & mask]
        if selected.size:
            if np.any(selected < 0):
                raise ValueError("人口源数据包含未解释的负人数，已停止统计")
            total += int(selected.sum(dtype=np.int64))
            valid_count += int(selected.size)
            lo, hi = int(selected.min()), int(selected.max())
            minimum = lo if minimum is None else min(minimum, lo)
            maximum = hi if maximum is None else max(maximum, hi)
    return {"estimated_population": total, "valid_cell_count": valid_count,
            "nodata_cell_count": nodata_count, "selected_cell_count": inside_count,
            "minimum_cell_persons": minimum, "maximum_cell_persons": maximum}


def prepare(root, envelope=DEFAULT_ENVELOPE):
    """Index actual DEMs and copy an aligned population source window once."""
    if DEPENDENCY_ERROR:
        raise RuntimeError(DEPENDENCY_ERROR)
    base = _base(root)
    envelope = _bbox(*envelope)
    terrain = []
    for path in sorted((base / "raw" / "terrain" / "original").glob("*/*_dem.tif")):
        match = re.search(r"_(N\d+E\d+)_dem\.tif$", path.name)
        if not match:
            continue
        with rasterio.open(path) as src:
            if not src.crs or src.crs.to_epsg() != 4326:
                raise ValueError("DEM坐标系与ASTER产品定义不一致：" + path.name)
            centre = (src.width//2, src.height//2)
            sample = int(src.read(1, window=Window(centre[0], centre[1], 1, 1))[0, 0])
            terrain.append({"tile_id": match[1], "path": str(path.relative_to(base)),
                "sha256": _hash(path), "bounds_wsen": list(src.bounds),
                "shape": [src.height, src.width], "horizontal_crs": "EPSG:4326",
                "vertical_datum": "EGM96 per ASTER GDEM V3 product specification",
                "vertical_datum_provenance": ASTER_VERTICAL_PROVENANCE,
                "vertical_crs_embedded": False, "unit": "m", "nodata": -9999,
                "nodata_basis": "ASTER GDEM V3 product specification; may be absent from TIFF tag",
                "zero_is_valid": True, "pixel_size_degrees": [abs(src.transform.a), abs(src.transform.e)],
                "centre_pixel_read": sample, "guide_url": ASTER_GUIDE,
                "catalog_url": ASTER_CATALOG})
    population_path = base / "raw" / "population" / "original" / "PopSE_China2020_100m.tif"
    metadata_path = base / "raw" / "population" / "popse_raster_metadata.json"
    if not terrain:
        raise FileNotFoundError("没有已解压的ASTER DEM")
    if not population_path.is_file():
        raise FileNotFoundError("没有已解压的PopSE2020人口源栅格")
    metadata = _json(metadata_path) if metadata_path.exists() else None
    output = base / "prepared" / "population_envelope_sourcegrid.tif"
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".pending.tif")
    with rasterio.open(population_path) as src:
        crs = _source_crs(src, metadata)
        if src.transform.b or src.transform.d:
            raise ValueError("当前源像元查询不支持旋转网格")
        forward, inverse = _transformers(crs)
        win = _source_window(src, envelope, forward)
        if win is None:
            raise ValueError("人口栅格没有覆盖请求包络")
        profile = src.profile.copy()
        profile.update(driver="GTiff", height=int(win.height), width=int(win.width), count=1,
                       transform=src.window_transform(win), crs=crs.to_wkt(),
                       tiled=True, blockxsize=256, blockysize=256, compress="deflate")
        with rasterio.open(temp, "w", **profile) as dst:
            for block in _windows(win):
                data = src.read(1, window=block)
                target = Window(int(block.col_off-win.col_off), int(block.row_off-win.row_off), block.width, block.height)
                dst.write(data, 1, window=target)
            dst.update_tags(dataset="PopSE China2020", data_year="2020",
                            unit="estimated persons per source 100m grid cell",
                            processing="aligned source window; no resampling", datum_note="D_unknown retained")
        original_stats = _population_stats(src, win)
        exact_stats = _population_stats(src, win, envelope, inverse)
        source_transform = list(src.transform)[:6]
        source_wkt = crs.to_wkt()
        source_nodata = src.nodata
        source_shape = [src.height, src.width]
    with rasterio.open(temp) as cropped:
        copied_stats = _population_stats(cropped, Window(0, 0, cropped.width, cropped.height))
        if copied_stats != original_stats:
            raise RuntimeError("人口裁切前后源像元统计不一致")
        crop_shape = [cropped.height, cropped.width]
        crop_transform = list(cropped.transform)[:6]
        # A centre coordinate must round-trip through the original WKT.
        x, y = cropped.xy(cropped.height//2, cropped.width//2)
        lon, lat = inverse.transform(x, y)
        xx, yy = forward.transform(lon, lat)
        roundtrip = math.hypot(xx-x, yy-y)
        if roundtrip > .01:
            raise RuntimeError("人口坐标投影往返误差超限")
    temp.replace(output)
    manifest = {"schema": SCHEMA, "prepared_at": datetime.now(timezone.utc).isoformat(),
        "state": "ready", "coverage_envelope_wsen": list(envelope),
        "coverage_is_province_boundary": False,
        "terrain": {"dataset": "ASTER GDEM V3", "tiles": terrain, "tile_count": len(terrain),
                    "vertical_datum_provenance": ASTER_VERTICAL_PROVENANCE,
                    "horizontal_crs": "EPSG:4326", "vertical_datum": "EGM96", "unit": "m"},
        "population": {"dataset": "PopSE China2020 100m", "year": 2020,
            "source_path": str(population_path.relative_to(base)), "source_sha256": _hash(population_path),
            "path": str(output.relative_to(base)), "sha256": _hash(output),
            "source_crs_wkt": source_wkt, "source_crs_authority": crs.to_authority(),
            "source_datum": crs.datum.name, "coordinate_accuracy_m": None,
            "datum_shift_verified": False, "transformation_note": "Original D_unknown datum retained; WGS84 conversion has unknown positional accuracy.",
            "source_shape": source_shape, "source_transform": source_transform,
            "source_window": {"col_off": int(win.col_off), "row_off": int(win.row_off), "width": int(win.width), "height": int(win.height)},
            "shape": crop_shape, "transform": crop_transform, "nodata": source_nodata,
            "zero_is_valid": True, "unit": "estimated persons per source 100m grid cell",
            "resampling": "none", "province_clipped": False,
            "aggregation": "half-open geographic bbox; source pixel centres; no area weighting or resampling",
            "source_window_statistics": original_stats, "copied_window_statistics": copied_stats,
            "population_conservation_passed": True, "envelope_statistics": exact_stats,
            "projection_roundtrip_error_m": roundtrip}, "limitations": LIMITATIONS}
    _dump(base / "index.json", manifest)
    return manifest


class SpatialRuntime:
    def __init__(self, root):
        self.base = _base(root)

    def _index(self):
        path = self.base / "index.json"
        return _json(path) if path.is_file() else None

    def status(self):
        if DEPENDENCY_ERROR:
            return {"state": "unavailable", "ready": False, "reason": DEPENDENCY_ERROR, "limitations": LIMITATIONS}
        index = self._index()
        if not index:
            return {"state": "not_prepared", "ready": False, "limitations": LIMITATIONS}
        population = index["population"]
        missing = [row["tile_id"] for row in index["terrain"]["tiles"] if not (self.base/row["path"]).is_file()]
        if not (self.base/population["path"]).is_file():
            missing.append("population")
        return {"state": "ready" if not missing else "incomplete", "ready": not missing,
            "prepared_at": index["prepared_at"], "coverage_envelope_wsen": index["coverage_envelope_wsen"],
            "coverage_is_province_boundary": False, "terrain_tile_count": index["terrain"]["tile_count"],
            "terrain_vertical_datum": index["terrain"]["vertical_datum"], "population_year": population["year"],
            "terrain_vertical_datum_provenance": index["terrain"].get("vertical_datum_provenance", ASTER_VERTICAL_PROVENANCE),
            "population_unit": population["unit"], "population_source_datum": population["source_datum"],
            "population_conservation_passed": population["population_conservation_passed"],
            "capabilities": ["point_elevation", "bbox_estimated_resident_population"],
            "missing_files": missing, "limitations": index["limitations"]}

    def point(self, lon, lat):
        lon, lat = float(lon), float(lat)
        if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("经纬度超出范围")
        result = {"kind": "elevation", "location": {"lon": lon, "lat": lat, "crs": "EPSG:4326"},
                  "unit": "m", "vertical_datum": "EGM96", "vertical_datum_provenance": ASTER_VERTICAL_PROVENANCE,
                  "not_1985_datum": True, "limitations": LIMITATIONS[:3]}
        if DEPENDENCY_ERROR:
            return {**result, "state": "unavailable", "elevation_m": None, "reason": DEPENDENCY_ERROR}
        index = self._index()
        if not index:
            return {**result, "state": "not_prepared", "elevation_m": None}
        w, s, e, n = index["coverage_envelope_wsen"]
        if not (w <= lon <= e and s <= lat <= n):
            return {**result, "state": "out_of_coverage", "elevation_m": None}
        candidates = [row for row in index["terrain"]["tiles"]
                      if row["bounds_wsen"][0] <= lon < row["bounds_wsen"][2]
                      and row["bounds_wsen"][1] < lat <= row["bounds_wsen"][3]]
        for tile in candidates:
            path = self.base / tile["path"]
            if not path.is_file():
                continue
            with rasterio.open(path) as src:
                row, col = src.index(lon, lat)
                if not (0 <= row < src.height and 0 <= col < src.width):
                    continue
                value = src.read(1, window=Window(col, row, 1, 1), masked=True)
                if np.ma.is_masked(value[0, 0]) or int(value[0, 0]) == tile.get("nodata", -9999):
                    continue
                x, y = src.xy(row, col)
                return {**result, "state": "ready", "elevation_m": int(value[0, 0]),
                    "source": {"dataset": "ASTER GDEM V3", "tile_id": tile["tile_id"],
                               "filename": path.name, "sha256": tile["sha256"], "catalog_url": ASTER_CATALOG},
                    "source_pixel": {"row": row, "col": col, "centre_lon": x, "centre_lat": y},
                    "zero_may_represent_sea_level_water": int(value[0, 0]) == 0}
        return {**result, "state": "no_data", "elevation_m": None,
                "reason": "该点没有可用DEM像元；海上无分幅不能补零"}

    def population_bbox(self, west, south, east, north):
        requested = _bbox(west, south, east, north)
        result = {"kind": "population_bbox", "requested_bbox_wsen": list(requested), "year": 2020,
            "unit": "estimated resident persons", "not_evacuation_count": True,
            "aggregation": "source pixel centre within west<=lon<east and south<=lat<north; no resampling",
            "limitations": [LIMITATIONS[0], *LIMITATIONS[3:]]}
        if DEPENDENCY_ERROR:
            return {**result, "state": "unavailable", "estimated_population": None, "reason": DEPENDENCY_ERROR}
        index = self._index()
        if not index:
            return {**result, "state": "not_prepared", "estimated_population": None}
        coverage = index["coverage_envelope_wsen"]
        selected = (max(requested[0], coverage[0]), max(requested[1], coverage[1]),
                    min(requested[2], coverage[2]), min(requested[3], coverage[3]))
        if selected[0] >= selected[2] or selected[1] >= selected[3]:
            return {**result, "state": "out_of_coverage", "estimated_population": None}
        population = index["population"]
        path = self.base / population["path"]
        if not path.is_file():
            return {**result, "state": "unavailable", "estimated_population": None, "reason": "人口区域栅格文件不可读"}
        with rasterio.open(path) as src:
            crs = CRS.from_wkt(population["source_crs_wkt"])
            forward, inverse = _transformers(crs)
            window = _source_window(src, selected, forward)
            stats = _population_stats(src, window, selected, inverse) if window else {
                "estimated_population": 0, "valid_cell_count": 0, "nodata_cell_count": 0,
                "selected_cell_count": 0, "minimum_cell_persons": None, "maximum_cell_persons": None}
        return {**result, "state": "ready" if stats["valid_cell_count"] else "no_data", **stats,
            "estimated_population": stats["estimated_population"] if stats["valid_cell_count"] else None,
            "partial_coverage": list(selected) != list(requested), "evaluated_bbox_wsen": list(selected),
            "has_nodata": bool(stats["nodata_cell_count"]), "coordinate_accuracy_m": None,
            "source": {"dataset": population["dataset"], "year": population["year"],
                       "filename": path.name, "sha256": population["sha256"],
                       "source_datum": population["source_datum"], "datum_shift_verified": False,
                       "repository_doi": "10.6084/m9.figshare.24916140.v1"}}
