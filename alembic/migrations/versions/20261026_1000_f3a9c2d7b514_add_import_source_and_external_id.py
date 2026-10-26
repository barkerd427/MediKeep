"""Add import_source and external_id to imported record tables

Revision ID: f3a9c2d7b514
Revises: d7b2f4a8c615
Create Date: 2026-10-26 10:00:00.000000

Adds import_source and external_id to the record tables the health record
importer writes, indexed on import_source and (patient_id, external_id).
lab_test_components is indexed on (lab_result_id, external_id) and vitals,
which already has import_source, only gets external_id.
"""

from alembic import op
import sqlalchemy as sa

revision = "f3a9c2d7b514"
down_revision = "d7b2f4a8c615"
branch_labels = None
depends_on = None

PATIENT_TABLES = [
    "conditions",
    "allergies",
    "medications",
    "immunizations",
    "procedures",
    "encounters",
    "lab_results",
]


def upgrade() -> None:
    for table in PATIENT_TABLES:
        op.add_column(table, sa.Column("import_source", sa.String(), nullable=True))
        op.add_column(table, sa.Column("external_id", sa.String(), nullable=True))
        op.create_index(f"idx_{table}_import_source", table, ["import_source"])
        op.create_index(
            f"idx_{table}_patient_external_id", table, ["patient_id", "external_id"]
        )

    op.add_column(
        "lab_test_components", sa.Column("import_source", sa.String(), nullable=True)
    )
    op.add_column(
        "lab_test_components", sa.Column("external_id", sa.String(), nullable=True)
    )
    op.create_index(
        "idx_lab_test_components_import_source",
        "lab_test_components",
        ["import_source"],
    )
    op.create_index(
        "idx_lab_test_components_lab_result_external_id",
        "lab_test_components",
        ["lab_result_id", "external_id"],
    )

    op.add_column("vitals", sa.Column("external_id", sa.String(), nullable=True))
    op.create_index(
        "idx_vitals_patient_external_id", "vitals", ["patient_id", "external_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_vitals_patient_external_id", table_name="vitals")
    op.drop_column("vitals", "external_id")

    op.drop_index(
        "idx_lab_test_components_lab_result_external_id",
        table_name="lab_test_components",
    )
    op.drop_index(
        "idx_lab_test_components_import_source", table_name="lab_test_components"
    )
    op.drop_column("lab_test_components", "external_id")
    op.drop_column("lab_test_components", "import_source")

    for table in reversed(PATIENT_TABLES):
        op.drop_index(f"idx_{table}_patient_external_id", table_name=table)
        op.drop_index(f"idx_{table}_import_source", table_name=table)
        op.drop_column(table, "external_id")
        op.drop_column(table, "import_source")
