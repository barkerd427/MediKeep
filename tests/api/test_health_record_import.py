"""
Test the health record import API endpoints with a hand-written FHIR bundle and
a MyChart-style C-CDA.
"""

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud.patient import patient as patient_crud
from app.models.activity_log import ActivityLog
from app.models.models import (
    Allergy,
    Condition,
    ImportProvenance,
    Medication,
    PatientShare,
)
from app.schemas.patient import PatientCreate
from app.services.importers import upload
from tests.utils.user import create_random_user, create_user_token_headers

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "health_record_import"
FIXTURE = FIXTURES / "fhir_bundle_minimal.json"
MAPPER_FIXTURE = FIXTURES / "fhir_bundle_mapper.json"
MYCHART_CCD = FIXTURES / "mychart_like_ccd.xml"
FORMATS_URL = "/api/v1/health-records/import/formats"


def _preview_url(patient_id: int) -> str:
    return f"/api/v1/health-records/patient/{patient_id}/import/preview"


def _execute_url(patient_id: int) -> str:
    return f"/api/v1/health-records/patient/{patient_id}/import/execute"


def _upload(data: Optional[bytes] = None, name: str = "bundle.json"):
    """Build the files= argument for a multipart upload."""
    return {
        "file": (name, io.BytesIO(data or FIXTURE.read_bytes()), "application/json")
    }


def _xdm_zip(xml: bytes, members: int = 1) -> bytes:
    """Wrap a C-CDA in the folder layout MyChart's download produces."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(members):
            zf.writestr(
                f"MachineReadable_XDMFormat/IHE_XDM/SUBSET{i:02d}/DOC0001.XML", xml
            )
        zf.writestr("MachineReadable_XDMFormat/IHE_XDM/SUBSET00/METADATA.XML", "<x/>")
        zf.writestr("MachineReadable_XDMFormat/INDEX.HTM", "<html></html>")
    return buf.getvalue()


def _bundle_with_diagnosis(diagnosis: str) -> bytes:
    """The fixture with the first condition's text changed."""
    bundle = json.loads(FIXTURE.read_text())
    bundle["entry"][1]["resource"]["code"]["text"] = diagnosis
    return json.dumps(bundle).encode()


def _unsupported_only() -> bytes:
    """The fixture reduced to its Patient and Observation entries."""
    bundle = json.loads(FIXTURE.read_text())
    bundle["entry"] = [
        e
        for e in bundle["entry"]
        if e["resource"]["resourceType"] in ("Patient", "Observation")
    ]
    return json.dumps(bundle).encode()


def _conditions(db: Session, patient_id: int):
    return db.query(Condition).filter(Condition.patient_id == patient_id).all()


def _allergies(db: Session, patient_id: int):
    return db.query(Allergy).filter(Allergy.patient_id == patient_id).all()


def _medications(db: Session, patient_id: int):
    return db.query(Medication).filter(Medication.patient_id == patient_id).all()


def _provenance(db: Session, patient_id: int):
    return (
        db.query(ImportProvenance)
        .filter(ImportProvenance.patient_id == patient_id)
        .all()
    )


def _import_logs(db: Session, patient_id: int):
    return (
        db.query(ActivityLog)
        .filter(
            ActivityLog.patient_id == patient_id,
            ActivityLog.entity_type == "health_record_import",
        )
        .all()
    )


def _shared_user_headers(db: Session, user_with_patient, permission_level: str):
    """Create a second user, share the patient with them, return their headers."""
    owner = user_with_patient["user"]
    patient_id = user_with_patient["patient"].id
    other = create_random_user(db)["user"]
    patient_crud.create_for_user(
        db,
        user_id=other.id,
        patient_data=PatientCreate(
            first_name="Other", last_name="User", birth_date=date(1985, 5, 5)
        ),
    )
    db.add(
        PatientShare(
            patient_id=patient_id,
            shared_by_user_id=owner.id,
            shared_with_user_id=other.id,
            permission_level=permission_level,
            is_active=True,
        )
    )
    db.commit()
    return create_user_token_headers(other.username)


class TestFormats:
    """Test the formats endpoint."""

    def test_lists_formats_and_limit(self, client: TestClient, authenticated_headers):
        """Test that the formats endpoint lists the FHIR bundle format and the size limit."""
        response = client.get(FORMATS_URL, headers=authenticated_headers)
        assert response.status_code == 200
        body = response.json()
        assert {f["key"] for f in body["formats"]} == {"fhir_bundle", "ccda"}
        assert body["max_file_size_bytes"] == upload.MAX_IMPORT_FILE_SIZE

    def test_requires_auth(self, client: TestClient):
        """Test that the formats endpoint requires authentication."""
        assert client.get(FORMATS_URL).status_code == 401


class TestPreview:
    """Test the preview endpoint."""

    def test_counts_supported_and_unsupported(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that mapped resources are counted and the rest reported as unsupported."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "fhir"
        assert body["format_key"] == "fhir_bundle"
        assert body["total_resources"] == 4
        assert body["counts"] == {
            "condition": {"new": 2, "duplicate": 0, "changed": 0, "edited": 0},
            "allergy": {"new": 1, "duplicate": 0, "changed": 0, "edited": 0},
        }
        assert body["unsupported"] == {"Observation": 1}

    def test_patient_summary_and_mismatch_warnings(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that the file's patient is summarized and differences are warned about."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        body = response.json()
        assert body["patient"] == {
            "full_name": "Jane Quinn Sampleton",
            "birth_date": "1980-04-12",
            "gender": "female",
        }
        # The fixture patient is Test User, born 1990-01-01
        assert len(body["warnings"]) == 2
        assert "Jane Quinn Sampleton" in body["warnings"][0]
        assert "1980-04-12" in body["warnings"][1]

    def test_writes_nothing(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that a preview creates no rows and no provenance."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _preview_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        assert _conditions(db_session, patient_id) == []
        assert _allergies(db_session, patient_id) == []
        assert _provenance(db_session, patient_id) == []

    def test_rejects_wrong_extension(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that a file with an unsupported extension is rejected."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            files=_upload(b"<x/>", "export.csv"),
            headers=authenticated_headers,
        )
        assert response.status_code == 422
        assert "Unsupported file type" in response.text

    def test_rejects_oversized_file(
        self, client: TestClient, user_with_patient, authenticated_headers, monkeypatch
    ):
        """Test that a file over the size limit is rejected."""
        monkeypatch.setattr(upload, "MAX_IMPORT_FILE_SIZE", 100)
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            files=_upload(b"{" + b" " * 200 + b"}"),
            headers=authenticated_headers,
        )
        assert response.status_code == 422
        assert "exceeds" in response.text

    def test_rejects_non_bundle(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that JSON that is not a Bundle fails detection and explicit parsing."""
        patient_id = user_with_patient["patient"].id
        data = json.dumps({"resourceType": "Patient"}).encode()
        response = client.post(
            _preview_url(patient_id), files=_upload(data), headers=authenticated_headers
        )
        assert response.status_code == 400
        assert "Could not recognize" in response.text

        response = client.post(
            _preview_url(patient_id),
            data={"format_key": "fhir_bundle"},
            files=_upload(data),
            headers=authenticated_headers,
        )
        assert response.status_code == 400
        assert "not a FHIR Bundle" in response.text

    def test_rejects_deeply_nested_json(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that JSON nested past the parser's limit is a 400, not a crash."""
        patient_id = user_with_patient["patient"].id
        data = b"[" * 100000 + b"]" * 100000
        response = client.post(
            _preview_url(patient_id), files=_upload(data), headers=authenticated_headers
        )
        assert response.status_code == 400
        response = client.post(
            _preview_url(patient_id),
            data={"format_key": "fhir_bundle"},
            files=_upload(data),
            headers=authenticated_headers,
        )
        assert response.status_code == 400

    def test_unknown_format_key(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that an unknown format key is rejected."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            data={"format_key": "nope"},
            files=_upload(),
            headers=authenticated_headers,
        )
        assert response.status_code == 400
        assert "Unsupported format" in response.text


class TestExecute:
    """Test the execute endpoint."""

    def test_unsupported_only_writes_nothing_but_logs(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that a file with only unsupported resources creates no rows but is logged."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _execute_url(patient_id),
            files=_upload(_unsupported_only()),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"] == {}
        assert body["unsupported"] == {"Observation": 1}
        assert len(body["import_batch_id"]) == 32
        assert _conditions(db_session, patient_id) == []
        logs = _import_logs(db_session, patient_id)
        assert len(logs) == 1
        assert logs[0].action == "imported"
        assert logs[0].event_metadata["import_batch_id"] == body["import_batch_id"]

    def test_creates_rows_and_provenance(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that mapped resources become rows with one provenance row each."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"]["condition"] == {
            "created": 2,
            "updated": 0,
            "skipped_duplicate": 0,
            "skipped_edited": 0,
        }

        assert body["counts"]["allergy"]["created"] == 1

        rows = _conditions(db_session, patient_id)
        assert {r.diagnosis for r in rows} == {"Essential hypertension", "Asthma"}
        allergy = _allergies(db_session, patient_id)[0]
        assert (allergy.allergen, allergy.reaction) == ("Penicillin", "Hives")
        provenance = _provenance(db_session, patient_id)
        assert {(p.record_type, p.record_id) for p in provenance} == {
            *(("condition", r.id) for r in rows),
            ("allergy", allergy.id),
        }
        assert {p.external_id for p in provenance} == {"cond-1", "cond-2", "allergy-1"}
        assert {p.source for p in provenance} == {"fhir"}
        assert {p.import_batch_id for p in provenance} == {body["import_batch_id"]}

    def test_second_import_is_all_duplicates(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that importing the same file twice creates nothing the second time."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )

        preview = client.post(
            _preview_url(patient_id), files=_upload(), headers=authenticated_headers
        ).json()
        assert preview["counts"]["condition"] == {
            "new": 0,
            "duplicate": 2,
            "changed": 0,
            "edited": 0,
        }

        response = client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        assert response.json()["counts"]["condition"]["skipped_duplicate"] == 2
        assert response.json()["counts"]["condition"]["created"] == 0
        assert response.json()["counts"]["allergy"]["skipped_duplicate"] == 1
        assert len(_conditions(db_session, patient_id)) == 2
        assert len(_allergies(db_session, patient_id)) == 1

    def test_locally_edited_row_is_skipped_by_default(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that a row edited in MediKeep shows as changed and is not overwritten."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )

        edited = next(
            r for r in _conditions(db_session, patient_id) if r.diagnosis == "Asthma"
        )
        edited.diagnosis = "Childhood asthma"
        db_session.commit()

        preview = client.post(
            _preview_url(patient_id), files=_upload(), headers=authenticated_headers
        ).json()
        assert preview["counts"]["condition"] == {
            "new": 0,
            "duplicate": 1,
            "changed": 1,
            "edited": 1,
        }

        response = client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        assert response.json()["counts"]["condition"]["skipped_edited"] == 1
        db_session.refresh(edited)
        assert edited.diagnosis == "Childhood asthma"

    def test_locally_edited_row_is_overwritten_when_asked(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that skip_edited=false updates the edited row and keeps its notes."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )

        edited = next(
            r for r in _conditions(db_session, patient_id) if r.diagnosis == "Asthma"
        )
        edited.diagnosis = "Childhood asthma"
        edited.notes = "my note"
        db_session.commit()

        response = client.post(
            _execute_url(patient_id),
            data={"skip_edited": "false"},
            files=_upload(),
            headers=authenticated_headers,
        )
        assert response.json()["counts"]["condition"]["updated"] == 1
        db_session.refresh(edited)
        assert edited.diagnosis == "Asthma"
        assert edited.notes == "my note"

    def test_source_change_updates_row(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that a changed value in the source file updates the untouched row."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )

        response = client.post(
            _execute_url(patient_id),
            files=_upload(_bundle_with_diagnosis("Hypertension, essential")),
            headers=authenticated_headers,
        )
        assert response.json()["counts"]["condition"] == {
            "created": 0,
            "updated": 1,
            "skipped_duplicate": 1,
            "skipped_edited": 0,
        }
        assert {r.diagnosis for r in _conditions(db_session, patient_id)} == {
            "Hypertension, essential",
            "Asthma",
        }

    def test_preview_after_user_deleted_rows_writes_nothing(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that preview leaves stale provenance alone and execute replaces it."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        for row in _conditions(db_session, patient_id):
            db_session.delete(row)
        db_session.commit()

        preview = client.post(
            _preview_url(patient_id), files=_upload(), headers=authenticated_headers
        ).json()
        assert preview["counts"]["condition"]["new"] == 2
        assert not db_session.deleted
        assert not db_session.dirty
        assert len(_provenance(db_session, patient_id)) == 3

        response = client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        assert response.json()["counts"]["condition"]["created"] == 2
        rows = _conditions(db_session, patient_id)
        provenance = _provenance(db_session, patient_id)
        assert len(rows) == 2
        assert len(provenance) == 3
        assert {r.id for r in rows} <= {p.record_id for p in provenance}

    def test_repeated_resource_in_one_file_is_a_duplicate(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that two identical id-less resources in one upload create one row."""
        patient_id = user_with_patient["patient"].id
        bundle = json.loads(FIXTURE.read_text())
        condition = next(
            e for e in bundle["entry"] if e["resource"]["resourceType"] == "Condition"
        )
        condition["resource"].pop("id")
        bundle["entry"] = [condition, json.loads(json.dumps(condition))]
        data = json.dumps(bundle).encode()

        preview = client.post(
            _preview_url(patient_id), files=_upload(data), headers=authenticated_headers
        ).json()
        assert preview["counts"]["condition"] == {
            "new": 1,
            "duplicate": 1,
            "changed": 0,
            "edited": 0,
        }
        response = client.post(
            _execute_url(patient_id), files=_upload(data), headers=authenticated_headers
        )
        assert response.json()["counts"]["condition"]["created"] == 1
        assert response.json()["counts"]["condition"]["skipped_duplicate"] == 1
        assert len(_conditions(db_session, patient_id)) == 1
        assert len(_provenance(db_session, patient_id)) == 1

    def test_hash_match_refreshes_external_id(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that a record matched by content under a new source id keeps the new id."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id), files=_upload(), headers=authenticated_headers
        )
        bundle = json.loads(FIXTURE.read_text())
        for entry in bundle["entry"]:
            if entry["resource"]["resourceType"] == "Condition":
                entry["resource"]["id"] = "new-" + entry["resource"]["id"]
        data = json.dumps(bundle).encode()

        response = client.post(
            _execute_url(patient_id), files=_upload(data), headers=authenticated_headers
        )
        assert response.json()["counts"]["condition"]["skipped_duplicate"] == 2
        db_session.expire_all()
        assert {p.external_id for p in _provenance(db_session, patient_id)} == {
            "new-cond-1",
            "new-cond-2",
            "allergy-1",
        }

    def test_sections_filter(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that a sections list excludes record types not in it."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _execute_url(patient_id),
            data={"sections": "allergy"},
            files=_upload(),
            headers=authenticated_headers,
        )
        assert response.status_code == 200
        assert list(response.json()["counts"]) == ["allergy"]
        assert _conditions(db_session, patient_id) == []
        assert len(_allergies(db_session, patient_id)) == 1

        response = client.post(
            _execute_url(patient_id),
            data={"sections": "condition"},
            files=_upload(),
            headers=authenticated_headers,
        )
        assert response.json()["counts"]["condition"]["created"] == 2
        assert len(_conditions(db_session, patient_id)) == 2

    def test_view_only_share_is_forbidden(
        self, client: TestClient, db_session: Session, user_with_patient
    ):
        """Test that a user with a view-only share cannot import."""
        patient_id = user_with_patient["patient"].id
        headers = _shared_user_headers(db_session, user_with_patient, "view")
        assert (
            client.post(_preview_url(patient_id), files=_upload(), headers=headers)
        ).status_code == 403
        assert (
            client.post(_execute_url(patient_id), files=_upload(), headers=headers)
        ).status_code == 403
        assert _import_logs(db_session, patient_id) == []

    def test_edit_share_is_allowed(
        self, client: TestClient, db_session: Session, user_with_patient
    ):
        """Test that a user with an edit share can preview and import."""
        patient_id = user_with_patient["patient"].id
        headers = _shared_user_headers(db_session, user_with_patient, "edit")
        assert (
            client.post(_preview_url(patient_id), files=_upload(), headers=headers)
        ).status_code == 200
        assert (
            client.post(_execute_url(patient_id), files=_upload(), headers=headers)
        ).status_code == 200
        assert len(_import_logs(db_session, patient_id)) == 1


class TestMapperFixture:
    """Test the three mappers end to end on the mapper fixture."""

    def test_preview_counts(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test per-section counts, skipped resources and the unsupported list."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            files=_upload(MAPPER_FIXTURE.read_bytes()),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # 11 resources besides the Patient; the error, cancelled and duplicate ones map to nothing
        assert body["total_resources"] == 11
        assert body["counts"] == {
            "condition": {"new": 2, "duplicate": 0, "changed": 0, "edited": 0},
            "allergy": {"new": 3, "duplicate": 0, "changed": 0, "edited": 0},
            "medication": {"new": 2, "duplicate": 0, "changed": 0, "edited": 0},
        }
        assert body["unsupported"] == {"Observation": 1}

    def test_execute_writes_expected_rows(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test the values written for each record type."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _execute_url(patient_id),
            files=_upload(MAPPER_FIXTURE.read_bytes()),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        counts = response.json()["counts"]
        assert counts["condition"]["created"] == 2
        assert counts["allergy"]["created"] == 3
        assert counts["medication"]["created"] == 2

        htn = next(
            r for r in _conditions(db_session, patient_id) if r.icd10_code == "I10"
        )
        assert htn.snomed_code == "59621000"
        assert htn.severity == "moderate"
        assert htn.notes == "Diagnosed at annual physical."
        asthma = next(
            r for r in _conditions(db_session, patient_id) if r.diagnosis == "Asthma"
        )
        assert (asthma.status, asthma.onset_date, asthma.end_date) == (
            "resolved",
            date(1992, 6, 1),
            date(2004, 8, 15),
        )

        allergies = {a.allergen: a for a in _allergies(db_session, patient_id)}
        assert allergies["Penicillin"].reaction == "Hives, Shortness of breath"
        assert allergies["Penicillin"].severity == "severe"
        assert allergies["Peanut"].reaction == "Not specified"
        assert allergies["Peanut"].severity == "none"
        assert (allergies["Latex"].severity, allergies["Latex"].status) == (
            "severe",
            "inactive",
        )

        meds = {m.medication_name: m for m in _medications(db_session, patient_id)}
        lisinopril = meds["Lisinopril 10 MG Oral Tablet"]
        assert lisinopril.dosage == "Take 1 tablet by mouth once daily"
        assert lisinopril.route == "oral"
        assert lisinopril.indication == "Hypertension"
        albuterol = meds["Albuterol 0.09 MG/ACTUAT Metered Dose Inhaler"]
        assert (albuterol.dosage, albuterol.frequency) == ("2 puff", "2 times per day")
        assert albuterol.status == "completed"
        assert albuterol.effective_period_end == date(2004, 8, 15)

        provenance = _provenance(db_session, patient_id)
        assert len(provenance) == 7
        # The statement, not the request, is the lisinopril row's source
        assert "med-lisinopril" in {p.external_id for p in provenance}
        assert "rx-lisinopril" not in {p.external_id for p in provenance}

    def test_second_import_is_all_duplicates(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that importing the fixture twice creates nothing the second time."""
        patient_id = user_with_patient["patient"].id
        for _ in range(2):
            response = client.post(
                _execute_url(patient_id),
                files=_upload(MAPPER_FIXTURE.read_bytes()),
                headers=authenticated_headers,
            )
        counts = response.json()["counts"]
        assert {k: v["created"] for k, v in counts.items()} == {
            "condition": 0,
            "allergy": 0,
            "medication": 0,
        }
        assert {k: v["skipped_duplicate"] for k, v in counts.items()} == {
            "condition": 2,
            "allergy": 3,
            "medication": 2,
        }

    def test_edited_allergen_is_skipped(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that an allergy renamed in MediKeep is reported as changed and left alone."""
        patient_id = user_with_patient["patient"].id
        client.post(
            _execute_url(patient_id),
            files=_upload(MAPPER_FIXTURE.read_bytes()),
            headers=authenticated_headers,
        )
        peanut = next(
            a for a in _allergies(db_session, patient_id) if a.allergen == "Peanut"
        )
        peanut.allergen = "Peanuts (all forms)"
        db_session.commit()

        preview = client.post(
            _preview_url(patient_id),
            files=_upload(MAPPER_FIXTURE.read_bytes()),
            headers=authenticated_headers,
        ).json()
        assert preview["counts"]["allergy"] == {
            "new": 0,
            "duplicate": 2,
            "changed": 1,
            "edited": 1,
        }

        response = client.post(
            _execute_url(patient_id),
            files=_upload(MAPPER_FIXTURE.read_bytes()),
            headers=authenticated_headers,
        )
        assert response.json()["counts"]["allergy"]["skipped_edited"] == 1
        db_session.refresh(peanut)
        assert peanut.allergen == "Peanuts (all forms)"

    def test_sections_limits_writes(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test that sections=condition writes conditions only."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _execute_url(patient_id),
            data={"sections": "condition"},
            files=_upload(MAPPER_FIXTURE.read_bytes()),
            headers=authenticated_headers,
        )
        assert list(response.json()["counts"]) == ["condition"]
        assert len(_conditions(db_session, patient_id)) == 2
        assert _allergies(db_session, patient_id) == []
        assert _medications(db_session, patient_id) == []

    def test_truncation_warning_is_surfaced(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that a value cut to fit a column shows up in the preview warnings."""
        patient_id = user_with_patient["patient"].id
        bundle = json.loads(MAPPER_FIXTURE.read_text())
        bundle["entry"][1]["resource"]["code"]["text"] = "z" * 600
        response = client.post(
            _preview_url(patient_id),
            files=_upload(json.dumps(bundle).encode()),
            headers=authenticated_headers,
        )
        assert "Condition/cond-htn: diagnosis truncated to 500 characters" in (
            response.json()["warnings"]
        )


class TestCcdaFixture:
    """Test preview and execute on the MyChart-style C-CDA, as XML and as a zip."""

    # Resources the converter produces besides the Patient; the three mapped
    # types give 2 conditions, 1 allergy (the "no known food allergy" entry
    # maps to nothing) and 2 medications
    TOTAL_RESOURCES = 25
    EXPECTED_NEW = {"condition": 2, "allergy": 1, "medication": 2}

    def test_preview_xml(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test counts, the file's patient and the converter's notes for the XML."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            files=_upload(MYCHART_CCD.read_bytes(), "DOC0001.XML"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["source"], body["format_key"]) == ("ccda", "ccda")
        assert body["total_resources"] == self.TOTAL_RESOURCES
        assert {k: v["new"] for k, v in body["counts"].items()} == self.EXPECTED_NEW
        assert body["unsupported"]["Observation"] == 9
        assert body["patient"] == {
            "full_name": "Ms. Jane Quinn Sampleton",
            "birth_date": "1984-07-22",
            "gender": "female",
        }
        assert any("Plan of Treatment" in w for w in body["warnings"])
        assert any("selected patient" in w for w in body["warnings"])

    def test_preview_zip_matches_xml(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        """Test that the MyChart zip previews the same as its XML."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            files=_upload(_xdm_zip(MYCHART_CCD.read_bytes()), "MyChart.zip"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["format_key"] == "ccda"
        assert body["total_resources"] == self.TOTAL_RESOURCES
        assert {k: v["new"] for k, v in body["counts"].items()} == self.EXPECTED_NEW

    def test_execute_then_reimport(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        """Test rows and provenance from the zip, then duplicates from the XML."""
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _execute_url(patient_id),
            files=_upload(_xdm_zip(MYCHART_CCD.read_bytes()), "MyChart.zip"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        counts = response.json()["counts"]
        assert {k: v["created"] for k, v in counts.items()} == self.EXPECTED_NEW

        diagnoses = {c.diagnosis for c in _conditions(db_session, patient_id)}
        assert "Essential hypertension" in diagnoses
        assert {a.allergen for a in _allergies(db_session, patient_id)} == {
            "Penicillin G"
        }
        assert len(_medications(db_session, patient_id)) == 2
        provenance = _provenance(db_session, patient_id)
        assert len(provenance) == 5
        assert {p.source for p in provenance} == {"ccda"}
        assert len(_import_logs(db_session, patient_id)) == 1

        # The same document as bare XML carries the same ids, so nothing is created twice
        response = client.post(
            _execute_url(patient_id),
            files=_upload(MYCHART_CCD.read_bytes(), "DOC0001.XML"),
            headers=authenticated_headers,
        )
        counts = response.json()["counts"]
        assert {k: v["created"] for k, v in counts.items()} == {
            "condition": 0,
            "allergy": 0,
            "medication": 0,
        }
        assert {
            k: v["skipped_duplicate"] for k, v in counts.items()
        } == self.EXPECTED_NEW

    def test_zip_over_member_cap(
        self, client: TestClient, user_with_patient, authenticated_headers, monkeypatch
    ):
        """Test that a zip with too many entries is rejected."""
        monkeypatch.setattr(upload, "MAX_ZIP_MEMBERS", 2)
        patient_id = user_with_patient["patient"].id
        response = client.post(
            _preview_url(patient_id),
            data={"format_key": "ccda"},
            files=_upload(_xdm_zip(MYCHART_CCD.read_bytes(), members=3), "MyChart.zip"),
            headers=authenticated_headers,
        )
        assert response.status_code == 400
        assert "entries" in response.text
