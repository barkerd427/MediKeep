"""Vocabularies used by the FHIR mapper: code systems, status maps, units and limits.

Kept apart from the mapping functions so each table can be read and extended
without wading through the mapping logic.
"""

from typing import Dict

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10 = "http://hl7.org/fhir/sid/icd-10"
CPT = "http://www.ama-assn.org/go/cpt"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
CVX = "http://hl7.org/fhir/sid/cvx"

# LOINC code -> (Vitals column, converter from (value, unit) to stored unit)
VITAL_SIGN_CODES: Dict[str, str] = {
    "8480-6": "systolic_bp",
    "8462-4": "diastolic_bp",
    "8867-4": "heart_rate",
    "8310-5": "temperature",
    "8331-1": "temperature",  # oral temperature
    "29463-7": "weight",
    "3141-9": "weight",  # body weight measured
    "8302-2": "height",
    "2708-6": "oxygen_saturation",
    "59408-5": "oxygen_saturation",
    "2339-0": "blood_glucose",
    "41653-7": "blood_glucose",  # glucose, capillary
    "9279-1": "respiratory_rate",
    "39156-5": "bmi",
    "72514-3": "pain_scale",
}
BLOOD_PRESSURE_PANEL = "85354-9"
INTEGER_VITALS = {
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "respiratory_rate",
    "pain_scale",
}

# Interpretation codes (HL7 v3 ObservationInterpretation) -> lab component status.
INTERPRETATION_STATUS = {
    "H": "high",
    "HU": "high",
    "L": "low",
    "LU": "low",
    "HH": "critical",
    "LL": "critical",
    "AA": "critical",
    "A": "abnormal",
    "N": "normal",
}

# FHIR -> MediKeep status vocabularies.
CONDITION_STATUS = {
    "active": "active",
    "recurrence": "recurrence",
    "relapse": "relapse",
    "inactive": "inactive",
    "remission": "inactive",
    "resolved": "resolved",
}
ALLERGY_STATUS = {"active": "active", "inactive": "inactive", "resolved": "resolved"}
MEDICATION_STATEMENT_STATUS = {
    "active": "active",
    "intended": "active",
    "completed": "completed",
    "stopped": "stopped",
    "on-hold": "on-hold",
    "not-taken": "cancelled",
}
MEDICATION_REQUEST_STATUS = {
    "active": "active",
    "draft": "active",
    "completed": "completed",
    "stopped": "stopped",
    "on-hold": "on-hold",
    "cancelled": "cancelled",
}
PROCEDURE_STATUS = {
    "completed": "completed",
    "in-progress": "in_progress",
    "preparation": "scheduled",
    "on-hold": "postponed",
    "stopped": "cancelled",
    "not-done": "cancelled",
}
LAB_RESULT_STATUS = {
    "final": "completed",
    "amended": "completed",
    "corrected": "completed",
    "appended": "completed",
    "partial": "in-progress",
    "preliminary": "in-progress",
    "registered": "ordered",
    "cancelled": "cancelled",
}
MEDICATION_ROUTES = {
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
    "aural": "aural",
    "auricular (otic)": "aural",
    "otic": "aural",
}
IMMUNIZATION_ROUTES = {
    "intramuscular": "intramuscular",
    "im": "intramuscular",
    "subcutaneous": "subcutaneous",
    "sc": "subcutaneous",
    "intradermal": "intradermal",
    "id": "intradermal",
    "oral": "oral",
    "po": "oral",
    "nasal": "nasal",
    "intranasal": "nasal",
}
QUALITATIVE_VALUES = {
    "positive": "positive",
    "pos": "positive",
    "reactive": "positive",
    "negative": "negative",
    "neg": "negative",
    "non-reactive": "negative",
    "nonreactive": "negative",
    "detected": "detected",
    "present": "detected",
    "undetected": "undetected",
    "not detected": "undetected",
    "none detected": "undetected",
    "absent": "undetected",
}

# Field length limits taken from the Create schemas, so mapped values pass them.
MAX_LEN = {
    "condition_diagnosis": 500,
    "condition_code": 20,
    "icd10_code": 10,
    "allergen": 200,
    "reaction": 500,
    "medication_name": 100,
    "dosage": 50,
    "frequency": 50,
    "indication": 500,
    "vaccine_name": 200,
    "lot_number": 50,
    "manufacturer": 200,
    "site": 100,
    "location": 200,
    "procedure_name": 300,
    "procedure_code": 50,
    "facility": 300,
    "encounter_reason": 500,
    "visit_type": 100,
    "test_name": 200,
    "test_code": 50,
    "unit": 50,
    "ref_range_text": 500,
    "notes": 5000,
    "component_notes": 500,
}
