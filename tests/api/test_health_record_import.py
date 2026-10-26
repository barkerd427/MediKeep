"""API tests for the health record import endpoints (C-CDA / MyChart zip / FHIR Bundle)."""

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud.patient import patient as patient_crud
from app.models.activity_log import ActivityLog
from app.models.models import (
    Allergy,
    Condition,
    Encounter,
    Immunization,
    LabResult,
    LabTestComponent,
    Medication,
    PatientShare,
    Procedure,
    Vitals,
)
from app.schemas.patient import PatientCreate
from app.services.health_record_import import converter
from tests.utils.user import create_random_user, create_user_token_headers

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "health_record_import"
MYCHART_CCD = FIXTURES / "mychart_like_ccd.xml"
SYNTHEA_CCD = FIXTURES / "synthea_letha284_haag279.xml"

PREVIEW_URL = "/api/v1/health-records/import/preview"
EXECUTE_URL = "/api/v1/health-records/import/execute"
FORMATS_URL = "/api/v1/health-records/import/formats"


def _upload(path_or_bytes, name, content_type="application/xml"):
    data = (
        path_or_bytes
        if isinstance(path_or_bytes, bytes)
        else Path(path_or_bytes).read_bytes()
    )
    return {"file": (name, io.BytesIO(data), content_type)}


def _xdm_zip(xml_path: Path) -> bytes:
    """Wrap a C-CDA in the folder layout MyChart's download produces."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "MachineReadable_XDMFormat/IHE_XDM/SUBSET01/DOC0001.XML",
            xml_path.read_bytes(),
        )
        zf.writestr("MachineReadable_XDMFormat/IHE_XDM/SUBSET01/METADATA.XML", "<x/>")
        zf.writestr("MachineReadable_XDMFormat/INDEX.HTM", "<html></html>")
        zf.writestr("MachineReadable_XDMFormat/README.TXT", "stub")
    return buf.getvalue()


def _row_counts(db: Session, patient_id: int) -> dict:
    counts = {}
    for key, model in (
        ("conditions", Condition),
        ("allergies", Allergy),
        ("medications", Medication),
        ("immunizations", Immunization),
        ("procedures", Procedure),
        ("encounters", Encounter),
        ("vitals", Vitals),
        ("lab_results", LabResult),
    ):
        counts[key] = db.query(model).filter(model.patient_id == patient_id).count()
    counts["lab_test_components"] = (
        db.query(LabTestComponent)
        .join(LabResult, LabTestComponent.lab_result_id == LabResult.id)
        .filter(LabResult.patient_id == patient_id)
        .count()
    )
    return counts


# Expected rows for mychart_like_ccd.xml: one allergy is a negated "no known
# food allergy" entry and one immunization is a refusal, so both are reported
# as unmapped rather than imported.
MYCHART_EXPECTED = {
    "conditions": 2,
    "allergies": 1,
    "medications": 2,
    "immunizations": 1,
    "procedures": 2,
    "encounters": 2,
    "vitals": 1,
    "lab_results": 1,
    "lab_test_components": 3,
}


class TestFormats:
    def test_formats_lists_extensions_and_limit(
        self, client: TestClient, authenticated_headers
    ):
        response = client.get(FORMATS_URL, headers=authenticated_headers)
        assert response.status_code == 200
        body = response.json()
        extensions = {ext for fmt in body["formats"] for ext in fmt["extensions"]}
        assert extensions == {".xml", ".zip", ".json"}
        assert body["max_file_size_bytes"] == converter.MAX_IMPORT_FILE_SIZE
        assert body["import_sources"] == ["ccda", "fhir"]

    def test_formats_requires_auth(self, client: TestClient):
        assert client.get(FORMATS_URL).status_code == 401


class TestPreview:
    def test_preview_counts_for_mychart_ccd(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        patient_id = user_with_patient["patient"].id
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_format"] == "ccda_xml"
        assert body["import_source"] == "ccda"
        assert body["document_count"] == 1
        for key, expected in MYCHART_EXPECTED.items():
            assert body["counts"][key] == {
                "new": expected,
                "update": 0,
                "skipped": 0,
            }, key
        assert body["total_new"] == sum(MYCHART_EXPECTED.values())
        assert body["total_update"] == 0

        # The per-section conversion report from healthrecord-bridge rides along
        sections = {
            s["name"]: s for s in body["conversion_report"]["documents"][0]["sections"]
        }
        assert sections["Allergies"]["entries_seen"] == 2
        assert sections["Medications"]["skipped"]

        unmapped = {(u["resource_type"], u["reason"]) for u in body["unmapped"]}
        assert ("AllergyIntolerance", "negated entry (no known allergy)") in unmapped
        assert ("Immunization", "not administered") in unmapped
        assert any(
            r == "Observation" and "social-history" in reason for r, reason in unmapped
        )

        # Demographics in the file differ from the fixture patient (Test User, 1990)
        assert body["patient"]["full_name"] == "Ms. Jane Quinn Sampleton"
        assert any("differs from the selected patient" in w for w in body["warnings"])

    def test_preview_does_not_persist(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        patient_id = user_with_patient["patient"].id
        client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert all(v == 0 for v in _row_counts(db_session, patient_id).values())

    def test_preview_accepts_mychart_xdm_zip(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        patient_id = user_with_patient["patient"].id
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(
                _xdm_zip(MYCHART_CCD), "MyChart_Export.zip", "application/zip"
            ),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_format"] == "ccda_zip"
        assert body["counts"]["conditions"]["new"] == MYCHART_EXPECTED["conditions"]

    def test_preview_synthea_document(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        patient_id = user_with_patient["patient"].id
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(SYNTHEA_CCD, "synthea.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        counts = response.json()["counts"]
        assert counts["lab_results"]["new"] == 1
        assert counts["lab_test_components"]["new"] == 11
        assert counts["vitals"]["new"] == 1
        assert counts["immunizations"]["new"] == 1


class TestExecute:
    def test_execute_creates_rows_with_provenance(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        patient_id = user_with_patient["patient"].id
        response = client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        for key, expected in MYCHART_EXPECTED.items():
            assert body["counts"][key] == {
                "created": expected,
                "updated": 0,
                "skipped": 0,
            }, key
        assert body["total_created"] == sum(MYCHART_EXPECTED.values())
        assert _row_counts(db_session, patient_id) == MYCHART_EXPECTED

        condition = (
            db_session.query(Condition)
            .filter(Condition.patient_id == patient_id, Condition.icd10_code == "I10")
            .one()
        )
        assert condition.import_source == "ccda"
        assert condition.external_id
        assert condition.diagnosis == "Essential hypertension"
        assert condition.snomed_code == "59621000"
        assert condition.onset_date == date(2018, 2, 14)

        vitals = db_session.query(Vitals).filter(Vitals.patient_id == patient_id).one()
        assert vitals.import_source == "ccda"
        assert vitals.external_id.startswith("vitals:")
        assert (vitals.systolic_bp, vitals.diastolic_bp) == (128, 82)
        assert round(vitals.weight, 1) == 150.4  # 68.2 kg converted to pounds

        lab = (
            db_session.query(LabResult).filter(LabResult.patient_id == patient_id).one()
        )
        assert lab.is_panel is True and lab.status == "completed"
        components = {c.test_code: c for c in lab.test_components}
        assert components["2345-7"].status == "high"
        assert (
            components["2345-7"].external_id
            and components["2345-7"].import_source == "ccda"
        )

        # Provenance is visible read-only on the regular API
        listed = client.get(
            f"/api/v1/conditions/?patient_id={patient_id}",
            headers=authenticated_headers,
        )
        assert listed.status_code == 200
        assert {c["import_source"] for c in listed.json()} == {"ccda"}
        assert all(c["external_id"] for c in listed.json())

        # Exactly one activity-log entry for the import
        logs = (
            db_session.query(ActivityLog)
            .filter(
                ActivityLog.patient_id == patient_id,
                ActivityLog.description.like("Imported health record%"),
            )
            .all()
        )
        assert len(logs) == 1
        assert logs[0].event_metadata["operation"] == "health_record_import"
        assert logs[0].event_metadata["counts"]["conditions"]["created"] == 2

    def test_second_execute_updates_in_place(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        patient_id = user_with_patient["patient"].id
        first = client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert first.status_code == 200, first.text
        before = _row_counts(db_session, patient_id)
        condition = (
            db_session.query(Condition)
            .filter(Condition.patient_id == patient_id)
            .first()
        )
        condition.notes = "user note"
        condition.tags = ["mine"]
        condition.diagnosis = "edited locally"
        db_session.commit()
        condition_id = condition.id

        second = client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["total_created"] == 0
        assert body["total_updated"] == sum(MYCHART_EXPECTED.values())
        assert _row_counts(db_session, patient_id) == before

        db_session.expire_all()
        refreshed = db_session.get(Condition, condition_id)
        assert refreshed.diagnosis != "edited locally"  # source data wins on update
        assert refreshed.tags == ["mine"]  # user-owned tags are preserved

    def test_update_existing_false_skips_existing_rows(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        patient_id = user_with_patient["patient"].id
        client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        condition = (
            db_session.query(Condition)
            .filter(Condition.patient_id == patient_id)
            .first()
        )
        condition.diagnosis = "edited locally"
        db_session.commit()
        condition_id = condition.id

        response = client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id), "update_existing": "false"},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_created"] == 0
        assert body["total_updated"] == 0
        assert body["total_skipped"] == sum(MYCHART_EXPECTED.values())
        db_session.expire_all()
        assert db_session.get(Condition, condition_id).diagnosis == "edited locally"

    def test_fhir_bundle_json_goes_through_the_same_path(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        patient_id = user_with_patient["patient"].id
        bundle = converter.convert_upload(
            MYCHART_CCD.read_bytes(), "mychart_like_ccd.xml"
        ).bundle
        payload = json.dumps(bundle).encode("utf-8")

        preview = client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(payload, "bundle.json", "application/json"),
            headers=authenticated_headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["source_format"] == "fhir_json"
        assert preview.json()["import_source"] == "fhir"
        assert preview.json()["conversion_report"] is None
        assert (
            preview.json()["counts"]["conditions"]["new"]
            == MYCHART_EXPECTED["conditions"]
        )

        result = client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(payload, "bundle.json", "application/json"),
            headers=authenticated_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["total_created"] == sum(MYCHART_EXPECTED.values())
        assert {
            c.import_source
            for c in db_session.query(Condition).filter(
                Condition.patient_id == patient_id
            )
        } == {"fhir"}

        # The same ids from the XML path update rather than duplicate
        again = client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert again.json()["total_created"] == 0
        assert _row_counts(db_session, patient_id) == MYCHART_EXPECTED

    def test_import_never_creates_a_patient(
        self,
        client: TestClient,
        db_session: Session,
        user_with_patient,
        authenticated_headers,
    ):
        from app.models.models import Patient

        patient_id = user_with_patient["patient"].id
        before = db_session.query(Patient).count()
        client.post(
            EXECUTE_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert db_session.query(Patient).count() == before
        patient = db_session.get(Patient, patient_id)
        assert (patient.first_name, patient.last_name) == ("Test", "User")


class TestAccessControl:
    def _viewer_for(
        self, db_session: Session, owner_user, owner_patient_id, permission: str
    ):
        viewer = create_random_user(db_session)
        viewer_patient = patient_crud.create_for_user(
            db_session,
            user_id=viewer["user"].id,
            patient_data=PatientCreate(
                first_name="View",
                last_name="Only",
                birth_date=date(1980, 1, 1),
                gender="F",
            ),
        )
        viewer["user"].active_patient_id = viewer_patient.id
        db_session.add(
            PatientShare(
                patient_id=owner_patient_id,
                shared_by_user_id=owner_user.id,
                shared_with_user_id=viewer["user"].id,
                permission_level=permission,
                is_active=True,
            )
        )
        db_session.commit()
        return create_user_token_headers(viewer["username"])

    def test_view_only_share_gets_403(
        self, client: TestClient, db_session: Session, user_with_patient
    ):
        patient_id = user_with_patient["patient"].id
        headers = self._viewer_for(
            db_session, user_with_patient["user"], patient_id, "view"
        )
        for url in (PREVIEW_URL, EXECUTE_URL):
            response = client.post(
                url,
                data={"patient_id": str(patient_id)},
                files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
                headers=headers,
            )
            assert response.status_code == 403, response.text
        assert all(v == 0 for v in _row_counts(db_session, patient_id).values())

    def test_edit_share_can_import(
        self, client: TestClient, db_session: Session, user_with_patient
    ):
        patient_id = user_with_patient["patient"].id
        headers = self._viewer_for(
            db_session, user_with_patient["user"], patient_id, "edit"
        )
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=headers,
        )
        assert response.status_code == 200, response.text

    def test_unrelated_user_gets_403(
        self, client: TestClient, db_session: Session, user_with_patient
    ):
        patient_id = user_with_patient["patient"].id
        stranger = create_random_user(db_session)
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(patient_id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=create_user_token_headers(stranger["username"]),
        )
        assert response.status_code == 403

    def test_unknown_patient_gets_404(self, client: TestClient, authenticated_headers):
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": "999999"},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 404

    def test_requires_auth(self, client: TestClient, user_with_patient):
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
        )
        assert response.status_code == 401


class TestRejectedUploads:
    def test_malformed_xml_is_rejected(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            files=_upload(b"<not-a-clinical-document/>", "junk.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 400
        assert "conversion failed" in response.json()["message"].lower()

    def test_unsupported_extension_is_rejected(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            files=_upload(b"a,b\n1,2\n", "readings.csv", "text/csv"),
            headers=authenticated_headers,
        )
        assert response.status_code == 422

    def test_json_that_is_not_a_bundle_is_rejected(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            files=_upload(
                json.dumps({"resourceType": "Patient"}).encode(),
                "patient.json",
                "application/json",
            ),
            headers=authenticated_headers,
        )
        assert response.status_code == 400

    def test_zip_without_documents_is_rejected(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.TXT", "nothing here")
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            files=_upload(buf.getvalue(), "empty.zip", "application/zip"),
            headers=authenticated_headers,
        )
        assert response.status_code == 400

    def test_oversized_file_is_rejected(
        self, client: TestClient, user_with_patient, authenticated_headers, monkeypatch
    ):
        monkeypatch.setattr(converter, "MAX_IMPORT_FILE_SIZE", 1024)
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            files=_upload(MYCHART_CCD, "mychart_like_ccd.xml"),
            headers=authenticated_headers,
        )
        assert response.status_code == 422
        assert "exceeds" in response.json()["message"]

    def test_missing_file_is_rejected(
        self, client: TestClient, user_with_patient, authenticated_headers
    ):
        response = client.post(
            PREVIEW_URL,
            data={"patient_id": str(user_with_patient["patient"].id)},
            headers=authenticated_headers,
        )
        assert response.status_code == 422
