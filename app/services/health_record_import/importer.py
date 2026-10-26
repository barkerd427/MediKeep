"""Preview and apply a converted health record as idempotent upserts.

Rows are matched on ``(patient_id, external_id)`` (lab test components on
``(lab_result_id, external_id)``). A preview reports what an import would do;
``execute`` applies it in one transaction and writes one activity-log entry.
The import never creates a Patient: demographic differences between the file
and the selected MediKeep patient are reported as warnings only.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

from fastapi import Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.logging.config import get_logger
from app.core.utils.client_ip import get_client_ip
from app.core.utils.datetime_utils import to_utc
from app.crud.activity_log import activity_log
from app.crud.allergy import allergy as allergy_crud
from app.crud.condition import condition as condition_crud
from app.crud.encounter import encounter as encounter_crud
from app.crud.immunization import immunization as immunization_crud
from app.crud.lab_result import lab_result as lab_result_crud
from app.crud.lab_test_component import lab_test_component as lab_test_component_crud
from app.crud.medication import medication as medication_crud
from app.crud.procedure import procedure as procedure_crud
from app.crud.vitals import vitals as vitals_crud
from app.models.activity_log import ActionType, EntityType
from app.models.patient import Patient
from app.schemas.allergy import AllergyCreate
from app.schemas.condition import ConditionCreate
from app.schemas.encounter import EncounterCreate
from app.schemas.health_record_import import (
    EntityPreviewCount,
    EntityResultCount,
    HealthRecordImportPreviewResponse,
    HealthRecordImportResponse,
    ImportedPatientSummary,
    UnmappedGroup,
)
from app.schemas.immunization import ImmunizationCreate
from app.schemas.lab_result import LabResultCreate
from app.schemas.lab_test_component import LabTestComponentCreate
from app.schemas.medication import MedicationCreate
from app.schemas.procedure import ProcedureCreate
from app.schemas.vitals import VitalsCreate
from app.services.canonical_test_matching import canonical_test_matching
from app.services.health_record_import.converter import ConvertedRecord
from app.services.health_record_import.fhir_mapper import (
    MappedRecords,
    UnmappedItem,
    map_bundle,
)

logger = get_logger(__name__, "app")

PROVENANCE_FIELDS = ("external_id", "import_source")
# Fields the import never overwrites on an existing row: user-owned data.
PRESERVED_ON_UPDATE = {
    "tags",
    "practitioner_id",
    "pharmacy_id",
    "condition_id",
    "medication_id",
}


@dataclass
class EntitySpec:
    """How one MediKeep entity is validated and persisted."""

    key: str
    crud: Any
    schema: Type[BaseModel]
    exclude_on_dump: frozenset = frozenset()


ENTITY_SPECS: Dict[str, EntitySpec] = {
    "conditions": EntitySpec("conditions", condition_crud, ConditionCreate),
    "allergies": EntitySpec("allergies", allergy_crud, AllergyCreate),
    "medications": EntitySpec("medications", medication_crud, MedicationCreate),
    "immunizations": EntitySpec(
        "immunizations",
        immunization_crud,
        ImmunizationCreate,
        frozenset({"standardized_vaccine_who_code"}),
    ),
    "procedures": EntitySpec("procedures", procedure_crud, ProcedureCreate),
    "encounters": EntitySpec("encounters", encounter_crud, EncounterCreate),
    "vitals": EntitySpec("vitals", vitals_crud, VitalsCreate),
    "lab_results": EntitySpec("lab_results", lab_result_crud, LabResultCreate),
}
COMPONENT_SPEC = EntitySpec(
    "lab_test_components", lab_test_component_crud, LabTestComponentCreate
)
ENTITY_KEYS = [*ENTITY_SPECS.keys(), COMPONENT_SPEC.key]


@dataclass
class _Plan:
    """A validated import plan: per entity, the rows to create and to update."""

    mapped: MappedRecords
    create: Dict[str, List[dict]] = field(default_factory=dict)
    update: Dict[str, List[Tuple[Any, dict]]] = field(default_factory=dict)
    skipped: Counter = field(default_factory=Counter)
    # For lab results, components ride along inside the parent dict under "components".


def _canonical_lookup(test_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Match a component name the way the test-library sync does."""
    canonical = canonical_test_matching.find_canonical_match(test_name)
    if not canonical:
        return None, None
    info = canonical_test_matching.get_test_info(canonical) or {}
    category = info.get("category")
    return canonical, (category.lower() if isinstance(category, str) else None)


def _group_unmapped(items: List[UnmappedItem]) -> List[UnmappedGroup]:
    groups: Dict[Tuple[str, str], UnmappedGroup] = {}
    for item in items:
        key = (item.resource_type, item.reason)
        group = groups.get(key)
        if group is None:
            group = UnmappedGroup(
                resource_type=item.resource_type, reason=item.reason, count=0
            )
            groups[key] = group
        group.count += 1
        example = item.detail or item.resource_id
        if example and len(group.examples) < 3 and example not in group.examples:
            group.examples.append(example)
    return sorted(groups.values(), key=lambda g: (-g.count, g.resource_type, g.reason))


class HealthRecordImporter:
    """Preview or execute an import of a converted health record for one patient."""

    def __init__(self, db: Session):
        self.db = db

    # --- public API ---------------------------------------------------------

    def preview(
        self, converted: ConvertedRecord, *, patient: Patient
    ) -> HealthRecordImportPreviewResponse:
        plan = self._build_plan(converted, patient, update_existing=True)
        counts = {}
        for key in ENTITY_KEYS:
            counts[key] = EntityPreviewCount(
                new=len(plan.create.get(key, [])),
                update=len(plan.update.get(key, [])),
                skipped=plan.skipped.get(key, 0),
            )
        return HealthRecordImportPreviewResponse(
            source_format=converted.source_format,
            import_source=converted.import_source,
            document_count=converted.document_count,
            counts=counts,
            total_new=sum(c.new for c in counts.values()),
            total_update=sum(c.update for c in counts.values()),
            total_skipped=sum(c.skipped for c in counts.values()),
            conversion_report=converted.report_dict(),
            unmapped=_group_unmapped(plan.mapped.unmapped),
            warnings=plan.mapped.warnings,
            patient=self._patient_summary(plan.mapped),
        )

    def execute(
        self,
        converted: ConvertedRecord,
        *,
        patient: Patient,
        user_id: int,
        update_existing: bool = True,
        request: Optional[Request] = None,
    ) -> HealthRecordImportResponse:
        plan = self._build_plan(converted, patient, update_existing=update_existing)
        created: Counter = Counter()
        updated: Counter = Counter()
        try:
            for key, spec in ENTITY_SPECS.items():
                if key == "lab_results":
                    continue
                for data in plan.create.get(key, []):
                    self.db.add(spec.crud.model(**data))
                    created[key] += 1
                for db_obj, data in plan.update.get(key, []):
                    self._apply_update(db_obj, data)
                    updated[key] += 1
            self._persist_lab_results(plan, created, updated)
            self.db.flush()
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.error(
                "Health record import failed, transaction rolled back",
                extra={"patient_id": patient.id, "user_id": user_id},
            )
            raise

        counts = {
            key: EntityResultCount(
                created=created.get(key, 0),
                updated=updated.get(key, 0),
                skipped=plan.skipped.get(key, 0),
            )
            for key in ENTITY_KEYS
        }
        response = HealthRecordImportResponse(
            source_format=converted.source_format,
            import_source=converted.import_source,
            document_count=converted.document_count,
            counts=counts,
            total_created=sum(created.values()),
            total_updated=sum(updated.values()),
            total_skipped=sum(plan.skipped.values()),
            unmapped=_group_unmapped(plan.mapped.unmapped),
            warnings=plan.mapped.warnings,
        )
        self._log_activity(response, patient, user_id, request)
        return response

    # --- planning -----------------------------------------------------------

    def _build_plan(
        self, converted: ConvertedRecord, patient: Patient, *, update_existing: bool
    ) -> _Plan:
        mapped = map_bundle(
            converted.bundle,
            patient_id=patient.id,
            import_source=converted.import_source,
            canonical_lookup=_canonical_lookup,
        )
        self._compare_demographics(mapped, patient)
        plan = _Plan(mapped=mapped)

        for key, spec in ENTITY_SPECS.items():
            rows = mapped.entity_lists()[key]
            existing = self._existing_by_external_id(spec, patient.id, rows)
            for row in rows:
                components = row.pop("components", None)
                data = self._validate(spec, row, mapped, key)
                if data is None:
                    if components:
                        plan.skipped[COMPONENT_SPEC.key] += len(components)
                    continue
                if components is not None:
                    data["components"] = components
                db_obj = existing.get(data["external_id"])
                if db_obj is None:
                    plan.create.setdefault(key, []).append(data)
                elif update_existing:
                    plan.update.setdefault(key, []).append((db_obj, data))
                else:
                    plan.skipped[key] += 1
                    if components:
                        plan.skipped[COMPONENT_SPEC.key] += len(components)
        self._plan_components(plan, update_existing)
        return plan

    def _plan_components(self, plan: _Plan, update_existing: bool) -> None:
        """Validate components and count them; the parent decides create vs update."""
        for data in plan.create.get("lab_results", []):
            valid = []
            for component in data.get("components") or []:
                checked = self._validate_component(component, plan.mapped)
                if checked is None:
                    plan.skipped[COMPONENT_SPEC.key] += 1
                else:
                    valid.append(checked)
            data["components"] = valid
            plan.create.setdefault(COMPONENT_SPEC.key, []).extend(valid)
        for db_obj, data in plan.update.get("lab_results", []):
            existing = {
                c.external_id: c for c in db_obj.test_components if c.external_id
            }
            valid = []
            for component in data.get("components") or []:
                checked = self._validate_component(component, plan.mapped)
                if checked is None:
                    plan.skipped[COMPONENT_SPEC.key] += 1
                    continue
                current = existing.get(checked["external_id"])
                if current is None:
                    plan.create.setdefault(COMPONENT_SPEC.key, []).append(checked)
                elif update_existing:
                    plan.update.setdefault(COMPONENT_SPEC.key, []).append(
                        (current, checked)
                    )
                else:
                    plan.skipped[COMPONENT_SPEC.key] += 1
                    continue
                valid.append(checked)
            data["components"] = valid

    def _validate(
        self, spec: EntitySpec, row: dict, mapped: MappedRecords, key: str
    ) -> Optional[dict]:
        """Run the row through the entity's Create schema; report failures as unmapped."""
        provenance = {f: row.get(f) for f in PROVENANCE_FIELDS}
        try:
            model = spec.schema(
                **{k: v for k, v in row.items() if k not in PROVENANCE_FIELDS}
            )
        except ValidationError as exc:
            reasons = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'row'}: {err['msg']}"
                for err in exc.errors()[:3]
            )
            mapped.unmapped.append(
                UnmappedItem(
                    _resource_label(key),
                    "failed MediKeep validation",
                    provenance["external_id"],
                    reasons,
                )
            )
            return None
        data = model.model_dump(exclude=set(spec.exclude_on_dump))
        data.update(provenance)
        if key == "vitals" and data.get("recorded_date") is not None:
            data["recorded_date"] = to_utc(data["recorded_date"])
        return data

    def _validate_component(
        self, component: dict, mapped: MappedRecords
    ) -> Optional[dict]:
        # lab_result_id is assigned at persist time; the schema wants a positive int.
        row = {**component, "lab_result_id": 1}
        data = self._validate(COMPONENT_SPEC, row, mapped, COMPONENT_SPEC.key)
        if data is not None:
            data.pop("lab_result_id", None)
        return data

    def _existing_by_external_id(
        self, spec: EntitySpec, patient_id: int, rows: List[dict]
    ) -> Dict[str, Any]:
        ids = [r["external_id"] for r in rows if r.get("external_id")]
        if not ids:
            return {}
        model = spec.crud.model
        found: Dict[str, Any] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            for obj in (
                self.db.query(model)
                .filter(model.patient_id == patient_id, model.external_id.in_(chunk))
                .all()
            ):
                found[obj.external_id] = obj
        return found

    # --- persistence --------------------------------------------------------

    def _persist_lab_results(
        self, plan: _Plan, created: Counter, updated: Counter
    ) -> None:
        component_model = COMPONENT_SPEC.crud.model
        for data in plan.create.get("lab_results", []):
            components = data.pop("components", [])
            lab = lab_result_crud.model(**data)
            self.db.add(lab)
            self.db.flush()
            for component in components:
                self.db.add(component_model(**component, lab_result_id=lab.id))
                created[COMPONENT_SPEC.key] += 1
            created["lab_results"] += 1
        for db_obj, data in plan.update.get("lab_results", []):
            components = data.pop("components", [])
            self._apply_update(db_obj, data)
            updated["lab_results"] += 1
            existing = {
                c.external_id: c for c in db_obj.test_components if c.external_id
            }
            for component in components:
                current = existing.get(component["external_id"])
                if current is None:
                    self.db.add(component_model(**component, lab_result_id=db_obj.id))
                    created[COMPONENT_SPEC.key] += 1
                else:
                    self._apply_update(current, component)
                    updated[COMPONENT_SPEC.key] += 1

    @staticmethod
    def _apply_update(db_obj: Any, data: dict) -> None:
        for field_name, value in data.items():
            if field_name in PRESERVED_ON_UPDATE or field_name == "components":
                continue
            if hasattr(db_obj, field_name):
                setattr(db_obj, field_name, value)

    # --- reporting ----------------------------------------------------------

    @staticmethod
    def _compare_demographics(mapped: MappedRecords, patient: Patient) -> None:
        info = mapped.patient
        if not info:
            return
        differences = []
        file_first = (info.get("first_name") or "").strip().lower()
        file_last = (info.get("last_name") or "").strip().lower()
        if file_first and file_first != (patient.first_name or "").strip().lower():
            differences.append("first name")
        if file_last and file_last != (patient.last_name or "").strip().lower():
            differences.append("last name")
        if (
            info.get("birth_date")
            and patient.birth_date
            and info["birth_date"] != patient.birth_date
        ):
            differences.append("birth date")
        if differences:
            mapped.warnings.insert(
                0,
                "The patient in the file differs from the selected patient in: "
                + ", ".join(differences)
                + ". Records are imported for the selected patient; "
                "the patient's details are not changed.",
            )

    @staticmethod
    def _patient_summary(mapped: MappedRecords) -> Optional[ImportedPatientSummary]:
        if not mapped.patient:
            return None
        return ImportedPatientSummary(
            full_name=mapped.patient.get("full_name"),
            birth_date=mapped.patient.get("birth_date"),
            gender=mapped.patient.get("gender"),
        )

    def _log_activity(
        self,
        response: HealthRecordImportResponse,
        patient: Patient,
        user_id: int,
        request: Optional[Request],
    ) -> None:
        summary = ", ".join(
            f"{key} {c.created}+{c.updated}"
            for key, c in response.counts.items()
            if c.created or c.updated
        )
        try:
            activity_log.log_activity(
                self.db,
                action=ActionType.CREATED,
                entity_type=EntityType.PATIENT,
                entity_id=patient.id,
                patient_id=patient.id,
                user_id=user_id,
                description=(
                    f"Imported health record ({response.import_source}): "
                    f"{response.total_created} created, {response.total_updated} updated"
                    + (f" [{summary}]" if summary else "")
                ),
                metadata={
                    "operation": "health_record_import",
                    "source_format": response.source_format,
                    "import_source": response.import_source,
                    "document_count": response.document_count,
                    "counts": {k: v.model_dump() for k, v in response.counts.items()},
                },
                ip_address=get_client_ip(request) if request is not None else None,
                user_agent=(
                    request.headers.get("user-agent") if request is not None else None
                ),
            )
        except Exception as exc:  # logging must never fail the import
            logger.warning(
                "Activity logging failed for health record import",
                extra={"patient_id": patient.id, "user_id": user_id, "error": str(exc)},
            )
            try:
                self.db.rollback()
            except Exception:
                pass


def _resource_label(entity_key: str) -> str:
    return {
        "conditions": "Condition",
        "allergies": "AllergyIntolerance",
        "medications": "Medication",
        "immunizations": "Immunization",
        "procedures": "Procedure",
        "encounters": "Encounter",
        "vitals": "Observation (vital signs)",
        "lab_results": "DiagnosticReport",
        "lab_test_components": "Observation (laboratory)",
    }.get(entity_key, entity_key)


__all__ = ["ENTITY_KEYS", "HealthRecordImporter"]
