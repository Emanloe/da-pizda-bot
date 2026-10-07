"""Assemble one loot name from the quality, adjective, base, and source lists."""

import random

from loot.catalog import ADJECTIVES, BASES, QUALITIES, SOURCES
from loot.translit import transliterate


DOUBLE_ADJECTIVE_CHANCE = 0.1


def _weighted(rng: random.Random, items: tuple[dict, ...]) -> dict:
    if not items:
        raise ValueError("Loot list is empty")
    return rng.choices(items, weights=[item["weight"] for item in items], k=1)[0]


def _with_form(items: tuple[dict, ...], list_key: str) -> tuple[dict, ...]:
    return tuple(item for item in items if item.get(list_key))


def generate(rng: random.Random | None = None) -> dict:
    """Pick a base, then words that agree with it, then a genitive source."""
    rng = rng or random.Random()
    base = _weighted(rng, BASES)
    list_key = base["list"]
    quality = _weighted(rng, _with_form(QUALITIES, list_key))
    adjectives = _with_form(ADJECTIVES, list_key)
    adjective = _weighted(rng, adjectives)
    source = _weighted(rng, SOURCES)
    chosen = [adjective]
    remaining = tuple(
        item for item in adjectives if item[list_key] != adjective[list_key]
    )
    if remaining and rng.random() < DOUBLE_ADJECTIVE_CHANCE:
        chosen.append(_weighted(rng, remaining))
    name = " ".join((
        quality[list_key],
        *(item[list_key] for item in chosen),
        base["name"],
        source["name"],
    ))
    return {
        "name": name,
        "item_id": transliterate(name),
        "quality_id": quality["id"],
        "adjective_id": adjective["id"],
        "adjective_ids": tuple(item["id"] for item in chosen),
        "base_id": base["id"],
        "source_id": source["id"],
        "tier": quality["tier"],
        "kind": base["kind"],
    }
