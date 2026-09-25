from __future__ import annotations

import argparse
import asyncio
import json

from core.session import FiveKaSession
from exporters.csv_export import export_all
from parsers.catalog import collect_catalogs
from parsers.details import collect_product_details
from parsers.promos import collect_public_promos
from parsers.stores import collect_moscow_stores
from storage.database import Database


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="5ka Moscow parser"
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("stores", help="Собрать магазины Москвы")

    c = sub.add_parser("catalog", help="Собрать каталоги магазинов")
    c.add_argument(
        "--limit-stores",
        type=int,
        default=None,
        help="Ограничить число магазинов для теста",
    )

    d = sub.add_parser("details", help="Обогатить карточки товаров")
    d.add_argument(
        "--limit-products",
        type=int,
        default=None,
        help="Ограничить число товаров для теста",
    )

    sub.add_parser("promos", help="Собрать публичные промо")
    sub.add_parser("export", help="Экспорт CSV")
    sub.add_parser("status", help="Показать статус")
    sub.add_parser("retry-errors", help="Повторить error-задачи")

    a = sub.add_parser("pilot", help="Полный безопасный пилот")
    a.add_argument(
        "--stores",
        type=int,
        default=1,
        help="Сколько магазинов обработать в каталоге",
    )
    a.add_argument(
        "--details",
        type=int,
        default=20,
        help="Сколько карточек товаров обогатить",
    )

    return p


async def run_stores(db: Database) -> None:
    async with FiveKaSession() as session:
        await collect_moscow_stores(session, db)


async def run_catalog(db: Database, limit: int | None) -> None:
    async with FiveKaSession() as session:
        await collect_catalogs(session, db, limit)


async def run_details(db: Database, limit: int | None) -> None:
    async with FiveKaSession() as session:
        await collect_product_details(session, db, limit)


async def run_promos(db: Database) -> None:
    async with FiveKaSession() as session:
        await collect_public_promos(session, db)


async def run_pilot(db: Database, stores: int, details: int) -> None:
    async with FiveKaSession() as session:
        print("=== 1. STORES ===")
        await collect_moscow_stores(session, db)

        print()
        print("=== 2. CATALOG ===")
        await collect_catalogs(session, db, stores)

        print()
        print("=== 3. PRODUCT DETAILS ===")
        await collect_product_details(session, db, details)

        print()
        print("=== 4. PUBLIC PROMOS ===")
        await collect_public_promos(session, db)

    print()
    print("=== 5. EXPORT ===")
    export_all(db)


def main() -> int:
    args = parser().parse_args()
    db = Database()

    try:
        if args.command == "stores":
            asyncio.run(run_stores(db))
        elif args.command == "catalog":
            asyncio.run(run_catalog(db, args.limit_stores))
        elif args.command == "details":
            asyncio.run(run_details(db, args.limit_products))
        elif args.command == "promos":
            asyncio.run(run_promos(db))
        elif args.command == "export":
            export_all(db)
        elif args.command == "status":
            print(json.dumps(db.status(), ensure_ascii=False, indent=2))
        elif args.command == "retry-errors":
            print(json.dumps(db.retry_errors(), ensure_ascii=False, indent=2))
        elif args.command == "pilot":
            asyncio.run(run_pilot(db, args.stores, args.details))

        if args.command not in {"status"}:
            print()
            print("STATUS")
            print(json.dumps(db.status(), ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
