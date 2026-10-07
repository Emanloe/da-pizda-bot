"""Latin item id from a generated Russian name."""

import re


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_LETTERS = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_SEPARATORS = set(" \t\n-—–_")


def transliterate(name: str) -> str:
    parts = []
    for char in name.lower():
        if char in _LETTERS:
            parts.append(_LETTERS[char])
        elif char in _SEPARATORS:
            parts.append("_")
        elif "a" <= char <= "z" or char.isdigit():
            parts.append(char)
    item_id = re.sub(r"_+", "_", "".join(parts)).strip("_")
    if not _ID_PATTERN.fullmatch(item_id):
        raise ValueError(f"Name {name!r} did not transliterate to an item id")
    return item_id
