"""Generated-loot word lists. Qualities agree with the item in nominative."""

import json
import re
from pathlib import Path


_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "loot"
_QUALITIES_PATH = _DATA_DIR / "qualities.json"
_BASES_PATH = _DATA_DIR / "bases.json"
_ADJECTIVES_PATH = _DATA_DIR / "adjectives.json"
_SOURCES_PATH = _DATA_DIR / "sources.json"
_LISTS = ("m", "f", "n", "pl")
_QUALITY_FIELDS = {"id", "tier", "weight", *_LISTS}
_ADJECTIVE_FIELDS = {"id", "weight", *_LISTS}
_SOURCE_FIELDS = {"id", "name", "weight"}
_BASE_FIELDS = {"id", "name", "list", "kind", "weight"}
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _load_qualities(path=_QUALITIES_PATH) -> tuple[dict, ...]:
    try:
        with open(path, encoding="utf-8") as qualities_file:
            qualities = json.load(qualities_file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in loot qualities: {path}") from exc

    if not isinstance(qualities, list) or not qualities:
        raise ValueError("qualities.json must contain a non-empty list")

    normalized = []
    ids = set()
    tiers = set()
    for quality in qualities:
        if not isinstance(quality, dict) or set(quality) != _QUALITY_FIELDS:
            raise ValueError("Every quality must contain id, tier, weight, m, f, n, pl")
        quality_id = quality["id"]
        tier = quality["tier"]
        weight = quality["weight"]
        if not isinstance(quality_id, str) or not _ID_PATTERN.fullmatch(quality_id):
            raise ValueError(f"Invalid quality id: {quality_id!r}")
        if quality_id in ids:
            raise ValueError(f"Duplicate quality id: {quality_id}")
        if not isinstance(tier, int) or isinstance(tier, bool) or tier < 1:
            raise ValueError(f"Invalid quality tier for {quality_id}")
        if tier in tiers:
            raise ValueError(f"Duplicate quality tier: {tier}")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            raise ValueError(f"Invalid quality weight for {quality_id}")
        forms = {}
        for list_key in _LISTS:
            word = quality[list_key]
            if not isinstance(word, str) or not word.strip():
                raise ValueError(f"Quality {quality_id} has an empty {list_key} form")
            forms[list_key] = word
        ids.add(quality_id)
        tiers.add(tier)
        normalized.append({
            "id": quality_id,
            "tier": tier,
            "weight": weight,
            **forms,
        })

    return tuple(normalized)


def _load_json_list(path: Path, label: str):
    try:
        with open(path, encoding="utf-8") as data_file:
            items = json.load(data_file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in loot {label}: {path}") from exc
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path.name} must contain a non-empty list")
    return items


def _load_bases(path=_BASES_PATH) -> tuple[dict, ...]:
    normalized = []
    ids = set()
    names = set()
    for base in _load_json_list(path, "bases"):
        if not isinstance(base, dict) or set(base) != _BASE_FIELDS:
            raise ValueError("Every base must contain id, name, list, kind, weight")
        base_id = base["id"]
        name = base["name"]
        list_key = base["list"]
        kind = base["kind"]
        weight = base["weight"]
        if not isinstance(base_id, str) or not _ID_PATTERN.fullmatch(base_id):
            raise ValueError(f"Invalid base id: {base_id!r}")
        if base_id in ids:
            raise ValueError(f"Duplicate base id: {base_id}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid base name for {base_id}")
        if name in names:
            raise ValueError(f"Duplicate base name: {name}")
        if list_key not in _LISTS:
            raise ValueError(f"Base {base_id} has an unknown list {list_key!r}")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError(f"Base {base_id} has an empty kind")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            raise ValueError(f"Invalid base weight for {base_id}")
        ids.add(base_id)
        names.add(name)
        normalized.append({
            "id": base_id,
            "name": name,
            "list": list_key,
            "kind": kind,
            "weight": weight,
        })
    return tuple(normalized)


def _load_adjectives(path=_ADJECTIVES_PATH) -> tuple[dict, ...]:
    normalized = []
    ids = set()
    for adjective in _load_json_list(path, "adjectives"):
        if not isinstance(adjective, dict) or set(adjective) != _ADJECTIVE_FIELDS:
            raise ValueError("Every adjective must contain id, weight, m, f, n, pl")
        adjective_id = adjective["id"]
        weight = adjective["weight"]
        if not isinstance(adjective_id, str) or not _ID_PATTERN.fullmatch(adjective_id):
            raise ValueError(f"Invalid adjective id: {adjective_id!r}")
        if adjective_id in ids:
            raise ValueError(f"Duplicate adjective id: {adjective_id}")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            raise ValueError(f"Invalid adjective weight for {adjective_id}")
        forms = {}
        for list_key in _LISTS:
            word = adjective[list_key]
            if not isinstance(word, str) or not word.strip():
                raise ValueError(f"Adjective {adjective_id} has an empty {list_key} form")
            forms[list_key] = word
        ids.add(adjective_id)
        normalized.append({"id": adjective_id, "weight": weight, **forms})
    return tuple(normalized)


def _load_sources(path=_SOURCES_PATH) -> tuple[dict, ...]:
    normalized = []
    ids = set()
    names = set()
    for source in _load_json_list(path, "sources"):
        if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
            raise ValueError("Every source must contain id, name, weight")
        source_id = source["id"]
        name = source["name"]
        weight = source["weight"]
        if not isinstance(source_id, str) or not _ID_PATTERN.fullmatch(source_id):
            raise ValueError(f"Invalid source id: {source_id!r}")
        if source_id in ids:
            raise ValueError(f"Duplicate source id: {source_id}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid source name for {source_id}")
        if name in names:
            raise ValueError(f"Duplicate source name: {name}")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            raise ValueError(f"Invalid source weight for {source_id}")
        ids.add(source_id)
        names.add(name)
        normalized.append({"id": source_id, "name": name, "weight": weight})
    return tuple(normalized)


QUALITIES = _load_qualities()
ADJECTIVES = _load_adjectives()
BASES = _load_bases()
SOURCES = _load_sources()
