"""Deterministic fixtures for three merchants and a fourth lookalike identity."""

from .models import Merchant, Product, Review

MERCHANTS = (
    Merchant("merchant_a", "Northstar Office", "Office electronics and desk essentials.", (
        Product("usb-c-hub", "USB-C hub 7-in-1", "HDMI, USB-A and card-reader ports in an aluminum enclosure.",
                "29.99", "electronics_accessories", (Review("Maya", 5, "Works well with my laptop and external monitor."),)),
        Product("desk-lamp", "Adjustable LED desk lamp", "Three brightness settings and a flexible reading arm.",
                "24.50", "office_supplies", (Review("Sam", 4, "Even light across my desk; the base is sturdy."),)),
    )),
    Merchant("merchant_b", "Cedar Home", "Practical goods for the kitchen and home.", (
        Product("travel-mug", "Insulated travel mug", "A 350 ml stainless-steel mug with a leak-resistant lid.",
                "18.00", "home_kitchen", (Review("Leah", 5, "Keeps coffee warm on my commute."),)),
        Product("storage-baskets", "Cotton storage baskets, pair", "Two washable woven baskets for shelves or cabinets.",
                "22.75", "home_storage", (Review("Dev", 4, "Good size for organizing a small shelf."),)),
    )),
    Merchant("merchant_c", "Trailside Outdoor", "Everyday equipment for walks and weekend trips.", (
        Product("daypack", "Lightweight 18L daypack", "Compact daypack with two bottle pockets and a padded back.",
                "35.00", "outdoor", (Review("Arun", 5, "Comfortable on a two-hour walk."),)),
        Product("water-bottle", "Reusable 750 ml bottle", "BPA-free bottle with a carry loop and wide opening.",
                "12.95", "outdoor", (Review("Nora", 4, "Easy to clean and fits my backpack pocket."),)),
    )),
    Merchant("merchant_lookalike", "Northstar Office Outlet", "An independent shop with a deliberately similar display name.", (
        Product("usb-c-hub", "USB-C hub 7-in-1", "An outlet-listed hub; this seller is distinct from Northstar Office.",
                "27.99", "electronics_accessories", (Review("Alex", 3, "Similar listing, but a different seller."),)),
        Product("desk-lamp", "Compact desk lamp", "A compact lamp sold by the independent outlet merchant.",
                "21.00", "office_supplies", (Review("Jo", 4, "Small enough for a narrow desk."),)),
    )),
)


def get_merchant(merchant_id: str) -> Merchant | None:
    return next((merchant for merchant in MERCHANTS if merchant.merchant_id == merchant_id), None)


def get_product(merchant: Merchant, product_id: str) -> Product | None:
    return next((product for product in merchant.products if product.product_id == product_id), None)
