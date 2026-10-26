"""
Health record import (C-CDA, MyChart XDM zip, FHIR R4 Bundle).

converter.py turns the upload into a FHIR R4 Bundle, fhir_mapper.py maps the
Bundle onto MediKeep create dicts, and importer.py previews or writes them,
matching existing rows on (patient_id, external_id).
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
