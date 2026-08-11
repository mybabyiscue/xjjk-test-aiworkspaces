"""Scan the skill package for retired business residue and release blockers."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from preparation_contract import PreparationError, write_json_object


def retired_tokens() -> dict[str, list[str]]:
    return {
        "retired_domain": ["api", "test", "njxjjt", "com"],
        "retired_table": [
            "_".join(("live", "config")),
            "_".join(("fei", "shu", "account", "management")),
            "_".join(("project", "course", "subject")),
            "_".join(("questions", "info")),
            "_".join(("redpack", "activity", "info")),
            "_".join(("live", "reward", "config")),
            "_".join(("lottery", "activity")),
        ],
        "retired_route": [
            "/" + "/".join(("live", "goods", "save")),
            "/" + "/".join(("feiShu", "account", "status", "update")),
            "/" + "/".join(("live", "config", "delete")),
            "/" + "/".join(("live", "reward", "receive")),
            "/" + "/".join(("live", "reward", "rewardsuccess")),
            "/" + "/".join(("app", "courseRelation")),
            "/" + "/".join(("question", "submitAnswer")),
            "/" + "/".join(("open-apis", "tenant", "v2", "tenant", "query")),
        ],
    }


def forbidden_patterns() -> tuple[tuple[str, str], ...]:
    tokens: dict[str, list[str]] = retired_tokens()
    domain: str = r"\.".join(tokens["retired_domain"])
    tables: str = "|".join(re.escape(item) for item in tokens["retired_table"])
    routes: str = "|".join(re.escape(item) for item in tokens["retired_route"])
    cache_directory: str = "__" + "pycache" + "__"
    bytecode_suffix: str = r"\." + "pyc" + "$"
    return (
        ("retired_tenant_assignment", r"\btenant_id\s*=\s*153\b|\btenantId\s*:\s*153\b"),
        ("retired_domain", domain),
        ("retired_table", rf"\b({tables})\b"),
        ("retired_route", rf"({routes})"),
        ("fixed_row_fallback", r"\bif\s+row\s+else\s+['\"]?1['\"]?\b"),
        ("developer_absolute_path", r"\b[A-Za-z]:\\(?:Users|xjcode)\\"),
        ("compiled_cache", rf"({cache_directory}|{bytecode_suffix})"),
    )


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Scan one skill package for forbidden hardcoding.")
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def readable_files(skill_dir: Path) -> list[Path]:
    result: list[Path] = []
    for path in skill_dir.rglob("*"):
        if path.is_dir():
            continue
        result.append(path)
    return result


def scan_file(path: Path, skill_dir: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    bytecode_suffix: str = "." + "pyc"
    cache_directory: str = "__" + "pycache" + "__"
    if path.suffix == bytecode_suffix or cache_directory in path.parts:
        findings.append({"file": str(path.relative_to(skill_dir)), "line": 0, "pattern": "compiled_cache"})
        return findings
    try:
        text: str = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return findings
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern_name, pattern in forbidden_patterns():
            if re.search(pattern, line):
                findings.append({"file": str(path.relative_to(skill_dir)), "line": line_number, "pattern": pattern_name})
    return findings


def scan_skill(skill_dir: Path) -> list[dict[str, object]]:
    if not skill_dir.is_dir():
        raise PreparationError(f"skill-dir 不存在：{skill_dir}")
    findings: list[dict[str, object]] = []
    for path in readable_files(skill_dir):
        findings.extend(scan_file(path, skill_dir))
    return findings


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    skill_dir: Path = Path(arguments.skill_dir).resolve()
    findings: list[dict[str, object]] = scan_skill(skill_dir)
    report: dict[str, object] = {"valid": not findings, "finding_count": len(findings), "findings": findings}
    write_json_object(Path(arguments.report), report)
    if findings:
        for finding in findings:
            print(f"{finding['file']}:{finding['line']} {finding['pattern']}")
        return 1
    print("No retired business hardcoding found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
