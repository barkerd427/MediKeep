"""
Tests for the small FHIR helpers shared by the mappers.
"""

from datetime import date

from app.services.importers.mappers import _fhir as f


class TestFhirHelpers:
    """Test date parsing, number coercion and the bundle index."""

    def test_date_keeps_the_source_calendar_day(self):
        """Test that a dateTime near midnight keeps the date as written, with no zone shift."""
        assert f.parse_date("2024-01-01T23:30:00-05:00") == date(2024, 1, 1)
        assert f.parse_date("2024-01-01T00:30:00+09:00") == date(2024, 1, 1)
        assert f.parse_date("2024") == date(2024, 1, 1)
        assert f.parse_date("2024-07") == date(2024, 7, 1)
        assert f.parse_date("not a date") is None

    def test_to_float(self):
        """Test numbers and numeric strings coerce and anything else is None."""
        assert f.to_float("5") == 5.0
        assert f.to_float(0.5) == 0.5
        assert f.to_float("five") is None
        assert f.to_float(None) is None
        assert f.to_float(True) is None
        assert f.to_float(float("inf")) is None

    def test_bundle_index_is_built_once_per_bundle(self):
        """Test that lookups hit a cached index and a changed bundle is re-indexed."""
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {"resource": {"resourceType": "Medication", "id": "m1"}},
                {"resource": "not a resource"},
                {"resource": {"resourceType": 7, "id": "x"}},
            ],
        }
        first = f.bundle_index(bundle)
        assert first is f.bundle_index(bundle)
        assert (
            f.find_resource(bundle, "Medication", "m1")
            is bundle["entry"][0]["resource"]
        )
        assert f.find_resource(bundle, "Medication", "m2") is None
        assert first.by_type == {"Medication": [bundle["entry"][0]["resource"]]}
        bundle["entry"].append({"resource": {"resourceType": "Medication", "id": "m2"}})
        assert f.find_resource(bundle, "Medication", "m2") is not None
