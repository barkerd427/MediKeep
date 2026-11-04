"""
Small FHIR helpers shared by the resource mappers.
"""

from collections import OrderedDict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

SNOMED = "http://snomed.info/sct"
ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10 = "http://hl7.org/fhir/sid/icd-10"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"


def codings(concept: Optional[dict]) -> List[dict]:
    """Coding entries of a CodeableConcept; empty for None or malformed input."""
    if not isinstance(concept, dict):
        return []
    return [c for c in concept.get("coding") or [] if isinstance(c, dict)]


def code_for(concept: Optional[dict], *systems: str) -> Optional[str]:
    """Code of the first coding in one of the given systems, in priority order."""
    for system in systems:
        for coding in codings(concept):
            if coding.get("system") == system and coding.get("code"):
                return str(coding["code"])
    return None


def first_code(concept: Optional[dict]) -> Optional[str]:
    """First code in a CodeableConcept regardless of system."""
    for coding in codings(concept):
        if coding.get("code"):
            return str(coding["code"])
    return None


def display_for(concept: Optional[dict]) -> Optional[str]:
    """Text for a CodeableConcept, falling back from text to display to code."""
    if not isinstance(concept, dict):
        return None
    text = concept.get("text")
    if text and str(text).strip():
        return str(text).strip()
    for coding in codings(concept):
        if coding.get("display"):
            return str(coding["display"]).strip()
    return first_code(concept)


def status_code(resource: dict, field: str, system: str) -> str:
    """Lower-cased code of a status CodeableConcept such as clinicalStatus."""
    concept = resource.get(field)
    return (code_for(concept, system) or first_code(concept) or "").lower()


def parse_date(value: Any) -> Optional[date]:
    """Parse a FHIR date or dateTime of any precision into a date."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if len(text) == 4:
            return date(int(text), 1, 1)
        if len(text) == 7:
            return date(int(text[:4]), int(text[5:7]), 1)
        # The calendar date as written in the source's own zone; no UTC shift
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def to_float(value: Any) -> Optional[float]:
    """A finite float from a number or numeric string, else None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def period(resource: dict, base: str) -> Tuple[Optional[str], Optional[str]]:
    """(start, end) strings from the <base>DateTime or <base>Period element."""
    if resource.get(f"{base}DateTime"):
        return resource[f"{base}DateTime"], None
    value = resource.get(f"{base}Period") or {}
    return value.get("start"), value.get("end")


def notes_text(resource: dict) -> Optional[str]:
    """Join the resource's Annotation texts into one string."""
    texts = [n.get("text") for n in resource.get("note") or [] if isinstance(n, dict)]
    joined = "\n".join(t.strip() for t in texts if t and t.strip())
    return joined or None


def ref_id(reference: Optional[dict]) -> Optional[str]:
    """Resource id from a Reference, accepting "Type/id", "#id" and "urn:uuid:id"."""
    ref = reference.get("reference") if isinstance(reference, dict) else None
    if not ref:
        return None
    ref = str(ref).replace("urn:uuid:", "").lstrip("#")
    return ref.rsplit("/", 1)[-1] or None


class BundleIndex:
    """Resources of one bundle keyed by (type, id), plus room for mapper caches."""

    def __init__(self, bundle: dict):
        """Walk the bundle once."""
        self.resources: Dict[Tuple[str, str], dict] = {}
        self.by_type: Dict[str, List[dict]] = {}
        self.memo: Dict[str, Any] = {}  # Per-bundle caches owned by the mappers
        self.entries = len(bundle.get("entry") or [])
        for entry in bundle.get("entry") or []:
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                continue
            resource_type = resource.get("resourceType")
            if not isinstance(resource_type, str):
                continue
            self.by_type.setdefault(resource_type, []).append(resource)
            if resource.get("id") is not None:
                self.resources.setdefault(
                    (resource_type, str(resource["id"])), resource
                )

    def get(self, resource_type: str, resource_id: Any) -> Optional[dict]:
        """A resource by type and id, or None."""
        if resource_id is None:
            return None
        return self.resources.get((resource_type, str(resource_id)))


# The last few bundles seen, keyed by object id; the bundle itself is kept so
# the id cannot be reused while its index is cached
_INDEXES: "OrderedDict[int, Tuple[dict, BundleIndex]]" = OrderedDict()
_INDEX_LIMIT = 4


def bundle_index(bundle: dict) -> BundleIndex:
    """The BundleIndex for a bundle, built on first use and reused while cached."""
    entries = len(bundle.get("entry") or [])
    cached = _INDEXES.get(id(bundle))
    if cached is not None and cached[0] is bundle and cached[1].entries == entries:
        return cached[1]
    index = BundleIndex(bundle)
    _INDEXES[id(bundle)] = (bundle, index)
    while len(_INDEXES) > _INDEX_LIMIT:
        _INDEXES.popitem(last=False)
    return index


def find_resource(bundle: dict, resource_type: str, resource_id: str) -> Optional[dict]:
    """A bundle entry by type and id, or None."""
    return bundle_index(bundle).get(resource_type, resource_id)


def fit(
    value: Optional[str], limit: int, warnings: List[str], what: str
) -> Optional[str]:
    """Strip and shorten text to a column limit, recording a warning when it is cut."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        warnings.append(f"{what} truncated to {limit} characters")
        return text[: limit - 3].rstrip() + "..."
    return text


def norm(value: Optional[str]) -> Optional[str]:
    """Lower-cased, whitespace-collapsed text for hashing, or None."""
    if value is None:
        return None
    text = " ".join(str(value).split()).lower()
    return text or None


def iso(value: Optional[date]) -> Optional[str]:
    """ISO date string for hashing, or None."""
    return value.isoformat() if value else None
