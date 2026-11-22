"""SQLAlchemy Core definitions of the tables the pipeline touches.

Source of the DDL: supabase/migrations/20261122120000_reporting_monthly_clinic_supply_performance.sql.
"""

from sqlalchemy import (
    ARRAY,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

# Read-only source (public schema). Only these four columns are granted to reporting_pipeline.
telemetry_events = Table(
    "telemetry_events",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("event_type", String, nullable=False),
    Column("tags", JSONB, nullable=False),
    schema="public",
)

# Destination table, exactly as in the BRIEFING §5.
monthly_clinic_supply_performance = Table(
    "monthly_clinic_supply_performance",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()),
    Column("clinic_id", Text, nullable=False),
    Column("country", Text, nullable=False),
    Column("month_start", Date, nullable=False),
    Column("total_supply_cost", Numeric, nullable=False, server_default="0"),
    Column("supply_consumption_count", Integer, nullable=False, server_default="0"),
    Column("critical_stockout_count", Integer, nullable=False, server_default="0"),
    Column("expiry_risk_count", Integer, nullable=False, server_default="0"),
    Column("currency", Text, nullable=False),
    Column("computed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema="reporting",
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", UUID(as_uuid=False), primary_key=True),
    Column("pipeline_name", Text, nullable=False),
    Column("flow_run_id", Text),
    Column("trigger", Text, nullable=False),
    Column("triggered_by", Text),
    Column("months", ARRAY(Date), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", Text, nullable=False),
    Column("source_events_read", Integer, nullable=False, server_default="0"),
    Column("events_skipped", JSONB, nullable=False, server_default="{}"),
    Column("rows_upserted", Integer, nullable=False, server_default="0"),
    Column("rows_changed", Integer, nullable=False, server_default="0"),
    Column("warnings", JSONB, nullable=False, server_default="[]"),
    Column("error_stage", Text),
    Column("error_message", Text),
    Column("code_version", Text),
    schema="reporting",
)

pipeline_run_months = Table(
    "pipeline_run_months",
    metadata,
    Column("run_id", UUID(as_uuid=False), primary_key=True),
    Column("month_start", Date, primary_key=True),
    Column("status", Text, nullable=False),
    Column("source_event_counts", JSONB, nullable=False),
    Column("inventory_counts", JSONB),
    Column("clinics_without_events", ARRAY(Text), nullable=False),
    Column("coverage", JSONB),
    Column("rows_changed", Integer, nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema="reporting",
)

revisions = Table(
    "monthly_clinic_supply_performance_revisions",
    metadata,
    Column("revision_id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=False), nullable=False),
    Column("clinic_id", Text, nullable=False),
    Column("month_start", Date, nullable=False),
    Column("reason", Text, nullable=False),
    Column("previous", JSONB, nullable=False),
    Column("current", JSONB, nullable=False),
    Column("revised_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema="reporting",
)
