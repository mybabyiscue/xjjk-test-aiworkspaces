"""Synchronize structured interface evidence with the approved core interface table."""

from __future__ import annotations

import argparse
import re
from copy import deepcopy
from pathlib import Path

from workflow_contract import read_json_object, write_json


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Synchronize raw interface evidence from reviewed core interface rows."
    )
    parser.add_argument("--run-dir", required=True)
    return parser.parse_args()


def expand_case_expression(value: str) -> list[str]:
    result: list[str] = []
    for segment in re.split(r"[,，]", value):
        case_ids: list[str] = re.findall(r"TC\d+|TC-[A-Za-z0-9]+", segment, flags=re.IGNORECASE)
        if len(case_ids) == 2 and re.search(r"[-–—~至]", segment):
            start_match: re.Match[str] | None = re.fullmatch(r"TC(\d+)", case_ids[0], flags=re.IGNORECASE)
            end_match: re.Match[str] | None = re.fullmatch(r"TC(\d+)", case_ids[1], flags=re.IGNORECASE)
            if start_match and end_match:
                start_number: int = int(start_match.group(1))
                end_number: int = int(end_match.group(1))
                width: int = max(len(start_match.group(1)), len(end_match.group(1)))
                if start_number <= end_number:
                    result.extend(f"TC{number:0{width}d}" for number in range(start_number, end_number + 1))
                    continue
        result.extend(case_id.upper() for case_id in case_ids)
    return list(dict.fromkeys(result))


def core_rows(path: Path) -> list[tuple[list[str], str, str]]:
    rows: list[tuple[list[str], str, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells: list[str] = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not re.fullmatch(r"(?:GET|POST|PUT|DELETE|PATCH) /\S+", cells[2]):
            continue
        rows.append((expand_case_expression(cells[0]), cells[1], cells[2]))
    return rows


def synchronize(run_dir: Path) -> dict[str, object]:
    code_index: dict[str, object] = read_json_object(run_dir / "raw" / "code_entry_index.json", "code entry index")
    raw_entries: object = code_index.get("entries")
    if not isinstance(raw_entries, list):
        raise TypeError("code_entry_index.json.entries must be a list")
    entries: list[dict[str, object]] = [entry for entry in raw_entries if isinstance(entry, dict)]
    synchronized: dict[str, dict[str, object]] = {}
    missing: list[str] = []
    for case_ids, operation, signature in core_rows(run_dir / "core_process_interfaces.md"):
        method, route = signature.split(" ", 1)
        candidates: list[dict[str, object]] = [
            entry
            for entry in entries
            if entry.get("http_method") == method and entry.get("route") == route
        ]
        operation_anchor: str = operation.split(" ", 1)[0].strip()
        anchored: list[dict[str, object]] = [
            entry
            for entry in candidates
            if operation_anchor == f"{entry.get('class_name')}#{entry.get('method_name')}"
        ]
        selected_candidates: list[dict[str, object]] = anchored or candidates
        if len(selected_candidates) != 1:
            missing.append(f"{signature} ({operation_anchor}) candidates={len(selected_candidates)}")
            continue
        key: str = signature
        if key not in synchronized:
            synchronized[key] = deepcopy(selected_candidates[0])
            synchronized[key]["case_ids"] = []
        existing_case_ids: object = synchronized[key].get("case_ids")
        if not isinstance(existing_case_ids, list):
            raise TypeError(f"Synchronized case_ids must be a list: {signature}")
        synchronized[key]["case_ids"] = list(dict.fromkeys([*existing_case_ids, *case_ids]))
    if missing:
        raise ValueError("Core interface rows are missing exact code evidence: " + "; ".join(missing))
    payload: dict[str, object] = {"interfaces": list(synchronized.values())}
    write_json(run_dir / "raw" / "testcase_interface_evidence.json", payload)
    return payload


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    payload: dict[str, object] = synchronize(Path(arguments.run_dir).resolve())
    print(f"Synchronized {len(payload['interfaces'])} reviewed core interfaces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
