"""Immutable local catalog models; prices use decimal strings, never floats."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Review:
    author: str
    rating: int
    text: str


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    description: str
    price: str
    category: str
    reviews: tuple[Review, ...]
    currency: str = "USD"


@dataclass(frozen=True)
class Merchant:
    merchant_id: str
    name: str
    description: str
    products: tuple[Product, ...]
