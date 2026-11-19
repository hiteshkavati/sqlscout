"""Deterministic generator for a synthetic Indian e-commerce database.

Everything here is made up. The same seed always produces the same file, so the
evaluation set's gold answers stay valid.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 42
N_CUSTOMERS = 2000
N_ORDERS = 12000

SCHEMA = """
CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    city         TEXT NOT NULL,
    state        TEXT NOT NULL,
    signup_date  TEXT NOT NULL,
    segment      TEXT NOT NULL,
    email        TEXT NOT NULL
);
CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    price_inr    REAL NOT NULL,
    cost_inr     REAL NOT NULL
);
CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date      TEXT NOT NULL,
    status          TEXT NOT NULL,
    payment_method  TEXT NOT NULL,
    delivery_city   TEXT NOT NULL,
    discount_inr    REAL NOT NULL,
    total_inr       REAL NOT NULL
);
CREATE TABLE order_items (
    order_item_id   INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL,
    unit_price_inr  REAL NOT NULL
);
CREATE TABLE returns (
    return_id       INTEGER PRIMARY KEY,
    order_item_id   INTEGER NOT NULL REFERENCES order_items(order_item_id),
    reason          TEXT NOT NULL,
    refund_inr      REAL NOT NULL
);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
"""

CITIES = [
    ("Bengaluru", "Karnataka", 16),
    ("Mumbai", "Maharashtra", 15),
    ("Delhi", "Delhi", 15),
    ("Hyderabad", "Telangana", 11),
    ("Chennai", "Tamil Nadu", 9),
    ("Pune", "Maharashtra", 9),
    ("Kolkata", "West Bengal", 7),
    ("Ahmedabad", "Gujarat", 6),
    ("Jaipur", "Rajasthan", 6),
    ("Kochi", "Kerala", 6),
]

PAYMENTS = [
    ("UPI", 46),
    ("Credit Card", 16),
    ("Debit Card", 12),
    ("COD", 18),
    ("Net Banking", 5),
    ("Wallet", 3),
]
STATUSES = [("delivered", 80), ("cancelled", 8), ("returned", 7), ("shipped", 5)]
SEGMENTS = [("regular", 60), ("new", 28), ("premium", 12)]
RETURN_REASONS = [
    "damaged",
    "wrong item",
    "not as described",
    "size issue",
    "changed mind",
    "late delivery",
]

CATALOG = {
    "Electronics": (
        [
            "Wireless Earbuds",
            "Bluetooth Speaker",
            "Power Bank 10000mAh",
            "Smart Watch",
            "USB-C Charger",
            "Webcam HD",
            "Mechanical Keyboard",
            "Gaming Mouse",
            "Phone Stand",
            "Noise Cancelling Headphones",
            "Portable SSD 512GB",
            "LED Desk Lamp",
            "Fitness Band",
            "HDMI Cable",
            "Wi-Fi Router",
        ],
        (399, 8999),
    ),
    "Fashion": (
        [
            "Cotton T-Shirt",
            "Slim Fit Jeans",
            "Running Shoes",
            "Kurta Set",
            "Hoodie",
            "Formal Shirt",
            "Sports Socks Pack",
            "Backpack",
            "Sneakers",
            "Leather Wallet",
            "Saree",
            "Track Pants",
            "Sunglasses",
            "Cap",
            "Denim Jacket",
        ],
        (249, 3999),
    ),
    "Home & Kitchen": (
        [
            "Non-stick Pan",
            "Pressure Cooker",
            "Water Bottle",
            "Bedsheet Set",
            "Storage Containers",
            "Electric Kettle",
            "Mixer Grinder",
            "Dinner Set",
            "Wall Clock",
            "Curtains Pair",
            "Vacuum Flask",
            "Knife Set",
            "Air Fryer",
            "Cushion Covers",
            "Laundry Basket",
        ],
        (149, 6499),
    ),
    "Grocery": (
        [
            "Basmati Rice 5kg",
            "Cold Pressed Oil 1L",
            "Masala Tea 500g",
            "Almonds 500g",
            "Peanut Butter",
            "Oats 1kg",
            "Filter Coffee 250g",
            "Honey 500g",
            "Dark Chocolate Pack",
            "Mixed Dry Fruits",
            "Quinoa 1kg",
            "Millet Cookies",
            "Green Tea Box",
            "Ghee 500ml",
            "Protein Bars Pack",
        ],
        (89, 1299),
    ),
    "Beauty": (
        [
            "Face Wash",
            "Sunscreen SPF 50",
            "Hair Oil",
            "Moisturiser",
            "Lip Balm",
            "Shampoo",
            "Beard Trimmer",
            "Perfume 100ml",
            "Face Serum",
            "Body Lotion",
            "Hair Dryer",
            "Kajal",
            "Face Mask Pack",
            "Deodorant",
            "Nail Kit",
        ],
        (99, 2499),
    ),
    "Books": (
        [
            "Python Programming Guide",
            "Data Structures Handbook",
            "Machine Learning Primer",
            "Startup Playbook",
            "Personal Finance Basics",
            "Cookbook of South India",
            "Travel Journal",
            "Mindfulness Workbook",
            "Competitive Exam Guide",
            "Statistics Made Simple",
            "System Design Notes",
            "Kids Story Collection",
            "History of India",
            "Poetry Anthology",
            "Habit Building Guide",
        ],
        (149, 899),
    ),
    "Sports": (
        [
            "Yoga Mat",
            "Dumbbell Set",
            "Cricket Bat",
            "Football",
            "Skipping Rope",
            "Badminton Racket",
            "Resistance Bands",
            "Cycling Gloves",
            "Gym Bag",
            "Shaker Bottle",
            "Tennis Balls Pack",
            "Foam Roller",
            "Swim Goggles",
            "Trekking Pole",
            "Knee Support",
        ],
        (199, 5999),
    ),
}

# Festive season (Oct-Nov) and a smaller mid-year sale push order volume.
MONTH_WEIGHT = {1: 7, 2: 7, 3: 8, 4: 7, 5: 8, 6: 9, 7: 8, 8: 9, 9: 9, 10: 15, 11: 14, 12: 11}


def _weighted(rng: random.Random, pairs):
    values = [p[0] for p in pairs]
    weights = [p[-1] for p in pairs]
    return rng.choices(values, weights=weights, k=1)[0]


def _random_date(rng: random.Random, start: date, end: date) -> date:
    days = (end - start).days
    while True:
        d = start + timedelta(days=rng.randrange(days + 1))
        if rng.random() < MONTH_WEIGHT[d.month] / 15:
            return d


def build(path: str | Path, seed: int = SEED) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)

    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        city, state, _ = _weighted_row(rng, CITIES)
        signup = _random_date(rng, date(2023, 1, 1), date(2025, 12, 15))
        customers.append(
            (
                cid,
                city,
                state,
                signup.isoformat(),
                _weighted(rng, SEGMENTS),
                f"user{cid}@example.com",
            )
        )
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)

    products = []
    pid = 1
    for category, (names, (lo, hi)) in CATALOG.items():
        for name in names:
            price = round(rng.uniform(lo, hi) / 10) * 10 - 1
            price = max(price, lo)
            cost = round(price * rng.uniform(0.45, 0.8), 2)
            products.append((pid, name, category, float(price), cost))
            pid += 1
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
    pop = [1 / (i + 1) ** 0.6 for i in range(len(products))]  # a few products sell far more

    orders, items, returns = [], [], []
    item_id = 1
    return_id = 1
    for oid in range(1, N_ORDERS + 1):
        cust = customers[rng.randrange(len(customers))]
        odate = _random_date(
            rng, max(date(2025, 1, 1), date.fromisoformat(cust[3])), date(2025, 12, 31)
        )
        status = _weighted(rng, STATUSES)
        delivery_city = cust[1] if rng.random() < 0.9 else _weighted_row(rng, CITIES)[0]
        n_lines = rng.choices([1, 2, 3, 4], weights=[55, 28, 12, 5])[0]
        picked = set()
        subtotal = 0.0
        order_items = []
        for _ in range(n_lines):
            p = rng.choices(products, weights=pop, k=1)[0]
            if p[0] in picked:
                continue
            picked.add(p[0])
            qty = rng.choices([1, 2, 3], weights=[78, 17, 5])[0]
            order_items.append((item_id, oid, p[0], qty, p[3]))
            subtotal += qty * p[3]
            item_id += 1
        discount = 0.0
        if rng.random() < 0.35:
            discount = round(subtotal * rng.choice([0.05, 0.1, 0.15, 0.2]), 2)
        total = round(subtotal - discount, 2)
        orders.append(
            (
                oid,
                cust[0],
                odate.isoformat(),
                status,
                _weighted(rng, PAYMENTS),
                delivery_city,
                discount,
                total,
            )
        )
        items.extend(order_items)
        if status == "returned":
            it = order_items[0]
            returns.append((return_id, it[0], rng.choice(RETURN_REASONS), round(it[3] * it[4], 2)))
            return_id += 1
        elif status == "delivered" and rng.random() < 0.03:
            it = order_items[rng.randrange(len(order_items))]
            returns.append((return_id, it[0], rng.choice(RETURN_REASONS), round(it[3] * it[4], 2)))
            return_id += 1
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
    conn.executemany("INSERT INTO returns VALUES (?,?,?,?)", returns)
    conn.commit()
    conn.close()
    return path


def _weighted_row(rng: random.Random, rows):
    return rng.choices(rows, weights=[r[-1] for r in rows], k=1)[0]
