from __future__ import annotations

import asyncio

from config import CATALOG_API, DETAIL_REQUEST_DELAY, MAX_ATTEMPTS
from core.session import FiveKaSession
from storage.database import Database


async def collect_product_details(
    session: FiveKaSession,
    db: Database,
    limit_products: int | None,
) -> None:
    processed = 0

    while limit_products is None or processed < limit_products:
        row = db.next_product_for_details()
        if row is None:
            break

        plu = row["plu"]
        sap = row["sap_code"]
        if not sap:
            db.fail_product_details(plu, "Нет магазина для PLU", False)
            continue

        url = (
            f"{CATALOG_API}/catalog/v2/stores/{sap}/products/{plu}"
            "?mode=store&include_restrict=true"
        )

        attempts = 0
        while True:
            attempts += 1
            try:
                detail = await session.fetch_json(url)
                if not isinstance(detail, dict):
                    raise RuntimeError("Product detail is not object")
                db.complete_product_details(plu, detail)
                print(f"PLU {plu}: detail OK")
                break
            except Exception as exc:
                retry = attempts < MAX_ATTEMPTS
                db.fail_product_details(plu, repr(exc), retry)
                print(f"PLU {plu}: ERROR {attempts}/{MAX_ATTEMPTS}: {exc}")
                if not retry:
                    break
                await asyncio.sleep(1.5)

        processed += 1
        await asyncio.sleep(DETAIL_REQUEST_DELAY)
