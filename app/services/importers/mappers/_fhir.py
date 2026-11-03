"""
Small FHIR helpers shared by the resource mappers.
"""

from datetime import date
from typing import Any, List, Optional, Tuple

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
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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


def find_resource(bundle: dict, resource_type: str, resource_id: str) -> Optional[dict]:
    """A bundle entry by type and id, or None."""
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if (
            isinstance(resource, dict)
            and resource.get("resourceType") == resource_type
            and resource.get("id") == resource_id
        ):
            return resource
    return None


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
