"""Strictly recover metadata from names made by the historical loot generator."""

from hashlib import sha256
from pathlib import Path

from loot.catalog import ADJECTIVES, BASES, QUALITIES, SOURCES
from loot.translit import transliterate


# Freeze the catalog this migration was audited against. Later catalog edits must
# not reinterpret old canonical names under different generation rules.
_CATALOG_HASHES = {
    "qualities.json": "83a6290465393a7a2017669b0cc1a53fb8d00c47520e67ff8ed55c1447c3c7bf",
    "adjectives.json": "574339dc10202325487fd640350c6297d8657c9329f843d198c02f46a4a15c67",
    "bases.json": "bcaadd5269a00e09dc93e782385a92cc169e49a4d9bdceabb3e0038129a73626",
    "sources.json": "d1982dd82ded365ea9d067e5e0cbd907495f74265e6edd97e2cc1c3f266bc5f3",
}


def legacy_catalog_matches_snapshot() -> bool:
    catalog_dir = Path(__file__).resolve().parent.parent / "data" / "loot"
    return all(sha256((catalog_dir / filename).read_bytes()).hexdigest() == expected
               for filename, expected in _CATALOG_HASHES.items())


def _suffixes(value: str, parts, separator: str):
    for part, metadata in parts:
        suffix = separator + part
        if value.endswith(suffix):
            yield value[:-len(suffix)], metadata


def _valid_prefix(prefix: str, form: str, separator: str, convert) -> bool:
    adjectives = {convert(item[form]) for item in ADJECTIVES}
    for quality in QUALITIES:
        first_part = convert(quality[form]) + separator
        if not prefix.startswith(first_part):
            continue
        remainder = prefix[len(first_part):]
        if remainder in adjectives:
            return True
        for first_adjective in adjectives:
            next_part = first_adjective + separator
            if remainder.startswith(next_part):
                second_adjective = remainder[len(next_part):]
                if second_adjective in adjectives and second_adjective != first_adjective:
                    return True
    return False


def _possible_metadata(value: str, separator: str, convert) -> set[tuple[str, str]]:
    sources = [(convert(source["name"]), None) for source in SOURCES]
    bases = [(convert(base["name"]), (base["kind"], base["list"])) for base in BASES]
    result = set()
    for without_source, _ in _suffixes(value, sources, separator):
        for prefix, metadata in _suffixes(without_source, bases, separator):
            if _valid_prefix(prefix, metadata[1], separator, convert):
                result.add(metadata)
    return result


def recover_legacy_generated_metadata(item_id: str, name: str):
    """Return (status, metadata), rejecting ambiguous names and ID collisions."""
    if not isinstance(item_id, str) or not isinstance(name, str):
        return "unknown", None
    try:
        if transliterate(name) != item_id:
            return "unknown", None
    except ValueError:
        return "unknown", None
    exact = _possible_metadata(name, " ", lambda value: value)
    if not exact:
        return "unknown", None
    if len(exact) != 1:
        return "ambiguous", None
    possible_ids = _possible_metadata(item_id, "_", transliterate)
    if len(possible_ids) != 1 or exact != possible_ids:
        return "ambiguous", None
    return "recovered", next(iter(exact))
