"""Shared validation helpers for the TAPD code review workflow."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit, urlunsplit


REQUIRED_REVIEW_ARTIFACTS: Final[tuple[str, ...]] = (
    "source_manifest.json",
    "code_source_confirmation.json",
    "review_context.json",
    "raw/prepare_findings.json",
    "raw/parsed_test_cases.json",
    "raw/code_entry_index.json",
    "raw/route_consistency.json",
    "raw/testcase_interface_evidence.json",
    "raw/call_chain_evidence.json",
    "raw/table_evidence.json",
    "raw/table_resolution.json",
    "raw/requirement_code_matrix.json",
    "core_process_interfaces.md",
    "unit_test_interfaces.md",
    "table_information.md",
    "unresolved_tables.md",
    "testcase_evidence_summary.md",
    "code_review_report.md",
    "code_prepare_findings.md",
    "requirement_implementation_review.md",
    "requirement_findings.md",
    "requirement_review_status.json",
)

CORE_PROCESS_INTERFACE_HEADERS: Final[tuple[str, ...]] = (
    "用例编号",
    "接口名称/描述",
    "接口类型与地址",
    "请求参数",
    "返回参数",
    "调用链路",
    "代码位置",
)
UNIT_TEST_INTERFACE_HEADERS: Final[tuple[str, ...]] = (
    "用例编号",
    "方法签名",
    "输入边界场景",
    "需隔离的外部依赖",
    "当前覆盖状态",
    "代码位置",
)
TABLE_INFORMATION_HEADERS: Final[tuple[str, ...]] = (
    "用例编号",
    "所属平台",
    "库.表名",
    "物理注释",
    "确认等级",
    "判定依据说明",
    "关键字段",
    "读/写类型",
    "租户隔离",
)
REVIEW_DOCUMENT_TABLE_HEADERS: Final[dict[str, tuple[str, ...]]] = {
    "unit_test_interfaces.md": UNIT_TEST_INTERFACE_HEADERS,
    "core_process_interfaces.md": CORE_PROCESS_INTERFACE_HEADERS,
    "table_information.md": TABLE_INFORMATION_HEADERS,
}
MARKDOWN_SEPARATOR_CELL: Final[re.Pattern[str]] = re.compile(r":?-{3,}:?")


def read_json_object(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Invalid {label}: root must be an object: {path}")
    return value


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Cannot hash missing file: {path}")
    digest: hashlib._Hash = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_code_url(value: str, explicit_branch: str) -> tuple[str, str, str]:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Code URL must use HTTP or HTTPS: {value}")
    raw_path = parsed.path.rstrip("/")
    path_parts = [part for part in raw_path.split("/") if part]
    tree_index = next(
        (index for index, part in enumerate(path_parts) if part.lower() == "tree"),
        -1,
    )
    branch = explicit_branch.strip() or parsed.fragment.strip()
    if tree_index >= 0 and tree_index + 1 < len(path_parts):
        branch = unquote("/".join(path_parts[tree_index + 1 :])).strip()
        repository_parts = path_parts[:tree_index]
        if repository_parts and repository_parts[-1] == "-":
            repository_parts = repository_parts[:-1]
        raw_path = "/" + "/".join(repository_parts)
    clean_path = raw_path
    lowered_path = clean_path.lower()
    if tree_index >= 0:
        if not branch:
            raise ValueError(f"Code tree URL must include a branch: {value}")
        if not lowered_path.endswith(".git"):
            clean_path += ".git"
        clean_url = urlunsplit((parsed.scheme, parsed.netloc, clean_path, "", ""))
        return clean_url, "git", branch
    clean_url = urlunsplit((parsed.scheme, parsed.netloc, clean_path, parsed.query, ""))
    if lowered_path.endswith(".git"):
        branch = branch
        if not branch:
            raise ValueError(f"Git URL must include an explicit #branch: {value}")
        return clean_url, "git", branch
    if lowered_path.endswith(".zip"):
        if parsed.fragment:
            raise ValueError(f"ZIP URL must not include a branch fragment: {value}")
        return clean_url, "zip", ""
    if branch and lowered_path and not lowered_path.endswith(".zip"):
        if not lowered_path.endswith(".git"):
            clean_url = urlunsplit((parsed.scheme, parsed.netloc, clean_path + ".git", parsed.query, ""))
        return clean_url, "git", branch
    raise ValueError(f"Code URL must end with .git#branch, repository URL plus branch, or .zip: {value}")


def parse_mappings(values: list[str], label: str) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        key, separator, mapped_value = value.partition("=")
        normalized_key = key.strip()
        normalized_value = mapped_value.strip()
        if not separator or not normalized_key or not normalized_value:
            raise ValueError(f"Invalid {label} mapping {value}; expected service=value")
        if normalized_key in mappings:
            raise ValueError(f"Duplicate {label} mapping for {normalized_key}")
        mappings[normalized_key] = normalized_value
    return mappings


def require_exact_service_mappings(
    service_ids: set[str],
    mappings: dict[str, str],
    label: str,
) -> None:
    missing = sorted(service_ids - set(mappings))
    extra = sorted(set(mappings) - service_ids)
    if missing or extra:
        raise ValueError(f"Invalid {label} mappings; missing={missing}, extra={extra}")


def metadata_connection_names(path: Path) -> set[str]:
    document = read_json_object(path, "metadata document")
    connections = document.get("connections")
    if not isinstance(connections, list):
        raise TypeError("metadata_document.json.connections must be a list")
    names: set[str] = set()
    for connection in connections:
        if not isinstance(connection, dict):
            raise TypeError("metadata_document.json.connections entries must be objects")
        connection_info = connection.get("connection")
        if not isinstance(connection_info, dict):
            raise TypeError("metadata connection information must be an object")
        name = connection_info.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    if not names:
        raise ValueError("Metadata document contains no named connections")
    return names


def successful_sources(manifest: dict[str, object]) -> list[dict[str, object]]:
    raw_sources = manifest.get("code_sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("source_manifest.json.code_sources must contain at least one source")
    sources: list[dict[str, object]] = []
    failures: list[str] = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise TypeError("Each code source must be an object")
        service_id = str(raw_source.get("service_id", "")).strip()
        if raw_source.get("fetch_status") != "success":
            failures.append(service_id or "unknown")
            continue
        if raw_source.get("codegraph_status") != "healthy":
            raise ValueError(f"CodeGraph is not healthy for {service_id}")
        branch = str(raw_source.get("branch", "")).strip()
        commit = str(raw_source.get("commit", "")).strip()
        if raw_source.get("source_type") == "git" and (
            not branch or branch == "HEAD" or not commit or commit == "HEAD"
        ):
            raise ValueError(f"Git source has an invalid branch or commit: {service_id}")
        cache_path = Path(str(raw_source.get("cache_path", "")).strip())
        if not cache_path.is_dir():
            raise FileNotFoundError(f"Unreadable cache path for {service_id}: {cache_path}")
        if not (cache_path / ".codegraph" / "codegraph.db").is_file():
            raise FileNotFoundError(f"Missing CodeGraph database for {service_id}: {cache_path}")
        sources.append(raw_source)
    if failures:
        raise ValueError(f"Code sources were not fetched successfully: {', '.join(failures)}")
    return sources


def validate_approved_source_run(source_run_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest_path = source_run_dir / "source_manifest.json"
    confirmation_path = source_run_dir / "code_source_confirmation.json"
    manifest = read_json_object(manifest_path, "source manifest")
    confirmation = read_json_object(confirmation_path, "code source confirmation")
    successful_sources(manifest)
    if confirmation.get("approved") is not True:
        raise ValueError("Code source review has not been explicitly approved")
    if confirmation.get("source_run_id") != manifest.get("source_run_id"):
        raise ValueError("Code source confirmation belongs to a different source run")
    manifest_hash = sha256_file(manifest_path)
    if confirmation.get("manifest_sha256") != manifest_hash:
        raise ValueError("Code source manifest changed after approval")
    return manifest, confirmation


def validate_prepared_review_gate(
    run_dir: Path,
    manifest_path: Path,
    confirmation_path: Path,
    test_cases_path: Path,
    metadata_path: Path,
) -> dict[str, object]:
    context = read_json_object(run_dir / "review_context.json", "review context")
    manifest = read_json_object(manifest_path, "review source manifest")
    confirmation = read_json_object(confirmation_path, "code source confirmation")
    if confirmation.get("approved") is not True:
        raise ValueError("Code source confirmation is not approved")
    if context.get("review_run_id") != run_dir.name:
        raise ValueError("Review context run ID does not match the run directory")
    if context.get("source_run_id") != manifest.get("source_run_id"):
        raise ValueError("Review context and source manifest use different source runs")
    if confirmation.get("source_run_id") != manifest.get("source_run_id"):
        raise ValueError("Code source confirmation belongs to a different source run")
    inputs = context.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("review_context.json.inputs must be an object")
    metadata_context_path = review_input_path(context, "metadata_path")
    if metadata_context_path != metadata_path.resolve():
        raise ValueError("Metadata document path differs from the prepared review input")
    expected = {
        "test_cases_sha256": sha256_file(test_cases_path),
        "requirement_sha256": sha256_file(review_input_path(context, "requirement_path")),
        "questions_sha256": sha256_file(review_input_path(context, "questions_path")),
        "metadata_sha256": sha256_file(metadata_path),
        "manifest_sha256": sha256_file(manifest_path),
        "source_confirmation_sha256": sha256_file(confirmation_path),
    }
    for key, actual in expected.items():
        if inputs.get(key) != actual:
            raise ValueError(f"Review input changed after preparation: {key}")
    questions = context.get("questions")
    if not isinstance(questions, dict):
        raise TypeError("review_context.json.questions must be an object")
    if questions.get("sha256") != expected["questions_sha256"]:
        raise ValueError("Questions evidence changed after preparation")
    successful_sources(manifest)
    validate_gateway_evidence_integrity(context, manifest)
    return context


def review_input_path(context: dict[str, object], key: str) -> Path:
    inputs = context.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("review_context.json.inputs must be an object")
    raw_path = inputs.get(key)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"Review context is missing input path: {key}")
    return Path(raw_path).resolve()


def validate_gateway_evidence_integrity(context: dict[str, object], manifest: dict[str, object]) -> None:
    records: list[dict[str, object]] = []
    for key in ("gateway_evidence", "gateway_evidence_rules"):
        raw_records = context.get(key)
        if not isinstance(raw_records, dict):
            raise ValueError(f"review_context.json.{key} must be an object")
        records.extend(value for value in raw_records.values() if isinstance(value, dict))
    for record in records:
        evidence_file = Path(str(record.get("evidence_file", ""))).resolve()
        line_number = record.get("evidence_line")
        if not evidence_file.is_file() or not isinstance(line_number, int) or line_number < 1:
            raise ValueError("gateway_evidence_unresolved: evidence file or line is invalid")
        lines = evidence_file.read_text(encoding="utf-8").splitlines()
        if line_number > len(lines):
            raise ValueError("gateway_evidence_unresolved: evidence line is outside the file")
        raw_line = lines[line_number - 1]
        if sha256_file(evidence_file) != record.get("evidence_file_sha256"):
            raise ValueError(f"Gateway evidence file changed: {evidence_file}")
        if raw_line != record.get("raw_line") or sha256_text(raw_line) != record.get("raw_line_sha256"):
            raise ValueError(f"Gateway evidence line changed: {evidence_file}#L{line_number}")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def markdown_table_lines(headers: tuple[str, ...]) -> tuple[str, str]:
    return (
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    )


def first_markdown_table_headers(document: str) -> list[str]:
    lines = document.splitlines()
    for index in range(len(lines) - 1):
        header_cells = markdown_cells(lines[index])
        separator_cells = markdown_cells(lines[index + 1])
        if (
            header_cells
            and len(header_cells) == len(separator_cells)
            and all(MARKDOWN_SEPARATOR_CELL.fullmatch(cell) for cell in separator_cells)
        ):
            return header_cells
    return []


def markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def validate_review_document_schemas(run_dir: Path) -> None:
    for relative_path, expected_headers in REVIEW_DOCUMENT_TABLE_HEADERS.items():
        document_path = run_dir / relative_path
        if not document_path.is_file():
            raise FileNotFoundError(f"Missing review document: {document_path}")
        actual_headers = first_markdown_table_headers(
            document_path.read_text(encoding="utf-8-sig")
        )
        if actual_headers != list(expected_headers):
            raise ValueError(
                f"Invalid first Markdown table schema in {relative_path}; "
                f"expected={list(expected_headers)}, actual={actual_headers}"
            )


def artifact_hashes(run_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {
        relative_path: sha256_file(run_dir / relative_path)
        for relative_path in REQUIRED_REVIEW_ARTIFACTS
    }
    raw_dir = run_dir / "raw"
    for evidence_path in sorted(raw_dir.glob("*_evidence.json")):
        relative_path = evidence_path.relative_to(run_dir).as_posix()
        if relative_path not in hashes:
            hashes[relative_path] = sha256_file(evidence_path)
    return hashes
