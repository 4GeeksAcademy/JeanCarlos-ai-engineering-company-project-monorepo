"""Seed the incident manager with the legacy CSV of incidents-file-analyzer.

Usage (from the repository root, with DATABASE_URL configured):

    python scripts/seed_incidents.py [path/to/incidents-healthcore.csv]

Every row is validated with the shared analyzer rules, transformed with the
BRIEFING maps (status, category, title, dates, branch) and inserted with
origin "customer". Invalid rows are skipped and reported at the end. The script
is idempotent: rows whose incident_id was already imported are not inserted again.
Patient identifiers are never stored nor printed.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import Engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from packages.shared.incidents import (  # noqa: E402
    CSV_RULES, csv_row_errors, csv_row_to_incident, legacy_ref,
)
from services.api.models.incident import Incident  # noqa: E402

DEFAULT_CSV = ROOT / "data" / "raw" / "incidents-healthcore.csv"


@dataclass
class SeedReport:
    total_rows: int = 0
    inserted: int = 0
    already_present: int = 0
    invalid: list[tuple[int, str, list[str]]] = field(default_factory=list)

    @property
    def valid(self) -> int:
        return self.inserted + self.already_present


def _existing_keys(session: Session) -> tuple[set[str], set[tuple[str, object]]]:
    refs = set(session.exec(select(Incident.legacy_ref).where(Incident.legacy_ref.is_not(None))).all())
    fallback = set(
        session.exec(
            select(Incident.title, Incident.created_at)
            .where(Incident.origin == "customer", Incident.legacy_ref.is_(None))
        ).all()
    )
    return refs, fallback


def seed_incidents(csv_path: Path, engine: Engine) -> SeedReport:
    Incident.__table__.create(engine, checkfirst=True)
    report = SeedReport()
    with csv_path.open(encoding="utf-8", newline="") as handle, Session(engine) as session:
        refs, fallback = _existing_keys(session)
        # Row 1 is the header, so data rows start at line 2.
        for line, row in enumerate(csv.DictReader(handle), start=2):
            report.total_rows += 1
            ref = legacy_ref(row)
            errors = csv_row_errors(row)
            if errors:
                report.invalid.append((line, ref or "no incident_id", errors))
                continue
            values = csv_row_to_incident(row)
            if not values["title"]:
                report.invalid.append((line, ref or "no incident_id", ["empty_description"]))
                continue
            # Idempotency: incident_id; fallback title + created_at when the CSV has no id.
            key = (values["title"], values["created_at"])
            if (ref and ref in refs) or (not ref and key in fallback):
                report.already_present += 1
                continue
            session.add(Incident(**values, legacy_ref=ref))
            if ref:
                refs.add(ref)
            else:
                fallback.add(key)
            report.inserted += 1
        session.commit()
    return report


def print_report(report: SeedReport, csv_path: Path) -> None:
    print(f"Incident seed — {csv_path.name}")
    print(f"  Rows read ............ {report.total_rows}")
    print(f"  Valid ................ {report.valid}")
    print(f"    Inserted ........... {report.inserted}")
    print(f"    Already present .... {report.already_present}")
    print(f"  Invalid (skipped) .... {len(report.invalid)}")
    if report.invalid:
        by_rule = Counter(rule for _, _, rules in report.invalid for rule in rules)
        print("\nInvalid rows (not inserted):")
        for line, ref, rules in report.invalid:
            print(f"  - line {line} ({ref}): {'; '.join(CSV_RULES[rule] for rule in rules)}")
        print("\nInvalid rows by rule:")
        for rule, count in by_rule.most_common():
            print(f"  - {CSV_RULES[rule]}: {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed historical customer incidents from the legacy CSV.")
    parser.add_argument("csv", nargs="?", type=Path, default=DEFAULT_CSV, help="Path to the legacy CSV")
    args = parser.parse_args(argv)
    if not args.csv.is_file():
        print(f"CSV file not found: {args.csv}", file=sys.stderr)
        return 1

    from sqlalchemy.exc import SQLAlchemyError

    from services.api.database import get_inventory_engine

    try:
        report = seed_incidents(args.csv, get_inventory_engine())
    except (RuntimeError, SQLAlchemyError) as exc:
        print(f"Incident seed failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    except (UnicodeDecodeError, csv.Error) as exc:
        print(f"Could not read the CSV file: {type(exc).__name__}", file=sys.stderr)
        return 1
    print_report(report, args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
