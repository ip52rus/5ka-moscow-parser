from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import DB_PATH, MOSCOW_CITY_MARKERS, MOSCOW_REGION_ID


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.conn.close()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS stores (
                sap_code TEXT PRIMARY KEY,
                tenant TEXT,
                state TEXT,
                type TEXT,
                has_24h_delivery INTEGER,
                timezone TEXT,
                city TEXT,
                lat REAL,
                lon REAL,
                work_start_time TEXT,
                work_end_time TEXT,
                address TEXT,
                has_24h_works INTEGER,
                has_store_catalog INTEGER,
                region_id INTEGER,
                delivery_cost REAL,
                minimal_items_price REAL,
                is_moscow INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_stores_moscow
            ON stores(is_moscow, state, has_store_catalog);

            CREATE TABLE IF NOT EXISTS store_cells (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                top REAL NOT NULL,
                bottom REAL NOT NULL,
                left_lon REAL NOT NULL,
                right_lon REAL NOT NULL,
                depth INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                shop_count INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                UNIQUE(top, bottom, left_lon, right_lon, depth)
            );

            CREATE INDEX IF NOT EXISTS idx_store_cells_status
            ON store_cells(status);

            CREATE TABLE IF NOT EXISTS categories (
                category_id TEXT PRIMARY KEY,
                parent_id TEXT,
                name TEXT,
                image_link TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS store_categories (
                sap_code TEXT NOT NULL,
                category_id TEXT NOT NULL,
                is_leaf INTEGER NOT NULL,
                PRIMARY KEY (sap_code, category_id)
            );

            CREATE TABLE IF NOT EXISTS store_catalog_state (
                sap_code TEXT PRIMARY KEY,
                categories_status TEXT NOT NULL DEFAULT 'pending',
                catalog_status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sap_code TEXT NOT NULL,
                category_id TEXT NOT NULL,
                price_min TEXT NOT NULL DEFAULT '',
                price_max TEXT NOT NULL DEFAULT '',
                depth INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                product_count INTEGER,
                last_error TEXT,
                UNIQUE(sap_code, category_id, price_min, price_max, depth)
            );

            CREATE INDEX IF NOT EXISTS idx_catalog_tasks_status
            ON catalog_tasks(status, sap_code);

            CREATE TABLE IF NOT EXISTS products (
                plu TEXT PRIMARY KEY,
                name TEXT,
                uom TEXT,
                property_clarification TEXT,
                image_url TEXT,
                has_age_restriction INTEGER,
                rating_average REAL,
                rates_count INTEGER,
                description TEXT,
                brand TEXT,
                country TEXT,
                ingredients TEXT,
                attributes_json TEXT,
                nutrients_json TEXT,
                details_status TEXT NOT NULL DEFAULT 'pending',
                details_error TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS store_products (
                sap_code TEXT NOT NULL,
                plu TEXT NOT NULL,
                regular_price REAL,
                discount_price REAL,
                cpd_promo_price REAL,
                stock_limit REAL,
                is_available INTEGER,
                promo_json TEXT,
                labels_json TEXT,
                badges_json TEXT,
                orange_loyalty_points REAL,
                raw_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (sap_code, plu)
            );

            CREATE INDEX IF NOT EXISTS idx_store_products_plu
            ON store_products(plu);

            CREATE INDEX IF NOT EXISTS idx_store_products_offer
            ON store_products(discount_price, cpd_promo_price);

            CREATE TABLE IF NOT EXISTS store_product_categories (
                sap_code TEXT NOT NULL,
                plu TEXT NOT NULL,
                category_id TEXT NOT NULL,
                PRIMARY KEY (sap_code, plu, category_id)
            );

            CREATE TABLE IF NOT EXISTS public_promos (
                slug TEXT PRIMARY KEY,
                type TEXT,
                title TEXT,
                annonce TEXT,
                preview TEXT,
                active_since TEXT,
                active_till TEXT,
                external_link TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    # -------------------- stores --------------------

    def initialize_moscow_cell(self, bbox: dict[str, float]) -> None:
        count = self.conn.execute(
            "SELECT COUNT(*) FROM store_cells"
        ).fetchone()[0]
        if count:
            return

        self.conn.execute(
            """
            INSERT INTO store_cells
            (top, bottom, left_lon, right_lon, depth, status)
            VALUES (?, ?, ?, ?, 0, 'pending')
            """,
            (bbox["top"], bbox["bottom"], bbox["left"], bbox["right"]),
        )
        self.conn.commit()

    def next_store_cell(self):
        return self.conn.execute(
            """
            SELECT * FROM store_cells
            WHERE status IN ('pending', 'retry')
            ORDER BY depth ASC, id ASC
            LIMIT 1
            """
        ).fetchone()

    def increment_store_cell_attempt(self, cell_id: int) -> None:
        self.conn.execute(
            "UPDATE store_cells SET attempts=attempts+1 WHERE id=?",
            (cell_id,),
        )
        self.conn.commit()

    def split_store_cell(self, cell) -> None:
        mid_lat = (cell["top"] + cell["bottom"]) / 2
        mid_lon = (cell["left_lon"] + cell["right_lon"]) / 2
        depth = cell["depth"] + 1

        children = [
            (cell["top"], mid_lat, cell["left_lon"], mid_lon),
            (cell["top"], mid_lat, mid_lon, cell["right_lon"]),
            (mid_lat, cell["bottom"], cell["left_lon"], mid_lon),
            (mid_lat, cell["bottom"], mid_lon, cell["right_lon"]),
        ]
        for top, bottom, left_lon, right_lon in children:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO store_cells
                (top, bottom, left_lon, right_lon, depth, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (top, bottom, left_lon, right_lon, depth),
            )

        self.conn.execute(
            "UPDATE store_cells SET status='split' WHERE id=?",
            (cell["id"],),
        )
        self.conn.commit()

    def finish_store_cell(self, cell_id: int, count: int, status: str = "complete") -> None:
        self.conn.execute(
            """
            UPDATE store_cells
            SET status=?, shop_count=?, last_error=NULL
            WHERE id=?
            """,
            (status, count, cell_id),
        )
        self.conn.commit()

    def fail_store_cell(self, cell_id: int, error: str, retry: bool) -> None:
        self.conn.execute(
            """
            UPDATE store_cells
            SET status=?, last_error=?
            WHERE id=?
            """,
            ("retry" if retry else "error", error[:2000], cell_id),
        )
        self.conn.commit()

    @staticmethod
    def is_moscow_shop(shop: dict[str, Any]) -> bool:
        region_id = shop.get("region_id")
        city = shop.get("city")
        if region_id == MOSCOW_REGION_ID:
            return True
        return city in MOSCOW_CITY_MARKERS and region_id != 18

    def upsert_stores(self, shops: list[dict[str, Any]]) -> tuple[int, int]:
        before_all = self.store_count(target_only=False)
        before_moscow = self.store_count(target_only=True)
        now = utc_now()

        for shop in shops:
            sap = shop.get("sap_code")
            if not sap:
                continue

            self.conn.execute(
                """
                INSERT INTO stores (
                    sap_code, tenant, state, type, has_24h_delivery,
                    timezone, city, lat, lon, work_start_time, work_end_time,
                    address, has_24h_works, has_store_catalog, region_id,
                    delivery_cost, minimal_items_price, is_moscow,
                    raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sap_code) DO UPDATE SET
                    tenant=excluded.tenant,
                    state=excluded.state,
                    type=excluded.type,
                    has_24h_delivery=excluded.has_24h_delivery,
                    timezone=excluded.timezone,
                    city=excluded.city,
                    lat=excluded.lat,
                    lon=excluded.lon,
                    work_start_time=excluded.work_start_time,
                    work_end_time=excluded.work_end_time,
                    address=excluded.address,
                    has_24h_works=excluded.has_24h_works,
                    has_store_catalog=excluded.has_store_catalog,
                    region_id=excluded.region_id,
                    delivery_cost=excluded.delivery_cost,
                    minimal_items_price=excluded.minimal_items_price,
                    is_moscow=excluded.is_moscow,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    str(sap),
                    shop.get("tenant"),
                    shop.get("state"),
                    shop.get("type"),
                    int(bool(shop.get("has_24h_delivery"))),
                    shop.get("timezone"),
                    shop.get("city"),
                    float(shop["lat"]) if shop.get("lat") not in (None, "") else None,
                    float(shop["lon"]) if shop.get("lon") not in (None, "") else None,
                    shop.get("work_start_time"),
                    shop.get("work_end_time"),
                    shop.get("address"),
                    int(bool(shop.get("has_24h_works"))),
                    int(bool(shop.get("has_store_catalog"))),
                    shop.get("region_id"),
                    shop.get("delivery_cost"),
                    shop.get("minimal_items_price"),
                    int(self.is_moscow_shop(shop)),
                    json_text(shop),
                    now,
                ),
            )

        self.conn.commit()
        return (
            self.store_count(False) - before_all,
            self.store_count(True) - before_moscow,
        )

    def store_count(self, target_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM stores"
        params: tuple[Any, ...] = ()
        if target_only:
            sql += " WHERE is_moscow=1"
        return self.conn.execute(sql, params).fetchone()[0]

    def target_store_saps(
        self,
        *,
        catalog_only: bool = False,
        limit: int | None = None,
    ) -> list[str]:
        sql = """
            SELECT sap_code FROM stores
            WHERE is_moscow=1
              AND state='active'
              AND type='shop'
        """
        if catalog_only:
            sql += " AND has_store_catalog=1"
        sql += " ORDER BY sap_code"
        if limit is not None:
            sql += " LIMIT ?"
            rows = self.conn.execute(sql, (limit,)).fetchall()
        else:
            rows = self.conn.execute(sql).fetchall()
        return [row["sap_code"] for row in rows]

    # -------------------- categories/catalog tasks --------------------

    def get_store_catalog_state(self, sap: str):
        row = self.conn.execute(
            "SELECT * FROM store_catalog_state WHERE sap_code=?",
            (sap,),
        ).fetchone()
        if row:
            return row
        self.conn.execute(
            """
            INSERT INTO store_catalog_state
            (sap_code, categories_status, catalog_status, updated_at)
            VALUES (?, 'pending', 'pending', ?)
            """,
            (sap, utc_now()),
        )
        self.conn.commit()
        return self.get_store_catalog_state(sap)

    def set_store_catalog_state(
        self,
        sap: str,
        *,
        categories_status: str | None = None,
        catalog_status: str | None = None,
        last_error: str | None = None,
    ) -> None:
        self.get_store_catalog_state(sap)
        fields = ["updated_at=?"]
        values: list[Any] = [utc_now()]
        if categories_status is not None:
            fields.append("categories_status=?")
            values.append(categories_status)
        if catalog_status is not None:
            fields.append("catalog_status=?")
            values.append(catalog_status)
        fields.append("last_error=?")
        values.append(last_error)
        values.append(sap)
        self.conn.execute(
            f"UPDATE store_catalog_state SET {', '.join(fields)} WHERE sap_code=?",
            values,
        )
        self.conn.commit()

    def upsert_category_tree(self, sap: str, tree: list[dict[str, Any]]) -> list[str]:
        now = utc_now()
        leaf_ids: list[str] = []

        def walk(nodes: Any, parent_id: str | None = None) -> None:
            if not isinstance(nodes, list):
                return

            for node in nodes:
                if not isinstance(node, dict):
                    continue
                category_id = node.get("id")
                if not category_id:
                    continue
                category_id = str(category_id)
                children = node.get("categories")
                is_leaf = not isinstance(children, list) or len(children) == 0

                self.conn.execute(
                    """
                    INSERT INTO categories (
                        category_id, parent_id, name, image_link,
                        raw_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(category_id) DO UPDATE SET
                        parent_id=excluded.parent_id,
                        name=excluded.name,
                        image_link=excluded.image_link,
                        raw_json=excluded.raw_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        category_id,
                        parent_id,
                        node.get("name"),
                        node.get("image_link"),
                        json_text(node),
                        now,
                    ),
                )

                self.conn.execute(
                    """
                    INSERT INTO store_categories (sap_code, category_id, is_leaf)
                    VALUES (?, ?, ?)
                    ON CONFLICT(sap_code, category_id) DO UPDATE SET
                        is_leaf=excluded.is_leaf
                    """,
                    (sap, category_id, int(is_leaf)),
                )

                if is_leaf:
                    leaf_ids.append(category_id)
                else:
                    walk(children, category_id)

        walk(tree)
        self.conn.commit()
        return leaf_ids

    def ensure_catalog_tasks(self, sap: str, category_ids: list[str]) -> None:
        for category_id in category_ids:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO catalog_tasks (
                    sap_code, category_id, price_min, price_max,
                    depth, status
                )
                VALUES (?, ?, '', '', 0, 'pending')
                """,
                (sap, category_id),
            )
        self.conn.commit()

    def next_catalog_task(self, sap: str):
        return self.conn.execute(
            """
            SELECT * FROM catalog_tasks
            WHERE sap_code=?
              AND status IN ('pending', 'retry')
            ORDER BY depth ASC, id ASC
            LIMIT 1
            """,
            (sap,),
        ).fetchone()

    def increment_catalog_attempt(self, task_id: int) -> None:
        self.conn.execute(
            "UPDATE catalog_tasks SET attempts=attempts+1 WHERE id=?",
            (task_id,),
        )
        self.conn.commit()

    def finish_catalog_task(
        self,
        task_id: int,
        product_count: int,
        status: str = "complete",
    ) -> None:
        self.conn.execute(
            """
            UPDATE catalog_tasks
            SET status=?, product_count=?, last_error=NULL
            WHERE id=?
            """,
            (status, product_count, task_id),
        )
        self.conn.commit()

    def fail_catalog_task(self, task_id: int, error: str, retry: bool) -> None:
        self.conn.execute(
            """
            UPDATE catalog_tasks
            SET status=?, last_error=?
            WHERE id=?
            """,
            ("retry" if retry else "error", error[:2500], task_id),
        )
        self.conn.commit()

    def split_catalog_task(
        self,
        task,
        low: Decimal,
        mid: Decimal,
        high: Decimal,
    ) -> None:
        def fmt(v: Decimal) -> str:
            return format(v.normalize(), "f")

        children = [
            (fmt(low), fmt(mid)),
            (fmt(mid), fmt(high)),
        ]
        for pmin, pmax in children:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO catalog_tasks (
                    sap_code, category_id, price_min, price_max,
                    depth, status
                )
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (
                    task["sap_code"],
                    task["category_id"],
                    pmin,
                    pmax,
                    task["depth"] + 1,
                ),
            )

        self.conn.execute(
            """
            UPDATE catalog_tasks
            SET status='split'
            WHERE id=?
            """,
            (task["id"],),
        )
        self.conn.commit()

    def unresolved_catalog_count(self, sap: str) -> int:
        return self.conn.execute(
            """
            SELECT COUNT(*) FROM catalog_tasks
            WHERE sap_code=?
              AND status IN ('pending', 'retry')
            """,
            (sap,),
        ).fetchone()[0]

    def catalog_problem_count(self, sap: str) -> int:
        return self.conn.execute(
            """
            SELECT COUNT(*) FROM catalog_tasks
            WHERE sap_code=?
              AND status IN ('error', 'saturated')
            """,
            (sap,),
        ).fetchone()[0]

    def incomplete_catalog_saps(self, limit: int | None = None) -> list[str]:
        sql = """
            SELECT s.sap_code
            FROM stores s
            LEFT JOIN store_catalog_state st
              ON st.sap_code=s.sap_code
            WHERE s.is_moscow=1
              AND s.state='active'
              AND s.type='shop'
              AND s.has_store_catalog=1
              AND COALESCE(st.catalog_status, 'pending') <> 'complete'
            ORDER BY s.sap_code
        """
        if limit is not None:
            sql += " LIMIT ?"
            rows = self.conn.execute(sql, (limit,)).fetchall()
        else:
            rows = self.conn.execute(sql).fetchall()
        return [r["sap_code"] for r in rows]

    # -------------------- products --------------------

    def upsert_products_for_store(
        self,
        sap: str,
        category_id: str,
        products: list[dict[str, Any]],
    ) -> int:
        now = utc_now()
        added = 0

        for product in products:
            plu = product.get("plu")
            if plu is None:
                continue
            plu = str(plu)

            exists = self.conn.execute(
                "SELECT 1 FROM products WHERE plu=?",
                (plu,),
            ).fetchone()
            if not exists:
                added += 1

            images = product.get("image_links") or {}
            normal = images.get("normal") if isinstance(images, dict) else None
            small = images.get("small") if isinstance(images, dict) else None
            image_url = None
            if isinstance(normal, list) and normal:
                image_url = normal[0]
            elif isinstance(small, list) and small:
                image_url = small[0]

            rating = product.get("rating") or {}
            prices = product.get("prices") or {}
            if isinstance(prices, list):
                price_map = {
                    str(x.get("placement_type")): x.get("value")
                    for x in prices
                    if isinstance(x, dict)
                }
                regular = price_map.get("regular_primary")
                discount = price_map.get("promo_primary")
                cpd = None
            else:
                regular = prices.get("regular") if isinstance(prices, dict) else None
                discount = prices.get("discount") if isinstance(prices, dict) else None
                cpd = prices.get("cpd_promo_price") if isinstance(prices, dict) else None

            def fnum(value: Any) -> float | None:
                if value in (None, ""):
                    return None
                try:
                    return float(value)
                except Exception:
                    return None

            self.conn.execute(
                """
                INSERT INTO products (
                    plu, name, uom, property_clarification, image_url,
                    has_age_restriction, rating_average, rates_count,
                    raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plu) DO UPDATE SET
                    name=excluded.name,
                    uom=excluded.uom,
                    property_clarification=excluded.property_clarification,
                    image_url=COALESCE(excluded.image_url, products.image_url),
                    has_age_restriction=excluded.has_age_restriction,
                    rating_average=excluded.rating_average,
                    rates_count=excluded.rates_count,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    plu,
                    product.get("name"),
                    product.get("uom"),
                    product.get("property_clarification"),
                    image_url,
                    int(bool(product.get("has_age_restriction"))),
                    rating.get("rating_average") if isinstance(rating, dict) else None,
                    rating.get("rates_count") if isinstance(rating, dict) else None,
                    json_text(product),
                    now,
                ),
            )

            self.conn.execute(
                """
                INSERT INTO store_products (
                    sap_code, plu, regular_price, discount_price,
                    cpd_promo_price, stock_limit, is_available,
                    promo_json, labels_json, badges_json,
                    orange_loyalty_points, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sap_code, plu) DO UPDATE SET
                    regular_price=excluded.regular_price,
                    discount_price=excluded.discount_price,
                    cpd_promo_price=excluded.cpd_promo_price,
                    stock_limit=excluded.stock_limit,
                    is_available=excluded.is_available,
                    promo_json=excluded.promo_json,
                    labels_json=excluded.labels_json,
                    badges_json=excluded.badges_json,
                    orange_loyalty_points=excluded.orange_loyalty_points,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    sap,
                    plu,
                    fnum(regular),
                    fnum(discount),
                    fnum(cpd),
                    fnum(product.get("stock_limit")),
                    int(bool(product.get("is_available"))),
                    json_text(product.get("promo")),
                    json_text(product.get("labels")),
                    json_text(product.get("badges")),
                    fnum(product.get("orange_loyalty_points")),
                    json_text(product),
                    now,
                ),
            )

            self.conn.execute(
                """
                INSERT OR IGNORE INTO store_product_categories
                (sap_code, plu, category_id)
                VALUES (?, ?, ?)
                """,
                (sap, plu, category_id),
            )

        self.conn.commit()
        return added

    def next_product_for_details(self):
        return self.conn.execute(
            """
            SELECT p.plu,
                   (
                       SELECT sp.sap_code
                       FROM store_products sp
                       JOIN stores s ON s.sap_code=sp.sap_code
                       WHERE sp.plu=p.plu
                         AND s.is_moscow=1
                       LIMIT 1
                   ) AS sap_code
            FROM products p
            WHERE p.details_status IN ('pending', 'retry')
            ORDER BY CAST(p.plu AS INTEGER), p.plu
            LIMIT 1
            """
        ).fetchone()

    def complete_product_details(self, plu: str, detail: dict[str, Any]) -> None:
        attributes = detail.get("attributes") or []
        brand = None
        country = None
        if isinstance(attributes, list):
            for item in attributes:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").lower()
                if name == "бренд":
                    brand = item.get("value")
                elif name == "страна производства":
                    country = item.get("value")

        self.conn.execute(
            """
            UPDATE products
            SET description=?,
                brand=?,
                country=?,
                ingredients=?,
                attributes_json=?,
                nutrients_json=?,
                details_status='complete',
                details_error=NULL,
                raw_json=?,
                updated_at=?
            WHERE plu=?
            """,
            (
                detail.get("description"),
                brand,
                country,
                detail.get("ingredients"),
                json_text(detail.get("attributes")),
                json_text(detail.get("nutrients")),
                json_text(detail),
                utc_now(),
                plu,
            ),
        )
        self.conn.commit()

    def fail_product_details(self, plu: str, error: str, retry: bool) -> None:
        self.conn.execute(
            """
            UPDATE products
            SET details_status=?,
                details_error=?,
                updated_at=?
            WHERE plu=?
            """,
            ("retry" if retry else "error", error[:2000], utc_now(), plu),
        )
        self.conn.commit()

    # -------------------- public promos --------------------

    def replace_public_promos(self, offers: list[dict[str, Any]]) -> None:
        now = utc_now()
        for offer in offers:
            slug = offer.get("slug")
            if not slug:
                continue
            self.conn.execute(
                """
                INSERT INTO public_promos (
                    slug, type, title, annonce, preview,
                    active_since, active_till, external_link,
                    raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    type=excluded.type,
                    title=excluded.title,
                    annonce=excluded.annonce,
                    preview=excluded.preview,
                    active_since=excluded.active_since,
                    active_till=excluded.active_till,
                    external_link=excluded.external_link,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    str(slug),
                    offer.get("type"),
                    offer.get("title"),
                    offer.get("annonce"),
                    offer.get("preview"),
                    offer.get("active_since"),
                    offer.get("active_till"),
                    offer.get("external_link"),
                    json_text(offer),
                    now,
                ),
            )
        self.conn.commit()

    # -------------------- status --------------------

    def status(self) -> dict[str, Any]:
        def status_counts(table: str) -> dict[str, int]:
            rows = self.conn.execute(
                f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status"
            ).fetchall()
            return {r["status"]: r["n"] for r in rows}

        store_cell_status = status_counts("store_cells")
        catalog_task_status = status_counts("catalog_tasks")

        catalog_states = self.conn.execute(
            """
            SELECT catalog_status, COUNT(*) AS n
            FROM store_catalog_state
            GROUP BY catalog_status
            """
        ).fetchall()

        return {
            "stores_discovered_bbox": self.store_count(False),
            "stores_moscow": self.store_count(True),
            "stores_moscow_catalog": self.conn.execute(
                """
                SELECT COUNT(*) FROM stores
                WHERE is_moscow=1 AND state='active'
                  AND type='shop' AND has_store_catalog=1
                """
            ).fetchone()[0],
            "store_cells": store_cell_status,
            "categories": self.conn.execute(
                "SELECT COUNT(*) FROM categories"
            ).fetchone()[0],
            "products_unique": self.conn.execute(
                "SELECT COUNT(*) FROM products"
            ).fetchone()[0],
            "store_products": self.conn.execute(
                "SELECT COUNT(*) FROM store_products"
            ).fetchone()[0],
            "store_offers": self.conn.execute(
                """
                SELECT COUNT(*) FROM store_products
                WHERE discount_price IS NOT NULL
                   OR cpd_promo_price IS NOT NULL
                   OR (promo_json IS NOT NULL AND promo_json NOT IN ('null', '{}', '[]'))
                   OR (labels_json IS NOT NULL AND labels_json NOT IN ('null', '{}', '[]'))
                """
            ).fetchone()[0],
            "catalog_tasks": catalog_task_status,
            "catalog_store_states": {
                r["catalog_status"]: r["n"] for r in catalog_states
            },
            "product_details_complete": self.conn.execute(
                "SELECT COUNT(*) FROM products WHERE details_status='complete'"
            ).fetchone()[0],
            "public_promos": self.conn.execute(
                "SELECT COUNT(*) FROM public_promos"
            ).fetchone()[0],
        }

    def retry_errors(self) -> dict[str, int]:
        c1 = self.conn.execute(
            "UPDATE store_cells SET status='retry' WHERE status='error'"
        ).rowcount
        c2 = self.conn.execute(
            "UPDATE catalog_tasks SET status='retry' WHERE status='error'"
        ).rowcount
        c3 = self.conn.execute(
            """
            UPDATE products
            SET details_status='retry'
            WHERE details_status='error'
            """
        ).rowcount
        self.conn.commit()
        return {"store_cells": c1, "catalog_tasks": c2, "product_details": c3}
