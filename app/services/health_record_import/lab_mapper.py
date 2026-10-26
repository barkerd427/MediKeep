"""DiagnosticReports and laboratory Observations to LabResult rows with components."""

import re
from typing import Dict, List, Optional, Tuple

from app.services.health_record_import.fhir_mapper_base import (
    CanonicalLookup,
    UnmappedItem,
    _notes,
    _period_or_datetime,
    _ref_id,
    code_for,
    display_for,
    first_code,
    parse_date,
    quantity_of,
    truncate,
)
from app.services.health_record_import.vocab import (
    INTERPRETATION_STATUS,
    LAB_RESULT_STATUS,
    LOINC,
    MAX_LEN,
    QUALITATIVE_VALUES,
)


def _reference_range(
    observation: dict,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    ranges = [r for r in observation.get("referenceRange") or [] if isinstance(r, dict)]
    if not ranges:
        return None, None, None
    rng = ranges[0]
    low = (rng.get("low") or {}).get("value")
    high = (rng.get("high") or {}).get("value")
    text = rng.get("text")
    try:
        low = float(low) if low is not None else None
        high = float(high) if high is not None else None
    except (TypeError, ValueError):
        low, high = None, None
    if low is None and high is None and text:
        match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", str(text))
        if match:
            low, high = float(match.group(1)), float(match.group(2))
        else:
            gte = re.match(r"^\s*>=?\s*(-?\d+(?:\.\d+)?)", str(text))
            lte = re.match(r"^\s*<=?\s*(-?\d+(?:\.\d+)?)", str(text))
            if gte:
                low = float(gte.group(1))
            elif lte:
                high = float(lte.group(1))
    if low is not None and high is not None and high <= low:
        low, high = None, None
    if not text and (low is not None or high is not None):
        unit = ((rng.get("high") or rng.get("low") or {}).get("unit")) or ""
        if low is not None and high is not None:
            text = f"{low:g} - {high:g} {unit}".strip()
        elif low is not None:
            text = f">= {low:g} {unit}".strip()
        else:
            text = f"<= {high:g} {unit}".strip()
    return low, high, truncate(text, MAX_LEN["ref_range_text"])


def _interpretation_status(observation: dict) -> Optional[str]:
    for concept in observation.get("interpretation") or []:
        code = first_code(concept)
        if code and code.upper() in INTERPRETATION_STATUS:
            return INTERPRETATION_STATUS[code.upper()]
    return None


def map_lab_component(
    observation: dict,
    import_source: str,
    canonical_lookup: Optional[CanonicalLookup],
    order: int,
) -> Optional[dict]:
    name = display_for(observation.get("code"))
    if not name:
        return None
    value, unit = quantity_of(observation)
    component = {
        "external_id": observation.get("id"),
        "import_source": import_source,
        "test_name": truncate(name, MAX_LEN["test_name"]),
        "test_code": truncate(
            code_for(observation.get("code"), LOINC), MAX_LEN["test_code"]
        ),
        "status": _interpretation_status(observation),
        "display_order": order,
        "result_type": "quantitative",
        "value": None,
        "unit": None,
        "ref_range_min": None,
        "ref_range_max": None,
        "ref_range_text": None,
        "qualitative_value": None,
        "textual_value": None,
        "notes": truncate(_notes(observation), MAX_LEN["component_notes"]),
    }
    if value is not None:
        low, high, text = _reference_range(observation)
        component.update(
            value=value,
            unit=truncate(unit, MAX_LEN["unit"]),
            ref_range_min=low,
            ref_range_max=high,
            ref_range_text=text,
        )
    elif observation.get("valueCodeableConcept") is not None or observation.get(
        "valueString"
    ):
        text = (
            display_for(observation.get("valueCodeableConcept"))
            or str(observation.get("valueString") or "").strip()
        )
        qualitative = QUALITATIVE_VALUES.get(text.lower())
        if qualitative:
            component.update(result_type="qualitative", qualitative_value=qualitative)
        else:
            component.update(result_type="textual", textual_value=truncate(text, 5000))
        _, _, range_text = _reference_range(observation)
        component["ref_range_text"] = range_text
    elif observation.get("valueRange") is not None:
        rng = observation["valueRange"]
        low = (rng.get("low") or {}).get("value")
        high = (rng.get("high") or {}).get("value")
        text = f"{low} - {high} {(rng.get('high') or rng.get('low') or {}).get('unit') or ''}".strip()
        component.update(result_type="textual", textual_value=text)
    elif observation.get("valueBoolean") is not None:
        component.update(
            result_type="qualitative",
            qualitative_value="positive" if observation["valueBoolean"] else "negative",
        )
    elif observation.get("valueInteger") is not None:
        low, high, text = _reference_range(observation)
        component.update(
            value=float(observation["valueInteger"]),
            ref_range_min=low,
            ref_range_max=high,
            ref_range_text=text,
        )
    else:
        return None  # no value (for example dataAbsentReason)
    if canonical_lookup is not None:
        canonical, category = canonical_lookup(component["test_name"])
        component["canonical_test_name"] = canonical
        component["category"] = category
    return component


def map_lab_results(
    reports: List[dict],
    observations: Dict[str, dict],
    patient_id: int,
    import_source: str,
    canonical_lookup: Optional[CanonicalLookup],
    unmapped: List[UnmappedItem],
) -> List[dict]:
    """DiagnosticReports plus their laboratory Observations as LabResult dicts.

    Laboratory Observations that no report references are grouped by date into
    a synthetic result so they are still imported.
    """
    results: List[dict] = []
    claimed: set = set()
    for report in reports:
        name = display_for(report.get("code")) or "Laboratory report"
        raw_status = str(report.get("status") or "").lower()
        if raw_status == "entered-in-error":
            unmapped.append(
                UnmappedItem("DiagnosticReport", "entered in error", report.get("id"))
            )
            continue
        effective, _ = _period_or_datetime(report, "effective")
        completed = parse_date(effective or report.get("issued"))
        components = []
        for ref in report.get("result") or []:
            obs_id = _ref_id(ref)
            obs = observations.get(obs_id) if obs_id else None
            if obs is None:
                unmapped.append(
                    UnmappedItem(
                        "DiagnosticReport",
                        "result observation missing from bundle",
                        report.get("id"),
                        obs_id,
                    )
                )
                continue
            claimed.add(obs_id)
            component = map_lab_component(
                obs, import_source, canonical_lookup, len(components)
            )
            if component is None:
                unmapped.append(_no_value_item(obs))
                continue
            components.append(component)
        performers = [
            p.get("display")
            for p in report.get("performer") or []
            if isinstance(p, dict)
        ]
        results.append(
            _lab_result_dict(
                patient_id,
                import_source,
                report.get("id"),
                name,
                code_for(report.get("code"), LOINC) or first_code(report.get("code")),
                completed,
                LAB_RESULT_STATUS.get(raw_status, "completed"),
                next((p for p in performers if p), None),
                components,
                _notes(report),
            )
        )

    orphans: Dict[str, List[dict]] = {}
    for obs_id, obs in observations.items():
        if obs_id in claimed:
            continue
        effective, _ = _period_or_datetime(obs, "effective")
        day = parse_date(effective or obs.get("issued"))
        orphans.setdefault(day.isoformat() if day else "undated", []).append(obs)
    for day, group in sorted(orphans.items()):
        components = []
        for obs in group:
            component = map_lab_component(
                obs, import_source, canonical_lookup, len(components)
            )
            if component is None:
                unmapped.append(_no_value_item(obs))
                continue
            components.append(component)
        if not components:
            continue
        results.append(
            _lab_result_dict(
                patient_id,
                import_source,
                f"labs:{day}",
                f"Laboratory results {day}",
                None,
                parse_date(day),
                "completed",
                None,
                components,
                None,
            )
        )
    return results


def _no_value_item(observation: dict) -> UnmappedItem:
    absent = display_for(observation.get("dataAbsentReason"))
    reason = (
        f"laboratory observation has no value ({absent.lower()})"
        if absent
        else "laboratory observation has no usable value"
    )
    return UnmappedItem(
        "Observation",
        reason,
        observation.get("id"),
        display_for(observation.get("code")),
    )


def _lab_result_dict(
    patient_id,
    import_source,
    external_id,
    name,
    code,
    completed,
    status,
    facility,
    components,
    notes,
) -> dict:
    statuses = {c.get("status") for c in components if c.get("status")}
    if components and statuses and statuses <= {"normal"}:
        labs_result = "normal"
    elif statuses & {"critical"}:
        labs_result = "critical"
    elif statuses & {"high", "low", "abnormal"}:
        labs_result = "abnormal"
    else:
        labs_result = None
    return {
        "patient_id": patient_id,
        "external_id": external_id,
        "import_source": import_source,
        "test_name": truncate(name, MAX_LEN["test_name"]),
        "test_code": truncate(code, MAX_LEN["test_code"]),
        "completed_date": completed,
        "status": status,
        "labs_result": labs_result,
        "facility": truncate(facility, MAX_LEN["facility"]),
        "is_panel": bool(components),
        "notes": notes,
        "components": components,
    }
