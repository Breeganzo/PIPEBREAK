"""Deterministic synthetic retail / logistics warehouse.

The schema is deliberately ordinary: orders, line items, products, customers,
shipments and FX rates. Every corruption in the catalogue is injected into this
raw layer, so realism of the *schema* matters more than realism of the values.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from pipebreak import config

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2024-09-30")

CURRENCIES = ["USD", "EUR", "GBP", "SGD", "INR"]
BASE_RATE = {"USD": 1.0, "EUR": 1.09, "GBP": 1.27, "SGD": 0.74, "INR": 0.012}
COUNTRY_CCY = {
    "US": "USD", "DE": "EUR", "FR": "EUR", "GB": "GBP",
    "SG": "SGD", "IN": "INR", "NL": "EUR",
}
CATEGORIES = ["Apparel", "Home", "Electronics", "Grocery", "Outdoor", "Beauty"]
CARRIERS = ["NORDEX", "SEALINK", "AIRBRIDGE", "METROPOST"]
SOURCES = ["web", "mobile", "partner_api"]


def _customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    countries = rng.choice(list(COUNTRY_CCY), size=n, p=[.34, .16, .11, .15, .09, .09, .06])
    signup = START - pd.to_timedelta(rng.integers(30, 1200, n), unit="D")
    return pd.DataFrame({
        "customer_id": np.arange(1, n + 1),
        "country": countries,
        "signup_date": signup.normalize(),
        "segment": rng.choice(["consumer", "smb", "enterprise"], n, p=[.7, .22, .08]),
    })


def _products(rng: np.random.Generator, n: int) -> pd.DataFrame:
    cost = np.round(rng.lognormal(2.4, 0.7, n), 2)
    return pd.DataFrame({
        "sku": [f"SKU-{i:05d}" for i in range(1, n + 1)],
        "category": rng.choice(CATEGORIES, n),
        "cost_usd": cost,
        "weight_kg": np.round(rng.gamma(2.0, 0.6, n) + 0.05, 3),
    })


def _fx(rng: np.random.Generator) -> pd.DataFrame:
    days = pd.date_range(START - pd.Timedelta(days=5), END + pd.Timedelta(days=5), freq="D")
    rows = []
    for ccy in CURRENCIES:
        base = BASE_RATE[ccy]
        walk = np.cumsum(rng.normal(0, 0.0015, len(days))) if ccy != "USD" else np.zeros(len(days))
        rows.append(pd.DataFrame({
            "currency": ccy,
            "rate_date": days,
            "rate_to_usd": np.round(base * (1 + walk), 6),
        }))
    return pd.concat(rows, ignore_index=True)


def _orders(rng: np.random.Generator, n: int, customers: pd.DataFrame) -> pd.DataFrame:
    cust = rng.integers(1, len(customers) + 1, n)
    country = customers.set_index("customer_id").loc[cust, "country"].to_numpy()
    span = int((END - START).days)
    # Mild weekly seasonality so daily aggregates are not flat.
    offsets = rng.integers(0, span, n)
    ts = START + pd.to_timedelta(offsets, unit="D") \
        + pd.to_timedelta(rng.integers(0, 86400, n), unit="s")
    return pd.DataFrame({
        "order_id": np.arange(1, n + 1),
        "customer_id": cust,
        "order_ts": ts,
        "status": rng.choice(["completed", "refunded", "cancelled"], n, p=[.88, .07, .05]),
        "currency": [COUNTRY_CCY[c] for c in country],
        "ship_country": country,
        "source_system": rng.choice(SOURCES, n, p=[.52, .34, .14]),
    })


def _order_items(rng: np.random.Generator, orders: pd.DataFrame,
                 products: pd.DataFrame) -> pd.DataFrame:
    counts = rng.integers(1, 5, len(orders))
    order_ids = np.repeat(orders["order_id"].to_numpy(), counts)
    m = len(order_ids)
    idx = rng.integers(0, len(products), m)
    cost = products["cost_usd"].to_numpy()[idx]
    markup = rng.uniform(1.25, 2.8, m)
    ccy = orders.set_index("order_id").loc[order_ids, "currency"].to_numpy()
    rate = np.array([BASE_RATE[c] for c in ccy])
    # unit_price is stated in the order's own currency.
    unit_price = np.round(cost * markup / rate, 2)
    return pd.DataFrame({
        "item_id": np.arange(1, m + 1),
        "order_id": order_ids,
        "sku": products["sku"].to_numpy()[idx],
        "quantity": rng.integers(1, 6, m),
        "unit_price": unit_price,
        "discount_pct": np.round(rng.choice([0, 0, 0, 5, 10, 15, 20], m) / 100, 3),
    })


def _shipments(rng: np.random.Generator, orders: pd.DataFrame,
               items: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    shipped = orders[orders["status"] != "cancelled"].copy()
    weight = (items.merge(products[["sku", "weight_kg"]], on="sku")
              .assign(w=lambda d: d["weight_kg"] * d["quantity"])
              .groupby("order_id")["w"].sum())
    w = shipped["order_id"].map(weight).fillna(1.0).to_numpy()
    n = len(shipped)
    dispatch = shipped["order_ts"].to_numpy() + pd.to_timedelta(rng.integers(2, 48, n), unit="h")
    transit = rng.gamma(3.0, 18.0, n) + 6
    return pd.DataFrame({
        "shipment_id": np.arange(1, n + 1),
        "order_id": shipped["order_id"].to_numpy(),
        "carrier": rng.choice(CARRIERS, n, p=[.31, .27, .18, .24]),
        "dispatch_ts": dispatch,
        "delivery_ts": dispatch + pd.to_timedelta(transit.round(), unit="h"),
        "weight_kg": np.round(w, 3),
        "cost_usd": np.round(3.5 + w * rng.uniform(0.8, 1.9, n), 2),
    })


def build(db_path: Path | None = None, n_orders: int = 6000) -> Path:
    """Create a pristine warehouse. Overwrites any existing file."""
    db_path = Path(db_path or config.CLEAN_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = np.random.default_rng(config.SEED)
    customers = _customers(rng, 1800)
    products = _products(rng, 600)
    orders = _orders(rng, n_orders, customers)
    items = _order_items(rng, orders, products)
    shipments = _shipments(rng, orders, items, products)
    fx = _fx(rng)

    con = duckdb.connect(str(db_path))
    for name, df in [
        ("raw_customers", customers), ("raw_products", products),
        ("raw_orders", orders), ("raw_order_items", items),
        ("raw_shipments", shipments), ("raw_fx_rates", fx),
    ]:
        con.register("_tmp", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _tmp")
        con.unregister("_tmp")
    con.close()
    return db_path


def copy_clean(dest: Path) -> Path:
    """Snapshot the pristine warehouse to a task-specific database file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.CLEAN_DB, dest)
    return dest
