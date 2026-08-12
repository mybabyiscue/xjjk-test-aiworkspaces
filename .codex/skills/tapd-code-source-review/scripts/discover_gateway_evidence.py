"""Discover gateway prefixes and external configuration evidence from fetched sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


CONFIG_SUFFIXES = {".yml", ".yaml", ".properties"}
ROUTE_PATTERNS = (
    re.compile(r"(?i)\bpath\s*[:=]\s*[\"'`]?\s*(?P<path>/[^\"'`,;\s]+)"),
    re.compile(r"(?i)\b(?:url|uri|route)\s*[:=]\s*[\"'`]\s*(?P<path>/[^\"'`]+)"),
)
NACOS_IMPORT_PATTERN = re.compile(r"(?i)nacos:(?P<data_id>[^?\s]+)")
NACOS_PROPERTY_PATTERN = re.compile(r"(?i)(?:data-id|namespace|group)\s*[:=]\s*['\"]?(?P<value>[^'\"\s,]+)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover gateway evidence from fetched source code.")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates: list[dict[str, object]] = []
    for source in manifest.get("code_sources", []):
        if source.get("fetch_status") != "success":
            candidates.append({"source_id": source.get("service_id", ""), "status": "blocked", "reason": "fetch_status is not success"})
            continue
        candidates.extend(discover_source(source, Path(str(source["cache_path"]))))
    payload = {"source_run_id": manifest.get("source_run_id", ""), "candidates": candidates}
    raw_dir = manifest_path.parent / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "gateway_discovery.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    write_markdown(manifest_path.parent / "gateway_discovery.md", candidates)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def discover_source(source: dict[str, object], root: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    config_files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in CONFIG_SUFFIXES and not excluded(path)]
    for path in config_files:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line_number, line in enumerate(lines, start=1):
            for pattern in ROUTE_PATTERNS:
                for match in pattern.finditer(line):
                    prefix = strip_wildcards(match.group("path"))
                    results.append(build_candidate(source, path, line_number, line, prefix, "local_gateway"))
            for match in NACOS_IMPORT_PATTERN.finditer(line):
                results.append(build_candidate(source, path, line_number, line, "", "nacos_import", external_config(path, line, match.group("data_id"))))
    for path in [p for p in root.rglob("*.java") if not excluded(p)]:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if "RouteLocator" in line or ".path(" in line or ".predicate(" in line:
                for match in re.finditer(r"[\"'](?P<path>/[^\"']+)[\"']", line):
                    results.append(build_candidate(source, path, line_number, line, strip_wildcards(match.group("path")), "java_dsl"))
    if not results:
        results.append({"source_id": source.get("service_id", ""), "status": "unresolved", "confidence": "unresolved", "reason": "No local gateway route evidence found."})
    return deduplicate(results)


def external_config(path: Path, line: str, data_id: str) -> dict[str, str]:
    values = {"provider": "nacos", "data_id": data_id, "file": str(path)}
    for match in NACOS_PROPERTY_PATTERN.finditer(line):
        values[match.group(1).lower().replace("-", "_")] = match.group("value")
    return values


def build_candidate(source: dict[str, object], path: Path, line_number: int, line: str, prefix: str, evidence_type: str, external: dict[str, str] | None = None) -> dict[str, object]:
    resolved = path.resolve()
    return {"source_id": source.get("service_id", ""), "module": module_name(path), "candidate_prefix": prefix, "confidence": "high" if prefix else "medium", "evidence_type": evidence_type, "evidence": [{"file": str(resolved), "line": line_number, "raw_line": line, "raw_line_sha256": sha256_text(line), "file_sha256": sha256_file(resolved)}], "external_config": external or {}}


def module_name(path: Path) -> str:
    parts = path.parts
    for index, part in enumerate(parts):
        if part.startswith("mall4cloud-"):
            return part
    return ""


def excluded(path: Path) -> bool:
    return any(part in {".git", "target", "build", "dist", "node_modules"} for part in path.parts)


def strip_wildcards(value: str) -> str:
    normalized = value.strip().rstrip("/")
    normalized = re.sub(r"/(?:\*\*|\*)$", "", normalized)
    return normalized or "/"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deduplicate(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[object, object, object, object]] = set()
    result: list[dict[str, object]] = []
    for item in items:
        evidence = item.get("evidence", [{}])
        first = evidence[0] if isinstance(evidence, list) and evidence else {}
        key = (item.get("source_id"), item.get("candidate_prefix"), item.get("evidence_type"), first.get("file") if isinstance(first, dict) else "")
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def write_markdown(path: Path, candidates: list[dict[str, object]]) -> None:
    lines = ["# Gateway Discovery", "", "| Source | Module | Candidate | Confidence | Type | Status/Reason |", "|---|---|---|---|---|---|"]
    for item in candidates:
        lines.append(f"| {item.get('source_id', '')} | {item.get('module', '')} | {item.get('candidate_prefix', '')} | {item.get('confidence', '')} | {item.get('evidence_type', '')} | {item.get('reason', item.get('status', 'candidate'))} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
