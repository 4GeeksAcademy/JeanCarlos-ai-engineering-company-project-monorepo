"""Pure Pandas transformations of the business pipeline (PIPELINE_DESIGN.md §3).

No Prefect, no database: every function takes DataFrames and returns DataFrames or
plain dicts, so it can be tested on its own and called from the flow tasks.
No row loops: only vectorised Pandas operations.

KPI definitions (HealthCore pipeline BRIEFING §2 and §4), one row per clinic and month:

| Column | KPI | Rule |
| --- | --- | --- |
| total_supply_cost | Costo de insumos por clínica | sum(total_cost) of inbound_order_created |
| supply_consumption_count | Volumen de consumo de insumos | count of outbound_order_created |
| critical_stockout_count | Frecuencia de quiebre crítico | count of stock_threshold_triggered |
| expiry_risk_count | Conteo de riesgo de vencimiento | distinct lots (product_id, expiry_date) in supply_expiry_flagged |
| currency | — | USD (US) / GBP (UK) from the clinic registry; never converted |
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

INBOUND = "inbound_order_created"
OUTBOUND = "outbound_order_created"
STOCKOUT = "stock_threshold_triggered"
EXPIRY = "supply_expiry_flagged"
SOURCE_EVENT_TYPES = (INBOUND, OUTBOUND, STOCKOUT, EXPIRY)

TAG_KEYS = ("clinic_id", "country", "product_id", "quantity", "total_cost", "expiry_date", "department")
KPI_COLUMNS = (
    "total_supply_cost",
    "supply_consumption_count",
    "critical_stockout_count",
    "expiry_risk_count",
)
# Columns written to reporting.monthly_clinic_supply_performance (BRIEFING §5), id/computed_at aside.
DESTINATION_COLUMNS = ("clinic_id", "country", "month_start", *KPI_COLUMNS, "currency")
CURRENCY_BY_COUNTRY = {"US": "USD", "UK": "GBP"}
SKIP_REASONS = ("duplicate_event_id", "invalid_tags", "unknown_clinic", "inbound_without_cost", "country_mismatch")
# Guard required by BRIEFING §7: no output column may look like patient data.
_PATIENT_LIKE = re.compile(r"patient|diagnos|mrn|dob|birth|ssn|nhs_number", re.IGNORECASE)


class KpiValidationError(ValueError):
    """The month's KPI rows break an integrity rule; the month must not be loaded."""


# --- clinic registry ------------------------------------------------------------

def load_clinic_registry(path: Path) -> pd.DataFrame:
    """data/raw/clinics.csv: clinic_id (1–12) → location_id, country, currency."""
    registry = pd.read_csv(path, dtype={"location_id": str, "country": str, "currency": str, "region": str})
    registry["clinic_id"] = registry["clinic_id"].astype(int)
    return registry[["clinic_id", "location_id", "country", "currency"]].sort_values("clinic_id").reset_index(drop=True)


# --- normalize --------------------------------------------------------------------

def _parse_tags(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def month_start_of(timestamps: pd.Series) -> pd.Series:
    """First day of the UTC calendar month of each timestamp, as datetime.date."""
    utc = pd.to_datetime(timestamps, utc=True)
    return pd.to_datetime(pd.DataFrame({"year": utc.dt.year, "month": utc.dt.month, "day": 1})).dt.date


def normalize_events(raw: pd.DataFrame, registry: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Deduplicate by id, extract tags, convert timestamps and join the clinic registry.

    Returns the usable events and a count per skip/warning reason (for events_skipped):
    - duplicate_event_id, invalid_tags, unknown_clinic: dropped;
    - inbound_without_cost: kept (counts nothing towards cost, never as 0 cost);
    - country_mismatch: kept, the clinic's registry country wins.
    """
    columns = ["id", "timestamp", "month_start", "event_type", "clinic_id", "location_id", "country",
               "currency", "event_country", "product_id", "quantity", "total_cost", "expiry_date", "department"]
    skipped = dict.fromkeys(SKIP_REASONS, 0)
    if raw.empty:
        return pd.DataFrame(columns=columns), skipped

    events = raw.drop_duplicates(subset="id")
    skipped["duplicate_event_id"] = int(len(raw) - len(events))

    tags = events["tags"].map(_parse_tags)
    extracted = pd.json_normalize(tags.map(lambda value: value or {}).tolist()).reindex(columns=list(TAG_KEYS))
    extracted.index = events.index
    events = events.drop(columns="tags").join(extracted.rename(columns={"country": "event_country"}))
    events["clinic_id"] = pd.to_numeric(events["clinic_id"], errors="coerce")
    invalid = tags.isna() | events["clinic_id"].isna()
    skipped["invalid_tags"] = int(invalid.sum())
    events = events[~invalid]

    events = events.assign(
        clinic_id=events["clinic_id"].astype(int),
        timestamp=pd.to_datetime(events["timestamp"], utc=True),
        total_cost=pd.to_numeric(events["total_cost"], errors="coerce"),
    )
    events["month_start"] = month_start_of(events["timestamp"])

    joined = events.merge(registry, on="clinic_id", how="left", indicator=True)
    unknown = joined["_merge"] == "left_only"
    skipped["unknown_clinic"] = int(unknown.sum())
    joined = joined[~unknown].drop(columns="_merge")

    skipped["inbound_without_cost"] = int(((joined["event_type"] == INBOUND) & joined["total_cost"].isna()).sum())
    skipped["country_mismatch"] = int(
        (joined["event_country"].notna() & (joined["event_country"] != joined["country"])).sum()
    )
    return joined[columns].reset_index(drop=True), skipped


# --- aggregate --------------------------------------------------------------------

class KpiInputError(ValueError):
    """The events frame given to a KPI function is malformed (missing columns, wrong month type)."""


_KPI_INPUT_COLUMNS = ("event_type", "clinic_id", "month_start")


def _month_events(events: pd.DataFrame, event_type: str, month_start: date, extra: tuple[str, ...] = ()) -> pd.DataFrame:
    """Defensive slice shared by the KPI functions: one event type, one month, a usable clinic_id."""
    if not isinstance(events, pd.DataFrame):
        raise KpiInputError(f"events must be a DataFrame, got {type(events).__name__}")
    if not isinstance(month_start, date) or month_start.day != 1:
        raise KpiInputError(f"month_start must be the first day of a month, got {month_start!r}")
    missing = [column for column in (*_KPI_INPUT_COLUMNS, *extra) if column not in events.columns]
    if missing:
        raise KpiInputError(f"events is missing columns: {', '.join(missing)}")
    selected = events[(events["event_type"] == event_type) & (events["month_start"] == month_start)]
    clinic = pd.to_numeric(selected["clinic_id"], errors="coerce")
    return selected.assign(clinic_id=clinic)[clinic.notna()].astype({"clinic_id": int})


def total_supply_cost(events: pd.DataFrame, month_start: date) -> pd.Series:
    """KPI «Costo de insumos por clínica»: sum of inbound_order_created.total_cost per clinic.

    Missing, non-numeric or negative costs add nothing (they are reported as
    inbound_without_cost by normalize_events); they are never read as a 0 cost.
    """
    inbound = _month_events(events, INBOUND, month_start, ("total_cost",))
    cost = pd.to_numeric(inbound["total_cost"], errors="coerce")
    cost = cost.where(cost >= 0)
    return cost.groupby(inbound["clinic_id"]).sum(min_count=1).dropna().round(2).rename("total_supply_cost")


def supply_consumption_count(events: pd.DataFrame, month_start: date) -> pd.Series:
    """KPI «Volumen de consumo de insumos»: number of outbound_order_created events per clinic."""
    outbound = _month_events(events, OUTBOUND, month_start)
    return outbound.groupby("clinic_id").size().rename("supply_consumption_count")


def critical_stockout_count(events: pd.DataFrame, month_start: date) -> pd.Series:
    """KPI «Frecuencia de quiebre crítico»: stock_threshold_triggered events per clinic.

    Each event is already one crossing below the minimum (the API emits it on the falling edge).
    """
    stockouts = _month_events(events, STOCKOUT, month_start)
    return stockouts.groupby("clinic_id").size().rename("critical_stockout_count")


def expiry_risk_count(events: pd.DataFrame, month_start: date) -> pd.Series:
    """KPI «Conteo de riesgo de vencimiento»: distinct lots (product_id, expiry_date) flagged per clinic.

    The flag is deduplicated per browser, so several browsers may flag the same lot:
    counting events would inflate the number of lots at risk.
    """
    flagged = _month_events(events, EXPIRY, month_start, ("product_id", "expiry_date"))
    lots = flagged.dropna(subset=["product_id", "expiry_date"]).drop_duplicates(
        subset=["clinic_id", "product_id", "expiry_date"]
    )
    return lots.groupby("clinic_id").size().rename("expiry_risk_count")


def assemble_monthly_kpis(registry: pd.DataFrame, month_start: date, *kpi_series: pd.Series) -> pd.DataFrame:
    """Rows of reporting.monthly_clinic_supply_performance: one per registry clinic, zeros if no events."""
    kpis = pd.concat(kpi_series, axis=1) if kpi_series else pd.DataFrame()
    kpis = kpis.reindex(index=registry["clinic_id"], columns=list(KPI_COLUMNS)).astype(float).fillna(0)
    rows = registry.set_index("clinic_id").join(kpis).reset_index().assign(month_start=month_start)
    rows["total_supply_cost"] = rows["total_supply_cost"].astype(float).round(2)
    rows[list(KPI_COLUMNS[1:])] = rows[list(KPI_COLUMNS[1:])].astype(int)
    rows = rows.rename(columns={"clinic_id": "clinic_number", "location_id": "clinic_id"})
    return rows[list(DESTINATION_COLUMNS)].reset_index(drop=True)  # registry order (clinic 1 → 12)


def aggregate_monthly_kpis(events: pd.DataFrame, registry: pd.DataFrame, month_start: date) -> pd.DataFrame:
    """The four BRIEFING KPIs for one month, assembled into the destination rows."""
    return assemble_monthly_kpis(
        registry,
        month_start,
        total_supply_cost(events, month_start),
        supply_consumption_count(events, month_start),
        critical_stockout_count(events, month_start),
        expiry_risk_count(events, month_start),
    )


def consumption_by_department(events: pd.DataFrame, month_start: date) -> list[dict[str, Any]]:
    """KPI 2 detail ("por departamento"): outbound events per clinic and department."""
    month = events[(events["month_start"] == month_start) & (events["event_type"] == OUTBOUND)]
    if month.empty:
        return []
    detail = (
        month.assign(department=month["department"].fillna("unknown"))
        .groupby(["location_id", "department"]).size().rename("supply_consumption_count").reset_index()
        .rename(columns={"location_id": "clinic_id"})
    )
    return detail.to_dict(orient="records")


def source_event_counts(events: pd.DataFrame, month_start: date) -> dict[str, int]:
    """Events per event_type in the month; compared between runs to detect late events."""
    counts = events.loc[events["month_start"] == month_start, "event_type"].value_counts()
    return {event_type: int(counts.get(event_type, 0)) for event_type in SOURCE_EVENT_TYPES}


# --- validate ---------------------------------------------------------------------

def validate_kpis(kpis: pd.DataFrame, registry: pd.DataFrame, month_start: date) -> None:
    """Integrity rules of PIPELINE_DESIGN.md §6; raises KpiValidationError on the first broken rule."""
    expected = set(registry["location_id"])
    if len(kpis) != len(expected) or set(kpis["clinic_id"]) != expected or kpis["clinic_id"].duplicated().any():
        raise KpiValidationError(f"{month_start}: expected one row for each of the {len(expected)} clinics")
    if (kpis["month_start"] != month_start).any():
        raise KpiValidationError(f"{month_start}: rows from another month")
    if (kpis[list(KPI_COLUMNS)] < 0).any().any():
        raise KpiValidationError(f"{month_start}: negative KPI values")
    if (kpis["country"].map(CURRENCY_BY_COUNTRY) != kpis["currency"]).any():
        raise KpiValidationError(f"{month_start}: currency does not match the clinic country")
    if any(_PATIENT_LIKE.search(column) for column in kpis.columns):
        raise KpiValidationError(f"{month_start}: a column looks like patient data")


# --- coverage (reconciliation with inventory) ----------------------------------------

def coverage_report(
    events: pd.DataFrame,
    inventory_counts: pd.DataFrame,
    registry: pd.DataFrame,
    month_start: date,
    tolerance: float,
) -> dict[str, Any]:
    """Telemetry events / inventory movements per clinic, for inbound and outbound.

    inventory_counts: columns clinic_id (int), inbound, outbound for the month.
    - ratio ~1: telemetry complete; < 1 - tolerance: events lost; > 1 + tolerance: duplicates/test events.
    - clinics_without_events: no source event at all while the inventory has movements (capture gap).
    """
    month = events[events["month_start"] == month_start]
    telemetry = (
        month[month["event_type"].isin([INBOUND, OUTBOUND])]
        .assign(movement=lambda frame: frame["event_type"].map({INBOUND: "inbound", OUTBOUND: "outbound"}))
        .pivot_table(index="clinic_id", columns="movement", values="id", aggfunc="count")
        .reindex(columns=["inbound", "outbound"])
    )
    any_event = month.groupby("clinic_id").size().rename("source_events")
    inventory = inventory_counts.set_index("clinic_id").reindex(columns=["inbound", "outbound"])
    table = (
        registry.set_index("clinic_id")[["location_id"]]
        .join(telemetry.add_prefix("telemetry_"))
        .join(inventory.add_prefix("inventory_"))
        .join(any_event)
        .fillna(0)
    )
    for movement in ("inbound", "outbound"):  # two fixed columns, not a row loop
        table[f"{movement}_ratio"] = (
            table[f"telemetry_{movement}"] / table[f"inventory_{movement}"].where(table[f"inventory_{movement}"] > 0)
        ).round(3)
    ratios = table[["inbound_ratio", "outbound_ratio"]]
    out_of_range = (ratios.notna() & ((ratios < 1 - tolerance) | (ratios > 1 + tolerance))).any(axis=1)
    # Telemetry without any inventory movement is an infinite ratio (test events or duplicates).
    out_of_range |= ((table["telemetry_inbound"] > 0) & (table["inventory_inbound"] == 0)) | (
        (table["telemetry_outbound"] > 0) & (table["inventory_outbound"] == 0)
    )
    has_inventory = (table["inventory_inbound"] + table["inventory_outbound"]) > 0
    without_events = table[(table["source_events"] == 0) & has_inventory]["location_id"]
    clinics = table.reset_index()[["location_id", "telemetry_inbound", "inventory_inbound", "inbound_ratio",
                                   "telemetry_outbound", "inventory_outbound", "outbound_ratio"]]
    clinics = clinics.rename(columns={"location_id": "clinic_id"})
    count_columns = [column for column in clinics.columns if column.startswith(("telemetry_", "inventory_"))]
    clinics[count_columns] = clinics[count_columns].astype(int)
    clinics = clinics.astype(object).where(clinics.notna(), None)
    return {
        "tolerance": tolerance,
        "clinics": clinics.to_dict(orient="records"),
        "warnings": sorted(table.loc[out_of_range, "location_id"].tolist()),
        "clinics_without_events": sorted(without_events.tolist()),
        "ready_for_board": not bool(out_of_range.any() or len(without_events)),
    }
