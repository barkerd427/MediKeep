"""
Maps FHIR MedicationStatement and MedicationRequest resources onto the Medication table.
"""

from typing import Any, List, Optional, Tuple

from app.models.clinical import Medication

from . import _fhir as f
from .base_mapper import BaseResourceMapper, MappedRecord

STATEMENT_STATUS = {
    "active": "active",
    "intended": "active",
    "completed": "completed",
    "stopped": "stopped",
    "on-hold": "on-hold",
    "not-taken": "cancelled",
}
REQUEST_STATUS = {
    "active": "active",
    "draft": "active",
    "completed": "completed",
    "stopped": "stopped",
    "on-hold": "on-hold",
}
SKIPPED_STATUS = ("entered-in-error", "cancelled")

ROUTES = {
    "oral": "oral",
    "by mouth": "oral",
    "po": "oral",
    "injection": "injection",
    "topical": "topical",
    "cutaneous": "topical",
    "transdermal": "topical",
    "intravenous": "intravenous",
    "iv": "intravenous",
    "intramuscular": "intramuscular",
    "im": "intramuscular",
    "subcutaneous": "subcutaneous",
    "sc": "subcutaneous",
    "inhalation": "inhalation",
    "respiratory (inhalation)": "inhalation",
    "inhaled": "inhalation",
    "nasal": "nasal",
    "intranasal": "nasal",
    "rectal": "rectal",
    "sublingual": "sublingual",
    "otic": "aural",
    "auricular (otic)": "aural",
}
DOSE_UNITS = {
    "{tbl}": "tablet",
    "{capsule}": "capsule",
    "{puff}": "puff",
    "{spray}": "spray",
    "{drop}": "drop",
}
PERIOD_UNITS = {
    "s": "second",
    "min": "minute",
    "h": "hour",
    "d": "day",
    "wk": "week",
    "mo": "month",
    "a": "year",
}

MAX_NAME = 100
MAX_DOSAGE = 50
MAX_FREQUENCY = 50
MAX_ROUTE = 50
MAX_INDICATION = 500
MAX_NOTES = 5000


def medication_name(resource: dict, bundle: dict) -> Optional[str]:
    """Drug name from the inline concept, a contained Medication, or a bundle entry."""
    name = f.display_for(resource.get("medicationCodeableConcept"))
    if name:
        return name
    reference = resource.get("medicationReference") or {}
    target_id = f.ref_id(reference)
    for contained in resource.get("contained") or []:
        if isinstance(contained, dict) and contained.get("id") == target_id:
            return f.display_for(contained.get("code"))
    target = f.find_resource(bundle, "Medication", target_id) if target_id else None
    if target:
        return f.display_for(target.get("code"))
    return str(reference.get("display")).strip() if reference.get("display") else None


def _dose_text(dosage: dict) -> Optional[str]:
    """Dose from doseQuantity or doseRange, with UCUM count units spelled out."""
    for entry in dosage.get("doseAndRate") or []:
        if not isinstance(entry, dict):
            continue
        quantity = entry.get("doseQuantity")
        if isinstance(quantity, dict) and quantity.get("value") is not None:
            unit = quantity.get("unit") or quantity.get("code") or ""
            return f"{quantity['value']:g} {DOSE_UNITS.get(unit, unit)}".strip()
        rng = entry.get("doseRange")
        if isinstance(rng, dict):
            low = (rng.get("low") or {}).get("value")
            high = (rng.get("high") or {}).get("value")
            unit = (rng.get("high") or rng.get("low") or {}).get("unit") or ""
            if low is not None and high is not None:
                return f"{low:g}-{high:g} {unit}".strip()
    return None


def _frequency_text(timing: Optional[dict]) -> Optional[str]:
    """Timing as short text like "daily" or "2 times per day"."""
    if not isinstance(timing, dict):
        return None
    repeat = timing.get("repeat") or {}
    freq = repeat.get("frequency") or 1
    period = repeat.get("period")
    unit = PERIOD_UNITS.get(repeat.get("periodUnit"))
    if period and unit:
        if freq == 1 and period == 1:
            text = "daily" if unit == "day" else f"every {unit}"
        elif freq == 1:
            text = f"every {period:g} {unit}s"
        elif period == 1:
            text = f"{freq} times per {unit}"
        else:
            text = f"{freq} times per {period:g} {unit}s"
    else:
        text = f.display_for(timing.get("code"))
    when = repeat.get("when")
    if text and when:
        text = f"{text} ({', '.join(str(w) for w in when)})"
    return text


class _MedicationMapper(BaseResourceMapper):
    """Shared mapping for both medication resource types."""

    RECORD_TYPE = "medication"
    MODEL = Medication
    STATUS_MAP: dict = {}
    DOSAGE_FIELD = "dosage"

    def map(self, resource: dict, bundle: dict) -> Optional[MappedRecord]:
        """Map one resource; None when cancelled, in error or unnamed."""
        raw_status = str(resource.get("status") or "").lower()
        if raw_status in SKIPPED_STATUS:
            return None
        name = medication_name(resource, bundle)
        if not name:
            return None

        warnings: list = []
        dosages = resource.get(self.DOSAGE_FIELD) or []
        dosage = dosages[0] if dosages and isinstance(dosages[0], dict) else {}
        sig = " ".join(str(dosage.get("text") or "").split()) or None
        dose = _dose_text(dosage) or sig
        route_text = f.display_for(dosage.get("route"))
        route = ROUTES.get((route_text or "").lower(), route_text)
        status = self.STATUS_MAP.get(raw_status, "active")
        start, end = self.dates(resource, dosage)
        if start and end and end < start:
            warnings.append("end before start, end date dropped")
            end = None

        dose = f.fit(dose, MAX_DOSAGE, warnings, "dosage")
        # The full sig goes to notes when the dosage column does not hold it as is
        notes_parts = [f"Sig: {sig}"] if sig and sig != dose else []
        if f.notes_text(resource):
            notes_parts.append(f.notes_text(resource))
        reasons = [f.display_for(c) for c in resource.get("reasonCode") or []]

        name = f.fit(name, MAX_NAME, warnings, "name")
        data = {
            "medication_name": name,
            "medication_type": "prescription",
            "dosage": dose,
            "frequency": f.fit(
                _frequency_text(dosage.get("timing")),
                MAX_FREQUENCY,
                warnings,
                "frequency",
            ),
            "route": f.fit(route, MAX_ROUTE, warnings, "route"),
            "indication": f.fit(
                ", ".join(r for r in reasons if r) or None,
                MAX_INDICATION,
                warnings,
                "indication",
            ),
            "effective_period_start": start,
            "effective_period_end": end,
            "status": status,
            "notes": f.fit(
                "\n".join(notes_parts) or None, MAX_NOTES, warnings, "notes"
            ),
        }
        return MappedRecord(
            record_type=self.RECORD_TYPE,
            external_id=resource.get("id"),
            data=data,
            hash_parts=self._parts(
                name, data["dosage"], data["frequency"], start, status
            ),
            label=f"{self.RESOURCE_TYPE}/{resource.get('id', '?')}",
            warnings=warnings,
        )

    def hash_parts_for_row(self, row: Any) -> Tuple[Any, ...]:
        return self._parts(
            row.medication_name,
            row.dosage,
            row.frequency,
            row.effective_period_start,
            row.status,
        )

    @staticmethod
    def _parts(name, dosage, frequency, start, status) -> Tuple[Any, ...]:
        """Normalize the same way from a resource and from a row."""
        return (f.norm(name), f.norm(dosage), f.norm(frequency), f.iso(start), status)

    def dates(self, resource: dict, dosage: dict):
        """(start, end) dates for the resource type."""
        raise NotImplementedError


class MedicationStatementMapper(_MedicationMapper):
    """MedicationStatement -> medications row."""

    RESOURCE_TYPE = "MedicationStatement"
    STATUS_MAP = STATEMENT_STATUS
    DOSAGE_FIELD = "dosage"

    def dates(self, resource: dict, dosage: dict):
        start, end = f.period(resource, "effective")
        return f.parse_date(start), f.parse_date(end)


class MedicationRequestMapper(_MedicationMapper):
    """MedicationRequest -> medications row, unless a statement already covers it."""

    RESOURCE_TYPE = "MedicationRequest"
    STATUS_MAP = REQUEST_STATUS
    DOSAGE_FIELD = "dosageInstruction"

    def map(self, resource: dict, bundle: dict) -> Optional[MappedRecord]:
        # A statement for the same drug and start date is the better record of the two
        if self._has_statement(resource, bundle):
            return None
        return super().map(resource, bundle)

    def dates(self, resource: dict, dosage: dict):
        bounds = ((dosage.get("timing") or {}).get("repeat") or {}).get(
            "boundsPeriod"
        ) or {}
        validity = (resource.get("dispenseRequest") or {}).get("validityPeriod") or {}
        start = (
            bounds.get("start") or validity.get("start") or resource.get("authoredOn")
        )
        end = bounds.get("end") or validity.get("end")
        return f.parse_date(start), f.parse_date(end)

    def _has_statement(self, resource: dict, bundle: dict) -> bool:
        key = self._drug_key(resource, bundle)
        if key is None:
            return False
        dosages = resource.get(self.DOSAGE_FIELD) or []
        start, _ = self.dates(resource, dosages[0] if dosages else {})
        for entry in bundle.get("entry") or []:
            other = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(other, dict):
                continue
            if other.get("resourceType") != "MedicationStatement":
                continue
            if str(other.get("status") or "").lower() in SKIPPED_STATUS:
                continue
            if self._drug_key(other, bundle) != key:
                continue
            other_start = f.parse_date(f.period(other, "effective")[0])
            if start is None or other_start is None or start == other_start:
                return True
        return False

    @staticmethod
    def _drug_key(resource: dict, bundle: dict) -> Optional[str]:
        """RxNorm code when present, else the lower-cased name."""
        concept = resource.get("medicationCodeableConcept")
        return f.code_for(concept, f.RXNORM) or f.norm(
            medication_name(resource, bundle)
        )
