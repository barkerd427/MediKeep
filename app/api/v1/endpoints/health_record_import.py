"""
Health record import endpoints (C-CDA, MyChart zip, FHIR Bundle).
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.api import deps
from app.api.deps import BusinessLogicException, ValidationException
from app.core.http.error_handling import handle_database_errors
from app.core.logging.config import get_logger
from app.core.logging.helpers import log_data_access, log_endpoint_error
from app.models.models import Patient, User
from app.schemas.health_record_import import (
    HealthRecordImportFormatsResponse,
    HealthRecordImportPreviewResponse,
    HealthRecordImportResponse,
)
from app.services.health_record_import import converter
from app.services.health_record_import.importer import HealthRecordImporter

router = APIRouter()

# Initialize logger
logger = get_logger(__name__, "app")


def verify_import_patient(
    patient_id: int = Form(...),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> int:
    """Resolve patient_id from the form and require edit access to that patient."""
    return deps.verify_patient_access(
        patient_id, db, current_user, required_permission="edit"
    )


def _read_upload(file: UploadFile, request: Request) -> tuple[bytes, str]:
    """Check the filename, extension and size of the upload and return its bytes."""
    if not file or not file.filename:
        raise ValidationException(message="No file provided", request=request)

    # Validate file extension
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if f".{ext}" not in converter.ACCEPTED_EXTENSIONS:
        raise ValidationException(
            message=(
                "Unsupported file type. Accepted: "
                + ", ".join(sorted(converter.ACCEPTED_EXTENSIONS))
            ),
            request=request,
        )

    # Read one byte past the limit to detect oversized files
    raw = file.file.read(converter.MAX_IMPORT_FILE_SIZE + 1)
    if len(raw) > converter.MAX_IMPORT_FILE_SIZE:
        raise ValidationException(
            message=f"File exceeds {converter.MAX_IMPORT_FILE_SIZE // (1024 * 1024)} MB limit",
            request=request,
        )
    if not raw:
        raise ValidationException(message="The uploaded file is empty", request=request)
    return raw, file.filename


def _convert(raw: bytes, filename: str, request: Request, current_user_id: int):
    """Convert the upload, turning conversion errors into a 400 response."""
    try:
        return converter.convert_upload(raw, filename)
    except converter.HealthRecordConversionError as exc:
        log_endpoint_error(
            logger,
            request,
            "health_record_import_conversion_failed",
            exc,
            user_id=current_user_id,
        )
        raise BusinessLogicException(
            message=f"Health record conversion failed: {exc}", request=request
        ) from exc


@router.get("/import/formats", response_model=HealthRecordImportFormatsResponse)
def get_import_formats(
    *,
    current_user_id: int = Depends(deps.get_current_user_id),
) -> Any:
    """Return the accepted upload formats and size limit."""
    return HealthRecordImportFormatsResponse(
        formats=converter.ACCEPTED_FORMATS,
        max_file_size_bytes=converter.MAX_IMPORT_FILE_SIZE,
        import_sources=[converter.IMPORT_SOURCE_CCDA, converter.IMPORT_SOURCE_FHIR],
    )


@router.post("/import/preview", response_model=HealthRecordImportPreviewResponse)
def preview_import(
    *,
    request: Request,
    db: Session = Depends(deps.get_db),
    patient_id: int = Depends(verify_import_patient),
    file: UploadFile = File(...),
    current_user_id: int = Depends(deps.get_current_user_id),
) -> Any:
    """Convert the upload and report what an import would create or update."""
    raw, filename = _read_upload(file, request)
    converted = _convert(raw, filename, request, current_user_id)

    with handle_database_errors(request=request):
        # Patient existence and access were checked by verify_import_patient
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        preview = HealthRecordImporter(db).preview(converted, patient=patient)

        log_data_access(
            logger,
            request,
            current_user_id,
            "read",
            "HealthRecordImport",
            patient_id=patient_id,
            operation_type="import_preview",
            source_format=converted.source_format,
            count=preview.total_new + preview.total_update,
        )
        return preview


@router.post("/import/execute", response_model=HealthRecordImportResponse)
def execute_import(
    *,
    request: Request,
    db: Session = Depends(deps.get_db),
    patient_id: int = Depends(verify_import_patient),
    update_existing: bool = Form(True),
    file: UploadFile = File(...),
    current_user_id: int = Depends(deps.get_current_user_id),
) -> Any:
    """Import the upload into the patient's records, updating rows imported earlier."""
    raw, filename = _read_upload(file, request)
    converted = _convert(raw, filename, request, current_user_id)

    with handle_database_errors(request=request):
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        result = HealthRecordImporter(db).execute(
            converted,
            patient=patient,
            user_id=current_user_id,
            update_existing=update_existing,
            request=request,
        )

        log_data_access(
            logger,
            request,
            current_user_id,
            "create",
            "HealthRecordImport",
            patient_id=patient_id,
            operation_type="import_execute",
            source_format=converted.source_format,
            count=result.total_created + result.total_updated,
        )
        return result
