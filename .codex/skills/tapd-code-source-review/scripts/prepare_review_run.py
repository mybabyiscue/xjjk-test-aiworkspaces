"""Create an isolated review run after all human gates are satisfied."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from workflow_contract import (
    metadata_connection_names,
    parse_mappings,
    require_exact_service_mappings,
    sha256_file,
    successful_sources,
    validate_approved_source_run,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an isolated TAPD code review run.")
    parser.add_argument("--source-run-dir", required=True)
    parser.add_argument("--test-cases", required=True)
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--metadata-document", required=True)
    parser.add_argument("--platform", action="append", required=True)
    parser.add_argument("--source-scope", action="append")
    parser.add_argument("--gateway-prefix", action="append")
    parser.add_argument("--gateway-evidence", action="append")
    parser.add_argument("--gateway-auto-discover", action="store_true")
    parser.add_argument("--gateway-prefix-rule", action="append")
    parser.add_argument("--gateway-evidence-rule", action="append")
    parser.add_argument("--questions-decision", choices=("resolved", "ignored"), required=True)
    parser.add_argument("--questions-note", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    source_run_dir = Path(args.source_run_dir).resolve()
    test_cases_path = require_file(Path(args.test_cases), "test cases")
    requirement_path = require_file(Path(args.requirement), "requirement")
    questions_path = require_file(Path(args.questions), "questions")
    metadata_path = require_file(Path(args.metadata_document), "metadata document")
    manifest, confirmation = validate_approved_source_run(source_run_dir)
    sources = successful_sources(manifest)
    service_ids = {str(source["service_id"]) for source in sources}

    platforms = parse_mappings(args.platform, "platform")
    source_scopes = validate_source_scopes(sources, parse_mappings(args.source_scope or [], "source scope"))
    gateway_prefixes, gateway_evidence = resolve_gateway_inputs(
        args.gateway_prefix or [], args.gateway_evidence or [], source_run_dir, service_ids, args.gateway_auto_discover
    )
    gateway_prefix_rules = parse_mappings(args.gateway_prefix_rule or [], "gateway prefix rule")
    gateway_evidence_rules = parse_mappings(args.gateway_evidence_rule or [], "gateway evidence rule")
    require_exact_service_mappings(service_ids, platforms, "platform")
    require_exact_service_mappings(service_ids, gateway_prefixes, "gateway prefix")
    require_exact_service_mappings(service_ids, gateway_evidence, "gateway evidence")
    available_connections = metadata_connection_names(metadata_path)
    unknown_platforms = sorted(set(platforms.values()) - available_connections)
    if unknown_platforms:
        raise ValueError(f"Unknown metadata platforms: {unknown_platforms}")
    gateway_evidence_records = validate_gateway_mappings(gateway_prefixes, gateway_evidence)
    gateway_rules, gateway_rule_records = validate_gateway_rules(
        service_ids,
        gateway_prefix_rules,
        gateway_evidence_rules,
    )
    if not args.questions_note.strip():
        raise ValueError("Questions decision requires a non-empty note")

    output_root = Path(args.output_root)
    runs_dir = output_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = unique_run_id(runs_dir, datetime.now().strftime("%Y%m%d_%H%M%S_code_review"))
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=False, exist_ok=False)

    enriched_manifest = copy.deepcopy(manifest)
    enriched_manifest["review_run_id"] = run_id
    for source in enriched_manifest["code_sources"]:
        service_id = str(source["service_id"])
        source["platform"] = platforms[service_id]
        source["metadata_connection"] = platforms[service_id]
        source["platform_name"] = platforms[service_id]
        source["platform_status"] = "confirmed"
        source["source_scope"] = source_scopes[service_id]
        source["gateway_prefix"] = normalize_gateway_prefix(gateway_prefixes[service_id])
        source["gateway_evidence"] = gateway_evidence[service_id]
        source["gateway_prefix_rules"] = gateway_rules.get(service_id, [])

    manifest_path = run_dir / "source_manifest.json"
    confirmation_path = run_dir / "code_source_confirmation.json"
    write_json(manifest_path, enriched_manifest)
    write_json(confirmation_path, confirmation)
    copy_source_review_artifacts(source_run_dir, run_dir)
    context: dict[str, object] = {
        "review_run_id": run_id,
        "source_run_id": manifest.get("source_run_id"),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "questions": {
            "decision": args.questions_decision,
            "note": args.questions_note.strip(),
            "sha256": sha256_file(questions_path),
        },
        "inputs": {
            "test_cases_path": str(test_cases_path),
            "test_cases_sha256": sha256_file(test_cases_path),
            "requirement_path": str(requirement_path),
            "requirement_sha256": sha256_file(requirement_path),
            "questions_path": str(questions_path),
            "questions_sha256": sha256_file(questions_path),
            "metadata_path": str(metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "manifest_sha256": sha256_file(manifest_path),
            "source_confirmation_sha256": sha256_file(confirmation_path),
        },
        "gateway_evidence": gateway_evidence_records,
        "gateway_evidence_rules": gateway_rule_records,
    }
    write_json(run_dir / "review_context.json", context)
    print(str(run_dir))
    return 0


def resolve_gateway_inputs(
    prefixes: list[str],
    evidence: list[str],
    source_run_dir: Path,
    service_ids: set[str],
    auto_discover: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    if prefixes or evidence:
        if not prefixes or not evidence:
            raise ValueError("gateway prefix and gateway evidence must be provided together")
        return parse_mappings(prefixes, "gateway prefix"), parse_mappings(evidence, "gateway evidence")
    if not auto_discover:
        raise ValueError("Missing gateway mappings. Run discover_gateway_evidence.py or pass --gateway-auto-discover.")
    discovery_path = source_run_dir / "raw" / "gateway_discovery.json"
    if not discovery_path.is_file():
        raise FileNotFoundError(f"Missing gateway discovery output: {discovery_path}")
    payload = json.loads(discovery_path.read_text(encoding="utf-8"))
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise TypeError("gateway_discovery.json.candidates must be a list")
    result_prefixes: dict[str, str] = {}
    result_evidence: dict[str, str] = {}
    for service_id in service_ids:
        candidates = [item for item in raw_candidates if isinstance(item, dict) and item.get("source_id") == service_id and str(item.get("candidate_prefix", "")).strip() and item.get("evidence_type") == "local_gateway"]
        normalized = {normalize_gateway_prefix(str(item["candidate_prefix"])) for item in candidates}
        if len(normalized) != 1:
            raise ValueError(f"Gateway prefix unresolved for {service_id}; candidates={sorted(normalized)}. Review gateway_discovery.md and provide external config evidence.")
        candidate = candidates[0]
        raw_evidence = candidate.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence or not isinstance(raw_evidence[0], dict):
            raise ValueError(f"Gateway evidence unresolved for {service_id}; candidate has no verifiable evidence")
        evidence_item = raw_evidence[0]
        evidence_file = str(evidence_item.get("file", ""))
        line = evidence_item.get("line")
        if not evidence_file or not isinstance(line, int):
            raise ValueError(f"Gateway evidence unresolved for {service_id}; malformed evidence")
        result_prefixes[service_id] = next(iter(normalized))
        result_evidence[service_id] = f"{evidence_file}:{line}"
    return result_prefixes, result_evidence


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Missing {label}: {resolved}")
    return resolved


def validate_source_scopes(
    sources: list[dict[str, object]],
    mappings: dict[str, str],
) -> dict[str, str]:
    service_ids = {str(source.get("service_id", "")) for source in sources}
    extra = sorted(set(mappings) - service_ids)
    if extra:
        raise ValueError(f"Invalid source scope mappings; unknown services={extra}")
    scopes: dict[str, str] = {}
    for source in sources:
        service_id = str(source.get("service_id", ""))
        raw_scope = mappings.get(service_id, "").replace("\\", "/").strip("/")
        if not raw_scope:
            scopes[service_id] = ""
            continue
        relative_scope = Path(raw_scope)
        if relative_scope.is_absolute() or ".." in relative_scope.parts:
            raise ValueError(f"Source scope must be a repository-relative directory: {service_id}={raw_scope}")
        repository_root = Path(str(source.get("cache_path", ""))).resolve()
        scoped_root = (repository_root / relative_scope).resolve()
        try:
            scoped_root.relative_to(repository_root)
        except ValueError as exc:
            raise ValueError(f"Source scope escapes repository root: {service_id}={raw_scope}") from exc
        if not scoped_root.is_dir():
            raise FileNotFoundError(f"Source scope directory does not exist: {service_id}={raw_scope}")
        scopes[service_id] = relative_scope.as_posix()
    return scopes


def validate_gateway_mappings(prefixes: dict[str, str], evidence: dict[str, str]) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for service_id, prefix in prefixes.items():
        if not prefix.startswith("/"):
            raise ValueError(f"Gateway prefix must start with '/': {service_id}={prefix}")
        normalized_prefix = normalize_gateway_prefix(prefix)
        records[service_id] = validate_gateway_evidence(normalized_prefix, evidence[service_id], service_id, "service", "")
    return records


def validate_gateway_rules(
    service_ids: set[str],
    prefixes: dict[str, str],
    evidence: dict[str, str],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, object]]]:
    if set(prefixes) != set(evidence):
        missing = sorted(set(prefixes) - set(evidence))
        extra = sorted(set(evidence) - set(prefixes))
        raise ValueError(f"Invalid gateway rule evidence mappings; missing={missing}, extra={extra}")
    rules: dict[str, list[dict[str, str]]] = {}
    records: dict[str, dict[str, object]] = {}
    for rule_key, prefix in prefixes.items():
        service_id, separator, path_fragment = rule_key.partition(":")
        normalized_service_id = service_id.strip()
        normalized_fragment = path_fragment.replace("\\", "/").strip("/")
        if not separator or not normalized_service_id or not normalized_fragment:
            raise ValueError(
                f"Invalid gateway rule key {rule_key}; expected service_id:path_fragment"
            )
        if normalized_service_id not in service_ids:
            raise ValueError(f"Unknown service in gateway rule: {normalized_service_id}")
        if not prefix.startswith("/"):
            raise ValueError(f"Gateway rule prefix must start with '/': {rule_key}={prefix}")
        normalized_prefix = normalize_gateway_prefix(prefix)
        evidence_value = evidence[rule_key]
        records[rule_key] = validate_gateway_evidence(normalized_prefix, evidence_value, rule_key, "rule", normalized_fragment)
        rules.setdefault(normalized_service_id, []).append(
            {
                "path_fragment": normalized_fragment,
                "prefix": normalized_prefix,
                "evidence": evidence_value,
            }
        )
    for service_rules in rules.values():
        service_rules.sort(key=lambda item: len(item["path_fragment"]), reverse=True)
    return rules, records


def validate_gateway_evidence(prefix: str, evidence_value: str, label: str, source_kind: str, path_fragment: str) -> dict[str, object]:
    path_text, separator, line_text = evidence_value.rpartition(":")
    if not separator or not line_text.isdigit() or int(line_text) < 1:
        raise ValueError(f"Gateway evidence must use path:line: {label}={evidence_value}")
    evidence_path = Path(path_text)
    if not evidence_path.is_file():
        raise FileNotFoundError(f"Gateway evidence file does not exist: {path_text}")
    lines = evidence_path.read_text(encoding="utf-8").splitlines()
    line_number = int(line_text)
    if line_number > len(lines):
        raise ValueError(f"Gateway evidence line is outside the file: {evidence_value}")
    raw_line = lines[line_number - 1]
    candidates = parse_gateway_prefix_candidates(raw_line)
    if len(candidates) != 1:
        raise ValueError(
            f"gateway_evidence_unresolved: expected exactly one parseable gateway prefix; "
            f"label={label}; evidence={evidence_value}; candidates={candidates}"
        )
    evidence_prefix = normalize_gateway_prefix(candidates[0])
    if evidence_prefix != prefix:
        raise ValueError(
            f"gateway_evidence_unresolved: evidence prefix mismatch; label={label}; "
            f"expected={prefix}; parsed={evidence_prefix}; evidence={evidence_value}"
        )
    return {
        "evidence": evidence_value,
        "evidence_file": str(evidence_path.resolve()),
        "evidence_file_sha256": sha256_file(evidence_path),
        "evidence_line": line_number,
        "raw_line": raw_line,
        "raw_line_sha256": sha256_text(raw_line),
        "parsed_prefix": evidence_prefix,
        "input_prefix": prefix,
        "source_kind": source_kind,
        "path_fragment": path_fragment,
    }


def parse_gateway_prefix_candidates(raw_line: str) -> list[str]:
    patterns = gateway_evidence_patterns()
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(match.group("path") for match in re.finditer(pattern, raw_line))
    return list(dict.fromkeys(strip_gateway_wildcards(item) for item in candidates))


def gateway_evidence_patterns() -> tuple[str, ...]:
    policy_path = Path(__file__).resolve().parents[1] / "assets" / "review-policy.json"
    payload: object = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("review-policy.json must be an object")
    interface_detection = payload.get("interface_detection")
    if not isinstance(interface_detection, dict):
        raise TypeError("review-policy.json.interface_detection must be an object")
    raw_patterns = interface_detection.get("gateway_evidence_patterns")
    if not isinstance(raw_patterns, list) or not raw_patterns or not all(isinstance(item, str) and item for item in raw_patterns):
        raise ValueError("review-policy.json.interface_detection.gateway_evidence_patterns must be non-empty")
    return tuple(raw_patterns)


def strip_gateway_wildcards(value: str) -> str:
    normalized = value.strip().rstrip("/\r\n")
    normalized = re.sub(r"/(?:\*\*|\*)$", "", normalized)
    return normalized or "/"


def sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_gateway_prefix(prefix: str) -> str:
    normalized = "/" + prefix.strip("/")
    return "" if normalized == "/" else normalized


def copy_source_review_artifacts(source_run_dir: Path, review_run_dir: Path) -> None:
    source_files = {
        source_run_dir / "code_prepare_findings.md": review_run_dir / "code_prepare_findings.md",
        source_run_dir / "raw" / "prepare_findings.json": review_run_dir / "raw" / "prepare_findings.json",
    }
    for source, target in source_files.items():
        if not source.is_file():
            raise FileNotFoundError(f"Missing source review artifact: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def unique_run_id(runs_dir: Path, base_run_id: str) -> str:
    candidate = base_run_id
    counter = 1
    while (runs_dir / candidate).exists():
        candidate = f"{base_run_id}_{counter:02d}"
        counter += 1
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
