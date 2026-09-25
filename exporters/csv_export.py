from __future__ import annotations

import csv
from pathlib import Path

from config import EXPORT_DIR
from storage.database import Database


def write_query(db: Database, filename: str, sql: str) -> int:
    path = EXPORT_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    cur = db.conn.execute(sql)
    columns = [d[0] for d in cur.description]
    count = 0

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(columns)
        for row in cur:
            writer.writerow([row[col] for col in columns])
            count += 1

    print(f"{filename}: {count} строк")
    return count


def export_all(db: Database) -> None:
    write_query(
        db,
        "stores_moscow.csv",
        """
        SELECT
            sap_code,
            address,
            work_start_time,
            work_end_time,
            has_24h_works,
            lat,
            lon,
            timezone,
            has_store_catalog,
            state
        FROM stores
        WHERE is_moscow=1
        ORDER BY address, sap_code
        """,
    )

    write_query(
        db,
        "products.csv",
        """
        SELECT
            plu,
            name,
            brand,
            country,
            uom,
            property_clarification,
            image_url,
            description,
            ingredients,
            rating_average,
            rates_count,
            details_status
        FROM products
        ORDER BY CAST(plu AS INTEGER), plu
        """,
    )

    write_query(
        db,
        "store_products.csv",
        """
        SELECT
            sp.sap_code,
            s.address AS store_address,
            sp.plu,
            p.name,
            sp.regular_price,
            sp.discount_price,
            sp.cpd_promo_price,
            sp.stock_limit,
            sp.is_available,
            sp.labels_json,
            sp.promo_json
        FROM store_products sp
        JOIN stores s ON s.sap_code=sp.sap_code
        JOIN products p ON p.plu=sp.plu
        WHERE s.is_moscow=1
        ORDER BY sp.sap_code, p.name, sp.plu
        """,
    )

    write_query(
        db,
        "offers_moscow.csv",
        """
        SELECT
            sp.sap_code,
            s.address AS store_address,
            sp.plu,
            p.name,
            sp.regular_price,
            sp.discount_price,
            sp.cpd_promo_price,
            sp.stock_limit,
            sp.is_available,
            sp.labels_json,
            sp.promo_json
        FROM store_products sp
        JOIN stores s ON s.sap_code=sp.sap_code
        JOIN products p ON p.plu=sp.plu
        WHERE s.is_moscow=1
          AND (
               sp.discount_price IS NOT NULL
            OR sp.cpd_promo_price IS NOT NULL
            OR (sp.promo_json IS NOT NULL AND sp.promo_json NOT IN ('null','{}','[]'))
            OR (sp.labels_json IS NOT NULL AND sp.labels_json NOT IN ('null','{}','[]'))
          )
        ORDER BY sp.sap_code, p.name, sp.plu
        """,
    )

    write_query(
        db,
        "public_promos.csv",
        """
        SELECT
            slug, type, title, annonce,
            active_since, active_till,
            external_link, preview
        FROM public_promos
        ORDER BY active_since, slug
        """,
    )
