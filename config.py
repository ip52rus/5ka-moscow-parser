from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"

DB_PATH = DATA_DIR / "5ka_moscow.sqlite"
STORAGE_STATE_PATH = BASE_DIR / "5ka_storage_state.json"

MAIN_URL = "https://5ka.ru/"
CATALOG_WARMUP_URL = (
    "https://5ka.ru/catalog/"
    "molochnaya-produktsiya-i-yaytso--251C12887/"
)

CATALOG_API = "https://5d.5ka.ru/api"
SECOND_API = "https://api.5ka.ru/api"

SHOPS_URL = f"{CATALOG_API}/shops/v1/shops/"
CATEGORIES_URL = f"{CATALOG_API}/catalog/v2/stores"
PUBLIC_PROMOS_URL = (
    f"{SECOND_API}/public/v1/promo-offers/"
    "?limit=100&web_version=true"
)

# Bounding box with a margin around the entire subject of Moscow,
# including New Moscow. Final inclusion is NOT by bbox: it is filtered
# by region_id / city markers returned by 5ka.
MOSCOW_DISCOVERY_BBOX = {
    "top": 56.03,
    "bottom": 55.12,
    "left": 36.78,
    "right": 37.98,
}

# Live 5ka data observed on 26.08.2026:
# region_id=14 -> Moscow; region_id=18 -> Moscow Oblast.
MOSCOW_REGION_ID = 14
MOSCOW_CITY_MARKERS = {"msk", "Москва"}

STORE_SERVER_LIMIT = 500
STORE_MAX_DEPTH = 12
STORE_MIN_LAT_SPAN = 0.01
STORE_MIN_LON_SPAN = 0.01

CATALOG_LIMIT = 499
CATALOG_MAX_SPLIT_DEPTH = 10
CATALOG_MIN_PRICE_SPAN = 0.01

MAX_ATTEMPTS = 4

STORE_REQUEST_DELAY = 0.25
CATALOG_REQUEST_DELAY = 0.20
DETAIL_REQUEST_DELAY = 0.18

HEADER_KEYS = (
    "x-app-version",
    "x-device-id",
    "x-platform",
    "x-tenant-id",
    "x-capabilities",
)
