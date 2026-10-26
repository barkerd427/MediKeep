"""Map FHIR R4 resources onto MediKeep create dicts.

Everything here is a pure function of the Bundle: no database access. Each
output dict carries ``patient_id``, ``external_id`` (the FHIR resource id,
deterministic when produced by healthrecord-bridge) and ``import_source``, and
is shaped so it passes the matching ``*Create`` Pydantic schema. Anything the
mapper cannot place is recorded in ``MappedRecords.unmapped`` with the resource
type and a reason, so nothing disappears silently.
"""

from datetime import date
from typing import Dict, List, Optional

from app.services.health_record_import.fhir_mapper_base import (
    CanonicalLookup,
    MappedRecords,
    UnmappedItem,
    _category_codes,
    _codings,
    _notes,
    _period_or_datetime,
    code_for,
    display_for,
    first_code,
    parse_date,
    parse_datetime,
    truncate,
)
from app.services.health_record_import.lab_mapper import map_lab_results
from app.services.health_record_import.vitals_mapper import map_vital_signs
from app.services.health_record_import.vocab import (
    ALLERGY_STATUS,
    CONDITION_STATUS,
    CPT,
    ICD10,
    ICD10CM,
    IMMUNIZATION_ROUTES,
    MAX_LEN,
    MEDICATION_REQUEST_STATUS,
    MEDICATION_ROUTES,
    MEDICATION_STATEMENT_STATUS,
    PROCEDURE_STATUS,
    SNOMED,
)


def map_patient(resource: dict) -> dict:
    """Demographics the importer compares against the MediKeep patient."""
    names = [n for n in resource.get("name") or [] if isinstance(n, dict)]
    official = next((n for n in names if n.get("use") == "official"), None)
    name = official or (names[0] if names else {})
    given = name.get("given") or []
    return {
        "external_id": resource.get("id"),
        "first_name": str(given[0]).strip() if given else None,
        "last_name": str(name["family"]).strip() if name.get("family") else None,
        "full_name": name.get("text")
        or " ".join([*(str(g) for g in given), str(name.get("family") or "")]).strip()
        or None,
        "birth_date": parse_date(resource.get("birthDate")),
        "gender": resource.get("gender"),
    }


def map_condition(
    resource: dict, patient_id: int, import_source: str
) -> Optional[dict]:
    code = resource.get("code")
    diagnosis = display_for(code)
    if not diagnosis:
        return None
    clinical = (
        code_for(
            resource.get("clinicalStatus"),
            "http://terminology.hl7.org/CodeSystem/condition-clinical",
        )
        or first_code(resource.get("clinicalStatus"))
        or ""
    ).lower()
    onset_start, _ = _period_or_datetime(resource, "onset")
    abatement_start, _ = _period_or_datetime(resource, "abatement")
    onset_date = parse_date(onset_start)
    end_date = parse_date(abatement_start)
    status = CONDITION_STATUS.get(clinical)
    if status is None:
        status = "resolved" if end_date else "active"
    if status not in ("resolved", "inactive") and end_date:
        status = "resolved"
    if end_date and onset_date and end_date < onset_date:
        end_date = None
    icd10 = code_for(code, ICD10CM, ICD10)
    snomed = code_for(code, SNOMED)
    icd_display = next(
        (
            c.get("display")
            for c in _codings(code)
            if c.get("system") in (ICD10CM, ICD10) and c.get("display")
        ),
        None,
    )
    return {
        "patient_id": patient_id,
        "external_id": resource.get("id"),
        "import_source": import_source,
        "diagnosis": truncate(diagnosis, MAX_LEN["condition_diagnosis"]),
        "condition_name": truncate(diagnosis, MAX_LEN["condition_diagnosis"]),
        "status": status,
        "onset_date": onset_date,
        "end_date": end_date if status in ("resolved", "inactive") else None,
        "icd10_code": truncate(icd10, MAX_LEN["icd10_code"]),
        "snomed_code": truncate(snomed, MAX_LEN["condition_code"]),
        "code_description": truncate(icd_display, 500),
        "notes": _notes(resource),
    }


def map_allergy(resource: dict, patient_id: int, import_source: str) -> Optional[dict]:
    allergen = display_for(resource.get("code"))
    if not allergen:
        return None
    reactions = [r for r in resource.get("reaction") or [] if isinstance(r, dict)]
    manifestations = []
    severity = None
    for reaction in reactions:
        for concept in reaction.get("manifestation") or []:
            text = display_for(concept)
            if text and text not in manifestations:
                manifestations.append(text)
        if (
            reaction.get("severity") in ("mild", "moderate", "severe")
            and severity is None
        ):
            severity = reaction["severity"]
        if reaction.get("description") and not manifestations:
            manifestations.append(str(reaction["description"]).strip())
    if severity is None:
        criticality = resource.get("criticality")
        severity = {"low": "mild", "high": "severe"}.get(criticality, "none")
    clinical = (
        code_for(
            resource.get("clinicalStatus"),
            "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
        )
        or first_code(resource.get("clinicalStatus"))
        or ""
    ).lower()
    verification = (first_code(resource.get("verificationStatus")) or "").lower()
    status = ALLERGY_STATUS.get(clinical, "active")
    if verification == "unconfirmed":
        status = "unconfirmed"
    onset_start, _ = _period_or_datetime(resource, "onset")
    return {
        "patient_id": patient_id,
        "external_id": resource.get("id"),
        "import_source": import_source,
        "allergen": truncate(allergen, MAX_LEN["allergen"]),
        "reaction": truncate(", ".join(manifestations), MAX_LEN["reaction"])
        or "Not recorded",
        "severity": severity,
        "status": status,
        "onset_date": parse_date(onset_start),
        "notes": _notes(resource),
    }


def _frequency_text(timing: Optional[dict]) -> Optional[str]:
    if not isinstance(timing, dict):
        return None
    code_text = display_for(timing.get("code"))
    repeat = timing.get("repeat") or {}
    freq = repeat.get("frequency")
    period = repeat.get("period")
    unit = repeat.get("periodUnit")
    unit_words = {
        "s": "second",
        "min": "minute",
        "h": "hour",
        "d": "day",
        "wk": "week",
        "mo": "month",
        "a": "year",
    }
    if period and unit in unit_words:
        word = unit_words[unit]
        if (freq or 1) == 1 and period == 1:
            text = "daily" if unit == "d" else f"every {word}"
        elif (freq or 1) == 1:
            text = f"every {period:g} {word}s"
        else:
            text = f"{freq} times per {period:g} {word}" + ("s" if period != 1 else "")
    elif freq and not period:
        text = f"{freq} times"
    else:
        text = code_text
    when = repeat.get("when")
    if text and when and len(text) < 30:
        text = f"{text} ({', '.join(str(w) for w in when)})"
    return truncate(text, MAX_LEN["frequency"])


def _dose_text(dosage: dict) -> Optional[str]:
    for entry in dosage.get("doseAndRate") or []:
        quantity = entry.get("doseQuantity") if isinstance(entry, dict) else None
        if isinstance(quantity, dict) and quantity.get("value") is not None:
            unit = quantity.get("unit") or quantity.get("code") or ""
            unit = {
                "{tbl}": "tablet",
                "{capsule}": "capsule",
                "{puff}": "puff",
                "{spray}": "spray",
                "{drop}": "drop",
            }.get(unit, unit)
            return truncate(f"{quantity['value']:g} {unit}".strip(), MAX_LEN["dosage"])
        rng = entry.get("doseRange") if isinstance(entry, dict) else None
        if isinstance(rng, dict):
            low = (rng.get("low") or {}).get("value")
            high = (rng.get("high") or {}).get("value")
            unit = (rng.get("high") or rng.get("low") or {}).get("unit") or ""
            if low is not None and high is not None:
                return truncate(f"{low:g}-{high:g} {unit}".strip(), MAX_LEN["dosage"])
    return None


def map_medication(
    resource: dict, patient_id: int, import_source: str
) -> Optional[dict]:
    is_request = resource.get("resourceType") == "MedicationRequest"
    concept = resource.get("medicationCodeableConcept")
    name = display_for(concept) or (resource.get("medicationReference") or {}).get(
        "display"
    )
    if not name:
        return None
    dosages = resource.get("dosageInstruction" if is_request else "dosage") or []
    dosage = dosages[0] if dosages and isinstance(dosages[0], dict) else {}
    sig = str(dosage.get("text") or "").strip() or None
    dose = _dose_text(dosage) or (truncate(sig, MAX_LEN["dosage"]) if sig else None)
    route_text = display_for(dosage.get("route"))
    route = MEDICATION_ROUTES.get((route_text or "").lower()) if route_text else None

    raw_status = str(resource.get("status") or "").lower()
    status = (
        MEDICATION_REQUEST_STATUS if is_request else MEDICATION_STATEMENT_STATUS
    ).get(raw_status)

    if is_request:
        bounds = ((dosage.get("timing") or {}).get("repeat") or {}).get(
            "boundsPeriod"
        ) or {}
        start = parse_date(bounds.get("start") or resource.get("authoredOn"))
        end = parse_date(bounds.get("end"))
    else:
        start_text, end_text = _period_or_datetime(resource, "effective")
        start = parse_date(start_text)
        end = parse_date(end_text)
    if start and end and end < start:
        end = None

    notes_parts = []
    if sig and sig != dose:
        notes_parts.append(f"Sig: {sig}")
    if route_text and route is None:
        notes_parts.append(f"Route: {route_text}")
    extra_notes = _notes(resource)
    if extra_notes:
        notes_parts.append(extra_notes)
    reasons = [display_for(c) for c in resource.get("reasonCode") or []]
    indication = ", ".join(r for r in reasons if r) or None

    return {
        "patient_id": patient_id,
        "external_id": resource.get("id"),
        "import_source": import_source,
        "medication_name": truncate(name, MAX_LEN["medication_name"]),
        "medication_type": "prescription",
        "dosage": dose,
        "frequency": _frequency_text(dosage.get("timing")),
        "route": route,
        "indication": truncate(indication, MAX_LEN["indication"]),
        "effective_period_start": start,
        "effective_period_end": end,
        "status": status,
        "notes": truncate("\n".join(notes_parts), MAX_LEN["notes"]) or None,
    }


def map_immunization(
    resource: dict, patient_id: int, import_source: str
) -> Optional[dict]:
    name = display_for(resource.get("vaccineCode"))
    administered = parse_date(
        resource.get("occurrenceDateTime") or resource.get("occurrenceString")
    )
    if not name or not administered:
        return None
    route_text = display_for(resource.get("route"))
    route = IMMUNIZATION_ROUTES.get((route_text or "").lower()) if route_text else None
    dose_number = None
    for protocol in resource.get("protocolApplied") or []:
        if isinstance(protocol, dict) and isinstance(
            protocol.get("doseNumberPositiveInt"), int
        ):
            dose_number = protocol["doseNumberPositiveInt"]
            break
    notes_parts = []
    if route_text and route is None:
        notes_parts.append(f"Route: {route_text}")
    dose_quantity = resource.get("doseQuantity") or {}
    if dose_quantity.get("value") is not None:
        notes_parts.append(
            f"Dose: {dose_quantity['value']:g} {dose_quantity.get('unit') or ''}".strip()
        )
    performers = [
        (p.get("actor") or {}).get("display")
        for p in resource.get("performer") or []
        if isinstance(p, dict)
    ]
    performer = next((p for p in performers if p), None)
    if performer:
        notes_parts.append(f"Administered by: {performer}")
    extra = _notes(resource)
    if extra:
        notes_parts.append(extra)
    return {
        "patient_id": patient_id,
        "external_id": resource.get("id"),
        "import_source": import_source,
        "vaccine_name": truncate(name, MAX_LEN["vaccine_name"]),
        "date_administered": administered,
        "dose_number": dose_number,
        "lot_number": truncate(resource.get("lotNumber"), MAX_LEN["lot_number"]),
        "manufacturer": truncate(
            (resource.get("manufacturer") or {}).get("display"), MAX_LEN["manufacturer"]
        ),
        "site": truncate(display_for(resource.get("site")), MAX_LEN["site"]),
        "route": route,
        "expiration_date": parse_date(resource.get("expirationDate")),
        "location": truncate(
            (resource.get("location") or {}).get("display"), MAX_LEN["location"]
        ),
        "notes": truncate("\n".join(notes_parts), MAX_LEN["notes"]) or None,
    }


def map_procedure(
    resource: dict, patient_id: int, import_source: str
) -> Optional[dict]:
    name = display_for(resource.get("code"))
    start, _ = _period_or_datetime(resource, "performed")
    performed = parse_date(start)
    if not name or not performed:
        return None
    raw_status = str(resource.get("status") or "").lower()
    status = PROCEDURE_STATUS.get(raw_status)
    if status is None:
        status = "completed" if performed <= date.today() else "scheduled"
    code = code_for(resource.get("code"), CPT) or first_code(resource.get("code"))
    facility = None
    for performer in resource.get("performer") or []:
        if isinstance(performer, dict):
            facility = (performer.get("onBehalfOf") or {}).get("display")
            if facility:
                break
    facility = facility or (resource.get("location") or {}).get("display")
    sites = [display_for(c) for c in resource.get("bodySite") or []]
    description = ", ".join(s for s in sites if s) or None
    outcome = display_for(resource.get("outcome"))
    notes_parts = []
    if outcome:
        notes_parts.append(f"Outcome: {outcome}")
    if raw_status == "not-done":
        reason = display_for(resource.get("statusReason"))
        notes_parts.append("Not performed" + (f": {reason}" if reason else ""))
    extra = _notes(resource)
    if extra:
        notes_parts.append(extra)
    return {
        "patient_id": patient_id,
        "external_id": resource.get("id"),
        "import_source": import_source,
        "procedure_name": truncate(name, MAX_LEN["procedure_name"]),
        "procedure_code": truncate(code, MAX_LEN["procedure_code"]),
        "date": performed,
        "status": status,
        "description": truncate(description, 5000),
        "facility": truncate(facility, MAX_LEN["facility"]),
        "notes": truncate("\n".join(notes_parts), MAX_LEN["notes"]) or None,
    }


def map_encounter(
    resource: dict, patient_id: int, import_source: str
) -> Optional[dict]:
    period = resource.get("period") or {}
    when = parse_date(period.get("start") or period.get("end"))
    if not when:
        return None
    types = [display_for(c) for c in resource.get("type") or []]
    visit_type = next((t for t in types if t), None)
    reasons = [display_for(c) for c in resource.get("reasonCode") or []]
    reason = ", ".join(r for r in reasons if r) or visit_type
    if not reason:
        klass = resource.get("class") or {}
        reason = klass.get("display") or klass.get("code") or "Encounter"
    locations = [
        ((loc.get("location") or {}).get("display"))
        for loc in resource.get("location") or []
        if isinstance(loc, dict)
    ]
    location = next((loc for loc in locations if loc), None)
    if not location:
        location = (resource.get("serviceProvider") or {}).get("display")
    duration = None
    stay_note = None
    start_dt = parse_datetime(period.get("start"))
    end_dt = parse_datetime(period.get("end"))
    if start_dt and end_dt and (start_dt.tzinfo is None) == (end_dt.tzinfo is None):
        minutes = int((end_dt - start_dt).total_seconds() // 60)
        if 0 < minutes <= 600:  # EncounterCreate caps duration_minutes at 600
            duration = minutes
        elif minutes > 600:
            days, rem = divmod(minutes, 24 * 60)
            stay_note = f"Stay: {days} day{'s' if days != 1 else ''}" + (
                f" {rem // 60} h" if rem // 60 else ""
            )
    participants = [
        ((p.get("individual") or {}).get("display"))
        for p in resource.get("participant") or []
        if isinstance(p, dict)
    ]
    notes_parts = []
    seen_by = next((p for p in participants if p), None)
    if seen_by:
        notes_parts.append(f"Seen by: {seen_by}")
    if stay_note:
        notes_parts.append(stay_note)
    status = str(resource.get("status") or "").lower()
    if status and status not in ("finished", "unknown"):
        notes_parts.append(f"Status: {status}")
    discharge = display_for(
        ((resource.get("hospitalization") or {}).get("dischargeDisposition"))
    )
    if discharge:
        notes_parts.append(f"Discharge: {discharge}")
    extra = _notes(resource)
    if extra:
        notes_parts.append(extra)
    return {
        "patient_id": patient_id,
        "external_id": resource.get("id"),
        "import_source": import_source,
        "reason": truncate(reason, MAX_LEN["encounter_reason"]),
        "date": when,
        "visit_type": truncate(visit_type, MAX_LEN["visit_type"]),
        "location": truncate(location, MAX_LEN["location"]),
        "duration_minutes": duration,
        "notes": truncate("\n".join(notes_parts), MAX_LEN["notes"]) or None,
    }


# --- bundle entry point -----------------------------------------------------

_SKIPPED_STATUSES = {"entered-in-error"}
_IGNORED_TYPES = {
    "Organization",
    "Practitioner",
    "PractitionerRole",
    "Location",
    "DocumentReference",
    "Provenance",
    "Composition",
    "Device",
    "Medication",
    "Specimen",
    "Coverage",
}


def map_bundle(
    bundle: dict,
    *,
    patient_id: int,
    import_source: str,
    canonical_lookup: Optional[CanonicalLookup] = None,
) -> MappedRecords:
    """Map every resource in a FHIR Bundle to MediKeep create dicts."""
    out = MappedRecords()
    vital_observations: List[dict] = []
    lab_observations: Dict[str, dict] = {}
    reports: List[dict] = []
    patients: List[dict] = []

    simple = {
        "Condition": (map_condition, out.conditions),
        "AllergyIntolerance": (map_allergy, out.allergies),
        "MedicationStatement": (map_medication, out.medications),
        "MedicationRequest": (map_medication, out.medications),
        "Immunization": (map_immunization, out.immunizations),
        "Procedure": (map_procedure, out.procedures),
        "Encounter": (map_encounter, out.encounters),
    }

    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict):
            continue
        rtype = resource.get("resourceType")
        rid = resource.get("id")
        if not rid:
            out.unmapped.append(UnmappedItem(str(rtype), "resource has no id"))
            continue
        status = str(resource.get("status") or "").lower()

        if rtype == "Patient":
            patients.append(resource)
            continue
        if rtype in _IGNORED_TYPES:
            out.unmapped.append(UnmappedItem(rtype, "resource type not imported", rid))
            continue
        if rtype == "Observation":
            categories = _category_codes(resource)
            if status in _SKIPPED_STATUSES:
                out.unmapped.append(UnmappedItem(rtype, "entered in error", rid))
            elif "vital-signs" in categories:
                vital_observations.append(resource)
            elif "laboratory" in categories:
                lab_observations[rid] = resource
            else:
                label = ", ".join(categories) or "no category"
                out.unmapped.append(
                    UnmappedItem(
                        rtype,
                        f"observation category not imported ({label})",
                        rid,
                        display_for(resource.get("code")),
                    )
                )
            continue
        if rtype == "DiagnosticReport":
            reports.append(resource)
            continue
        if rtype == "AllergyIntolerance":
            verification = (
                first_code(resource.get("verificationStatus")) or ""
            ).lower()
            if verification in ("refuted", "entered-in-error"):
                reason = (
                    "negated entry (no known allergy)"
                    if verification == "refuted"
                    else "entered in error"
                )
                out.unmapped.append(
                    UnmappedItem(rtype, reason, rid, display_for(resource.get("code")))
                )
                continue
        if rtype == "Immunization" and status == "not-done":
            reason = display_for((resource.get("statusReason")))
            out.unmapped.append(
                UnmappedItem(
                    rtype,
                    "not administered",
                    rid,
                    f"{display_for(resource.get('vaccineCode')) or ''}"
                    f"{' (' + reason + ')' if reason else ''}".strip(),
                )
            )
            continue
        if rtype in simple:
            if status in _SKIPPED_STATUSES:
                out.unmapped.append(UnmappedItem(rtype, "entered in error", rid))
                continue
            mapper, target = simple[rtype]
            mapped = mapper(resource, patient_id, import_source)
            if mapped is None:
                out.unmapped.append(
                    UnmappedItem(
                        rtype,
                        "missing required fields (name or date)",
                        rid,
                        display_for(
                            resource.get("code")
                            or resource.get("vaccineCode")
                            or resource.get("medicationCodeableConcept")
                        ),
                    )
                )
                continue
            target.append(mapped)
            continue
        out.unmapped.append(UnmappedItem(str(rtype), "resource type not imported", rid))

    out.vitals = map_vital_signs(
        vital_observations, patient_id, import_source, out.unmapped
    )
    out.lab_results = map_lab_results(
        reports,
        lab_observations,
        patient_id,
        import_source,
        canonical_lookup,
        out.unmapped,
    )

    if patients:
        out.patient = map_patient(patients[0])
        if len(patients) > 1:
            out.warnings.append(
                f"The file describes {len(patients)} patients; demographics were compared "
                "against the first one and all records were assigned to the selected patient."
            )
    return out
