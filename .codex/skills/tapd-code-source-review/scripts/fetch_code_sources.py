"""Fetch or update code sources from a source_manifest.json."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
import os
from pathlib import Path
from typing import Callable, TypeVar

from codegraph_support import assert_codegraph_environment, prepare_codegraph_index


EXTERNAL_OPERATION_ATTEMPTS = 3
T = TypeVar("T")
LOGGER = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch code sources for tapd-code-source-review.")
    parser.add_argument("--manifest", required=True, help="Path to source_manifest.json.")
    parser.add_argument("--output-root", required=True, help="Output root for cache directories.")
    args = parser.parse_args()

    codegraph_environment = assert_codegraph_environment()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_root = Path(args.output_root)
    cache_root = output_root / "cache"
    errors: list[str] = []

    for source in manifest.get("code_sources", []):
        try:
            fetch_source(source, cache_root)
        except (
            OSError,
            ValueError,
            zipfile.BadZipFile,
            subprocess.CalledProcessError,
            urllib.error.URLError,
        ) as exc:
            source["fetch_status"] = "failed"
            source["stage_status"] = "blocked"
            source["error"] = str(exc)
            errors.append(f"{source.get('service_id')}: {exc}")
            manifest["errors"] = list(dict.fromkeys([*manifest.get("errors", []), *errors]))
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            write_fetch_result(manifest_path.parent / "code_fetch_result.md", manifest)
            raise

    for source in manifest.get("code_sources", []):
        cache_path = Path(str(source.get("cache_path", "")).strip())
        index_result = prepare_codegraph_index(codegraph_environment, cache_path)
        source["codegraph_action"] = index_result.action
        source["codegraph_status"] = "healthy"
        source["stage_status"] = "completed"

    manifest["errors"] = []
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    write_fetch_result(manifest_path.parent / "code_fetch_result.md", manifest)
    return 0


def fetch_source(source: dict, cache_root: Path) -> None:
    source_type = source.get("source_type")
    if source_type == "git":
        fetch_git(source, cache_root / "repos" / source["url_hash"])
        return
    if source_type == "zip":
        fetch_zip(source, cache_root / "archives" / source["url_hash"])
        return
    raise ValueError("暂不支持无法识别的代码来源类型，请提供 .git 或 .zip 地址。")


def fetch_git(source: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    url = source["input_url"]
    requested_branch = source.get("branch", "").strip()
    if target.exists() and (target / ".git").exists():
        run_git(["git", "-C", str(target), "fetch", "--all", "--prune"])
    else:
        clone_git(url, target)
    if requested_branch:
        try:
            run_git(["git", "-C", str(target), "checkout", requested_branch])
        except subprocess.CalledProcessError:
            run_git(["git", "-C", str(target), "checkout", "-b", requested_branch, f"origin/{requested_branch}"])
        run_git(["git", "-C", str(target), "merge", "--ff-only", f"origin/{requested_branch}"])
    commit = run_git(["git", "-C", str(target), "rev-parse", "HEAD"]).strip()
    branch = run_git(["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    remote = run_git(["git", "-C", str(target), "remote", "get-url", "origin"]).strip()
    changed_files = run_git(["git", "-C", str(target), "diff", "--name-only", f"{commit}^", commit]).splitlines()
    source.update({"cache_path": str(target), "commit": commit, "branch": branch, "remote_url": remote, "changed_files": changed_files, "fetch_status": "success", "error": ""})


def fetch_zip(source: dict, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    archive_path = target / "source.zip"
    extract_dir = target / "extracted"
    download_zip(source["input_url"], archive_path)
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extract_dir)
    source.update({"cache_path": str(extract_dir), "commit": "", "branch": "", "fetch_status": "success", "error": ""})


def run_git(command: list[str]) -> str:
    return retry_external_operation(
        lambda: run_git_once(command),
        "git",
        {"command": command},
        EXTERNAL_OPERATION_ATTEMPTS,
    )


def run_git_once(command: list[str]) -> str:
    environment = os.environ.copy()
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return completed.stdout


def clone_git(url: str, target: Path) -> None:
    def clone_once() -> None:
        if target.exists():
            shutil.rmtree(target)
        run_git_once(["git", "clone", "--quiet", url, str(target)])

    retry_external_operation(
        clone_once,
        "git_clone",
        {"url": url, "target": str(target)},
        EXTERNAL_OPERATION_ATTEMPTS,
    )


def download_zip(url: str, archive_path: Path) -> None:
    def download_once() -> None:
        urllib.request.urlretrieve(url, archive_path)

    retry_external_operation(
        download_once,
        "zip_download",
        {"url": url, "target": str(archive_path)},
        EXTERNAL_OPERATION_ATTEMPTS,
    )


def retry_external_operation(
    operation: Callable[[], T],
    operation_name: str,
    request_parameters: dict[str, object],
    max_attempts: int,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except (OSError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
            if attempt == max_attempts:
                raise
            LOGGER.warning(
                "External operation failed; retrying.",
                extra={
                    "operation": operation_name,
                    "request_parameters": request_parameters,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    raise RuntimeError("External operation retry loop ended unexpectedly")


def write_fetch_result(path: Path, manifest: dict) -> None:
    lines = [
        "# 代码获取结果",
        "",
        f"- 运行ID：{manifest.get('source_run_id', '')}",
        f"- 获取结果：{'失败' if manifest.get('errors') else '成功'}",
        "",
        "| 服务编号 | 类型 | 名称 | 分支 | Commit | 缓存路径 | 状态 | 问题 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in manifest.get("code_sources", []):
        lines.append(
            f"| {item.get('service_id', '')} | {item.get('source_type', '')} | {item.get('resolved_name', '')} | {item.get('branch', '') or '无'} | {item.get('commit', '') or '无'} | {item.get('cache_path', '') or '无'} | {item.get('fetch_status', '')} | {item.get('error', '') or '无'} |"
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
