"""Bazaaro product catalog: an original general-merchandise marketplace catalog.

Product imagery is generated client-side (gradient tile + glyph), so the catalog
ships no third-party images or brand assets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    category: str
    price: float
    list_price: float
    rating: float
    reviews: int
    stock: int
    glyph: str
    hue: int
    fast_delivery: bool
    seller: str
    blurb: str
    limited: bool = False  # flash-sale drop item: small stock, per-customer limit

    @property
    def discount_pct(self) -> int:
        if self.list_price <= self.price:
            return 0
        return int(round(100 * (1 - self.price / self.list_price)))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["discount_pct"] = self.discount_pct
        return data


CATEGORIES: dict[str, str] = {
    "electronics": "Electronics",
    "home": "Home & Kitchen",
    "books": "Books",
    "fashion": "Fashion",
    "sports": "Sports & Outdoors",
    "toys": "Toys & Games",
}

#: Load tests draw from this stock, so it is generous; a few items are scarce on purpose.
_P = Product
PRODUCTS: list[Product] = [
    _P(
        "bz-101",
        "Aurora Noise-Cancelling Headphones",
        "electronics",
        129.0,
        179.0,
        4.6,
        2841,
        5000,
        "🎧",
        262,
        True,
        "Lumen Audio",
        "40-hour battery, adaptive ANC, fold-flat hinge.",
    ),
    _P(
        "bz-102",
        "Pocket 4K Action Camera",
        "electronics",
        189.0,
        189.0,
        4.3,
        932,
        5000,
        "📷",
        205,
        True,
        "Driftline",
        "Waterproof to 10 m with stabilised 4K60 capture.",
    ),
    _P(
        "bz-103",
        'Slate 11" E-Reader',
        "electronics",
        99.0,
        139.0,
        4.7,
        5120,
        5000,
        "📱",
        220,
        True,
        "Paperwave",
        "Glare-free display, warm light, weeks of battery.",
    ),
    _P(
        "bz-104",
        "Orbit Smart Speaker",
        "electronics",
        59.0,
        79.0,
        4.2,
        3310,
        5000,
        "🔊",
        290,
        False,
        "Lumen Audio",
        "Room-filling sound with a privacy mic switch.",
    ),
    _P(
        "bz-105",
        "Volt 65W GaN Charger",
        "electronics",
        34.0,
        49.0,
        4.8,
        7702,
        5000,
        "🔌",
        160,
        True,
        "Ampere Co.",
        "Charges a laptop and phone at once, travel-sized.",
    ),
    _P(
        "bz-201",
        "Cast-Iron Dutch Oven 5.5 qt",
        "home",
        64.0,
        89.0,
        4.8,
        11203,
        5000,
        "🍲",
        12,
        True,
        "Hearthstone",
        "Enamelled, oven-safe to 260 °C, lifetime finish.",
    ),
    _P(
        "bz-202",
        "Pour-Over Coffee Set",
        "home",
        42.0,
        42.0,
        4.5,
        1874,
        5000,
        "☕",
        28,
        False,
        "Morning Ritual",
        "Glass carafe, steel filter and gooseneck kettle.",
    ),
    _P(
        "bz-203",
        "Linen Throw Blanket",
        "home",
        38.0,
        55.0,
        4.4,
        2290,
        5000,
        "🛋️",
        40,
        True,
        "Fieldhouse",
        "Stone-washed linen, breathable year-round.",
    ),
    _P(
        "bz-204",
        "Chef's Knife 8\"",
        "home",
        74.0,
        99.0,
        4.7,
        4402,
        5000,
        "🔪",
        350,
        True,
        "Hearthstone",
        "High-carbon steel, full tang, balanced grip.",
    ),
    _P(
        "bz-205",
        "Self-Watering Herb Planter",
        "home",
        29.0,
        36.0,
        4.1,
        978,
        5000,
        "🌿",
        120,
        False,
        "Greenrow",
        "Grows basil, mint and thyme on any windowsill.",
    ),
    _P(
        "bz-301",
        "The Quiet Algorithm (Hardcover)",
        "books",
        24.0,
        30.0,
        4.6,
        3021,
        5000,
        "📘",
        225,
        True,
        "Northlight Press",
        "A novel about a city that learns to listen.",
    ),
    _P(
        "bz-302",
        "Systems Under Load",
        "books",
        39.0,
        49.0,
        4.9,
        640,
        5000,
        "📗",
        140,
        False,
        "Queue Theory Books",
        "Capacity planning for people who ship things.",
    ),
    _P(
        "bz-303",
        "Weekend Bread Baking",
        "books",
        19.0,
        25.0,
        4.7,
        2155,
        5000,
        "📙",
        30,
        True,
        "Crumb & Co.",
        "Twelve doughs, one oven, zero stress.",
    ),
    _P(
        "bz-304",
        "Field Guide to Night Skies",
        "books",
        22.0,
        22.0,
        4.5,
        811,
        5000,
        "📕",
        0,
        False,
        "Northlight Press",
        "Star charts for every season, glow-ink maps.",
    ),
    _P(
        "bz-401",
        "Everyday Merino Crew Tee",
        "fashion",
        48.0,
        60.0,
        4.4,
        3390,
        5000,
        "👕",
        200,
        True,
        "Woolbound",
        "Odour-resistant, soft, made for travel.",
    ),
    _P(
        "bz-402",
        "Trail Runner Sneakers",
        "fashion",
        96.0,
        130.0,
        4.5,
        2604,
        5000,
        "👟",
        15,
        True,
        "Stridewell",
        "Grippy outsole, breathable mesh upper.",
    ),
    _P(
        "bz-403",
        "Waxed Canvas Backpack",
        "fashion",
        82.0,
        82.0,
        4.6,
        1460,
        5000,
        "🎒",
        35,
        False,
        "Fieldhouse",
        "Laptop sleeve, weatherproof waxed canvas.",
    ),
    _P(
        "bz-404",
        "Polarised Sunglasses",
        "fashion",
        45.0,
        70.0,
        4.2,
        1802,
        5000,
        "🕶️",
        190,
        True,
        "Brightline",
        "UV400 lenses in a lightweight frame.",
    ),
    _P(
        "bz-501",
        "Ultralight 2-Person Tent",
        "sports",
        219.0,
        279.0,
        4.7,
        987,
        5000,
        "⛺",
        100,
        True,
        "Ridgeway",
        "1.6 kg packed, sets up in four minutes.",
    ),
    _P(
        "bz-502",
        "Adjustable Dumbbell Pair",
        "sports",
        249.0,
        299.0,
        4.6,
        1544,
        5000,
        "🏋️",
        0,
        False,
        "Ironleaf",
        "2.5–24 kg per hand with a quick-dial selector.",
    ),
    _P(
        "bz-503",
        "Insulated Steel Bottle 1 L",
        "sports",
        27.0,
        35.0,
        4.8,
        9013,
        5000,
        "🧴",
        185,
        True,
        "Ridgeway",
        "Cold for 24 h, hot for 12 h, leakproof lid.",
    ),
    _P(
        "bz-601",
        "1000-Piece Lighthouse Puzzle",
        "toys",
        21.0,
        27.0,
        4.6,
        1320,
        5000,
        "🧩",
        210,
        True,
        "Tabletop Joy",
        "Thick, matte pieces with a poster guide.",
    ),
    _P(
        "bz-602",
        "Build-a-Bot Coding Kit",
        "toys",
        69.0,
        89.0,
        4.5,
        702,
        5000,
        "🤖",
        170,
        True,
        "Tinkerbox",
        "Snap-together robot, block and Python coding.",
    ),
    _P(
        "bz-603",
        "Strategy Board Game: Harbor Lights",
        "toys",
        44.0,
        44.0,
        4.7,
        2088,
        3,
        "🎲",
        330,
        False,
        "Tabletop Joy",
        "Trade, build and race the tide. 2–5 players.",
    ),
    _P(
        "bz-701",
        "Lumen One Limited Turntable (Drop #1)",
        "electronics",
        149.0,
        299.0,
        4.9,
        412,
        50,
        "💿",
        300,
        True,
        "Lumen Audio",
        "Numbered limited run in walnut. Flash-sale drop: 50 units, 2 per customer.",
        True,
    ),
]

PRODUCTS_BY_ID: dict[str, Product] = {p.id: p for p in PRODUCTS}


def inventory_seed() -> dict[str, int]:
    """Stock levels passed to the inventory service at testbed start."""
    return {p.id: p.stock for p in PRODUCTS}


def search(
    query: str = "",
    category: str = "",
    sort: str = "featured",
    max_price: float | None = None,
    fast_only: bool = False,
) -> list[Product]:
    q = query.strip().lower()
    items = [
        p
        for p in PRODUCTS
        if (not category or p.category == category)
        and (not q or q in p.name.lower() or q in p.blurb.lower() or q in p.seller.lower())
        and (max_price is None or p.price <= max_price)
        and (not fast_only or p.fast_delivery)
    ]
    if sort == "price_asc":
        items.sort(key=lambda p: p.price)
    elif sort == "price_desc":
        items.sort(key=lambda p: -p.price)
    elif sort == "rating":
        items.sort(key=lambda p: (-p.rating, -p.reviews))
    elif sort == "discount":
        items.sort(key=lambda p: -p.discount_pct)
    return items
