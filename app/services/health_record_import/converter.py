"""Turn an uploaded health record file into a FHIR R4 Bundle.

Accepts a bare C-CDA XML document, the IHE XDM zip that Epic MyChart produces
for "Download my record" (or any zip holding C-CDA XML), or a FHIR R4 Bundle
JSON file, which passes through untouched. C-CDA input is converted with
healthrecord-bridge, which also returns a per-section ConversionReport.
"""

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple

from healthrecord_bridge.ccda import CcdaError, ConversionReport, convert_bytes, xdm
from healthrecord_bridge.fhir.bundle import merge_bundles

from app.core.logging.config import get_logger

logger = get_logger(__name__, "app")

# Upload ceiling. Vitals CSV imports allow 10 MB; a multi-year C-CDA export with
# every lab result is routinely larger than that, so this is five times higher.
MAX_IMPORT_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Zip members are checked before extraction so a small archive cannot expand
# into something that exhausts memory.
MAX_ZIP_MEMBER_BYTES = 100 * 1024 * 1024  # 100 MB per XML document
MAX_ZIP_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB across all documents

IMPORT_SOURCE_CCDA = "ccda"
IMPORT_SOURCE_FHIR = "fhir"

ACCEPTED_FORMATS: List[Dict[str, object]] = [
    {
        "key": "ccda_xml",
        "name": "C-CDA XML document",
        "extensions": [".xml"],
        "description": "HL7 C-CDA R2.1 Continuity of Care Document as a single XML file.",
    },
    {
        "key": "ccda_zip",
        "name": "MyChart export (IHE XDM zip)",
        "extensions": [".zip"],
        "description": (
            "The zip file produced by Epic MyChart's Download my record, "
            "or any zip containing C-CDA XML documents."
        ),
    },
    {
        "key": "fhir_json",
        "name": "FHIR R4 Bundle (JSON)",
        "extensions": [".json"],
        "description": "A FHIR R4 Bundle of type collection, transaction or searchset.",
    },
]

ACCEPTED_EXTENSIONS = {
    ext for fmt in ACCEPTED_FORMATS for ext in fmt["extensions"]  # type: ignore[union-attr]
}


class HealthRecordConversionError(ValueError):
    """The upload could not be turned into a FHIR Bundle."""


@dataclass
class ConvertedRecord:
    """Result of converting one upload."""

    bundle: dict
    report: Optional[ConversionReport]
    source_format: str
    import_source: str
    document_count: int

    def report_dict(self) -> Optional[dict]:
        return self.report.to_dict() if self.report is not None else None


def detect_format(filename: str, data: bytes) -> str:
    """Return the format key for an upload, from its extension first, then its bytes."""
    suffix = PurePosixPath(filename or "").suffix.lower()
    if suffix == ".zip":
        return "ccda_zip"
    if suffix == ".xml":
        return "ccda_xml"
    if suffix == ".json":
        return "fhir_json"
    if suffix:
        raise HealthRecordConversionError(
            f"Unsupported file type '{suffix}'. Accepted: "
            + ", ".join(sorted(ACCEPTED_EXTENSIONS))
        )
    head = data[:64].lstrip()
    if head.startswith(b"PK"):
        return "ccda_zip"
    if head.startswith(b"<"):
        return "ccda_xml"
    if head.startswith(b"{"):
        return "fhir_json"
    raise HealthRecordConversionError(
        "Could not determine the file type. Accepted: "
        + ", ".join(sorted(ACCEPTED_EXTENSIONS))
    )


def convert_upload(data: bytes, filename: str) -> ConvertedRecord:
    """Convert the upload to a FHIR R4 Bundle.

    Raises HealthRecordConversionError when the file is empty, over the size
    limit, of an unsupported type, or not a parseable health record.
    """
    if not data:
        raise HealthRecordConversionError("The uploaded file is empty")
    if len(data) > MAX_IMPORT_FILE_SIZE:
        raise HealthRecordConversionError(
            f"File exceeds {MAX_IMPORT_FILE_SIZE // (1024 * 1024)} MB limit"
        )

    source_format = detect_format(filename, data)
    if source_format == "fhir_json":
        bundle = _load_fhir_bundle(data)
        return ConvertedRecord(
            bundle=bundle,
            report=None,
            source_format=source_format,
            import_source=IMPORT_SOURCE_FHIR,
            document_count=1,
        )

    try:
        if source_format == "ccda_zip":
            bundle, report = _convert_zip(data, filename)
        else:
            bundle, report = convert_bytes(data, filename or "upload.xml")
    except CcdaError as exc:
        raise HealthRecordConversionError(f"Not a valid C-CDA document: {exc}") from exc
    except (zipfile.BadZipFile, ValueError) as exc:
        raise HealthRecordConversionError(str(exc)) from exc

    logger.info(
        "Converted health record upload",
        extra={
            "source_format": source_format,
            "document_count": len(report.documents),
            "resource_counts": report.resource_counts,
        },
    )
    return ConvertedRecord(
        bundle=bundle,
        report=report,
        source_format=source_format,
        import_source=IMPORT_SOURCE_CCDA,
        document_count=len(report.documents),
    )


def _convert_zip(data: bytes, filename: str) -> Tuple[dict, ConversionReport]:
    bundles = []
    documents = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in xdm.iter_xml_members(zf):
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise HealthRecordConversionError(
                    f"{info.filename} is {info.file_size} bytes, over the "
                    f"{MAX_ZIP_MEMBER_BYTES} byte limit for one document"
                )
            total += info.file_size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise HealthRecordConversionError(
                    "Archive expands past the "
                    f"{MAX_ZIP_TOTAL_BYTES // (1024 * 1024)} MB limit"
                )
            with zf.open(info) as fh:
                member = fh.read(MAX_ZIP_MEMBER_BYTES + 1)
            bundle, report = convert_bytes(member, f"{filename}:{info.filename}")
            bundles.append(bundle)
            documents.extend(report.documents)
    if not bundles:
        raise HealthRecordConversionError("The zip file contains no XML documents")
    return merge_bundles(bundles), ConversionReport(documents)


def _load_fhir_bundle(data: bytes) -> dict:
    try:
        bundle = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthRecordConversionError("The file is not valid JSON") from exc
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise HealthRecordConversionError(
            "The JSON file is not a FHIR Bundle (resourceType must be 'Bundle')"
        )
    entries = bundle.get("entry")
    if entries is None:
        bundle["entry"] = []
    elif not isinstance(entries, list):
        raise HealthRecordConversionError("Bundle.entry must be a list")
    for entry in bundle["entry"]:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict) or "resourceType" not in resource:
            raise HealthRecordConversionError(
                "Every Bundle entry must hold a resource with a resourceType"
            )
    return bundle
