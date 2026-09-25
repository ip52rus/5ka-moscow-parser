from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from urllib.parse import urlencode

from config import (
    CATALOG_API,
    CATALOG_LIMIT,
    CATALOG_MAX_SPLIT_DEPTH,
    CATALOG_MIN_PRICE_SPAN,
    CATALOG_REQUEST_DELAY,
    MAX_ATTEMPTS,
)
from core.session import FiveKaSession
from storage.database import Database


def categories_url(sap: str) -> str:
    return (
        f"{CATALOG_API}/catalog/v2/stores/{sap}/categories"
        "?mode=store&include_restrict=true&include_subcategories=1"
    )


def products_url(task) -> str:
    params = {
        "mode": "store",
        "limit": CATALOG_LIMIT,
        "include_restrict": "true",
    }
    if task["price_min"]:
        params["price_min"] = task["price_min"]
    if task["price_max"]:
        params["price_max"] = task["price_max"]

    return (
        f"{CATALOG_API}/catalog/v2/stores/{task['sap_code']}/categories/"
        f"{task['category_id']}/products?"
        + urlencode(params)
    )


def price_range_from_payload(payload: dict) -> tuple[Decimal, Decimal] | None:
    filters = payload.get("filters")
    if not isinstance(filters, list):
        return None

    for item in filters:
        if not isinstance(item, dict) or item.get("field_name") != "price":
            continue
        try:
            low = Decimal(str(item.get("range_min_val")))
            high = Decimal(str(item.get("range_max_val")))
            return low, high
        except (InvalidOperation, TypeError):
            return None

    return None


def split_range(task, payload: dict):
    if task["depth"] >= CATALOG_MAX_SPLIT_DEPTH:
        return None

    observed = price_range_from_payload(payload)
    try:
        low = Decimal(task["price_min"]) if task["price_min"] else observed[0]
        high = Decimal(task["price_max"]) if task["price_max"] else observed[1]
    except Exception:
        return None

    if high - low <= Decimal(str(CATALOG_MIN_PRICE_SPAN)):
        return None

    mid = ((low + high) / Decimal("2")).quantize(
        Decimal("0.01"),
        rounding=ROUND_FLOOR,
    )
    if mid <= low or mid >= high:
        return None

    return low, mid, high


async def prepare_store(session: FiveKaSession, db: Database, sap: str) -> None:
    state = db.get_store_catalog_state(sap)
    if state["categories_status"] == "complete":
        return

    try:
        tree = await session.fetch_json(categories_url(sap))
        if not isinstance(tree, list):
            raise RuntimeError("Дерево категорий не является списком")

        leaf_ids = db.upsert_category_tree(sap, tree)
        if not leaf_ids:
            raise RuntimeError("Не найдено листовых категорий")

        db.ensure_catalog_tasks(sap, leaf_ids)
        db.set_store_catalog_state(
            sap,
            categories_status="complete",
            catalog_status="running",
        )
        print(f"  categories={len(leaf_ids)}")
    except Exception as exc:
        db.set_store_catalog_state(
            sap,
            categories_status="error",
            catalog_status="error",
            last_error=repr(exc),
        )
        raise


async def collect_store_catalog(
    session: FiveKaSession,
    db: Database,
    sap: str,
) -> None:
    await prepare_store(session, db, sap)

    while True:
        task = db.next_catalog_task(sap)
        if task is None:
            break

        db.increment_catalog_attempt(task["id"])

        try:
            payload = await session.fetch_json(products_url(task))
            products = payload.get("products", []) if isinstance(payload, dict) else []
            if not isinstance(products, list):
                raise RuntimeError("products[] отсутствует")

            new_unique = db.upsert_products_for_store(
                sap,
                task["category_id"],
                products,
            )

            print(
                f"  task={task['id']:<6} cat={task['category_id']} "
                f"range={task['price_min'] or '*'}..{task['price_max'] or '*'} "
                f"products={len(products):<3} new_unique={new_unique}"
            )

            if len(products) >= CATALOG_LIMIT:
                split = split_range(task, payload)
                if split is None:
                    db.finish_catalog_task(
                        task["id"],
                        len(products),
                        "saturated",
                    )
                    print("    ВНИМАНИЕ: категория saturated=499 и не разделилась.")
                else:
                    db.split_catalog_task(task, *split)
            else:
                db.finish_catalog_task(task["id"], len(products), "complete")

        except Exception as exc:
            attempt = task["attempts"] + 1
            retry = attempt < MAX_ATTEMPTS
            db.fail_catalog_task(task["id"], repr(exc), retry)
            print(
                f"  task={task['id']} ERROR {attempt}/{MAX_ATTEMPTS}: {exc}"
            )
            await asyncio.sleep(1.5)
            continue

        await asyncio.sleep(CATALOG_REQUEST_DELAY)

    if db.unresolved_catalog_count(sap):
        db.set_store_catalog_state(sap, catalog_status="running")
    elif db.catalog_problem_count(sap):
        db.set_store_catalog_state(
            sap,
            catalog_status="incomplete",
            last_error="Есть error/saturated catalog tasks",
        )
    else:
        db.set_store_catalog_state(sap, catalog_status="complete")


async def collect_catalogs(
    session: FiveKaSession,
    db: Database,
    limit_stores: int | None,
) -> None:
    saps = db.incomplete_catalog_saps(limit_stores)
    print(f"Магазинов в очереди каталога: {len(saps)}")

    for index, sap in enumerate(saps, start=1):
        print()
        print(f"[{index}/{len(saps)}] SAP {sap}")
        try:
            await collect_store_catalog(session, db, sap)
        except Exception as exc:
            print(f"  STORE ERROR: {exc}")
            await asyncio.sleep(2.0)
