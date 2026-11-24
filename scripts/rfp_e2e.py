"""End-to-end run of the RFP flow with simulated approvals (Hito 9 Parte 3) — reproducible, no UI
clicks: Part 1 (intake) → Part 2 (generation + evaluation) → Part 3 (approval per department,
arbitration, final document). Every approval is a programmatic resume of the paused department
thread, the same call the backoffice makes.

    python scripts/rfp_e2e.py tests/fixtures/rfp/healthcore-request-1.pdf     # plan "standard"
    python scripts/rfp_e2e.py tests/fixtures/rfp/healthcore-request-2.pdf --plan capacity --out docs/rfp/evidence

Uses the real model (LiteLLM gateway) and the database of the backend (RFP_DATABASE_URL or
DATABASE_URL). Prints the status after every step and writes <out>/e2e-<plan>-<rfp_id>.json and
the final document as Markdown. The same plans run with a scripted model in
tests/pipelines/test_rfp_e2e.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--plan", choices=["standard", "capacity"], default="standard")
    parser.add_argument("--out", type=Path, default=None, help="folder for the JSON report and the final document")
    args = parser.parse_args(argv)

    from data.pipelines.rfp_intake import simulation
    from data.pipelines.rfp_intake.models import create_tables
    from scripts.rfp_intake import engine

    eng = engine()
    create_tables(eng)
    report = simulation.run(eng, args.pdf, args.plan)
    for row in report["timeline"]:
        sections = " ".join(f"{d}={s['generation']}/{s['approval']}" for d, s in row["sections"].items())
        print(f"[{row['seconds']:>6} s] {row['status']:<22} {row['stage']:<18} {row['step']}  {sections}")
    print(f"\n{report['rfp_id']} ({report['ticket_id']}) → {report['status']} / {report['stage']}; "
          f"{len(report['trace'])} node executions traced")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stem = f"e2e-{args.plan}-{report['rfp_id']}"
        (args.out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["final_document"]:
            (args.out / f"{stem}-final-document.md").write_text(report["final_document"]["markdown"], encoding="utf-8")
        print(f"report: {args.out / (stem + '.json')}")
    return 0 if report["status"] == "terminado" else 2


if __name__ == "__main__":
    sys.exit(main())
