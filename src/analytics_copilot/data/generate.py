"""Deterministic synthetic data for Switchback Outfitters, a fictional outdoor-gear retailer.

The data is random but not featureless. A handful of effects are planted on purpose so
that questions about the data have known, checkable answers (see docs/data.md):

  A. September 2026: camping products stock out in every California store, so in-store
     camping sales there collapse. All states also have a normal seasonal Aug -> Sep dip.
  B. March-May 2026: a defective batch of the Summit Down Jacket drives returns up.
  C. October 1 - November 15 2025: the FALL25 promotion (15% off) lifts order volume.
  D. June 2026 onward: social-media spend in Texas is paused and online demand there drops.
  E. Data-quality problems: future-dated orders, zero-quantity lines, duplicate returns,
     customers with no state, and products whose cost exceeds their price.

Everything is driven by one seeded random generator, so the same seed gives the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-09-30")  # last day with data

# state -> (share of demand, region, cities)
STATES: dict[str, tuple[float, str, list[str]]] = {
    "CA": (0.24, "West", ["Los Angeles", "San Francisco", "San Diego", "Sacramento"]),
    "TX": (0.14, "South", ["Austin", "Houston", "Dallas"]),
    "NY": (0.11, "Northeast", ["New York", "Buffalo"]),
    "FL": (0.09, "South", ["Miami", "Orlando"]),
    "WA": (0.09, "West", ["Seattle", "Spokane"]),
    "CO": (0.09, "West", ["Denver", "Boulder"]),
    "IL": (0.07, "Midwest", ["Chicago"]),
    "OR": (0.06, "West", ["Portland", "Bend"]),
    "AZ": (0.06, "West", ["Phoenix", "Flagstaff"]),
    "MA": (0.05, "Northeast", ["Boston"]),
}
OUTLET_CITIES = {"Los Angeles", "Houston", "Denver", "Seattle"}
LATE_OPENING = ("Bend", pd.Timestamp("2025-04-12"))  # opened during the data window

# Monthly demand multipliers, January..December.
SEASON = {
    "summer": [0.45, 0.5, 0.7, 0.95, 1.25, 1.55, 1.65, 1.5, 1.0, 0.7, 0.55, 0.65],
    "winter": [1.6, 1.4, 1.0, 0.6, 0.4, 0.3, 0.3, 0.4, 0.65, 1.05, 1.5, 1.9],
    "flat": [0.9, 0.85, 0.95, 1.0, 1.05, 1.05, 1.05, 1.05, 1.0, 1.0, 1.1, 1.3],
}
ORDER_SEASON = [0.8, 0.8, 0.9, 1.0, 1.1, 1.15, 1.15, 1.1, 0.95, 0.95, 1.1, 1.3]


@dataclass(frozen=True)
class Subcategory:
    category: str
    name: str
    noun: str
    base_price: float
    season: str
    n_products: int
    demand: float  # relative share of line items


SUBCATEGORIES = [
    Subcategory("Camping", "Tents", "Tent", 249, "summer", 10, 4.0),
    Subcategory("Camping", "Sleeping Bags", "Sleeping Bag", 139, "summer", 8, 4.0),
    Subcategory("Camping", "Camp Stoves", "Stove", 89, "summer", 6, 3.0),
    Subcategory("Camping", "Camp Furniture", "Camp Chair", 69, "summer", 6, 3.5),
    Subcategory("Hiking", "Hiking Boots", "Hiking Boot", 159, "flat", 10, 5.0),
    Subcategory("Hiking", "Backpacks", "Backpack", 129, "flat", 10, 5.0),
    Subcategory("Hiking", "Trekking Poles", "Trekking Poles", 79, "summer", 5, 2.0),
    Subcategory("Climbing", "Harnesses", "Harness", 69, "flat", 5, 1.5),
    Subcategory("Climbing", "Climbing Shoes", "Climbing Shoe", 129, "flat", 6, 1.8),
    Subcategory("Climbing", "Ropes", "Rope", 199, "flat", 4, 0.8),
    Subcategory("Snow", "Skis", "Ski", 549, "winter", 6, 1.2),
    Subcategory("Snow", "Snowboards", "Snowboard", 449, "winter", 5, 1.0),
    Subcategory("Snow", "Ski Jackets", "Ski Jacket", 279, "winter", 8, 2.2),
    Subcategory("Snow", "Goggles", "Goggles", 119, "winter", 5, 2.0),
    Subcategory("Water", "Kayaks", "Kayak", 799, "summer", 4, 0.6),
    Subcategory("Water", "Paddles", "Paddle", 129, "summer", 4, 1.0),
    Subcategory("Water", "Life Vests", "Life Vest", 89, "summer", 4, 1.2),
    Subcategory("Apparel", "Base Layers", "Base Layer", 59, "winter", 10, 6.0),
    Subcategory("Apparel", "Rain Jackets", "Rain Jacket", 149, "flat", 8, 4.0),
    Subcategory("Apparel", "Down Jackets", "Down Jacket", 229, "winter", 8, 3.5),
    Subcategory("Apparel", "T-Shirts", "Tee", 29, "summer", 10, 9.0),
    Subcategory("Accessories", "Headlamps", "Headlamp", 39, "flat", 6, 6.0),
    Subcategory("Accessories", "Water Bottles", "Bottle", 25, "flat", 6, 8.0),
    Subcategory("Accessories", "Navigation", "GPS Unit", 199, "flat", 4, 1.0),
]
BRANDS = ["Ridgeline", "Alder & Ash", "Northfork", "Basecamp Co", "Tidewater", "Granite Peak",
          "Cirrus"]
MODELS = ["Trailhead", "Summit", "Cascade", "Sierra", "Talus", "Juniper", "Meridian", "Basalt",
          "Sawtooth", "Aspen", "Kestrel", "Drift", "Hollow", "Vista"]
# Named products the planted effects refer to: first product of its subcategory.
HERO_PRODUCTS = {"Tents": "Ridgeline Trailhead 2P Tent", "Down Jackets": "Granite Peak Summit Down Jacket"}

FIRST_NAMES = ["Ava", "Liam", "Maya", "Noah", "Zoe", "Ethan", "Priya", "Lucas", "Sofia", "Omar",
               "Hana", "Diego", "Grace", "Kai", "Leila", "Mateo", "Nora", "Ravi", "Elena", "Sam",
               "Iris", "Theo", "Mina", "Jonah", "Aria", "Felix", "Yuki", "Caleb", "Luz", "Owen"]
LAST_NAMES = ["Garcia", "Nguyen", "Smith", "Patel", "Kim", "Johnson", "Lopez", "Chen", "Brown",
              "Davis", "Martinez", "Singh", "Wilson", "Anderson", "Thomas", "Moore", "Lee",
              "Clark", "Lewis", "Walker", "Hall", "Young", "Allen", "Wright", "King", "Scott"]

PROMO = ("FALL25", pd.Timestamp("2025-10-01"), pd.Timestamp("2025-11-15"), 0.15)
CA_STOCKOUT_START = pd.Timestamp("2026-09-01")
DEFECT_WINDOW = (pd.Timestamp("2026-03-01"), pd.Timestamp("2026-05-31"))
TX_SOCIAL_PAUSE = pd.Timestamp("2026-06-01")
PRICE_INCREASE = pd.Timestamp("2025-03-01")  # list prices rose 5% on this day


def _stores() -> pd.DataFrame:
    rows = [dict(store_id=1, store_name="Online", channel="online", city=None, state=None,
                 region=None, opened_on=date(2018, 3, 1))]
    rng = np.random.default_rng(7)
    for state, (_, region, cities) in STATES.items():
        for city in cities:
            opened = date(int(rng.integers(2012, 2023)), int(rng.integers(1, 13)), 1)
            if city == LATE_OPENING[0]:
                opened = LATE_OPENING[1].date()
            rows.append(dict(store_id=len(rows) + 1, store_name=f"Switchback {city}",
                             channel="retail", city=city, state=state, region=region,
                             opened_on=opened))
            if city in OUTLET_CITIES:
                rows.append(dict(store_id=len(rows) + 1, store_name=f"Switchback {city} Outlet",
                                 channel="outlet", city=city, state=state, region=region,
                                 opened_on=date(int(rng.integers(2016, 2023)), 6, 1)))
    return pd.DataFrame(rows)


def _products(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for sub in SUBCATEGORIES:
        models = rng.permutation(MODELS)
        for i in range(sub.n_products):
            brand = BRANDS[int(rng.integers(len(BRANDS)))]
            noun = sub.noun if sub.name != "Tents" else f"{int(rng.choice([1, 2, 3, 4, 6]))}P Tent"
            name = f"{brand} {models[i % len(models)]} {noun}"
            if i == 0 and sub.name in HERO_PRODUCTS:
                name = HERO_PRODUCTS[sub.name]
            price = round(sub.base_price * float(rng.lognormal(0, 0.25))) - 0.01
            cost = round(price * float(rng.uniform(0.42, 0.6)), 2)
            launched = START - pd.Timedelta(days=int(rng.integers(200, 1500)))
            if rng.random() < 0.12:  # launched during the data window
                launched = START + pd.Timedelta(days=int(rng.integers(60, 900)))
            discontinued = None
            if i > 0 and rng.random() < 0.08:
                discontinued = (START + pd.Timedelta(days=int(rng.integers(300, 950)))).date()
            rows.append(dict(category=sub.category, subcategory=sub.name, brand=brand,
                             product_name=name, list_price=price, unit_cost=cost,
                             launched_on=launched.date(), discontinued_on=discontinued,
                             popularity=1.0 / (i + 1) ** 0.8, demand=sub.demand,
                             season=sub.season))
    df = pd.DataFrame(rows)
    # Product names can collide (same brand + model + noun); make them unique.
    dup = df.duplicated("product_name", keep="first")
    df.loc[dup, "product_name"] = df.loc[dup, "product_name"] + " II"
    df.insert(0, "product_id", np.arange(1, len(df) + 1))
    df.insert(1, "sku", [f"SB-{c[:3].upper()}-{pid:04d}" for c, pid in
                         zip(df.category, df.product_id, strict=True)])
    return df


def _customers(rng: np.random.Generator, n: int = 32_000) -> pd.DataFrame:
    states = list(STATES)
    weights = np.array([STATES[s][0] for s in states])
    state = rng.choice(states, size=n, p=weights / weights.sum()).astype(object)
    city = np.array([STATES[s][2][int(rng.integers(len(STATES[s][2])))] for s in state],
                    dtype=object)
    no_state = rng.random(n) < 0.005  # planted data-quality issue E
    state[no_state] = None
    city[no_state] = None
    # 35% signed up before the data window, the rest during it, accelerating over time.
    early = rng.random(n) < 0.35
    span = (END - START).days
    offsets = np.where(early, -rng.integers(1, 5 * 365, n), (rng.random(n) ** 0.8 * span))
    signup = (START + pd.to_timedelta(offsets.astype(int), unit="D")).date
    first = rng.choice(FIRST_NAMES, n)
    last = rng.choice(LAST_NAMES, n)
    ids = np.arange(1, n + 1)
    return pd.DataFrame(dict(
        customer_id=ids, first_name=first, last_name=last,
        email=[f"{f.lower()}.{la.lower()}.{i}@example.com" for f, la, i in
               zip(first, last, ids, strict=True)],
        phone=[f"555-{int(x):04d}" for x in rng.integers(0, 10_000, n)],
        city=city, state=state, signup_date=signup,
        segment=rng.choice(["consumer", "pro", "business"], n, p=[0.85, 0.1, 0.05]),
        loyalty_tier=rng.choice(["none", "silver", "gold"], n, p=[0.7, 0.22, 0.08]),
    ))


def _order_headers(rng, stores, customers) -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    t = (days - START).days.to_numpy() / 365.0
    rate = 230 * 1.17**t * np.array([ORDER_SEASON[m - 1] for m in days.month])
    rate *= np.select([days.dayofweek == 5, days.dayofweek == 6, days.dayofweek == 4],
                      [1.3, 1.2, 1.05], 1.0)
    holiday = ((days.month == 11) & (days.day >= 20)) | ((days.month == 12) & (days.day <= 23))
    rate *= np.where(holiday, 1.45, 1.0)
    rate *= np.where((days >= PROMO[1]) & (days <= PROMO[2]), 1.12, 1.0)  # effect C
    counts = rng.poisson(rate)
    day = np.repeat(days.to_numpy(), counts)
    n = len(day)
    frac = np.repeat(t / t.max(), counts)

    online = rng.random(n) < 0.28 + 0.14 * frac
    hour = np.where(online, rng.choice(24, n, p=_hour_weights(online=True)),
                    rng.choice(24, n, p=_hour_weights(online=False)))
    ordered_at = (pd.to_datetime(day) + pd.to_timedelta(hour, unit="h")
                  + pd.to_timedelta(rng.integers(0, 3600, n), unit="s"))

    states = list(STATES)
    w = np.array([STATES[s][0] for s in states])
    ship_state = rng.choice(states, n, p=w / w.sum())

    # Effect D: with Texas social ads paused, a quarter of Texas online demand disappears.
    lost = online & (ship_state == "TX") & (ordered_at >= TX_SOCIAL_PAUSE) & (rng.random(n) < 0.25)
    keep = ~lost
    ordered_at, ship_state, online = ordered_at[keep], ship_state[keep], online[keep]
    n = len(ordered_at)

    store_id = np.ones(n, dtype=int)
    physical = stores[stores.channel != "online"]
    for state in states:
        in_state = physical[physical.state == state]
        mask = (~online) & (ship_state == state)
        size = np.where(in_state.channel == "outlet", 0.6, 1.0)
        store_id[mask] = rng.choice(in_state.store_id.to_numpy(), mask.sum(), p=size / size.sum())
    # Stores cannot sell before they open: send those orders to the other store in the state.
    for row in physical.itertuples():
        early = (store_id == row.store_id) & (ordered_at < pd.Timestamp(row.opened_on))
        if early.any():
            alt = physical[(physical.state == row.state) & (physical.store_id != row.store_id)]
            store_id[early] = alt.store_id.iloc[0]

    customer_id = _pick_customers(rng, customers, ship_state, ordered_at)

    promo = (ordered_at >= PROMO[1]) & (ordered_at <= PROMO[2] + pd.Timedelta(days=1))
    promo_code = np.where(promo & (rng.random(n) < 0.4), PROMO[0],
                          np.where(rng.random(n) < 0.03, "WELCOME10", None))
    status = np.where(rng.random(n) < 0.025, "cancelled", "completed")
    orders = pd.DataFrame(dict(customer_id=customer_id, store_id=store_id,
                               channel=np.where(online, "online", "in_store"),
                               ordered_at=ordered_at, ship_state=ship_state, status=status,
                               promo_code=promo_code))
    orders = orders.sort_values("ordered_at", kind="stable").reset_index(drop=True)
    orders.insert(0, "order_id", np.arange(100_001, 100_001 + len(orders)))
    return orders


def _hour_weights(*, online: bool) -> np.ndarray:
    h = np.arange(24)
    if online:
        w = 0.3 + np.exp(-((h - 20) ** 2) / 18) + 0.5 * np.exp(-((h - 12) ** 2) / 8)
    else:
        w = np.where((h >= 9) & (h <= 20), 1 + np.exp(-((h - 15) ** 2) / 10), 0.0)
    return w / w.sum()


def _pick_customers(rng, customers, ship_state, ordered_at) -> np.ndarray:
    """Pick a customer per order who had signed up by then; 93% live in the ship state."""
    signup = pd.to_datetime(customers.signup_date)
    out = np.empty(len(ship_state), dtype=int)
    order_day = ordered_at.normalize()
    any_state = rng.random(len(ship_state)) < 0.07
    pools = {s: customers[customers.state == s] for s in STATES}
    pools[None] = customers
    for key, pool in pools.items():
        mask = any_state if key is None else (~any_state) & (ship_state == key)
        if not mask.any():
            continue
        order = np.argsort(signup[pool.index].to_numpy(), kind="stable")
        ids = pool.customer_id.to_numpy()[order]
        dates = signup[pool.index].to_numpy()[order]
        k = np.searchsorted(dates, order_day[mask].to_numpy(), side="right")
        k = np.maximum(k, 1)
        out[mask] = ids[(rng.random(mask.sum()) * k).astype(int)]
    return out


def _order_items(rng, orders, products) -> pd.DataFrame:
    n_items = np.minimum(1 + rng.poisson(0.9, len(orders)), 6)
    order_idx = np.repeat(np.arange(len(orders)), n_items)
    when = orders.ordered_at.to_numpy()[order_idx]
    month = pd.DatetimeIndex(when).to_period("M")
    product_id = np.empty(len(order_idx), dtype=int)

    launched = pd.to_datetime(products.launched_on).to_numpy()
    disc = pd.to_datetime(products.discontinued_on).to_numpy()
    base = (products.popularity * products.demand).to_numpy()
    for period in np.unique(month):
        mask = month == period
        start = period.start_time.to_datetime64()
        season = np.array([SEASON[s][period.month - 1] for s in products.season])
        active = (launched <= start) & ~(disc <= start)
        w = base * season * active
        product_id[mask] = rng.choice(products.product_id.to_numpy(), mask.sum(), p=w / w.sum())

    price = products.set_index("product_id").list_price.reindex(product_id).to_numpy()
    price = np.where(when < PRICE_INCREASE.to_datetime64(), np.round(price / 1.05, 2), price)
    quantity = np.where(price < 50, rng.choice([1, 1, 1, 2, 2, 3], len(price)), 1)
    quantity = np.where(rng.random(len(price)) < 0.03, quantity + 1, quantity)

    promo = orders.promo_code.to_numpy()[order_idx]
    rate = np.select([promo == PROMO[0], promo == "WELCOME10"], [PROMO[3], 0.10], 0.0)
    discount = np.round(price * quantity * rate, 2)
    return pd.DataFrame(dict(order_id=orders.order_id.to_numpy()[order_idx],
                             product_id=product_id, quantity=quantity, unit_price=price,
                             discount=discount))


def _stockouts(rng, stores, products) -> pd.DataFrame:
    physical = stores[stores.channel != "online"]
    rows = []
    # Effect A: the top camping products run out in every California store in September.
    camping = products[(products.category == "Camping") & (products.popularity > 0.35)]
    for store in physical[physical.state == "CA"].itertuples():
        for pid in camping.product_id:
            rows.append(dict(store_id=store.store_id, product_id=pid,
                             started_on=(CA_STOCKOUT_START
                                         + pd.Timedelta(days=int(rng.integers(0, 3)))).date(),
                             ended_on=None))
    # Background noise: short stockouts anywhere, any time.
    for _ in range(80):
        start = START + pd.Timedelta(days=int(rng.integers(0, (END - START).days - 20)))
        rows.append(dict(store_id=int(rng.choice(physical.store_id)),
                         product_id=int(rng.choice(products.product_id)),
                         started_on=start.date(),
                         ended_on=(start + pd.Timedelta(days=int(rng.integers(3, 15)))).date()))
    return pd.DataFrame(rows).drop_duplicates(["store_id", "product_id", "started_on"])


def _apply_stockouts(rng, orders, items, stockouts) -> pd.DataFrame:
    """Remove 90% of in-store lines sold while their product was out of stock at that store."""
    lines = items.merge(orders[["order_id", "store_id", "channel", "ordered_at"]], on="order_id")
    lines["_row"] = np.arange(len(lines))
    hit = lines.merge(stockouts, on=["store_id", "product_id"])
    ended = pd.to_datetime(hit.ended_on).fillna(END + pd.Timedelta(days=1))
    hit = hit[(hit.channel == "in_store")
              & (hit.ordered_at >= pd.to_datetime(hit.started_on))
              & (hit.ordered_at < ended + pd.Timedelta(days=1))]
    drop = hit._row[rng.random(len(hit)) < 0.9].unique()
    return items.drop(index=items.index[drop]).reset_index(drop=True)


def _returns(rng, orders, items, products) -> pd.DataFrame:
    lines = items.merge(orders[["order_id", "ordered_at", "status"]], on="order_id")
    lines = lines.merge(products[["product_id", "category", "product_name"]], on="product_id")
    eligible = (lines.status == "completed") & (lines.quantity > 0)
    p = np.where(lines.category == "Apparel", 0.07, 0.04)
    jacket = (lines.product_name == HERO_PRODUCTS["Down Jackets"]) & lines.ordered_at.between(
        *DEFECT_WINDOW)  # effect B
    p = np.where(jacket, 0.24, p)
    returned = eligible & (rng.random(len(lines)) < p)
    r = lines[returned].copy()
    reasons = ["wrong size", "changed mind", "defective", "not as described", "arrived late"]
    r["reason"] = rng.choice(reasons, len(r), p=[0.35, 0.3, 0.12, 0.15, 0.08])
    r.loc[jacket[returned] & (rng.random(len(r)) < 0.85), "reason"] = "defective"
    r["returned_on"] = (r.ordered_at.dt.normalize()
                        + pd.to_timedelta(rng.integers(2, 36, len(r)), unit="D"))
    r = r[r.returned_on <= END]
    revenue = r.quantity * r.unit_price - r.discount
    r["refund_amount"] = np.round(np.where(rng.random(len(r)) < 0.1, revenue * 0.5, revenue), 2)
    r["returned_on"] = r.returned_on.dt.date
    r = r.sort_values("returned_on", kind="stable")
    out = r[["order_item_id", "returned_on", "reason", "refund_amount"]].reset_index(drop=True)
    # Effect E: a dozen returns were recorded twice by the returns system.
    dupes = out.sample(12, random_state=int(rng.integers(1 << 31)))
    out = pd.concat([out, dupes], ignore_index=True)
    out.insert(0, "return_id", np.arange(1, len(out) + 1))
    return out


def _marketing(rng) -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    rows = []
    shares = {"search": 0.45, "social": 0.25, "email": 0.1, "display": 0.2}
    season = np.array([ORDER_SEASON[m - 1] for m in days.month])
    for state, (weight, _, _) in STATES.items():
        for channel, share in shares.items():
            amount = 9000 * weight * share * season * rng.lognormal(0, 0.15, len(days))
            if state == "TX" and channel == "social":
                amount = np.where(days >= TX_SOCIAL_PAUSE, 0.0, amount)  # effect D
            rows.append(pd.DataFrame(dict(spend_date=days.date, channel=channel, state=state,
                                          amount=np.round(amount, 2))))
    return pd.concat(rows, ignore_index=True)


def _employees(rng, stores) -> pd.DataFrame:
    titles = [("Store Manager", 92_000, 1), ("Assistant Manager", 68_000, 2),
              ("Sales Associate", 41_000, 10), ("Gear Technician", 48_000, 3)]
    rows = []
    for store in stores[stores.channel != "online"].itertuples():
        for title, pay, count in titles:
            for _ in range(count):
                rows.append(dict(store_id=store.store_id,
                                 full_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                                 job_title=title,
                                 salary=round(pay * float(rng.uniform(0.9, 1.15)), 2),
                                 ssn_last4=f"{int(rng.integers(0, 10_000)):04d}"))
    df = pd.DataFrame(rows)
    df.insert(0, "employee_id", np.arange(1, len(df) + 1))
    return df


def generate(seed: int = 42) -> dict[str, pd.DataFrame]:
    """Build every table. Returns {table name: rows} in foreign-key load order."""
    rng = np.random.default_rng(seed)
    stores = _stores()
    products = _products(rng)
    customers = _customers(rng)
    orders = _order_headers(rng, stores, customers)
    items = _order_items(rng, orders, products)
    stockouts = _stockouts(rng, stores, products)
    items = _apply_stockouts(rng, orders, items, stockouts)

    # Orders that lost every line to a stockout never happened.
    orders = orders[orders.order_id.isin(items.order_id)].reset_index(drop=True)
    items.insert(0, "order_item_id", np.arange(1, len(items) + 1))

    # Effect E: planted data-quality problems.
    future = rng.choice(len(orders), 30, replace=False)
    orders.loc[future, "ordered_at"] = orders.loc[future, "ordered_at"].map(
        lambda ts: ts.replace(year=2027) if not (ts.month == 2 and ts.day == 29)
        else ts.replace(year=2027, day=28))
    items.loc[rng.choice(len(items), 150, replace=False), "quantity"] = 0
    bad_cost = rng.choice(len(products), 8, replace=False)
    products.loc[bad_cost, "unit_cost"] = np.round(products.loc[bad_cost, "list_price"] * 1.2, 2)

    returns = _returns(rng, orders, items, products)
    return {
        "retail.stores": stores,
        "retail.customers": customers,
        "retail.products": products.drop(columns=["popularity", "demand", "season"]),
        "retail.orders": orders,
        "retail.order_items": items,
        "retail.returns": returns,
        "retail.marketing_spend": _marketing(rng),
        "retail.stockouts": stockouts,
        "hr.employees": _employees(rng, stores),
    }
