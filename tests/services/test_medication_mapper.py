"""
Tests for the FHIR MedicationStatement and MedicationRequest mappers.
"""

from datetime import date

from app.models.clinical import Medication
from app.services.importers.mappers.medication_mapper import (
    MedicationRequestMapper,
    MedicationStatementMapper,
)
from app.services.importers.provenance import content_hash

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
EMPTY = {"resourceType": "Bundle", "entry": []}


def _concept(code, display):
    return {"coding": [{"system": RXNORM, "code": code, "display": display}]}


def _statement(**overrides):
    resource = {
        "resourceType": "MedicationStatement",
        "id": "ms1",
        "status": "active",
        "medicationCodeableConcept": _concept("314076", "Lisinopril 10 MG Oral Tablet"),
        "effectivePeriod": {"start": "2019-03-02"},
        "reasonCode": [{"text": "Hypertension"}],
        "dosage": [
            {"text": "Take 1 tablet by mouth once daily", "route": {"text": "Oral"}}
        ],
    }
    resource.update(overrides)
    return resource


def _request(**overrides):
    resource = {
        "resourceType": "MedicationRequest",
        "id": "mr1",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": _concept("745679", "Albuterol Inhaler"),
        "authoredOn": "2003-01-10",
        "dosageInstruction": [
            {
                "route": {"coding": [{"display": "Respiratory tract route"}]},
                "timing": {
                    "repeat": {
                        "frequency": 2,
                        "period": 1,
                        "periodUnit": "d",
                        "boundsPeriod": {"start": "2003-01-10", "end": "2004-08-15"},
                    }
                },
                "doseAndRate": [{"doseQuantity": {"value": 2, "unit": "{puff}"}}],
            }
        ],
    }
    resource.update(overrides)
    return resource


def _bundle(*resources):
    return {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}


class TestMedicationStatementMapper:
    """Test MedicationStatement resources become medication rows."""

    mapper = MedicationStatementMapper()

    def test_sig_route_dates_and_indication(self):
        """Test the sig becomes the dosage and route, dates and reason are carried over."""
        mapped = self.mapper.map(_statement(), EMPTY)
        assert mapped.record_type == "medication"
        assert mapped.data["medication_name"] == "Lisinopril 10 MG Oral Tablet"
        assert mapped.data["dosage"] == "Take 1 tablet by mouth once daily"
        assert mapped.data["frequency"] is None
        assert mapped.data["route"] == "oral"
        assert mapped.data["indication"] == "Hypertension"
        assert mapped.data["effective_period_start"] == date(2019, 3, 2)
        assert mapped.data["effective_period_end"] is None
        assert mapped.data["status"] == "active"
        assert mapped.data["medication_type"] == "prescription"
        assert mapped.data["notes"] is None

    def test_status_mapping(self):
        """Test intended, not-taken and unknown statuses."""
        assert (
            self.mapper.map(_statement(status="intended"), EMPTY).data["status"]
            == "active"
        )
        assert (
            self.mapper.map(_statement(status="not-taken"), EMPTY).data["status"]
            == "cancelled"
        )
        assert (
            self.mapper.map(_statement(status="unknown"), EMPTY).data["status"]
            == "active"
        )

    def test_entered_in_error_is_skipped(self):
        """Test that an entered-in-error statement is not mapped."""
        assert self.mapper.map(_statement(status="entered-in-error"), EMPTY) is None

    def test_long_sig_goes_to_notes(self):
        """Test that a sig over the dosage limit is kept in full in the notes."""
        sig = (
            "Take one tablet by mouth every morning with food and a full glass of water"
        )
        mapped = self.mapper.map(_statement(dosage=[{"text": sig}]), EMPTY)
        assert mapped.data["dosage"].endswith("...")
        assert mapped.data["notes"] == f"Sig: {sig}"
        assert "dosage truncated to 50 characters" in mapped.warnings

    def test_unknown_route_text_is_kept(self):
        """Test that a route MediKeep has no value for is stored as given."""
        resource = _statement(dosage=[{"route": {"text": "Intravitreal"}}])
        assert self.mapper.map(resource, EMPTY).data["route"] == "Intravitreal"

    def test_end_before_start_is_dropped(self):
        """Test that an end date before the start is dropped with a warning."""
        resource = _statement(
            effectivePeriod={"start": "2019-03-02", "end": "2018-01-01"}
        )
        mapped = self.mapper.map(resource, EMPTY)
        assert mapped.data["effective_period_end"] is None
        assert mapped.warnings == ["end before start, end date dropped"]

    def test_reference_to_bundle_medication(self):
        """Test that a medicationReference is resolved against the bundle."""
        medication = {
            "resourceType": "Medication",
            "id": "m9",
            "code": {"text": "Metformin 500 MG Oral Tablet"},
        }
        resource = _statement(medicationReference={"reference": "Medication/m9"})
        resource.pop("medicationCodeableConcept")
        mapped = self.mapper.map(resource, _bundle(medication, resource))
        assert mapped.data["medication_name"] == "Metformin 500 MG Oral Tablet"

    def test_without_name_is_skipped(self):
        """Test that a statement with no resolvable drug name is not mapped."""
        resource = _statement(medicationReference={"reference": "Medication/missing"})
        resource.pop("medicationCodeableConcept")
        assert self.mapper.map(resource, EMPTY) is None

    def test_hash_matches_row(self):
        """Test that a row built from the mapped data hashes the same as the resource."""
        mapped = self.mapper.map(_statement(), EMPTY)
        row = Medication(patient_id=1, **mapped.data)
        assert content_hash(self.mapper.hash_parts_for_row(row)) == content_hash(
            mapped.hash_parts
        )
        row.dosage = "2 tablets"
        assert content_hash(self.mapper.hash_parts_for_row(row)) != content_hash(
            mapped.hash_parts
        )


class TestMedicationRequestMapper:
    """Test MedicationRequest resources become medication rows."""

    mapper = MedicationRequestMapper()

    def test_dose_timing_route_and_bounds(self):
        """Test structured dose, timing and bounds are rendered as text and dates."""
        mapped = self.mapper.map(_request(), EMPTY)
        assert mapped.data["dosage"] == "2 puff"
        assert mapped.data["frequency"] == "2 times per day"
        assert mapped.data["route"] == "Respiratory tract route"
        assert mapped.data["effective_period_start"] == date(2003, 1, 10)
        assert mapped.data["effective_period_end"] == date(2004, 8, 15)

    def test_contained_medication(self):
        """Test that a contained Medication supplies the name."""
        resource = _request(
            contained=[
                {
                    "resourceType": "Medication",
                    "id": "inner",
                    "code": {"text": "Albuterol 0.09 MG/ACTUAT Inhaler"},
                }
            ],
            medicationReference={"reference": "#inner"},
        )
        resource.pop("medicationCodeableConcept")
        mapped = self.mapper.map(resource, EMPTY)
        assert mapped.data["medication_name"] == "Albuterol 0.09 MG/ACTUAT Inhaler"

    def test_no_start_when_no_bounds(self):
        """Test that authoredOn is not used as the start when the timing has no bounds."""
        resource = _request(dosageInstruction=[{"text": "2 puffs twice daily"}])
        mapped = self.mapper.map(resource, EMPTY)
        assert mapped.data["effective_period_start"] is None
        assert mapped.hash_parts[3] is None
        assert mapped.data["dosage"] == "2 puffs twice daily"

    def test_validity_period_is_start(self):
        """Test that dispenseRequest.validityPeriod is the start when the timing has no bounds."""
        resource = _request(
            dosageInstruction=[{"text": "2 puffs twice daily"}],
            dispenseRequest={"validityPeriod": {"start": "2003-02-01"}},
        )
        mapped = self.mapper.map(resource, EMPTY)
        assert mapped.data["effective_period_start"] == date(2003, 2, 1)

    def test_frequency_wording(self):
        """Test the wording for daily, every N days and timed doses."""
        cases = [
            ({"frequency": 1, "period": 1, "periodUnit": "d"}, "daily"),
            ({"frequency": 1, "period": 2, "periodUnit": "wk"}, "every 2 weeks"),
            (
                {"frequency": 3, "period": 1, "periodUnit": "d", "when": ["AC"]},
                "3 times per day (AC)",
            ),
            ({"frequency": 1, "period": 1, "periodUnit": "h"}, "every hour"),
            ({"frequency": 1, "period": 0.5, "periodUnit": "d"}, "2 times per day"),
            ({"frequency": 1, "period": 0.333, "periodUnit": "d"}, "3 times per day"),
            ({"frequency": 1, "period": 0.25, "periodUnit": "h"}, "4 times per hour"),
        ]
        for repeat, expected in cases:
            resource = _request(dosageInstruction=[{"timing": {"repeat": repeat}}])
            assert self.mapper.map(resource, EMPTY).data["frequency"] == expected

    def test_cancelled_and_error_are_skipped(self):
        """Test that cancelled and entered-in-error requests are not mapped."""
        for status in ("cancelled", "entered-in-error"):
            assert self.mapper.map(_request(status=status), EMPTY) is None

    def test_draft_is_active(self):
        """Test that a draft request is stored as active."""
        assert (
            self.mapper.map(_request(status="draft"), EMPTY).data["status"] == "active"
        )

    def test_statement_for_same_drug_wins(self):
        """Test that a request is skipped when a statement covers the same drug and start."""
        statement = _statement(
            medicationCodeableConcept=_concept("745679", "Albuterol Inhaler"),
            effectivePeriod={"start": "2003-01-10"},
        )
        assert self.mapper.map(_request(), _bundle(statement, _request())) is None

    def test_statement_with_other_start_does_not_block(self):
        """Test that a statement with a different start date leaves the request in."""
        statement = _statement(
            medicationCodeableConcept=_concept("745679", "Albuterol Inhaler"),
            effectivePeriod={"start": "2010-01-01"},
        )
        assert self.mapper.map(_request(), _bundle(statement, _request())) is not None

    def test_statement_without_start_blocks(self):
        """Test that a statement with no start date still covers the request."""
        statement = _statement(
            medicationCodeableConcept=_concept("745679", "Albuterol Inhaler")
        )
        statement.pop("effectivePeriod")
        assert self.mapper.map(_request(), _bundle(statement, _request())) is None

    def test_hash_matches_row(self):
        """Test that a row built from the mapped data hashes the same as the resource."""
        mapped = self.mapper.map(_request(), EMPTY)
        row = Medication(patient_id=1, **mapped.data)
        assert content_hash(self.mapper.hash_parts_for_row(row)) == content_hash(
            mapped.hash_parts
        )
