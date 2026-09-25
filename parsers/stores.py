from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from config import (
    MAX_ATTEMPTS,
    MOSCOW_DISCOVERY_BBOX,
    SHOPS_URL,
    STORE_MAX_DEPTH,
    STORE_MIN_LAT_SPAN,
    STORE_MIN_LON_SPAN,
    STORE_REQUEST_DELAY,
    STORE_SERVER_LIMIT,
)
from core.session import FiveKaSession
from storage.database import Database


def make_url(cell) -> str:
    return SHOPS_URL + "?" + urlencode(
        {
            "top_latitude": cell["top"],
            "bottom_latitude": cell["bottom"],
            "left_longitude": cell["left_lon"],
            "right_longitude": cell["right_lon"],
        }
    )


def can_split(cell) -> bool:
    return (
        cell["depth"] < STORE_MAX_DEPTH
        and (cell["top"] - cell["bottom"]) > STORE_MIN_LAT_SPAN
        and (cell["right_lon"] - cell["left_lon"]) > STORE_MIN_LON_SPAN
    )


async def collect_moscow_stores(session: FiveKaSession, db: Database) -> None:
    db.initialize_moscow_cell(MOSCOW_DISCOVERY_BBOX)

    while True:
        cell = db.next_store_cell()
        if cell is None:
            break

        db.increment_store_cell_attempt(cell["id"])

        try:
            payload = await session.fetch_json(make_url(cell))
            shops = payload.get("shops", []) if isinstance(payload, dict) else []
            if not isinstance(shops, list):
                raise RuntimeError("shops[] отсутствует в ответе")

            new_all, new_moscow = db.upsert_stores(shops)
            count = len(shops)

            print(
                f"cell={cell['id']:<4} depth={cell['depth']:<2} "
                f"shops={count:<3} new={new_all:<3} "
                f"new_moscow={new_moscow:<3} "
                f"moscow_total={db.store_count(True)}"
            )

            if count >= STORE_SERVER_LIMIT:
                if can_split(cell):
                    db.split_store_cell(cell)
                else:
                    db.finish_store_cell(cell["id"], count, "saturated")
                    print("  ВНИМАНИЕ: saturated bbox не удалось разделить.")
            else:
                db.finish_store_cell(cell["id"], count, "complete")

        except Exception as exc:
            attempt = cell["attempts"] + 1
            retry = attempt < MAX_ATTEMPTS
            db.fail_store_cell(cell["id"], repr(exc), retry)
            print(f"cell={cell['id']} ERROR {attempt}/{MAX_ATTEMPTS}: {exc}")
            await asyncio.sleep(2.0)
            continue

        await asyncio.sleep(STORE_REQUEST_DELAY)
