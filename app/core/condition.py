"""Stock condition: new, used/refurbished, or not stated.

Offers carry a free-text condition ("Grade A refurbished", "New", "new surplus").
Everything that must not misdescribe stock (landing pages, ads, keywords) asks
this module, so a claim like "brand new" is possible only when the catalogue
itself says the stock is new, and "refurbished" is never printed on new stock.

Rules are deliberately conservative: text that does not clearly say "new" is
never treated as new.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

NEW = "new"
USED = "used"          # used, refurbished, graded, open-box, ex-lease ...
MIXED = "mixed"        # the catalogue holds both new and used/unknown stock
UNKNOWN = "unknown"    # no offers, or no condition stated

_USED_RE = re.compile(
    r"refurb|renew(?:ed)?\b|recondition|\bused\b|pre[- ]?owned|second[- ]?hand|\bgrade\s*[a-d]\b|"
    r"open[- ]?box|\bex[- ]?(?:lease|corporate|demo|display|rental)\b|\bdemo\b|like[- ]new|as[- ]new|\bcpo\b",
    re.IGNORECASE,
)
_NEW_RE = re.compile(r"\bnew\b|brand[- ]new|\bsealed\b|\bunused\b|\bnos\b|new[- ]old[- ]stock", re.IGNORECASE)


def classify(condition: str | None) -> str:
    """'new', 'used' or 'unknown' for one condition string."""
    text = (condition or "").strip()
    if not text:
        return UNKNOWN
    if _USED_RE.search(text):
        return USED
    if _NEW_RE.search(text):
        return NEW
    return UNKNOWN


def stock_profile(conditions: Iterable[str | None]) -> str:
    """The honest description of a whole catalogue line.

    new    every offer is stated as new
    used   at least one is used/refurbished and none is new
    mixed  new stock alongside used or unstated stock
    unknown nothing is stated
    """
    kinds = {classify(c) for c in conditions}
    if not kinds or kinds == {UNKNOWN}:
        return UNKNOWN
    if kinds == {NEW}:
        return NEW
    if NEW in kinds:
        return MIXED
    return USED
