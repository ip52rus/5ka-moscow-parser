from __future__ import annotations

import argparse
import csv
import html
import json
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "5ka_moscow.sqlite"
EXPORT_DIR = BASE_DIR / "exports"


def parse_args():
    p = argparse.ArgumentParser(
        description="Client-ready report for one 5ka store"
    )
    p.add_argument(
        "--sap",
        required=True,
        help="SAP-код магазина, например 300R",
    )
    return p.parse_args()


def fmt_price(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):,.2f} ₽".replace(",", " ")
    except Exception:
        return str(value)


def fmt_bool(value: Any) -> str:
    return "Да" if value else "Нет"


def clean_json(value: Any) -> Any:
    if value in (None, "", "null", "{}", "[]"):
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def best_offer_price(row: sqlite3.Row) -> float | None:
    candidates = []
    for key in ("discount_price", "cpd_promo_price"):
        value = row[key]
        if value is None:
            continue
        try:
            value = float(value)
        except Exception:
            continue
        if value > 0:
            candidates.append(value)

    if not candidates:
        return None
    return min(candidates)


def is_offer(row: sqlite3.Row) -> bool:
    regular = row["regular_price"]
    promo_price = best_offer_price(row)
    promo_data = clean_json(row["promo_json"])

    if promo_price is not None:
        if regular is None:
            return True
        try:
            if promo_price < float(regular):
                return True
        except Exception:
            return True

    return promo_data is not None


def discount_percent(regular: Any, promo: Any) -> str:
    if regular in (None, "") or promo in (None, ""):
        return ""
    try:
        regular = float(regular)
        promo = float(promo)
        if regular <= 0 or promo >= regular:
            return ""
        return f"{round((regular - promo) / regular * 100)}%"
    except Exception:
        return ""


def promo_text(value: Any, conn: sqlite3.Connection | None = None) -> str:
    data = clean_json(value)
    if data is None:
        return ""

    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if parsed != data:
                return promo_text(parsed, conn)
        except Exception:
            pass
        return data

    if isinstance(data, dict):
        # 1) Threshold price / rebate.
        rebate = data.get("rebate")
        if isinstance(rebate, dict):
            units = rebate.get("units_to_activate")
            if units not in (None, ""):
                try:
                    units = int(units)
                except Exception:
                    pass
                return f"Цена предложения действует при покупке от {units} шт."

        # 2) N+M mechanic.
        n_plus_m = data.get("n_plus_m")
        if isinstance(n_plus_m, dict):
            n = n_plus_m.get("units_to_activate")
            m = n_plus_m.get("units_to_add")
            if n not in (None, "") and m not in (None, ""):
                try:
                    n = int(n)
                except Exception:
                    pass
                try:
                    m = int(m)
                except Exception:
                    pass

                if m == 1:
                    return f"{n}+1: при покупке {n} шт. ещё 1 шт. бесплатно"
                return (
                    f"{n}+{m}: при покупке {n} шт. "
                    f"ещё {m} шт. бесплатно"
                )

        # 3) Econombo.
        econombo = data.get("econombo")
        if isinstance(econombo, dict):
            ids = econombo.get("include_categories")
            names: list[str] = []

            if conn is not None and isinstance(ids, list) and ids:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"""
                    SELECT category_id, name
                    FROM categories
                    WHERE category_id IN ({placeholders})
                    """,
                    [str(x) for x in ids],
                ).fetchall()

                by_id = {
                    str(row["category_id"]): row["name"]
                    for row in rows
                    if row["name"]
                }
                names = [
                    by_id[str(category_id)]
                    for category_id in ids
                    if str(category_id) in by_id
                ]

            # Do not expose backend category IDs if we cannot resolve them.
            if names:
                unique_names = list(dict.fromkeys(names))
                return (
                    "Участвует в комбо-предложении с товарами из категорий: "
                    + ", ".join(unique_names)
                )

            return "Участвует в комбо-предложении"

        # If the API later adds a readable textual promo, use it.
        for key in ("name", "title", "description", "label", "text", "message"):
            v = data.get(key)
            if v:
                return str(v)

        # Never expose unknown technical JSON to a client.
        return "Специальное предложение"

    if isinstance(data, list):
        texts = []
        for item in data:
            if item in (None, "", {}, []):
                continue
            text = promo_text(item, conn)
            if text:
                texts.append(text)
        return " · ".join(dict.fromkeys(texts))

    return "Специальное предложение"


def write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)


def html_escape(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def main() -> int:
    args = parse_args()
    sap = args.sap.strip()

    if not DB_PATH.exists():
        raise SystemExit(f"База не найдена: {DB_PATH}")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        store = conn.execute(
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
                state
            FROM stores
            WHERE sap_code=?
            """,
            (sap,),
        ).fetchone()

        if store is None:
            raise SystemExit(f"Магазин SAP {sap} не найден в базе.")

        catalog = conn.execute(
            """
            SELECT
                sp.plu,
                p.name,
                p.uom,
                p.property_clarification,
                sp.regular_price,
                sp.discount_price,
                sp.cpd_promo_price,
                sp.is_available,
                sp.stock_limit,
                sp.promo_json
            FROM store_products sp
            JOIN products p ON p.plu=sp.plu
            WHERE sp.sap_code=?
            ORDER BY p.name COLLATE NOCASE, sp.plu
            """,
            (sap,),
        ).fetchall()

        offers = [row for row in catalog if is_offer(row)]

        # ---------------- CSV: catalog ----------------
        catalog_csv_rows = []
        for row in catalog:
            promo_price = best_offer_price(row)
            catalog_csv_rows.append([
                row["plu"],
                row["name"],
                row["uom"],
                row["property_clarification"],
                row["regular_price"],
                promo_price,
                row["is_available"],
                row["stock_limit"],
            ])

        catalog_csv = EXPORT_DIR / f"catalog_{sap}.csv"
        write_csv(
            catalog_csv,
            [
                "PLU",
                "Товар",
                "Ед. изм.",
                "Уточнение",
                "Обычная цена",
                "Цена предложения",
                "Доступен",
                "Остаток / лимит",
            ],
            catalog_csv_rows,
        )

        # ---------------- CSV: offers ----------------
        offers_csv_rows = []
        for row in offers:
            promo_price = best_offer_price(row)
            offers_csv_rows.append([
                row["plu"],
                row["name"],
                row["regular_price"],
                promo_price,
                discount_percent(row["regular_price"], promo_price),
                promo_text(row["promo_json"], conn),
                row["is_available"],
            ])

        offers_csv = EXPORT_DIR / f"offers_{sap}.csv"
        write_csv(
            offers_csv,
            [
                "PLU",
                "Товар",
                "Обычная цена",
                "Цена предложения",
                "Скидка",
                "Условия / промо",
                "Доступен",
            ],
            offers_csv_rows,
        )

        # ---------------- HTML pages ----------------
        if store["has_24h_works"]:
            hours = "Круглосуточно"
        else:
            start = store["work_start_time"] or "—"
            end = store["work_end_time"] or "—"
            # Make 00:15:00 look cleaner in a client-facing report.
            start = str(start)[:5] if len(str(start)) >= 5 else str(start)
            end = str(end)[:5] if len(str(end)) >= 5 else str(end)
            hours = f"{start}–{end}"

        common_css = """
<style>
    * { box-sizing: border-box; }
    body {
        margin: 0;
        background: #f4f6f8;
        color: #20252b;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    }
    .page {
        max-width: 1240px;
        margin: 0 auto;
        padding: 28px 22px 60px;
    }
    .topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        margin-bottom: 18px;
        flex-wrap: wrap;
    }
    .brand {
        font-weight: 800;
        font-size: 20px;
        text-decoration: none;
        color: #20252b;
    }
    .nav {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
    }
    .nav a, .button {
        display: inline-block;
        text-decoration: none;
        padding: 10px 15px;
        border-radius: 10px;
        border: 1px solid #d9dfe5;
        background: white;
        color: #20252b;
        font-size: 14px;
        font-weight: 700;
    }
    .nav a.active {
        background: #20252b;
        color: white;
        border-color: #20252b;
    }
    .hero, section {
        background: white;
        border-radius: 18px;
        padding: 28px 30px;
        box-shadow: 0 4px 18px rgba(0,0,0,.07);
        margin-bottom: 22px;
    }
    h1 {
        margin: 0 0 8px;
        font-size: 30px;
    }
    h2 {
        margin: 0 0 6px;
        font-size: 23px;
    }
    .subtitle, .section-note {
        color: #6b7280;
        font-size: 14px;
    }
    .subtitle { margin-bottom: 24px; }
    .section-note { margin: 0 0 18px; }
    .store-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
    }
    .card {
        background: #f8fafc;
        border: 1px solid #e7ebef;
        border-radius: 12px;
        padding: 15px 16px;
    }
    .label {
        color: #7a828b;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: .04em;
        margin-bottom: 5px;
    }
    .value {
        font-size: 16px;
        font-weight: 600;
    }
    .stats {
        display: flex;
        gap: 12px;
        margin-top: 18px;
        flex-wrap: wrap;
    }
    .stat {
        background: #eef2f6;
        border-radius: 999px;
        padding: 8px 13px;
        font-size: 13px;
        font-weight: 600;
    }
    .actions {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
        margin-top: 18px;
    }
    .action-card {
        display: block;
        text-decoration: none;
        color: #20252b;
        background: #f8fafc;
        border: 1px solid #e2e7ec;
        border-radius: 14px;
        padding: 20px;
    }
    .action-card strong {
        display: block;
        font-size: 20px;
        margin-bottom: 6px;
    }
    .action-card span {
        color: #69727c;
        font-size: 14px;
    }
    .table-wrap {
        overflow-x: auto;
        border: 1px solid #e5e9ed;
        border-radius: 12px;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        background: white;
    }
    th {
        position: sticky;
        top: 0;
        background: #f4f6f8;
        text-align: left;
        padding: 11px 12px;
        border-bottom: 1px solid #dce1e6;
        white-space: nowrap;
    }
    td {
        padding: 10px 12px;
        border-bottom: 1px solid #eef1f3;
        vertical-align: top;
    }
    tbody tr:hover { background: #fafbfc; }
    .offer-price { font-weight: 700; color: #08783e; }
    .discount { font-weight: 700; }
    .footer {
        text-align: center;
        color: #8a929a;
        font-size: 12px;
        margin-top: 28px;
    }
    @media (max-width: 760px) {
        .store-grid, .actions { grid-template-columns: 1fr; }
        .page { padding: 16px 10px 40px; }
        .hero, section { border-radius: 12px; padding: 18px; }
    }
    @media print {
        body { background: white; }
        .page { max-width: none; padding: 0; }
        .hero, section { box-shadow: none; border: 1px solid #ddd; }
        th { position: static; }
        .nav { display: none; }
    }
</style>
"""

        nav_home = f"""
<div class="topbar">
    <a class="brand" href="report_{html_escape(sap)}.html">Пятёрочка — SAP {html_escape(sap)}</a>
    <div class="nav">
        <a href="catalog_{html_escape(sap)}.html">Каталог товаров</a>
        <a href="offers_{html_escape(sap)}.html">Предложения магазина</a>
    </div>
</div>
"""

        store_header = f"""
<div class="hero">
    <h1>Пятёрочка — SAP {html_escape(sap)}</h1>
    <div class="subtitle">Данные конкретного магазина</div>

    <div class="store-grid">
        <div class="card">
            <div class="label">Адрес</div>
            <div class="value">{html_escape(store['address'])}</div>
        </div>
        <div class="card">
            <div class="label">Режим работы</div>
            <div class="value">{html_escape(hours)}</div>
        </div>
        <div class="card">
            <div class="label">Статус</div>
            <div class="value">{html_escape(store['state'])}</div>
        </div>
    </div>

    <div class="stats">
        <div class="stat">Товаров в каталоге: {len(catalog):,}</div>
        <div class="stat">Предложений: {len(offers):,}</div>
    </div>
</div>
"""

        home_html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Пятёрочка — SAP {html_escape(sap)}</title>
{common_css}
</head>
<body>
<div class="page">
    {nav_home}
    <div class="hero">
        <h1>Пятёрочка — SAP {html_escape(sap)}</h1>
        <div class="subtitle">Данные конкретного магазина</div>

        <div class="store-grid">
            <div class="card">
                <div class="label">Адрес</div>
                <div class="value">{html_escape(store['address'])}</div>
            </div>
            <div class="card">
                <div class="label">Режим работы</div>
                <div class="value">{html_escape(hours)}</div>
            </div>
            <div class="card">
                <div class="label">Статус</div>
                <div class="value">{html_escape(store['state'])}</div>
            </div>
        </div>

        <div class="actions">
            <a class="action-card" href="catalog_{html_escape(sap)}.html">
                <strong>Каталог товаров</strong>
                <span>{len(catalog):,} позиций</span>
            </a>
            <a class="action-card" href="offers_{html_escape(sap)}.html">
                <strong>Предложения магазина</strong>
                <span>{len(offers):,} позиций</span>
            </a>
        </div>
    </div>
    <div class="footer">Данные магазина Пятёрочка SAP {html_escape(sap)}.</div>
</div>
</body>
</html>
"""

        catalog_rows_html = []
        for row in catalog:
            promo_price = best_offer_price(row)
            has_discount = (
                promo_price is not None
                and row["regular_price"] is not None
                and promo_price < float(row["regular_price"])
            )
            catalog_rows_html.append(
                "<tr>"
                f"<td>{html_escape(row['plu'])}</td>"
                f"<td>{html_escape(row['name'])}</td>"
                f"<td>{html_escape(fmt_price(row['regular_price']))}</td>"
                f"<td class=\"{'offer-price' if has_discount else ''}\">{html_escape(fmt_price(promo_price))}</td>"
                f"<td>{'Да' if row['is_available'] else 'Нет'}</td>"
                "</tr>"
            )

        catalog_html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Каталог товаров — SAP {html_escape(sap)}</title>
{common_css}
</head>
<body>
<div class="page">
    <div class="topbar">
        <a class="brand" href="report_{html_escape(sap)}.html">Пятёрочка — SAP {html_escape(sap)}</a>
        <div class="nav">
            <a class="active" href="catalog_{html_escape(sap)}.html">Каталог товаров</a>
            <a href="offers_{html_escape(sap)}.html">Предложения магазина</a>
        </div>
    </div>

    {store_header}

    <section>
        <h1>Каталог товаров</h1>
        <p class="section-note">Магазин SAP {html_escape(sap)} — {len(catalog):,} позиций.</p>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>PLU</th>
                        <th>Товар</th>
                        <th>Обычная цена</th>
                        <th>Цена предложения</th>
                        <th>Доступен</th>
                    </tr>
                </thead>
                <tbody>{''.join(catalog_rows_html)}</tbody>
            </table>
        </div>
    </section>
</div>
</body>
</html>
"""

        offer_rows_html = []
        for row in offers:
            promo_price = best_offer_price(row)
            offer_rows_html.append(
                "<tr>"
                f"<td>{html_escape(row['plu'])}</td>"
                f"<td>{html_escape(row['name'])}</td>"
                f"<td>{html_escape(fmt_price(row['regular_price']))}</td>"
                f"<td class=\"offer-price\">{html_escape(fmt_price(promo_price))}</td>"
                f"<td class=\"discount\">{html_escape(discount_percent(row['regular_price'], promo_price))}</td>"
                f"<td>{html_escape(promo_text(row['promo_json']))}</td>"
                "</tr>"
            )

        offers_html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Предложения магазина — SAP {html_escape(sap)}</title>
{common_css}
</head>
<body>
<div class="page">
    <div class="topbar">
        <a class="brand" href="report_{html_escape(sap)}.html">Пятёрочка — SAP {html_escape(sap)}</a>
        <div class="nav">
            <a href="catalog_{html_escape(sap)}.html">Каталог товаров</a>
            <a class="active" href="offers_{html_escape(sap)}.html">Предложения магазина</a>
        </div>
    </div>

    {store_header}

    <section>
        <h1>Предложения магазина</h1>
        <p class="section-note">Товары со сниженной ценой и/или промо-условиями — {len(offers):,} позиций.</p>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>PLU</th>
                        <th>Товар</th>
                        <th>Обычная цена</th>
                        <th>Цена предложения</th>
                        <th>Скидка</th>
                        <th>Условия / промо</th>
                    </tr>
                </thead>
                <tbody>{''.join(offer_rows_html)}</tbody>
            </table>
        </div>
    </section>
</div>
</body>
</html>
"""

        report_path = EXPORT_DIR / f"report_{sap}.html"
        catalog_html_path = EXPORT_DIR / f"catalog_{sap}.html"
        offers_html_path = EXPORT_DIR / f"offers_{sap}.html"

        report_path.write_text(home_html, encoding="utf-8")
        catalog_html_path.write_text(catalog_html, encoding="utf-8")
        offers_html_path.write_text(offers_html, encoding="utf-8")

        print("=" * 72)
        print(f"Магазин: SAP {sap}")
        print(f"Адрес: {store['address']}")
        print(f"Режим работы: {hours}")
        print(f"Товаров: {len(catalog)}")
        print(f"Предложений: {len(offers)}")
        print("=" * 72)
        print(f"Главная HTML: {report_path}")
        print(f"Каталог HTML: {catalog_html_path}")
        print(f"Предложения HTML: {offers_html_path}")
        print(f"Каталог CSV: {catalog_csv}")
        print(f"Предложения CSV: {offers_csv}")

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
