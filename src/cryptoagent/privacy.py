"""Keep the user's personal positions out of persistent records.

The user shares holdings and entry prices in conversation when asking for an assessment; those
details must never be written to claims, the journal, predictions or recommendations. This guard
rejects text that reads like a personal position. It is a backstop for the skills' rule, not a
proof: phrasing it doesn't recognise can still slip through, so the skills must not write such
details in the first place.
"""

from __future__ import annotations

import json
import re

_PATTERNS = [
    r"\bI(?:'ve| have)? (?:bought|own|hold|sold|added|averaged|entered|paid)\b",
    r"\bmy (?:position|positions|bag|bags|holdings?|entry|entries|cost basis|portfolio|stack|average|avg|trade|buy)\b",
    r"\b(?:the )?user(?:'s)? (?:position|positions|holdings?|entry|entries|cost basis|portfolio|bought|owns|holds|"
    r"paid|average|avg)\b",
    r"\b(?:average|avg)\.? (?:entry|cost|buy price)\b",
    r"\bcost basis\b",
]
_REGEX = re.compile("|".join(_PATTERNS), re.IGNORECASE)


class PrivacyError(ValueError):
    pass


def check(*values) -> None:
    """Raise if any value (strings, or dicts/lists serialised as JSON) reads like a personal position."""
    for value in values:
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        match = _REGEX.search(text)
        if match:
            raise PrivacyError(f"refusing to store personal position details ({match.group(0)!r}): the user's holdings "
                               "and entry prices stay in the conversation; record the market view anonymously")
