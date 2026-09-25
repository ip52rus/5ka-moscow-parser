from __future__ import annotations

from config import PUBLIC_PROMOS_URL
from core.session import FiveKaSession
from storage.database import Database


async def collect_public_promos(
    session: FiveKaSession,
    db: Database,
) -> None:
    payload = await session.fetch_json(
        PUBLIC_PROMOS_URL,
        use_5d_headers=False,
        credentials=False,
    )
    offers = payload.get("offers", []) if isinstance(payload, dict) else []
    if not isinstance(offers, list):
        raise RuntimeError("promo offers[] отсутствует")
    db.replace_public_promos(offers)
    print(f"Публичных промо сохранено: {len(offers)}")
