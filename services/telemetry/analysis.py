"""Technical telemetry report: operational metrics computed from telemetry_events.

Every metric follows the same order:
load (SQL) -> refine (Pandas) -> convert types -> group -> aggregate -> serve.

- SQL applies the date window [start, end) and the event_type filter only.
- Dimensions inside `tags` are extracted and filtered in Pandas.
- Timestamps are converted with pd.to_datetime(..., utc=True) before any grouping.
- No loops: only vectorised Pandas operations.

Functions are pure: they read from the given engine and return JSON-serialisable
lists of dicts, so calling them twice with the same arguments gives the same result.
The window is decided by the caller (the endpoint); functions never apply defaults.
"""

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from services.api.services.telemetry_store import telemetry_events

API_ERROR_CLASSES = ("4xx", "5xx")


# --- load (SQL) ---------------------------------------------------------------

def load_events(
    engine: Engine,
    start: datetime,
    end: datetime,
    event_types: Sequence[str] | None = None,
    *,
    with_tags: bool = True,
) -> pd.DataFrame:
    """Rows in [start, end) UTC, optionally restricted to event_types. Never the whole table."""
    columns = [telemetry_events.c.timestamp, telemetry_events.c.event_type]
    if with_tags:
        columns.append(telemetry_events.c.tags)
    statement = select(*columns).where(
        telemetry_events.c.timestamp >= start,
        telemetry_events.c.timestamp < end,
    )
    if event_types is not None:
        statement = statement.where(telemetry_events.c.event_type.in_(list(event_types)))
    with engine.connect() as connection:
        return pd.read_sql(statement, connection)


# --- refine / convert helpers ---------------------------------------------------

def with_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert timestamps to UTC datetimes (never group on strings) and derive the day."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date
    return df


def extract_tags(df: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    """Add the given `tags` keys as columns and drop rows where any of them is null."""
    tags = df["tags"].map(lambda value: json.loads(value) if isinstance(value, str) else value)
    extracted = pd.json_normalize(tags.tolist()).reindex(columns=list(keys))
    extracted.index = df.index
    return df.drop(columns="tags").join(extracted).dropna(subset=list(keys))


def to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Serve: JSON-friendly list of dicts (ISO dates, plain Python numbers)."""
    if df.empty:
        return []
    df = df.copy()
    if "date" in df:
        df["date"] = df["date"].astype(str)
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


# --- metrics ------------------------------------------------------------------

def events_per_day(engine: Engine, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """How much telemetry does the system produce per day, and how varied is it?"""
    df = load_events(engine, start, end, with_tags=False)
    if df.empty:
        return []
    df = with_dates(df)
    daily = (
        df.groupby("date")
        .agg(events=("event_type", "count"), distinct_event_types=("event_type", "nunique"))
        .reset_index()
    )
    return to_records(daily)


def event_type_distribution(engine: Engine, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Which event types dominate each day (count and share of that day's events)?"""
    df = load_events(engine, start, end, with_tags=False)
    if df.empty:
        return []
    df = with_dates(df)
    counts = df.groupby(["date", "event_type"]).size().rename("events").reset_index()
    counts["share_of_day"] = (
        counts["events"] / counts.groupby("date")["events"].transform("sum")
    ).round(4)
    return to_records(counts.sort_values(["date", "events"], ascending=[True, False]))


def error_rate_by_type(engine: Engine, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Per day and API route: what share of requests failed (4xx client, 5xx server)?

    Source: api_latency_aggregated windows (request_count per route/method/status class).
    4xx includes expected business rejections (e.g. insufficient stock, direct stock
    edit); 5xx are server failures.
    """
    df = load_events(engine, start, end, ["api_latency_aggregated"])
    if df.empty:
        return []
    df = extract_tags(df, ["route_template", "method", "status_class", "request_count"])
    df = with_dates(df)
    df["request_count"] = pd.to_numeric(df["request_count"])
    df["errors_4xx"] = df["request_count"].where(df["status_class"] == "4xx", 0)
    df["errors_5xx"] = df["request_count"].where(df["status_class"] == "5xx", 0)
    grouped = (
        df.groupby(["date", "route_template", "method"])
        .agg(
            requests=("request_count", "sum"),
            errors_4xx=("errors_4xx", "sum"),
            errors_5xx=("errors_5xx", "sum"),
        )
        .reset_index()
    )
    grouped["error_rate"] = ((grouped["errors_4xx"] + grouped["errors_5xx"]) / grouped["requests"]).round(4)
    grouped["server_error_rate"] = (grouped["errors_5xx"] / grouped["requests"]).round(4)
    return to_records(grouped.sort_values(["date", "error_rate"], ascending=[True, False]))


def api_latency_by_route(engine: Engine, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Per day and API route: how fast does it respond (request-weighted p50/p95, worst p95)?

    Latency is measured from the browser through the Next.js proxy to FastAPI.
    """
    df = load_events(engine, start, end, ["api_latency_aggregated"])
    if df.empty:
        return []
    df = extract_tags(df, ["route_template", "method", "request_count", "p50_ms", "p95_ms"])
    df = with_dates(df)
    df[["request_count", "p50_ms", "p95_ms"]] = df[["request_count", "p50_ms", "p95_ms"]].apply(pd.to_numeric)
    df["p50_weighted"] = df["p50_ms"] * df["request_count"]
    df["p95_weighted"] = df["p95_ms"] * df["request_count"]
    grouped = (
        df.groupby(["date", "route_template", "method"])
        .agg(
            requests=("request_count", "sum"),
            p50_weighted=("p50_weighted", "sum"),
            p95_weighted=("p95_weighted", "sum"),
            p95_max_ms=("p95_ms", "max"),
        )
        .reset_index()
    )
    grouped["p50_ms"] = (grouped["p50_weighted"] / grouped["requests"]).round(1)
    grouped["p95_ms"] = (grouped["p95_weighted"] / grouped["requests"]).round(1)
    grouped["p95_max_ms"] = grouped["p95_max_ms"].round(1)
    result = grouped[["date", "route_template", "method", "requests", "p50_ms", "p95_ms", "p95_max_ms"]]
    return to_records(result.sort_values(["date", "p95_ms"], ascending=[True, False]))


def frontend_errors_per_day(engine: Engine, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Which unhandled frontend errors happen each day, on how many screens and releases?"""
    df = load_events(engine, start, end, ["frontend_unhandled_error"])
    if df.empty:
        return []
    df = extract_tags(df, ["route_template", "error_code", "release"])
    df = with_dates(df)
    grouped = (
        df.groupby(["date", "error_code"])
        .agg(
            errors=("error_code", "count"),
            affected_routes=("route_template", "nunique"),
            releases=("release", "nunique"),
        )
        .reset_index()
    )
    return to_records(grouped.sort_values(["date", "errors"], ascending=[True, False]))


def auth_failure_rate(engine: Engine, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Per day: what share of login attempts failed?

    failures / (auth_login_failed + auth_login_succeeded), both loaded in one IN query.
    """
    df = load_events(
        engine, start, end, ["auth_login_failed", "auth_login_succeeded"], with_tags=False
    )
    if df.empty:
        return []
    df = with_dates(df)
    df["is_failure"] = (df["event_type"] == "auth_login_failed").astype(int)
    grouped = (
        df.groupby("date")
        .agg(attempts=("event_type", "count"), failures=("is_failure", "sum"))
        .reset_index()
    )
    grouped["failure_rate"] = (grouped["failures"] / grouped["attempts"]).round(4)
    return to_records(grouped)


METRICS = {
    "events_per_day": events_per_day,
    "event_type_distribution": event_type_distribution,
    "error_rate_by_type": error_rate_by_type,
    "api_latency_by_route": api_latency_by_route,
    "frontend_errors_per_day": frontend_errors_per_day,
    "auth_failure_rate": auth_failure_rate,
}


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_report(engine: Engine, start: datetime, end: datetime) -> dict[str, Any]:
    """Run every metric over the same [start, end) window."""
    return {
        "period": {"from": iso_utc(start), "to": iso_utc(end)},
        "metrics": {name: metric(engine, start, end) for name, metric in METRICS.items()},
    }
