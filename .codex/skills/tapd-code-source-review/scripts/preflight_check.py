"""Create a code source review run and validate HTTP code URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from codegraph_support import assert_codegraph_environment
from workflow_contract import parse_code_url, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Create tapd-code-source-review preflight outputs.")
    parser.add_argument("--code-url", action="append", required=True, help="HTTP/HTTPS code URL. Repeat for multiple services.")
    parser.add_argument("--branch", action="append", default=[], help="Branch for each repository URL. May be embedded in /tree/<branch> URLs.")
    parser.add_argument("--test-cases", required=True)
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--metadata-document", required=True)
    parser.add_argument("--output-root", required=True, help="Output root for code source runs.")
    args = parser.parse_args()

    assert_codegraph_environment()

    output_root = Path(args.output_root)
    runs_dir = output_root / "runs"
    latest_dir = output_root / "latest"
    runs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    run_id = ensure_unique_run_id(runs_dir, now.strftime("%Y%m%d_%H%M%S_code_source"))
    run_dir = runs_dir / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)

    previous_run_id = read_previous_run_id(latest_dir)
    sources = []
    errors = validate_required_inputs(
        Path(args.test_cases),
        Path(args.requirement),
        Path(args.questions),
        Path(args.metadata_document),
    )
    if len(args.branch) > len(args.code_url):
        errors.append("More --branch values were provided than --code-url values")
    for index, url in enumerate(args.code_url, start=1):
        normalized = url.strip()
        service_id = f"service_{index:03d}"
        error = ""
        clean_url = normalized
        source_type = "unknown"
        branch = ""
        try:
            explicit_branch = args.branch[index - 1].strip() if index <= len(args.branch) else ""
            clean_url, source_type, branch = parse_code_url(normalized, explicit_branch)
        except ValueError as exc:
            error = str(exc)
            errors.append(f"{service_id}: {error}")
        resolved_name = resolve_name(clean_url)
        sources.append(
            {
                "service_id": service_id,
                "input_url": clean_url,
                "source_type": source_type,
                "resolved_name": resolved_name,
                "branch": branch,
                "commit": "",
                "cache_path": "",
                "fetch_status": "pending" if not error else "failed",
                "url_hash": hashlib.sha256(f"{clean_url}#{branch}".encode("utf-8")).hexdigest()[:16],
                "error": error,
                "source_role": infer_source_role(clean_url),
                "source_role_confidence": "medium",
            }
        )

    manifest = {
        "source_run_id": run_id,
        "previous_source_run_id": previous_run_id,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "code_sources": sources,
        "errors": errors,
        "inputs": input_hashes(
            Path(args.test_cases),
            Path(args.requirement),
            Path(args.questions),
            Path(args.metadata_document),
        ),
    }
    confirmation = {
        "approved": False,
        "reason": "代码源准备阶段已初始化，等待代码获取、初步审查和人工确认。",
        "source_run_id": run_id,
        "approved_at": "",
        "approver": "",
    }

    write_json(run_dir / "source_manifest.json", manifest)
    write_json(run_dir / "code_source_confirmation.json", confirmation)
    write_input_check(run_dir / "input_check.md", manifest)
    sync_latest(run_dir, latest_dir)
    print(str(run_dir))
    return 1 if errors else 0


def resolve_name(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    name = Path(path).name or "unknown-source"
    if name.endswith(".git"):
        name = name[:-4]
    if name.endswith(".zip"):
        name = name[:-4]
    return name or "unknown-source"


def infer_source_role(url: str) -> str:
    """Infer only a generic repository role from path markers; never invent business roles."""
    lowered = urlparse(url).path.lower()
    if any(marker in lowered for marker in ("frontend", "web", "admin-ui", "mall4cloud-ui")):
        return "frontend"
    if "gateway" in lowered:
        return "gateway"
    if any(marker in lowered for marker in ("shared", "common", "api")):
        return "shared"
    if any(marker in lowered for marker in ("service", "backend", "server")):
        return "backend"
    return "unknown"


def validate_required_inputs(
    test_cases_path: Path,
    requirement_path: Path,
    questions_path: Path,
    metadata_path: Path,
) -> list[str]:
    inputs = {
        "test cases": test_cases_path,
        "requirement": requirement_path,
        "questions": questions_path,
        "metadata document": metadata_path,
    }
    return [f"Missing {label}: {path}" for label, path in inputs.items() if not path.is_file()]


def input_hashes(
    test_cases_path: Path,
    requirement_path: Path,
    questions_path: Path,
    metadata_path: Path,
) -> dict[str, str]:
    inputs = {
        "test_cases_sha256": test_cases_path,
        "requirement_sha256": requirement_path,
        "questions_sha256": questions_path,
        "metadata_sha256": metadata_path,
    }
    return {key: sha256_file(path) for key, path in inputs.items() if path.is_file()}


def ensure_unique_run_id(runs_dir: Path, base_run_id: str) -> str:
    candidate = base_run_id
    index = 1
    while (runs_dir / candidate).exists():
        candidate = f"{base_run_id}_{index:02d}"
        index += 1
    return candidate


def read_previous_run_id(latest_dir: Path) -> str:
    manifest_path = latest_dir / "source_manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("source_run_id", "")).strip()


def write_input_check(path: Path, manifest: dict) -> None:
    lines = [
        "# 代码源输入检查",
        "",
        f"- 运行ID：{manifest['source_run_id']}",
        f"- 上一次运行ID：{manifest.get('previous_source_run_id') or '无'}",
        f"- 检查结果：{'失败' if manifest.get('errors') else '通过'}",
        "",
        "## 代码来源",
        "",
        "| 服务编号 | 类型 | 名称 | URL | 状态 | 问题 |",
        "|---|---|---|---|---|---|",
    ]
    for item in manifest["code_sources"]:
        lines.append(
            f"| {item['service_id']} | {item['source_type']} | {item['resolved_name']} | {item['input_url']} | {item['fetch_status']} | {item.get('error') or '无'} |"
        )
    if manifest.get("errors"):
        lines.extend(["", "## 阻塞项", ""])
        lines.extend(f"- {error}" for error in manifest["errors"])
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def sync_latest(run_dir: Path, latest_dir: Path) -> None:
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(run_dir, latest_dir)


if __name__ == "__main__":
    raise SystemExit(main())
