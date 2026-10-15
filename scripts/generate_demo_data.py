"""Generate the small synthetic e-commerce demo dataset in ``data/demo``.

The data is entirely fake and produced from a fixed random seed, so running the
script twice yields identical files. A handful of data-quality problems
(missing emails, duplicated order lines, extreme quantities, missing
satisfaction scores) are injected on purpose so that the ``profile`` tool has
something real to find.

Usage:
    python scripts/generate_demo_data.py [--out data/demo]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_CUSTOMERS = 400
N_PRODUCTS = 48
N_ORDERS = 2500
N_TICKETS = 600

COUNTRIES = ["US", "GB", "DE", "PK", "AE", "CA", "FR", "NL"]
COUNTRY_WEIGHTS = [0.30, 0.14, 0.12, 0.12, 0.08, 0.09, 0.08, 0.07]
SEGMENTS = ["consumer", "small_business", "enterprise"]
CATEGORIES = {
    "Electronics": (40.0, 900.0),
    "Home": (10.0, 250.0),
    "Books": (5.0, 60.0),
    "Sports": (15.0, 300.0),
    "Beauty": (5.0, 120.0),
    "Toys": (8.0, 150.0),
}
FIRST = ["Ava", "Liam", "Noah", "Emma", "Omar", "Sara", "Ali", "Mia", "Zoe", "Leo", "Hina", "Yusuf"]
LAST = ["Smith", "Khan", "Müller", "Brown", "Ahmed", "Dubois", "Jansen", "Lee", "Garcia", "Raza"]


def make_customers(rng: np.random.Generator) -> pd.DataFrame:
    ids = np.arange(1, N_CUSTOMERS + 1)
    names = [f"{rng.choice(FIRST)} {rng.choice(LAST)}" for _ in ids]
    emails: list[str | None] = [f"customer{i}@example.com" for i in ids]
    for idx in rng.choice(N_CUSTOMERS, size=14, replace=False):
        emails[idx] = None  # injected: missing emails
    signup = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 540, N_CUSTOMERS), "D")
    return pd.DataFrame(
        {
            "customer_id": ids,
            "name": names,
            "email": emails,
            "country": rng.choice(COUNTRIES, N_CUSTOMERS, p=COUNTRY_WEIGHTS),
            "segment": rng.choice(SEGMENTS, N_CUSTOMERS, p=[0.7, 0.22, 0.08]),
            "signup_date": signup.date,
        }
    )


def make_products(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    cats = list(CATEGORIES)
    for pid in range(1, N_PRODUCTS + 1):
        cat = cats[(pid - 1) % len(cats)]
        low, high = CATEGORIES[cat]
        price = round(float(rng.uniform(low, high)), 2)
        rows.append(
            {
                "product_id": pid,
                "product_name": f"{cat} item {pid:03d}",
                "category": cat,
                "unit_price": price,
                "unit_cost": round(price * float(rng.uniform(0.45, 0.75)), 2),
            }
        )
    return pd.DataFrame(rows)


def make_orders(rng: np.random.Generator) -> pd.DataFrame:
    # Mild upward trend plus a Q4 bump so monthly charts have a visible shape.
    days = np.arange(0, 640)
    weights = (
        1.0 + days / 640 + 0.6 * np.isin((pd.Timestamp("2024-03-01") + pd.to_timedelta(days, "D")).month, [11, 12])
    )
    weights = weights / weights.sum()
    order_days = np.sort(rng.choice(days, N_ORDERS, p=weights))
    return pd.DataFrame(
        {
            "order_id": np.arange(10001, 10001 + N_ORDERS),
            "customer_id": rng.integers(1, N_CUSTOMERS + 1, N_ORDERS),
            "order_date": pd.Timestamp("2024-03-01") + pd.to_timedelta(order_days, "D"),
            "status": rng.choice(
                ["delivered", "shipped", "cancelled", "returned"], N_ORDERS, p=[0.82, 0.08, 0.06, 0.04]
            ),
            "channel": rng.choice(["web", "mobile_app", "marketplace"], N_ORDERS, p=[0.5, 0.35, 0.15]),
        }
    )


def make_order_items(rng: np.random.Generator, orders: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    rows = []
    prices = products.set_index("product_id")["unit_price"]
    for order_id in orders["order_id"]:
        n_lines = int(rng.integers(1, 5))
        for pid in rng.choice(products["product_id"], n_lines, replace=False):
            rows.append(
                {
                    "order_id": int(order_id),
                    "product_id": int(pid),
                    "quantity": int(rng.integers(1, 4)),
                    "unit_price": float(prices[pid]),
                    "discount": float(rng.choice([0.0, 0.0, 0.0, 0.05, 0.1, 0.2])),
                }
            )
    items = pd.DataFrame(rows)
    outliers = rng.choice(len(items), 6, replace=False)
    items.loc[outliers, "quantity"] = rng.integers(60, 120, 6)  # injected: extreme quantities
    duplicates = items.sample(9, random_state=SEED)  # injected: exact duplicate lines
    return pd.concat([items, duplicates], ignore_index=True)


def make_tickets(rng: np.random.Generator) -> pd.DataFrame:
    satisfaction = rng.choice([1, 2, 3, 4, 5], N_TICKETS, p=[0.06, 0.1, 0.2, 0.34, 0.3]).astype("float")
    satisfaction[rng.choice(N_TICKETS, 75, replace=False)] = np.nan  # injected: unanswered surveys
    opened = pd.Timestamp("2024-03-01") + pd.to_timedelta(rng.integers(0, 640 * 24, N_TICKETS), "h")
    return pd.DataFrame(
        {
            "ticket_id": np.arange(1, N_TICKETS + 1),
            "customer_id": rng.integers(1, N_CUSTOMERS + 1, N_TICKETS),
            "opened_at": opened.strftime("%Y-%m-%d %H:%M:%S"),
            "topic": rng.choice(["delivery", "refund", "product_defect", "account", "other"], N_TICKETS),
            "priority": rng.choice(["low", "medium", "high"], N_TICKETS, p=[0.5, 0.35, 0.15]),
            "resolution_hours": np.round(rng.gamma(2.0, 9.0, N_TICKETS), 1),
            "satisfaction": satisfaction,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "demo")
    out: Path = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)

    customers = make_customers(rng)
    products = make_products(rng)
    orders = make_orders(rng)
    items = make_order_items(rng, orders, products)
    tickets = make_tickets(rng)

    customers.to_csv(out / "customers.csv", index=False)
    products.to_excel(out / "products.xlsx", sheet_name="products", index=False)
    orders.to_parquet(out / "orders.parquet", index=False)
    items.to_csv(out / "order_items.csv", index=False)

    db_path = out / "support.sqlite"
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as conn:
        tickets.to_sql("tickets", conn, index=False)
    conn.close()

    for path in sorted(out.iterdir()):
        print(f"{path.name:<20} {path.stat().st_size / 1024:8.1f} KiB")


if __name__ == "__main__":
    main()
