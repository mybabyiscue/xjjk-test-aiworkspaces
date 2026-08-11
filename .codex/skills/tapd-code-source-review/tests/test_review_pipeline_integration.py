"""Integration smoke test for isolated review generation and publication."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.workflow_contract import sha256_file, write_json


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"


def test_review_run_is_generated_and_published_as_one_batch(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    source_dir = cache_dir / "src" / "main" / "java" / "example"
    source_dir.mkdir(parents=True)
    (cache_dir / ".codegraph").mkdir()
    (cache_dir / ".codegraph" / "codegraph.db").write_bytes(b"index")
    (source_dir / "ActivityController.java").write_text(
        """
package example;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
@RestController
@RequestMapping("/mp/activity")
public class ActivityController {
    @PostMapping("/save")
    public String save(ActivityDTO dto) { return "ok"; }
}
""".strip(),
        encoding="utf-8",
    )
    (source_dir / "ActivityDTO.java").write_text(
        "package example; public class ActivityDTO { private String activityName; }\n",
        encoding="utf-8",
    )
    run_git(cache_dir, "init")
    run_git(cache_dir, "config", "user.email", "test@example.com")
    run_git(cache_dir, "config", "user.name", "Test User")
    run_git(cache_dir, "add", ".")
    run_git(cache_dir, "commit", "-m", "initial")
    run_git(cache_dir, "branch", "-M", "main")
    run_git(cache_dir, "update-ref", "refs/remotes/origin/main", "HEAD")
    run_git(cache_dir, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    run_git(cache_dir, "checkout", "-b", "feature-activity")

    source_run = tmp_path / "code_sources" / "runs" / "source-1"
    (source_run / "raw").mkdir(parents=True)
    manifest_path = source_run / "source_manifest.json"
    write_json(manifest_path, {
        "source_run_id": "source-1",
        "code_sources": [{
            "service_id": "service_001",
            "source_type": "git",
            "resolved_name": "activity-service",
            "branch": "feature-activity",
            "commit": "abc123",
            "cache_path": str(cache_dir),
            "fetch_status": "success",
            "codegraph_status": "healthy",
        }],
        "errors": [],
    })
    write_json(source_run / "code_source_confirmation.json", {
        "approved": True,
        "source_run_id": "source-1",
        "manifest_sha256": sha256_file(manifest_path),
    })
    (source_run / "code_prepare_findings.md").write_text("# Findings\n\nNone.\n", encoding="utf-8")
    write_json(source_run / "raw" / "prepare_findings.json", {"findings": []})

    test_cases = tmp_path / "test_cases.md"
    requirement = tmp_path / "requirement.md"
    questions = tmp_path / "questions.md"
    metadata = tmp_path / "metadata_document.json"
    gateway = tmp_path / "gateway.yml"
    test_cases.write_text(
        "## TC001 Activity save /mp/activity/save\n- Expected: activity saved\n",
        encoding="utf-8",
    )
    requirement.write_text("# Activity save\n", encoding="utf-8")
    questions.write_text("# Questions\n\nNone.\n", encoding="utf-8")
    write_json(metadata, {
        "connections": [{
            "connection": {"name": "Test Platform"},
            "schemas": [],
        }],
    })
    gateway.write_text("Path=/product/**\n", encoding="utf-8")
    review_root = tmp_path / "code_review"

    prepared = run_script(
        "prepare_review_run.py",
        "--source-run-dir", str(source_run),
        "--test-cases", str(test_cases),
        "--requirement", str(requirement),
        "--questions", str(questions),
        "--metadata-document", str(metadata),
        "--platform", "service_001=Test Platform",
        "--gateway-prefix", "service_001=/product",
        "--gateway-evidence", f"service_001={gateway}:1",
        "--questions-decision", "resolved",
        "--questions-note", "No open questions",
        "--output-root", str(review_root),
    )
    review_run = Path(prepared.stdout.strip().splitlines()[-1])

    run_script(
        "analyze_testcase_evidence.py",
        "--run-dir", str(review_run),
        "--manifest", str(review_run / "source_manifest.json"),
        "--source-confirmation", str(review_run / "code_source_confirmation.json"),
        "--test-cases", str(test_cases),
        "--metadata-document", str(metadata),
        "--policy", str(SKILL_ROOT / "assets" / "review-policy.json"),
    )
    review_manifest_path = review_run / "source_manifest.json"
    valid_review_manifest = review_manifest_path.read_text(encoding="utf-8")
    invalid_review_manifest = json.loads(valid_review_manifest)
    invalid_review_manifest["code_sources"][0]["fetch_status"] = "failed"
    write_json(review_manifest_path, invalid_review_manifest)
    with pytest.raises(subprocess.CalledProcessError):
        run_script(
            "validate_publish_review.py",
            "--run-dir", str(review_run),
            "--test-cases", str(test_cases),
            "--output-root", str(review_root),
        )
    review_manifest_path.write_text(valid_review_manifest, encoding="utf-8", newline="\n")
    run_script(
        "validate_publish_review.py",
        "--run-dir", str(review_run),
        "--test-cases", str(test_cases),
        "--output-root", str(review_root),
    )

    latest = review_root / "latest"
    assert (latest / "review_validation.json").is_file()
    assert json.loads((latest / "review_context.json").read_text(encoding="utf-8"))["review_run_id"] == review_run.name
    assert "/product/mp/activity/save" in (latest / "core_process_interfaces.md").read_text(encoding="utf-8")

    route_path = review_run / "raw" / "route_consistency.json"
    route_payload = json.loads(route_path.read_text(encoding="utf-8"))
    route_payload["route_conflict_count"] = 1
    route_payload["blocking_route_issue_count"] = 1
    write_json(route_path, route_payload)
    with pytest.raises(subprocess.CalledProcessError):
        run_script(
            "validate_publish_review.py",
            "--run-dir", str(review_run),
            "--test-cases", str(test_cases),
            "--output-root", str(review_root),
        )
    assert json.loads((latest / "review_context.json").read_text(encoding="utf-8"))["review_run_id"] == review_run.name
    assert json.loads((review_run / "review_validation.json").read_text(encoding="utf-8"))["valid"] is False


def run_script(script_name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_ROOT / script_name), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
