"""Strictly recover metadata from names made by the historical loot generator."""

from hashlib import sha256
from pathlib import Path

from loot.catalog import ADJECTIVES, BASES, QUALITIES, SOURCES
from loot.translit import transliterate


# Freeze the catalog this migration was audited against. Later catalog edits must
# not reinterpret old canonical names under different generation rules.
_CATALOG_HASHES = {
    "qualities.json": "4e697c74ef3c31db1ac3c0e85d265944d8e570f4dec993ddac61050071348cd3",
    "adjectives.json": "affe4101519e453b5bd57e1f7ccca403deb8404e634b62532821da99a7c3ecf8",
    "bases.json": "957f280887f39ef5313ed56701d83f9a33926cda50f3a23b4afccec3d55940e0",
    "sources.json": "bc42607e71de3d39b33486ad2bd915f7612ea144f05e5f02129b905b727c2ba7",
}


def _catalog_sha256(path: Path) -> str:
    # Git checks out LF on Linux and CRLF on Windows; line endings are not catalog data.
    return sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def legacy_catalog_matches_snapshot() -> bool:
    catalog_dir = Path(__file__).resolve().parent.parent / "data" / "loot"
    return all(_catalog_sha256(catalog_dir / filename) == expected
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
