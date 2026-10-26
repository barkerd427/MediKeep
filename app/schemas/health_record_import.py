"""Pydantic v2 schemas for the health record import feature (C-CDA / FHIR)."""

from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class HealthRecordImportFormat(BaseModel):
    """One accepted upload format."""

    key: str
    name: str
    extensions: List[str]
    description: str


class HealthRecordImportFormatsResponse(BaseModel):
    """Accepted formats and limits for the import endpoints."""

    formats: List[HealthRecordImportFormat]
    max_file_size_bytes: int
    import_sources: List[str] = Field(
        description="Values written to import_source: 'ccda' for XML/zip, 'fhir' for JSON"
    )


class UnmappedGroup(BaseModel):
    """Resources the mapper could not place, grouped by type and reason."""

    resource_type: str
    reason: str
    count: int
    examples: List[str] = Field(
        default_factory=list, description="Up to three details or ids"
    )


class ImportedPatientSummary(BaseModel):
    """Demographics found in the file, for the user to compare against the patient."""

    full_name: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None


class EntityPreviewCount(BaseModel):
    """Per-entity counts for a preview."""

    new: int = 0
    update: int = 0
    skipped: int = 0


class EntityResultCount(BaseModel):
    """Per-entity counts after an import."""

    created: int = 0
    updated: int = 0
    skipped: int = 0


class HealthRecordImportPreviewResponse(BaseModel):
    """Response from the preview endpoint."""

    source_format: str
    import_source: str
    document_count: int
    counts: Dict[str, EntityPreviewCount]
    total_new: int
    total_update: int
    total_skipped: int
    conversion_report: Optional[dict] = Field(
        None,
        description="healthrecord-bridge ConversionReport.to_dict(); null for FHIR input",
    )
    unmapped: List[UnmappedGroup]
    warnings: List[str]
    patient: Optional[ImportedPatientSummary] = None


class HealthRecordImportResponse(BaseModel):
    """Response from the execute endpoint."""

    source_format: str
    import_source: str
    document_count: int
    counts: Dict[str, EntityResultCount]
    total_created: int
    total_updated: int
    total_skipped: int
    unmapped: List[UnmappedGroup]
    warnings: List[str]
