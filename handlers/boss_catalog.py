"""Boss definitions and one-time selection for a future battle."""

from text_resources import _get_resource_value, get_text_list


LEGACY_REQUIRED_HITS = 5


def _positive_hp(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid boss HP for {label}")
    return value


def _load_unique_bosses() -> tuple[dict, ...]:
    catalog = _get_resource_value("boss.catalog")
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("boss.catalog must contain at least one unique boss")
    bosses = []
    for boss_id, entry in catalog.items():
        if not isinstance(boss_id, str) or not isinstance(entry, dict):
            raise ValueError("Invalid unique boss entry")
        name, emoji, description = (
            entry.get("name"), entry.get("emoji"), entry.get("description")
        )
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid boss name for {boss_id}")
        if not isinstance(emoji, str) or not emoji.strip():
            raise ValueError(f"Invalid boss emoji for {boss_id}")
        if description is not None and not isinstance(description, str):
            raise ValueError(f"Invalid boss description for {boss_id}")
        bosses.append({
            "id": boss_id,
            "name": name,
            "emoji": emoji,
            "description": description or None,
            "max_hp": _positive_hp(entry.get("hp"), boss_id),
        })
    return tuple(bosses)


UNIQUE_BOSSES = _load_unique_bosses()
BOSS_CATALOG_IDS = tuple(boss["id"] for boss in UNIQUE_BOSSES)

_GENERATION = _get_resource_value("boss.generation")
if not isinstance(_GENERATION, dict):
    raise ValueError("boss.generation must be a mapping")
UNIQUE_CHANCE = _GENERATION.get("unique_chance")
if (isinstance(UNIQUE_CHANCE, bool) or not isinstance(UNIQUE_CHANCE, (int, float))
        or not 0 <= UNIQUE_CHANCE <= 1):
    raise ValueError("boss.generation.unique_chance must be between 0 and 1")
MIN_GENERATED_HP = _positive_hp(_GENERATION.get("min_hp"), "generated minimum")
MAX_GENERATED_HP = _positive_hp(_GENERATION.get("max_hp"), "generated maximum")
if MIN_GENERATED_HP > MAX_GENERATED_HP:
    raise ValueError("Generated boss HP range is inverted")
GENERATED_EMOJI = _GENERATION.get("emoji")
if not isinstance(GENERATED_EMOJI, str) or not GENERATED_EMOJI.strip():
    raise ValueError("Generated boss emoji is missing")
NAME_PARTS = tuple(
    tuple(get_text_list(f"boss.generation.{key}"))
    for key in ("first_parts", "second_parts", "third_parts")
)
if any(not parts or any(not part.strip() or len(part.split()) != 1 for part in parts)
       for parts in NAME_PARTS):
    raise ValueError("Generated boss name pools must contain single words")


def choose_boss(rng) -> dict:
    """Select a configured unique boss or generate three independent words and HP."""
    if rng.random() < UNIQUE_CHANCE:
        return dict(rng.choice(UNIQUE_BOSSES))
    return {
        "id": "generated",
        "name": " ".join(rng.choice(parts) for parts in NAME_PARTS),
        "emoji": GENERATED_EMOJI,
        "description": None,
        "max_hp": rng.randint(MIN_GENERATED_HP, MAX_GENERATED_HP),
    }


def boss_required_hits(battle: dict) -> int:
    """Old in-memory battles without per-boss HP keep their original five hits."""
    return battle["boss"].get("max_hp", LEGACY_REQUIRED_HITS)
