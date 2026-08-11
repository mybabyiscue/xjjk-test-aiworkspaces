"""Record the user's explicit decision for halted requirement review findings."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from workflow_contract import read_json_object, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve requirement implementation review findings.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--decision", choices=("resolved", "ignored"), required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()

    note = args.note.strip()
    if not note:
        raise ValueError("Requirement review resolution requires a non-empty note")
    status_path = Path(args.run_dir).resolve() / "requirement_review_status.json"
    status = read_json_object(status_path, "requirement review status")
    if status.get("status") != "halted":
        raise ValueError("Requirement review is not halted")
    status["status"] = "completed_with_risk"
    status["resolution"] = {
        "decision": args.decision,
        "note": note,
        "decided_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_json(status_path, status)
    print(str(status_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
