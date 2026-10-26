"""
Maps vital-sign Observations to Vitals rows, converting units on the way.
"""

from typing import Dict, Iterable, List, Optional, Tuple

from app.services.health_record_import.fhir_mapper_base import (
    UnmappedItem,
    code_for,
    display_for,
    first_code,
    parse_datetime,
    quantity_of,
)
from app.services.health_record_import.vocab import (
    BLOOD_PRESSURE_PANEL,
    INTEGER_VITALS,
    LOINC,
    VITAL_SIGN_CODES,
)


def convert_vital_value(
    column: str, value: float, unit: Optional[str]
) -> Optional[float]:
    """
    Convert a measurement to the unit the Vitals column stores.

    Vitals stores temperature in Fahrenheit, weight in pounds, height in inches
    and glucose in mg/dL. Returns None for a unit it cannot convert.
    """
    u = (unit or "").strip()
    lowered = u.lower()
    if column == "temperature":
        if lowered in ("cel", "c", "°c", "degc", "celsius"):
            return value * 9 / 5 + 32
        if lowered in ("[degf]", "f", "°f", "degf", "fahrenheit", ""):
            return value
        return None
    if column == "weight":
        if lowered in ("kg", "kgs", "kilogram", "kilograms"):
            return value * 2.2046226218
        if lowered in ("g", "gram", "grams"):
            return value * 0.0022046226218
        if lowered in ("[lb_av]", "lb", "lbs", "pound", "pounds", ""):
            return value
        if lowered in ("[oz_av]", "oz"):
            return value / 16
        return None
    if column == "height":
        if lowered in ("cm", "centimeter", "centimeters"):
            return value / 2.54
        if lowered in ("m", "meter", "meters"):
            return value / 0.0254
        if lowered in ("mm",):
            return value / 25.4
        if lowered in ("[in_i]", "in", "inch", "inches", ""):
            return value
        if lowered in ("[ft_i]", "ft", "feet"):
            return value * 12
        return None
    if column == "blood_glucose":
        if lowered == "mmol/l":
            return value * 18.0182
        if lowered in ("mg/dl", ""):
            return value
        return None
    return value


def _vitals_key(effective: Optional[str]) -> str:
    """Grouping key for a measurement time, normalized to ISO format."""
    parsed = parse_datetime(effective)
    if parsed is None:
        return "unknown"
    return parsed.isoformat()


def map_vital_signs(
    observations: Iterable[dict],
    patient_id: int,
    import_source: str,
    unmapped: List[UnmappedItem],
) -> List[dict]:
    """
    Group vital-sign Observations by effective time into Vitals rows.

    The row's external_id is "vitals:<time>" rather than an Observation id,
    which lets a later export add measurements to an existing row.
    """
    rows: Dict[str, dict] = {}
    for obs in observations:
        effective = obs.get("effectiveDateTime") or (
            obs.get("effectivePeriod") or {}
        ).get("start")
        recorded = parse_datetime(effective)
        if recorded is None:
            unmapped.append(
                UnmappedItem(
                    "Observation", "vital sign has no effective time", obs.get("id")
                )
            )
            continue
        key = _vitals_key(effective)
        row = rows.setdefault(
            key,
            {
                "patient_id": patient_id,
                "external_id": f"vitals:{key}",
                "import_source": import_source,
                "recorded_date": recorded,
                "_observation_ids": [],
            },
        )
        loinc = code_for(obs.get("code"), LOINC) or first_code(obs.get("code"))
        # Blood pressure panels carry systolic and diastolic as components
        measurements: List[Tuple[str, float, Optional[str]]] = []
        if loinc == BLOOD_PRESSURE_PANEL or obs.get("component"):
            for component in obs.get("component") or []:
                c_code = code_for(component.get("code"), LOINC) or first_code(
                    component.get("code")
                )
                column = VITAL_SIGN_CODES.get(c_code or "")
                value, unit = quantity_of(component)
                if column and value is not None:
                    measurements.append((column, value, unit))
            if not measurements and loinc in VITAL_SIGN_CODES:
                value, unit = quantity_of(obs)
                if value is not None:
                    measurements.append((VITAL_SIGN_CODES[loinc], value, unit))
        else:
            column = VITAL_SIGN_CODES.get(loinc or "")
            value, unit = quantity_of(obs)
            if column and value is not None:
                measurements.append((column, value, unit))
        if not measurements:
            reason = (
                "vital sign code not mapped"
                if loinc not in VITAL_SIGN_CODES
                else "vital sign has no numeric value"
            )
            unmapped.append(
                UnmappedItem(
                    "Observation",
                    reason,
                    obs.get("id"),
                    f"{loinc or 'no code'} {display_for(obs.get('code')) or ''}".strip(),
                )
            )
            continue
        placed = False
        for column, value, unit in measurements:
            converted = convert_vital_value(column, value, unit)
            if converted is None:
                unmapped.append(
                    UnmappedItem(
                        "Observation",
                        f"unit '{unit}' not supported for {column}",
                        obs.get("id"),
                        display_for(obs.get("code")),
                    )
                )
                continue
            if column in INTEGER_VITALS:
                row[column] = int(round(converted))
            else:
                row[column] = round(converted, 2)
            placed = True
        if placed:
            row["_observation_ids"].append(obs.get("id"))
    result = []
    for key in sorted(rows):
        row = rows[key]
        if not row["_observation_ids"]:
            continue
        row.pop("_observation_ids")
        # Calculate BMI when the source did not record one
        if row.get("weight") and row.get("height") and not row.get("bmi"):
            row["bmi"] = round((row["weight"] / (row["height"] ** 2)) * 703, 1)
        result.append(row)
    return result
