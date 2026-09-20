from datetime import datetime
from decimal import Decimal

from tests.models import Base, Customer, Order, OrderItem, OrderStatus, Product

CUSTOMERS = [
    ("Lena Fischer", "lena@fischer.de", "DE"),
    ("Marco Rossi", "marco@rossi.it", "IT"),
    ("Aisha Khan", "aisha@khan.co.uk", "UK"),
    ("Jonas Berg", "jonas@berg.se", "SE"),
]

PRODUCTS = [
    ("Linen shirt", Decimal("59.00")),
    ("Canvas tote", Decimal("24.00")),
    ("Wool scarf", Decimal("38.50")),
]

ORDERS = [
    # customer index, status, day of month, (product index, quantity) items
    (0, OrderStatus.SHIPPED, 1, [(0, 1), (1, 2)]),
    (0, OrderStatus.PAID, 4, [(2, 1)]),
    (1, OrderStatus.PENDING, 6, [(1, 3)]),
    (1, OrderStatus.SHIPPED, 9, [(0, 2)]),
    (2, OrderStatus.REFUNDED, 11, [(2, 2), (1, 1)]),
    (2, OrderStatus.PAID, 14, [(0, 1)]),
    (3, OrderStatus.PENDING, 17, [(1, 1)]),
]


def build_sample_data() -> list[Base]:
    """Build the rows every test starts from, always in the same order."""
    customers = [
        Customer(name=name, email=email, region=region)
        for name, email, region in CUSTOMERS
    ]
    products = [Product(name=name, price=price) for name, price in PRODUCTS]

    orders = []
    for customer_index, status, day, item_specs in ORDERS:
        items = [
            OrderItem(
                product=products[product_index],
                quantity=quantity,
                unit_price=products[product_index].price,
            )
            for product_index, quantity in item_specs
        ]
        orders.append(
            Order(
                customer=customers[customer_index],
                status=status,
                total=sum(
                    (item.unit_price * item.quantity for item in items),
                    Decimal(0),
                ),
                created_at=datetime(2026, 9, day, 10, 30),
                items=items,
            )
        )

    return [*customers, *products, *orders]
