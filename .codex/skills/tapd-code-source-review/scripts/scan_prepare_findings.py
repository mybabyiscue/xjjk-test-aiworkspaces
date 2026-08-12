"""Scan fetched code sources for initial review findings."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import hashlib
from pathlib import Path

from review_policy import read_policy, require_section


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan code sources for initial prepare findings.")
    parser.add_argument("--manifest", required=True, help="Path to source_manifest.json.")
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "assets" / "review-policy.json"))
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_dir = manifest_path.parent
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    policy = read_policy(Path(args.policy))
    prepare_policy = require_section(policy, "prepare_scan")
    source_policy = require_section(policy, "source_scan")
    rules = load_rules(prepare_policy)
    excluded_dirs = set(str(value) for value in source_policy.get("excluded_directories", []))
    extensions = set(str(value) for value in source_policy.get("extensions", []))
    findings: list[dict[str, object]] = []
    for source in manifest.get("code_sources", []):
        if source.get("fetch_status") != "success":
            raise ValueError(f"Code source is not successful: {source.get('service_id')}")
        cache_path = Path(source.get("cache_path", ""))
        if not cache_path.exists():
            findings.append(build_finding("P1", source, "", 0, "代码缓存路径不存在", "无法读取代码进行初步审查。"))
            continue
        findings.extend(scan_source(source, cache_path, rules, excluded_dirs, extensions))

    (raw_dir / "prepare_findings.json").write_text(
        json.dumps({"findings": findings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    write_findings_markdown(run_dir / "code_prepare_findings.md", findings)
    sync_latest_if_run_dir(manifest_path)
    return 0


def load_rules(policy: dict[str, object]) -> list[tuple[str, str, str, re.Pattern[str]]]:
    raw_rules = policy.get("rules")
    if not isinstance(raw_rules, list):
        raise TypeError("review-policy.json.prepare_scan.rules must be a list")
    rules: list[tuple[str, str, str, re.Pattern[str]]] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise TypeError("prepare scan rule must be an object")
        rules.append((str(raw["priority"]), str(raw.get("category", "other")), str(raw["title"]), re.compile(str(raw["pattern"]))))
    return rules


def scan_source(source: dict[str, object], root: Path, rules: list[tuple[str, str, str, re.Pattern[str]]], excluded_dirs: set[str], extensions: set[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in iter_text_files(root, excluded_dirs, extensions):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for priority, category, title, pattern in rules:
                if pattern.search(line):
                    findings.append(
                        build_finding(priority, category, source, path, line_no, title, summarize_line(line))
                    )
    return findings


def iter_text_files(root: Path, excluded_dirs: set[str], extensions: set[str]):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in excluded_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in extensions:
            continue
        if path.stat().st_size > 1024 * 1024:
            continue
        yield path


def build_finding(priority: str, category: str, source: dict[str, object], file_path: Path, line: int, title: str, snippet: str) -> dict[str, object]:
    file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.is_file() else ""
    snippet_hash = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
    finding_id = hashlib.sha256(f"{source.get('service_id')}:{file_path}:{line}:{title}".encode("utf-8")).hexdigest()[:16]
    return {
        "finding_id": finding_id,
        "track": "code_quality",
        "severity": priority,
        "category": category,
        "status": "open",
        "title": title,
        "description": "Deterministic scan candidate; Codex semantic review must confirm or reject it.",
        "impact": "May affect correctness, security, or operability.",
        "recommendation": "Review the complete symbol and its callers/callees before classifying the candidate.",
        "source_id": source.get("service_id", ""),
        "commit": source.get("commit", ""),
        "symbol": "",
        "evidence": [{"file": str(file_path.resolve()), "start_line": line, "end_line": line, "snippet": snippet, "snippet_sha256": snippet_hash, "file_sha256": file_hash, "commit": source.get("commit", "")}],
        "related_requirement_ids": [],
        "related_case_ids": [],
    }


def summarize_line(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"(?i)(password|passwd|pwd|token|secret|access[_-]?key)(\s*[:=]\s*)['\"][^'\"]+['\"]", r"\1\2\"***\"", cleaned)
    return cleaned[:240]


def write_findings_markdown(path: Path, findings: list[dict]) -> None:
    lines = ["# 代码源初步审查结果", ""]
    if not findings:
        lines.extend(["## 结论", "", "- 未发现明显阻塞项。", ""])
    else:
        lines.extend(["## 问题清单", ""])
        for index, item in enumerate(findings, start=1):
            lines.extend(
                [
                    f"### {index}. [{item['severity']}] {item['title']}",
                    "",
                    f"- Source：{item['source_id']}",
                    f"- Category：{item['category']}",
                    f"- 风险影响：{item['impact']}",
                    f"- 建议处理：{item['recommendation']}",
                    "",
                ]
            )
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8", newline="\n")


def sync_latest_if_run_dir(manifest_path: Path) -> None:
    run_dir = manifest_path.parent
    if run_dir.name == "latest":
        return
    output_root = run_dir.parent.parent
    latest_dir = output_root / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(run_dir, latest_dir)


if __name__ == "__main__":
    raise SystemExit(main())
