"""Unit tests for the FHIR R4 to MediKeep mapper (health record import)."""

from datetime import date, datetime, timezone

import pytest

from app.services.health_record_import.fhir_mapper import (
    map_allergy,
    map_bundle,
    map_condition,
    map_encounter,
    map_immunization,
    map_lab_results,
    map_medication,
    map_procedure,
    map_vital_signs,
)
from app.services.health_record_import.vitals_mapper import convert_vital_value

PATIENT_ID = 7
SOURCE = "ccda"


def _cc(code, display, system="http://snomed.info/sct", text=None):
    concept = {"coding": [{"system": system, "code": code, "display": display}]}
    if text:
        concept["text"] = text
    return concept


def _status(code, system):
    return {"coding": [{"system": system, "code": code}]}


def _obs(
    obs_id, loinc, value, unit, when="2024-03-01T09:05:00-06:00", category="vital-signs"
):
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "status": "final",
        "category": [{"coding": [{"code": category}]}],
        "code": _cc(loinc, loinc, system="http://loinc.org"),
        "effectiveDateTime": when,
        "valueQuantity": {"value": value, "unit": unit},
    }


class TestCondition:
    def test_codes_dates_and_active_status(self):
        resource = {
            "resourceType": "Condition",
            "id": "c1",
            "clinicalStatus": _status(
                "active", "http://terminology.hl7.org/CodeSystem/condition-clinical"
            ),
            "code": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "59621000",
                        "display": "Essential hypertension",
                    },
                    {
                        "system": "http://hl7.org/fhir/sid/icd-10-cm",
                        "code": "I10",
                        "display": "Essential (primary) hypertension",
                    },
                ]
            },
            "onsetDateTime": "2018-02-14",
        }
        mapped = map_condition(resource, PATIENT_ID, SOURCE)
        assert mapped["patient_id"] == PATIENT_ID
        assert mapped["external_id"] == "c1"
        assert mapped["import_source"] == SOURCE
        assert mapped["diagnosis"] == "Essential hypertension"
        assert mapped["icd10_code"] == "I10"
        assert mapped["snomed_code"] == "59621000"
        assert mapped["onset_date"] == date(2018, 2, 14)
        assert mapped["status"] == "active"
        assert mapped["end_date"] is None

    def test_resolved_with_abatement(self):
        resource = {
            "resourceType": "Condition",
            "id": "c2",
            "clinicalStatus": _status(
                "resolved", "http://terminology.hl7.org/CodeSystem/condition-clinical"
            ),
            "code": _cc("195967001", "Asthma"),
            "onsetPeriod": {"start": "2010-05"},
            "abatementDateTime": "2015-01-20",
        }
        mapped = map_condition(resource, PATIENT_ID, SOURCE)
        assert mapped["status"] == "resolved"
        assert mapped["onset_date"] == date(2010, 5, 1)
        assert mapped["end_date"] == date(2015, 1, 20)

    def test_missing_clinical_status_is_derived(self):
        resource = {
            "resourceType": "Condition",
            "id": "c3",
            "code": _cc("1", "X"),
            "abatementDateTime": "2020-01-01",
        }
        assert map_condition(resource, PATIENT_ID, SOURCE)["status"] == "resolved"
        resource.pop("abatementDateTime")
        assert map_condition(resource, PATIENT_ID, SOURCE)["status"] == "active"

    def test_without_name_is_rejected(self):
        assert (
            map_condition({"resourceType": "Condition", "id": "c4"}, PATIENT_ID, SOURCE)
            is None
        )


class TestAllergy:
    def test_reaction_and_severity(self):
        resource = {
            "resourceType": "AllergyIntolerance",
            "id": "a1",
            "clinicalStatus": _status(
                "active",
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
            ),
            "code": _cc(
                "7980",
                "Penicillin G",
                system="http://www.nlm.nih.gov/research/umls/rxnorm",
            ),
            "criticality": "high",
            "onsetDateTime": "2012-06-04",
            "reaction": [
                {"manifestation": [_cc("247472004", "Hives")], "severity": "moderate"},
                {"manifestation": [_cc("267036007", "Dyspnea")]},
            ],
        }
        mapped = map_allergy(resource, PATIENT_ID, SOURCE)
        assert mapped["allergen"] == "Penicillin G"
        assert mapped["reaction"] == "Hives, Dyspnea"
        assert mapped["severity"] == "moderate"
        assert mapped["status"] == "active"
        assert mapped["onset_date"] == date(2012, 6, 4)

    def test_criticality_fallback_and_unknown_severity(self):
        base = {
            "resourceType": "AllergyIntolerance",
            "id": "a2",
            "code": _cc("1", "Latex"),
        }
        assert (
            map_allergy({**base, "criticality": "high"}, PATIENT_ID, SOURCE)["severity"]
            == "severe"
        )
        assert (
            map_allergy({**base, "criticality": "low"}, PATIENT_ID, SOURCE)["severity"]
            == "mild"
        )
        mapped = map_allergy(base, PATIENT_ID, SOURCE)
        assert mapped["severity"] == "none"
        assert mapped["reaction"] == "Not recorded"

    def test_unconfirmed_verification(self):
        resource = {
            "resourceType": "AllergyIntolerance",
            "id": "a3",
            "code": _cc("1", "Shellfish"),
            "verificationStatus": _status(
                "unconfirmed",
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
            ),
        }
        assert map_allergy(resource, PATIENT_ID, SOURCE)["status"] == "unconfirmed"


class TestMedication:
    def test_statement_dose_frequency_route_and_period(self):
        resource = {
            "resourceType": "MedicationStatement",
            "id": "m1",
            "status": "active",
            "medicationCodeableConcept": _cc(
                "314076",
                "lisinopril 10 MG Oral Tablet",
                system="http://www.nlm.nih.gov/research/umls/rxnorm",
            ),
            "effectivePeriod": {"start": "2023-01-10"},
            "dosage": [
                {
                    "text": "Take 1 tablet (10 mg total) by mouth daily.",
                    "route": _cc(
                        "C38288", "Oral", system="http://ncithesaurus-stage.nci.nih.gov"
                    ),
                    "doseAndRate": [{"doseQuantity": {"value": 10, "unit": "mg"}}],
                    "timing": {
                        "repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}
                    },
                }
            ],
            "reasonCode": [_cc("59621000", "Essential hypertension")],
        }
        mapped = map_medication(resource, PATIENT_ID, SOURCE)
        assert mapped["medication_name"] == "lisinopril 10 MG Oral Tablet"
        assert mapped["dosage"] == "10 mg"
        assert mapped["frequency"] == "daily"
        assert mapped["route"] == "oral"
        assert mapped["status"] == "active"
        assert mapped["effective_period_start"] == date(2023, 1, 10)
        assert mapped["effective_period_end"] is None
        assert mapped["indication"] == "Essential hypertension"
        assert "Sig: Take 1 tablet" in mapped["notes"]

    def test_request_bounds_and_status_map(self):
        resource = {
            "resourceType": "MedicationRequest",
            "id": "m2",
            "status": "stopped",
            "authoredOn": "2024-03-01T09:15:00-06:00",
            "medicationCodeableConcept": _cc(
                "617310", "atorvastatin 20 MG Oral Tablet"
            ),
            "dosageInstruction": [
                {
                    "timing": {
                        "repeat": {
                            "frequency": 2,
                            "period": 1,
                            "periodUnit": "d",
                            "boundsPeriod": {
                                "start": "2024-03-01",
                                "end": "2025-03-01",
                            },
                        }
                    },
                    "route": _cc("x", "Transdermal patch"),
                }
            ],
        }
        mapped = map_medication(resource, PATIENT_ID, SOURCE)
        assert mapped["status"] == "stopped"
        assert mapped["effective_period_start"] == date(2024, 3, 1)
        assert mapped["effective_period_end"] == date(2025, 3, 1)
        assert mapped["frequency"] == "2 times per 1 day"
        # Unknown routes are kept in the notes rather than rejected
        assert mapped["route"] is None
        assert "Route: Transdermal patch" in mapped["notes"]

    def test_long_name_is_truncated_to_schema_limit(self):
        resource = {
            "resourceType": "MedicationStatement",
            "id": "m3",
            "status": "completed",
            "medicationCodeableConcept": {"text": "x" * 150},
        }
        assert (
            len(map_medication(resource, PATIENT_ID, SOURCE)["medication_name"]) <= 100
        )


class TestImmunization:
    def test_fields(self):
        resource = {
            "resourceType": "Immunization",
            "id": "i1",
            "status": "completed",
            "vaccineCode": _cc(
                "150",
                "Influenza, injectable, quadrivalent",
                system="http://hl7.org/fhir/sid/cvx",
            ),
            "occurrenceDateTime": "2023-10-12",
            "lotNumber": "FLU2023-AB12",
            "manufacturer": {"display": "Example Vaccines Inc"},
            "route": _cc("C28161", "Intramuscular"),
            "site": _cc("368208006", "Left upper arm structure"),
            "protocolApplied": [{"doseNumberPositiveInt": 2}],
        }
        mapped = map_immunization(resource, PATIENT_ID, SOURCE)
        assert mapped["vaccine_name"].startswith("Influenza")
        assert mapped["date_administered"] == date(2023, 10, 12)
        assert mapped["lot_number"] == "FLU2023-AB12"
        assert mapped["manufacturer"] == "Example Vaccines Inc"
        assert mapped["dose_number"] == 2
        assert mapped["route"] == "intramuscular"
        assert mapped["site"] == "Left upper arm structure"

    def test_without_date_is_rejected(self):
        resource = {
            "resourceType": "Immunization",
            "id": "i2",
            "status": "completed",
            "vaccineCode": _cc("1", "Tdap"),
        }
        assert map_immunization(resource, PATIENT_ID, SOURCE) is None


class TestProcedure:
    def test_cpt_code_date_and_status(self):
        resource = {
            "resourceType": "Procedure",
            "id": "p1",
            "status": "completed",
            "code": {
                "coding": [
                    {
                        "system": "http://www.ama-assn.org/go/cpt",
                        "code": "45378",
                        "display": "Colonoscopy, diagnostic",
                    },
                    {
                        "system": "http://snomed.info/sct",
                        "code": "73761001",
                        "display": "Colonoscopy",
                    },
                ]
            },
            "performedDateTime": "2022-09-08T08:15:00-05:00",
            "performer": [
                {
                    "actor": {"display": "Dr X"},
                    "onBehalfOf": {"display": "Example Endoscopy Center"},
                }
            ],
        }
        mapped = map_procedure(resource, PATIENT_ID, SOURCE)
        assert mapped["procedure_name"] == "Colonoscopy, diagnostic"
        assert mapped["procedure_code"] == "45378"
        assert mapped["date"] == date(2022, 9, 8)
        assert mapped["status"] == "completed"
        assert mapped["facility"] == "Example Endoscopy Center"

    @pytest.mark.parametrize(
        "fhir_status,expected",
        [
            ("in-progress", "in_progress"),
            ("preparation", "scheduled"),
            ("not-done", "cancelled"),
            ("on-hold", "postponed"),
        ],
    )
    def test_status_vocabulary(self, fhir_status, expected):
        resource = {
            "resourceType": "Procedure",
            "id": "p2",
            "status": fhir_status,
            "code": _cc("1", "Appendectomy"),
            "performedPeriod": {"start": "2020-01-01"},
        }
        assert map_procedure(resource, PATIENT_ID, SOURCE)["status"] == expected


class TestEncounter:
    def test_reason_type_location_and_duration(self):
        resource = {
            "resourceType": "Encounter",
            "id": "e1",
            "status": "finished",
            "class": {"code": "AMB", "display": "ambulatory"},
            "type": [_cc("99213", "Office or other outpatient visit")],
            "period": {
                "start": "2024-03-01T09:00:00-06:00",
                "end": "2024-03-01T09:30:00-06:00",
            },
            "reasonCode": [_cc("59621000", "Essential hypertension")],
            "location": [{"location": {"display": "Example Family Medicine"}}],
            "participant": [{"individual": {"display": "Robert Clinician MD"}}],
        }
        mapped = map_encounter(resource, PATIENT_ID, SOURCE)
        assert mapped["reason"] == "Essential hypertension"
        assert mapped["visit_type"] == "Office or other outpatient visit"
        assert mapped["date"] == date(2024, 3, 1)
        assert mapped["location"] == "Example Family Medicine"
        assert mapped["duration_minutes"] == 30
        assert "Seen by: Robert Clinician MD" in mapped["notes"]

    def test_reason_falls_back_to_type_then_class(self):
        resource = {
            "resourceType": "Encounter",
            "id": "e2",
            "status": "planned",
            "class": {"display": "inpatient"},
            "period": {"start": "2024-01-01"},
        }
        mapped = map_encounter(resource, PATIENT_ID, SOURCE)
        assert mapped["reason"] == "inpatient"
        assert "Status: planned" in mapped["notes"]


class TestVitals:
    @pytest.mark.parametrize(
        "column,value,unit,expected",
        [
            ("temperature", 37.0, "Cel", 98.6),
            ("temperature", 98.6, "[degF]", 98.6),
            ("weight", 68.2, "kg", 150.36),
            ("weight", 150.0, "[lb_av]", 150.0),
            ("height", 170.0, "cm", 66.93),
            ("height", 1.7, "m", 66.93),
            ("blood_glucose", 5.5, "mmol/L", 99.1),
            ("blood_glucose", 104.0, "mg/dL", 104.0),
            ("oxygen_saturation", 97.0, "%", 97.0),
        ],
    )
    def test_unit_conversion(self, column, value, unit, expected):
        assert round(convert_vital_value(column, value, unit), 2) == expected

    def test_unknown_unit_is_rejected(self):
        assert convert_vital_value("weight", 10, "stone") is None

    def test_grouping_by_effective_time_and_bmi(self):
        when = "2024-03-01T09:05:00-06:00"
        observations = [
            _obs("o1", "8480-6", 128, "mm[Hg]", when),
            _obs("o2", "8462-4", 82, "mm[Hg]", when),
            _obs("o3", "8867-4", 71.6, "/min", when),
            _obs("o4", "8310-5", 37.0, "Cel", when),
            _obs("o5", "29463-7", 68.2, "kg", when),
            _obs("o6", "8302-2", 170, "cm", when),
            _obs("o7", "2708-6", 97, "%", when),
            _obs("o8", "9279-1", 16, "/min", when),
            _obs("o9", "2339-0", 5.5, "mmol/L", when),
            _obs("o10", "8867-4", 80, "/min", "2024-03-02T09:00:00-06:00"),
        ]
        unmapped = []
        rows = map_vital_signs(observations, PATIENT_ID, SOURCE, unmapped)
        assert unmapped == []
        assert len(rows) == 2
        first = rows[0]
        assert first["recorded_date"] == datetime(
            2024, 3, 1, 15, 5, tzinfo=timezone.utc
        )
        assert first["external_id"] == "vitals:2024-03-01T15:05:00+00:00"
        assert first["systolic_bp"] == 128 and first["diastolic_bp"] == 82
        assert first["heart_rate"] == 72  # rounded to an integer column
        assert first["temperature"] == 98.6
        assert first["weight"] == 150.36
        assert first["height"] == 66.93
        assert first["oxygen_saturation"] == 97
        assert first["respiratory_rate"] == 16
        assert first["blood_glucose"] == 99.1
        assert first["bmi"] == 23.6
        assert rows[1]["heart_rate"] == 80

    def test_blood_pressure_panel_components(self):
        panel = {
            "resourceType": "Observation",
            "id": "bp",
            "status": "final",
            "category": [{"coding": [{"code": "vital-signs"}]}],
            "code": _cc("85354-9", "Blood pressure panel", system="http://loinc.org"),
            "effectiveDateTime": "2024-01-01",
            "component": [
                {
                    "code": _cc("8480-6", "Systolic", system="http://loinc.org"),
                    "valueQuantity": {"value": 120, "unit": "mm[Hg]"},
                },
                {
                    "code": _cc("8462-4", "Diastolic", system="http://loinc.org"),
                    "valueQuantity": {"value": 80, "unit": "mm[Hg]"},
                },
            ],
        }
        rows = map_vital_signs([panel], PATIENT_ID, SOURCE, [])
        assert rows[0]["systolic_bp"] == 120 and rows[0]["diastolic_bp"] == 80

    def test_unmapped_code_and_unit_are_reported(self):
        unmapped = []
        rows = map_vital_signs(
            [_obs("x1", "77606-2", 7.3, "%"), _obs("x2", "29463-7", 10, "stone")],
            PATIENT_ID,
            SOURCE,
            unmapped,
        )
        assert rows == []
        reasons = {u.reason for u in unmapped}
        assert "vital sign code not mapped" in reasons
        assert any("stone" in r for r in reasons)


class TestLabResults:
    def _report_and_observations(self):
        when = "2024-03-01T07:30:00-06:00"
        glucose = _obs("g", "2345-7", 104, "mg/dL", when, category="laboratory")
        glucose["code"]["coding"][0][
            "display"
        ] = "Glucose [Mass/volume] in Serum or Plasma"
        glucose["interpretation"] = [{"coding": [{"code": "H"}]}]
        glucose["referenceRange"] = [
            {"low": {"value": 70}, "high": {"value": 99}, "text": "70 - 99 mg/dL"}
        ]
        sodium = _obs("s", "2951-2", 138, "mmol/L", when, category="laboratory")
        sodium["code"]["coding"][0]["display"] = "Sodium"
        sodium["referenceRange"] = [{"text": "135 - 145 mmol/L"}]
        hiv = {
            "resourceType": "Observation",
            "id": "h",
            "status": "final",
            "category": [{"coding": [{"code": "laboratory"}]}],
            "code": _cc("75622-1", "HIV 1 and 2 Ab", system="http://loinc.org"),
            "effectiveDateTime": when,
            "valueCodeableConcept": _cc("260385009", "Negative"),
        }
        absent = {
            "resourceType": "Observation",
            "id": "k",
            "status": "final",
            "category": [{"coding": [{"code": "laboratory"}]}],
            "code": _cc("2823-3", "Potassium", system="http://loinc.org"),
            "effectiveDateTime": when,
            "dataAbsentReason": {"coding": [{"code": "unknown", "display": "Unknown"}]},
        }
        report = {
            "resourceType": "DiagnosticReport",
            "id": "dr1",
            "status": "final",
            "code": _cc(
                "24323-8",
                "Comprehensive metabolic 2000 panel",
                system="http://loinc.org",
            ),
            "effectiveDateTime": when,
            "performer": [{"display": "Example Reference Laboratory"}],
            "result": [
                {"reference": f"Observation/{o}"}
                for o in ("g", "s", "h", "k", "missing")
            ],
        }
        return report, {o["id"]: o for o in (glucose, sodium, hiv, absent)}

    def test_report_with_components(self):
        report, observations = self._report_and_observations()
        unmapped = []
        lookup = lambda name: (
            ("Glucose", "chemistry") if name.startswith("Glucose") else (None, None)
        )
        results = map_lab_results(
            [report], observations, PATIENT_ID, SOURCE, lookup, unmapped
        )
        assert len(results) == 1
        lab = results[0]
        assert lab["external_id"] == "dr1"
        assert lab["test_name"].startswith("Comprehensive metabolic")
        assert lab["test_code"] == "24323-8"
        assert lab["completed_date"] == date(2024, 3, 1)
        assert lab["status"] == "completed"
        assert lab["facility"] == "Example Reference Laboratory"
        assert lab["is_panel"] is True
        assert lab["labs_result"] == "abnormal"
        components = {c["external_id"]: c for c in lab["components"]}
        assert set(components) == {"g", "s", "h"}
        glucose = components["g"]
        assert glucose["status"] == "high"  # from the interpretation code
        assert glucose["value"] == 104 and glucose["unit"] == "mg/dL"
        assert (glucose["ref_range_min"], glucose["ref_range_max"]) == (70, 99)
        assert (
            glucose["canonical_test_name"] == "Glucose"
            and glucose["category"] == "chemistry"
        )
        sodium = components["s"]
        assert sodium["status"] is None  # left for the schema to derive from the range
        assert (sodium["ref_range_min"], sodium["ref_range_max"]) == (135, 145)
        hiv = components["h"]
        assert (
            hiv["result_type"] == "qualitative"
            and hiv["qualitative_value"] == "negative"
        )
        reasons = {u.reason for u in unmapped}
        assert "result observation missing from bundle" in reasons
        assert "laboratory observation has no value (unknown)" in reasons

    def test_orphan_lab_observations_are_grouped_by_date(self):
        _, observations = self._report_and_observations()
        results = map_lab_results([], observations, PATIENT_ID, SOURCE, None, [])
        assert len(results) == 1
        assert results[0]["external_id"] == "labs:2024-03-01"
        assert len(results[0]["components"]) == 3


class TestBundle:
    def test_bundle_routes_resources_and_reports_unmapped(self):
        bundle = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "pt",
                        "name": [{"family": "Sampleton", "given": ["Jane"]}],
                        "birthDate": "1984-07-22",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Organization",
                        "id": "org",
                        "name": "Example",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "c1",
                        "code": _cc("1", "Hypertension"),
                    }
                },
                {
                    "resource": {
                        "resourceType": "AllergyIntolerance",
                        "id": "nka",
                        "code": _cc("2", "No known allergies"),
                        "verificationStatus": _status("refuted", "x"),
                    }
                },
                {
                    "resource": {
                        "resourceType": "Immunization",
                        "id": "nd",
                        "status": "not-done",
                        "vaccineCode": _cc("3", "Tdap"),
                        "occurrenceDateTime": "2024-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "smoke",
                        "status": "final",
                        "category": [{"coding": [{"code": "social-history"}]}],
                        "code": _cc("72166-2", "Smoking"),
                    }
                },
                {"resource": _obs("v1", "8867-4", 70, "/min")},
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "err",
                        "status": "entered-in-error",
                        "code": _cc("9", "Oops"),
                    }
                },
            ],
        }
        mapped = map_bundle(bundle, patient_id=PATIENT_ID, import_source="fhir")
        assert mapped.patient["first_name"] == "Jane" and mapped.patient[
            "birth_date"
        ] == date(1984, 7, 22)
        assert [c["external_id"] for c in mapped.conditions] == ["c1"]
        assert (
            mapped.vitals[0]["heart_rate"] == 70
            and mapped.vitals[0]["import_source"] == "fhir"
        )
        reasons = {(u.resource_type, u.reason) for u in mapped.unmapped}
        assert ("Organization", "resource type not imported") in reasons
        assert ("AllergyIntolerance", "negated entry (no known allergy)") in reasons
        assert ("Immunization", "not administered") in reasons
        assert (
            "Observation",
            "observation category not imported (social-history)",
        ) in reasons
        assert ("Condition", "entered in error") in reasons
