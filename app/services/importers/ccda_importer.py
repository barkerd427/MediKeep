"""
C-CDA importer: a Continuity of Care Document as XML, or the IHE XDM zip that
MyChart produces for "Download my record". Conversion to FHIR is done by the
healthrecord-bridge package, which is an optional install.
"""

from typing import Iterator, List, Tuple

from .base_importer import BaseHealthRecordImporter, ImportFormatError
from .upload import iter_zip_members

MISSING_LIBRARY_MESSAGE = (
    "C-CDA import needs the healthrecord-bridge package. "
    "Install it with: pip install -r requirements-import.txt"
)

# How much of a file to look at when deciding whether it is a C-CDA
SNIFF_BYTES = 4096
MAX_NOTES = 20


def looks_like_ccda(data: bytes) -> bool:
    """True when the start of the file names the CDA namespace and root element."""
    head = data[:SNIFF_BYTES]
    return b"urn:hl7-org:v3" in head and b"ClinicalDocument" in head


class CcdaImporter(BaseHealthRecordImporter):
    """Importer for C-CDA XML documents and MyChart XDM zips."""

    FORMAT_KEY = "ccda"
    FORMAT_NAME = "C-CDA document or MyChart export (XML, zip)"
    EXTENSIONS = ("xml", "zip")
    SOURCE = "ccda"

    def validate_format(self, data: bytes, filename: str) -> bool:
        """Accept a C-CDA .xml, or a .zip holding at least one."""
        name = filename.lower()
        if name.endswith(".xml"):
            return looks_like_ccda(data)
        if name.endswith(".zip"):
            try:
                return any(True for _ in self._documents(data))
            except ImportFormatError:
                return False
        return False

    def to_bundle(self, data: bytes, filename: str) -> Tuple[dict, List[str]]:
        """Convert every document in the upload into one merged Bundle."""
        try:
            from healthrecord_bridge.ccda import CcdaError, convert_bytes, merge_bundles
        except ImportError as exc:
            raise ImportFormatError(MISSING_LIBRARY_MESSAGE) from exc

        if filename.lower().endswith(".zip"):
            documents = list(self._documents(data))
            if not documents:
                raise ImportFormatError("The zip file contains no C-CDA documents")
        else:
            documents = [(filename, data)]

        bundles = []
        notes: List[str] = []
        for name, content in documents:
            try:
                bundle, report = convert_bytes(content, name)
            except CcdaError as exc:
                raise ImportFormatError(f"Not a valid C-CDA document: {exc}") from exc
            bundles.append(bundle)
            notes.extend(
                _report_notes(report, prefix=name if len(documents) > 1 else "")
            )
        return merge_bundles(bundles), notes[:MAX_NOTES]

    @staticmethod
    def _documents(data: bytes) -> Iterator[Tuple[str, bytes]]:
        """Yield the C-CDA members of a zip; XDM metadata and other XML are skipped."""
        for name, content in iter_zip_members(data, (".xml",)):
            if looks_like_ccda(content):
                yield name, content


def _report_notes(report, prefix: str = "") -> List[str]:
    """Turn the library's conversion report into one line per thing not converted."""
    lead = f"{prefix}: " if prefix else ""
    notes = []
    for document in report.documents:
        for unknown in document.unknown_sections:
            title = unknown.title or unknown.code or "untitled"
            notes.append(
                f"{lead}Section '{title}' is not converted yet "
                f"({unknown.entries} entries)"
            )
        for section in document.sections:
            if section.null_flavor:
                notes.append(
                    f"{lead}Section '{section.name}' is marked {section.null_flavor}"
                )
            reasons = {}
            for skipped in section.skipped:
                reasons[skipped.reason] = reasons.get(skipped.reason, 0) + 1
            for reason, count in reasons.items():
                notes.append(f"{lead}{section.name}: {count} skipped ({reason})")
    return notes
