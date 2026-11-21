"""
Cleanup functions: turn the different spellings of the same key into one.

Pure functions only. No values from the portal data live here; supplier ids are learned from the
supplier profiles at runtime.
"""

import re
from datetime import date, datetime

_LIST_SEP = re.compile(r"\s*[|,;]\s*")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return isinstance(value, str) and not value.strip()


def clean(value) -> str | None:
    """Strip a value to a string, or None when it is empty or missing."""
    return None if is_blank(value) else str(value).strip()


def split_list(value) -> list[str]:
    """Split a joined list such as 'a|b' or 'a, b' into its parts."""
    text = clean(value)
    if text is None:
        return []
    return [part for part in _LIST_SEP.split(text) if part]


def po_line(value) -> str | None:
    """
    Bring PO line numbers to one form.

    SAP item numbers are zero-padded and count in steps of ten ('00010' is line 1, '00510' is
    line 51). Plain numbers ('1') are taken as they are.
    """
    text = clean(value)
    if text is None or not text.isdigit():
        return text
    number = int(text)
    if len(text) >= 5 and text.startswith("0") and number % 10 == 0:
        return str(number // 10)
    return str(number)


def parse_number(value, decimal_comma: bool = False) -> float | None:
    """Parse '1,5200' (decimal comma) or '44,000' (thousands comma). None when unparseable."""
    text = clean(value)
    if text is None:
        return None
    text = text.replace(" ", "").replace(" ", "")
    if decimal_comma:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value, formats: tuple[str, ...] = ("%Y-%m-%d",)) -> str | None:
    """Return an ISO date (YYYY-MM-DD), or None when the value matches none of the formats."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = clean(value)
    if text is None:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def po_line_state(quantity: float | None, delivered_date: str | None) -> str:
    """
    State of a PO line, derived from facts rather than status codes ('GL', 'delivered', ...),
    which differ between exports: 'cancellation' (negative quantity), 'delivered' (has a delivery
    date) or 'open'.
    """
    if quantity is not None and quantity < 0:
        return "cancellation"
    return "delivered" if delivered_date else "open"


def name_key(value) -> str:
    """Comparable form of a name: lower case, letters and digits only."""
    return _NON_ALNUM.sub("", str(value).lower())


class SupplierResolver:
    """
    Maps every way the data names a supplier to one supplier id.

    Learned from the supplier profiles: the id itself ('SUP-0019'), its number ('19'), the name,
    the legal name and the portal folder name ('SUP-0019_Dongguan_Precision_Housings').
    """

    def __init__(self, profiles: list[dict]):
        self._by_key: dict[str, str] = {}
        self._by_number: dict[int, str] = {}
        for profile in profiles:
            supplier_id = clean(profile.get("supplier_id"))
            if supplier_id is None:
                continue
            for name in (supplier_id, profile.get("name"), profile.get("legal_name")):
                if clean(name):
                    self._by_key[name_key(name)] = supplier_id
            for number in (re.sub(r"\D", "", supplier_id), clean(profile.get("vendor_no"))):
                if number and number.isdigit():
                    self._by_number[int(number)] = supplier_id

    def resolve(self, value) -> str | None:
        text = clean(value)
        if text is None:
            return None
        if text.isdigit():
            return self._by_number.get(int(text))
        found = self._by_key.get(name_key(text))
        if found:
            return found
        # Folder names: '<id>_<name>'
        head = text.split("_", 1)[0]
        return self._by_key.get(name_key(head)) if head != text else None
