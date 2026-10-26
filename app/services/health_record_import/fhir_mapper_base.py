"""Shared helpers and result types for the FHIR mapper modules."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from app.services.health_record_import.vocab import MAX_LEN

CanonicalLookup = Callable[[str], Tuple[Optional[str], Optional[str]]]


@dataclass
class UnmappedItem:
    """A resource (or part of one) the mapper did not place."""

    resource_type: str
    reason: str
    resource_id: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class MappedRecords:
    """Everything the mapper produced for one Bundle."""

    conditions: List[dict] = field(default_factory=list)
    allergies: List[dict] = field(default_factory=list)
    medications: List[dict] = field(default_factory=list)
    immunizations: List[dict] = field(default_factory=list)
    procedures: List[dict] = field(default_factory=list)
    encounters: List[dict] = field(default_factory=list)
    vitals: List[dict] = field(default_factory=list)
    lab_results: List[dict] = field(default_factory=list)  # each has "components"
    patient: Optional[dict] = None
    unmapped: List[UnmappedItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def entity_lists(self) -> Dict[str, List[dict]]:
        return {
            "conditions": self.conditions,
            "allergies": self.allergies,
            "medications": self.medications,
            "immunizations": self.immunizations,
            "procedures": self.procedures,
            "encounters": self.encounters,
            "vitals": self.vitals,
            "lab_results": self.lab_results,
        }


# --- small FHIR helpers -----------------------------------------------------


def _codings(concept: Optional[dict]) -> List[dict]:
    if not isinstance(concept, dict):
        return []
    return [c for c in concept.get("coding") or [] if isinstance(c, dict)]


def code_for(concept: Optional[dict], *systems: str) -> Optional[str]:
    """Return the code from the first coding whose system is one of ``systems``."""
    for system in systems:
        for coding in _codings(concept):
            if coding.get("system") == system and coding.get("code"):
                return str(coding["code"])
    return None


def display_for(concept: Optional[dict]) -> Optional[str]:
    """Human-readable text for a CodeableConcept: text, then a display, then a code."""
    if not isinstance(concept, dict):
        return None
    text = concept.get("text")
    if text and str(text).strip():
        return str(text).strip()
    for coding in _codings(concept):
        if coding.get("display"):
            return str(coding["display"]).strip()
    for coding in _codings(concept):
        if coding.get("code"):
            return str(coding["code"])
    return None


def first_code(concept: Optional[dict]) -> Optional[str]:
    for coding in _codings(concept):
        if coding.get("code"):
            return str(coding["code"])
    return None


def parse_date(value) -> Optional[date]:
    """FHIR date/dateTime/instant (any precision) to a date; None when unusable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if len(text) == 4:
            return date(int(text), 1, 1)
        if len(text) == 7:
            return date(int(text[:4]), int(text[5:7]), 1)
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_datetime(value) -> Optional[datetime]:
    """FHIR dateTime to a datetime.

    Values with a UTC offset come back as aware UTC datetimes; date-only values
    and values without an offset come back naive (midnight for dates).
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        if "T" in text:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc)
            return parsed
        day = parse_date(text)
        return datetime(day.year, day.month, day.day) if day else None
    except ValueError:
        return None


def truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _period_or_datetime(
    resource: dict, base: str
) -> Tuple[Optional[str], Optional[str]]:
    """Return (start, end) strings from ``<base>DateTime`` or ``<base>Period``."""
    if resource.get(f"{base}DateTime"):
        return resource[f"{base}DateTime"], None
    period = resource.get(f"{base}Period") or {}
    return period.get("start"), period.get("end")


def _notes(resource: dict) -> Optional[str]:
    texts = [n.get("text") for n in resource.get("note") or [] if isinstance(n, dict)]
    joined = "\n".join(t.strip() for t in texts if t and t.strip())
    return truncate(joined, MAX_LEN["notes"]) if joined else None


def _ref_id(reference: Optional[dict]) -> Optional[str]:
    ref = (reference or {}).get("reference") if isinstance(reference, dict) else None
    if not ref or not isinstance(ref, str):
        return None
    ref = ref.replace("urn:uuid:", "")
    return ref.rsplit("/", 1)[-1] or None


def _category_codes(resource: dict) -> List[str]:
    codes = []
    for concept in resource.get("category") or []:
        for coding in _codings(concept):
            if coding.get("code"):
                codes.append(str(coding["code"]).lower())
        text = (concept or {}).get("text") if isinstance(concept, dict) else None
        if text:
            codes.append(str(text).lower())
    return codes


# --- per-resource mappers ---------------------------------------------------


def quantity_of(observation: dict) -> Tuple[Optional[float], Optional[str]]:
    """Return (value, unit) from ``valueQuantity``; (None, None) when absent or not numeric."""
    quantity = observation.get("valueQuantity")
    if not isinstance(quantity, dict) or quantity.get("value") is None:
        return None, None
    try:
        return float(quantity["value"]), quantity.get("unit") or quantity.get("code")
    except (TypeError, ValueError):
        return None, None
