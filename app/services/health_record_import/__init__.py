"""Health record import: C-CDA / MyChart XDM / FHIR R4 Bundle to MediKeep records.

``converter`` turns an upload into a FHIR R4 Bundle (via healthrecord-bridge),
``fhir_mapper`` maps the Bundle's resources onto MediKeep create dicts, and
``importer`` previews or applies them as idempotent upserts keyed on
``(patient_id, external_id)``.
"""

from app.services.health_record_import.converter import (
    ACCEPTED_FORMATS,
    MAX_IMPORT_FILE_SIZE,
    ConvertedRecord,
    HealthRecordConversionError,
    convert_upload,
)
from app.services.health_record_import.importer import HealthRecordImporter

__all__ = [
    "ACCEPTED_FORMATS",
    "MAX_IMPORT_FILE_SIZE",
    "ConvertedRecord",
    "HealthRecordConversionError",
    "HealthRecordImporter",
    "convert_upload",
]
