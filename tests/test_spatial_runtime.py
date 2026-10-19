"""Meaningful spatial boundaries, nodata, conservation and CRS regression tests."""
import json
from pathlib import Path

import numpy as np
import pytest
from pyproj import CRS, Transformer
import rasterio
from rasterio.transform import from_origin

from spatial_runtime import SpatialRuntime, prepare
from spatial_runtime.runtime import _population_stats, _source_crs, _source_window, _transformers
from rasterio.windows import Window


def write_raster(path, values, transform, crs="EPSG:4326", nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
                       count=1, dtype=values.dtype, crs=crs, transform=transform, nodata=nodata) as dst:
        dst.write(values, 1)


@pytest.fixture
def prepared(tmp_path):
    base = tmp_path / "data" / "spatial_runtime"
    dem = np.array([[7, 0], [-9999, 19]], dtype=np.int16)
    write_raster(base / "raw/terrain/original/N29E122/ASTGTMV003_N29E122_dem.tif", dem,
                 from_origin(122, 30, .5, .5))
    population = np.arange(30, dtype=np.int16).reshape(5, 6)
    population[1, 2] = 32767
    write_raster(base / "raw/population/original/PopSE_China2020_100m.tif", population,
                 from_origin(118, 32, 1, 1), nodata=32767)
    index = prepare(tmp_path)
    return SpatialRuntime(tmp_path), index, population


def test_source_window_is_copied_without_population_change(prepared):
    runtime, index, values = prepared
    valid = values[values != 32767]
    pop = index["population"]
    assert pop["population_conservation_passed"] is True
    assert pop["source_window_statistics"] == pop["copied_window_statistics"]
    assert pop["envelope_statistics"]["estimated_population"] == int(valid.sum())
    assert pop["resampling"] == "none"
    assert runtime.status()["ready"] is True
    assert runtime.status()["population_year"] == 2020


def test_geographic_cell_centres_partition_population_without_double_count(prepared):
    runtime, _, _ = prepared
    whole = runtime.population_bbox(118, 27, 124, 32)
    left = runtime.population_bbox(118, 27, 120.5, 32)
    right = runtime.population_bbox(120.5, 27, 124, 32)
    assert left["estimated_population"] + right["estimated_population"] == whole["estimated_population"]
    assert left["selected_cell_count"] + right["selected_cell_count"] == whole["selected_cell_count"]
    assert whole["valid_cell_count"] == 29
    assert whole["nodata_cell_count"] == 1
    assert whole["year"] == 2020
    assert whole["not_evacuation_count"] is True


def test_valid_zero_population_is_not_missing(prepared):
    runtime, _, _ = prepared
    point_cell = runtime.population_bbox(118, 31, 119, 32)
    assert point_cell["estimated_population"] == 0
    assert point_cell["valid_cell_count"] == 1
    assert point_cell["state"] == "ready"


def test_nodata_population_is_not_reported_as_zero_people(prepared):
    runtime, _, _ = prepared
    missing = runtime.population_bbox(120, 30, 121, 31)
    assert missing["state"] == "no_data"
    assert missing["valid_cell_count"] == 0
    assert missing["nodata_cell_count"] == 1


def test_partial_region_and_missing_sea_tiles(prepared):
    runtime, _, _ = prepared
    partial = runtime.population_bbox(117, 27, 119, 29)
    assert partial["partial_coverage"] is True
    assert partial["evaluated_bbox_wsen"] == [118, 27, 119, 29]
    assert runtime.population_bbox(10, 10, 11, 11)["state"] == "out_of_coverage"
    assert runtime.point(123.5, 28.5)["state"] == "no_data"
    assert runtime.point(115, 28)["state"] == "out_of_coverage"


def test_dem_query_exact_pixel_and_product_fill(prepared):
    runtime, _, _ = prepared
    assert runtime.point(122.25, 29.75)["elevation_m"] == 7
    sea = runtime.point(122.75, 29.75)
    assert sea["elevation_m"] == 0
    assert sea["zero_may_represent_sea_level_water"] is True
    assert sea["not_1985_datum"] is True
    assert runtime.point(122.25, 29.25)["state"] == "no_data"
    assert runtime.point(122.75, 29.25)["elevation_m"] == 19


@pytest.mark.parametrize("bbox", [(120, 30, 119, 31), (118, 32, 124, 27), (0, 0, float("nan"), 20)])
def test_invalid_boundaries_rejected(prepared, bbox):
    with pytest.raises(ValueError):
        prepared[0].population_bbox(*bbox)


def test_unprepared_status_and_query(tmp_path):
    runtime = SpatialRuntime(tmp_path)
    assert runtime.status()["state"] == "not_prepared"
    assert runtime.point(122, 29)["elevation_m"] is None
    assert runtime.population_bbox(118, 27, 124, 32)["estimated_population"] is None


def test_spatial_data_root_matches_app_root(prepared):
    runtime, _, _ = prepared
    direct = SpatialRuntime(runtime.base)
    assert direct.status()["ready"] is True
    assert direct.point(122.25, 29.75) == runtime.point(122.25, 29.75)


def test_missing_optional_dependencies_do_not_break_qa_initialization(tmp_path, monkeypatch):
    import spatial_runtime.runtime as module
    monkeypatch.setattr(module, "DEPENDENCY_ERROR", "空间查询依赖尚未安装")
    runtime = module.SpatialRuntime(tmp_path)
    assert runtime.status()["state"] == "unavailable"
    assert runtime.point(122, 29)["state"] == "unavailable"
    assert runtime.population_bbox(118, 27, 124, 32)["estimated_population"] is None


def test_albers_uses_saved_wkt_without_relabelling_and_projects_bbox(tmp_path):
    wkt = ('PROJCS["Albers_Conic_Equal_Area",GEOGCS["GCS_Krassovsky_1942",'
           'DATUM["D_unknown",SPHEROID["krass",6378245,298.3]],PRIMEM["Greenwich",0],'
           'UNIT["Degree",0.0174532925199433]],PROJECTION["Albers"],'
           'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
           'PARAMETER["central_meridian",105],PARAMETER["standard_parallel_1",25],'
           'PARAMETER["standard_parallel_2",47],PARAMETER["latitude_of_origin",0],UNIT["Meter",1]]')
    crs = CRS.from_wkt(wkt)
    forward, inverse = _transformers(crs)
    x, y = forward.transform(122.4, 29.9)
    values = np.array([[3, 8], [0, 32767]], dtype=np.int16)
    path = tmp_path / "albers.tif"
    write_raster(path, values, from_origin(x-100, y+100, 100, 100), crs.to_wkt(), nodata=32767)
    with rasterio.open(path) as src:
        read_crs = _source_crs(src, {"geotiff_metadata": {"PCSCitationGeoKey": "ESRI PE String = " + wkt}})
        assert "unknown" in read_crs.datum.name.lower()
        assert read_crs.to_epsg() is None
        stats = _population_stats(src, Window(0, 0, 2, 2), (122.39, 29.89, 122.41, 29.91), inverse)
        assert stats["estimated_population"] == 11
        assert stats["valid_cell_count"] == 3
        assert stats["nodata_cell_count"] == 1
        win = _source_window(src, (122.39, 29.89, 122.41, 29.91), forward)
        assert win == Window(0, 0, 2, 2)
