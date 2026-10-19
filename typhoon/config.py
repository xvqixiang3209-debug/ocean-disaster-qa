"""Configuration for the typhoon analogue subsystem."""

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
WORKSPACE_DIR = PROJECT_DIR.parent

DEFAULT_IBTRACS_ZIP = WORKSPACE_DIR / "ibtracs.zip"
DEFAULT_DATABASE = PACKAGE_DIR / "data" / "typhoon_history.sqlite3"
DEFAULT_RAG_DATABASE = PACKAGE_DIR / "data" / "typhoon_rag.sqlite3"
DEFAULT_CURRENT_TYPHOON = PACKAGE_DIR / "current" / "current_typhoon.json"
DEFAULT_STORM_SURGE_ARCHIVE = WORKSPACE_DIR / "2001-2016台风风暴潮数据.rar"
DEFAULT_STORM_SURGE_DATA = DEFAULT_DATABASE
DEFAULT_STORM_NUMBER_MAP = PACKAGE_DIR / "data" / "jma_storm_number_sid_2001_2016.json"

# Approximate Zhejiang coastal reference points, from south to north.  They are
# used only for candidate filtering/alignment, not as a replacement for an
# operational warning polygon.
ZHEJIANG_COAST_POINTS = [
    (27.50, 120.55),  # Cangnan/Pingyang coast
    (27.99, 120.70),  # Wenzhou
    (28.30, 121.20),  # Yuhuan
    (28.66, 121.42),  # Taizhou
    (29.10, 121.90),  # Sanmen coast
    (29.87, 121.55),  # Ningbo
    (29.99, 122.21),  # Zhoushan
    (30.62, 121.15),  # Hangzhou Bay south shore
    (30.75, 121.00),  # Jiaxing coast
]

# Broad pre-filter for storms that may affect Zhejiang.  The final filtering
# also checks great-circle distance to the coastal reference points.
ZHEJIANG_INFLUENCE_BBOX = {
    "lat_min": 23.0,
    "lat_max": 33.0,
    "lon_min": 117.0,
    "lon_max": 128.0,
}
ZHEJIANG_MAX_DISTANCE_KM = 800.0

SIMILARITY_WEIGHTS = {
    "intensity": 0.00,
    "track": 1.00,
}

INTENSITY_COMPONENT_WEIGHTS = {
    "max_wind": 0.50,
    "min_pressure": 0.30,
    "closest_wind": 0.20,
}

TRACK_COMPONENT_WEIGHTS = {
    "dtw": 0.70,
    "direction": 0.20,
    "speed": 0.10,
}

STRICT_THRESHOLDS = {"wind_kt": 15.0, "pressure_hpa": 20.0}
RELAXED_THRESHOLDS = {"wind_kt": 25.0, "pressure_hpa": 30.0}

# Normalization scales.  Distances at or above these values contribute the
# maximum penalty for the corresponding component.
NORMALIZATION_SCALES = {
    "wind_kt": 40.0,
    "pressure_hpa": 50.0,
    "track_km": 800.0,
    "speed_kt": 30.0,
    "direction_deg": 180.0,
}

MAX_PATH_CANDIDATES = 250
MIN_TRACK_POINTS = 4
