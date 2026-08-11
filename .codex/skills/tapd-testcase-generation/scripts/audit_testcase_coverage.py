"""Audit testcase artifacts against atomic, evidence-backed test points."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TypedDict

DIMENSIONS = {"Happy Path", "Alternative/Error", "Edge Cases", "Compatibility"}
DISPOSITIONS = {"case", "question"}
RULES_PATH = Path(__file__).resolve().parents[1] / "references" / "audit-rules.json"

def load_rules() -> dict[str, Any]:
    value: object = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("audit-rules.json root must be an object")
    return value

class ManifestItem(TypedDict):
    id: str
    title: str

class CoverageManifest(TypedDict):
    function_points: list[ManifestItem]
    bdd_scenarios: list[ManifestItem]

CASE_RE = re.compile(r"^###\s+(TC\d{3})\s+-\s+(.+)$", re.MULTILINE)
FIELD_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*\s*[：:]\s*(.*?)\s*$", re.MULTILINE)

def read_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"empty file: {path}")
    return text

def extract_manifest(requirement: str, existing: CoverageManifest | None) -> CoverageManifest:
    function_section = re.search(r"^##[^\n]*(?:核心功能点|core function)[^\n]*$([\s\S]*?)(?=^##\s|\Z)", requirement, re.MULTILINE | re.IGNORECASE)
    source = function_section.group(1) if function_section else requirement
    titles = [re.sub(r"\s+", " ", m.group(1).strip()) for m in re.finditer(r"^\s*\d+[.)、]\s+\*\*(.+?)\*\*\s*[：:]", source, re.MULTILINE)]
    if not titles:
        titles = [re.sub(r"\s+", " ", m.group(1).strip()) for m in re.finditer(r"(?:^|\n)\s*\d+[.)、]\s+(.+?)(?=\n|$)", source)]
    if not titles:
        raise ValueError("requirement.md has no core function points")
    bdd_section = re.search(r"^##[^\n]*(?:BDD|验收标准|acceptance criteria)[^\n]*$([\s\S]*?)(?=^##\s|\Z)", requirement, re.MULTILINE | re.IGNORECASE)
    bdd_source = bdd_section.group(1) if bdd_section else ""
    bdd_titles = [re.sub(r"\s+", " ", m.group(1).strip()) for m in re.finditer(r"^#{1,6}\s+((?:场景|Scenario)\s*[^\n]+)", bdd_source, re.MULTILINE | re.IGNORECASE)]
    if not bdd_titles:
        bdd_titles = [re.sub(r"\s+", " ", m.group(1).strip()) for m in re.finditer(r"(?:\*\*)?((?:场景|Scenario)\s*[^\n*]+)", bdd_source, re.IGNORECASE)]
    if bdd_section is not None and not bdd_titles:
        raise ValueError("未提取到任何场景")
    old = existing or {"function_points": [], "bdd_scenarios": []}
    def reconcile(values: list[str], prefix: str, field: str) -> list[ManifestItem]:
        by_title = {item["title"]: item["id"] for item in old[field]}
        next_number = max([int(item["id"].split("-")[-1]) for item in old[field]] or [0]) + 1
        result: list[ManifestItem] = []
        for title in values:
            ident = by_title.get(title)
            if ident is None:
                ident, next_number = f"{prefix}-{next_number:03d}", next_number + 1
            result.append({"id": ident, "title": title})
        return result
    return {"function_points": reconcile(titles, "FP", "function_points"), "bdd_scenarios": reconcile(bdd_titles, "BDD", "bdd_scenarios")}

def parse_cases(markdown: str) -> list[dict[str, Any]]:
    headings = list(CASE_RE.finditer(markdown))
    if not headings:
        raise ValueError("test_cases.md has no cases")
    parsed: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        block = markdown[heading.end():end]
        fields = {match.group(1).strip(): match.group(2).strip() for match in FIELD_RE.finditer(block)}
        for name in ("测试步骤", "预期结果"):
            list_match = re.search(rf"^-\s+\*\*{re.escape(name)}\*\*\s*[：:]\s*\n([\s\S]*?)(?=^-\s+\*\*|\Z)", block, re.MULTILINE)
            if list_match:
                fields[name] = "；".join(item.strip() for item in re.findall(r"^\s*\d+\.\s+(.+)$", list_match.group(1), re.MULTILINE))
        if len(fields) < 10:
            raise ValueError(f"{heading.group(1)} has incomplete fields")
        parsed.append({"case_id": heading.group(1), "title": heading.group(2).strip(), "fields": fields})
    return parsed

def load_matrix(path: Path) -> list[dict[str, Any]]:
    value: object = json.loads(read_text(path))
    if not isinstance(value, list):
        raise ValueError("testpoint_matrix.json root must be an array")
    required = {"test_point_id", "function_point_id", "atomic_rule_id", "bdd_scenario_id", "rule_text", "source_section", "source_evidence", "active_layer", "scenario_dimension", "given", "when", "then", "priority", "disposition", "question_id", "case_id"}
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("testpoint_matrix.json field contract is invalid")
        if item["scenario_dimension"] not in DIMENSIONS or item["disposition"] not in DISPOSITIONS:
            raise ValueError(f"invalid test point enum: {item.get('test_point_id')}")
        if not all(isinstance(item[field], str) and item[field].strip() for field in ("test_point_id", "atomic_rule_id", "rule_text", "source_section", "source_evidence", "active_layer", "given", "when", "then", "priority")):
            raise ValueError(f"empty required test point field: {item.get('test_point_id')}")
        if item["disposition"] == "question" and not isinstance(item["question_id"], str):
            raise ValueError(f"question disposition requires question_id: {item.get('test_point_id')}")
        result.append(item)
    return result

def audit_directory(output: Path) -> tuple[str, list[str]]:
    requirement = read_text(output / "requirement.md")
    cases = parse_cases(read_text(output / "test_cases.md"))
    matrix = load_matrix(output / "testpoint_matrix.json")
    failures: list[str] = []
    rules = load_rules()
    vague_patterns = tuple(str(item) for item in rules.get("vague_assertions", []))
    scenario_cues = {str(key): tuple(str(item) for item in value) for key, value in dict(rules.get("scenario_cues", {})).items()}
    allowed_risk_tags = {str(item) for item in rules.get("risk_tags", [])}
    manifest = extract_manifest(requirement, None)
    known_fp = {item["id"] for item in manifest["function_points"]}
    known_bdd = {item["id"] for item in manifest["bdd_scenarios"]}
    bdd_matches = list(re.finditer(r"^###\s+(?:场景|Scenario)[^\n]+", requirement, re.MULTILINE | re.IGNORECASE))
    bdd_text: dict[str, str] = {}
    for index, match in enumerate(bdd_matches):
        end = bdd_matches[index + 1].start() if index + 1 < len(bdd_matches) else len(requirement)
        bdd_text[f"BDD-{index + 1:03d}"] = requirement[match.start():end]
    def grams(value: str) -> set[str]:
        compact = re.sub(r"\s+", "", value)
        return {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}
    case_ids = {case["case_id"] for case in cases}
    point_ids = [str(item["test_point_id"]) for item in matrix]
    atomic_ids = [str(item["atomic_rule_id"]) for item in matrix]
    if len(point_ids) != len(set(point_ids)): failures.append("duplicate test_point_id")
    if len(atomic_ids) != len(set(atomic_ids)): failures.append("duplicate atomic_rule_id")
    mapped_cases = [item["case_id"] for item in matrix if item["disposition"] == "case"]
    if len(mapped_cases) != len(set(mapped_cases)): failures.append("one case is mapped by multiple test points")
    by_case = {item["case_id"]: item for item in matrix if item["disposition"] == "case"}
    titles = [case["title"] for case in cases]
    if len(titles) != len(set(titles)): failures.append("duplicate testcase title")
    normalized_gwt: list[str] = []
    for case in cases:
        point = by_case.get(case["case_id"])
        if point is None: failures.append(f"{case['case_id']} has no test point"); continue
        fields = case["fields"]
        if fields.get("关联需求点") != point["test_point_id"]: failures.append(f"{case['case_id']} mapping mismatch")
        if str(point["function_point_id"]) not in known_fp: failures.append(f"{case['case_id']} unknown function point")
        if point["bdd_scenario_id"] is not None and point["bdd_scenario_id"] not in known_bdd: failures.append(f"{case['case_id']} unknown BDD scenario")
        if point["bdd_scenario_id"] in bdd_text and len(grams(str(point["rule_text"]) + str(point["given"]) + str(point["when"]) + str(point["then"])) & grams(bdd_text[str(point["bdd_scenario_id"])])) < 2: failures.append(f"{case['case_id']} BDD semantic mismatch")
        if str(point["source_evidence"]) not in requirement: failures.append(f"{case['case_id']} source evidence is not in requirement.md")
        gwt = "|".join(str(fields.get(name, "")).strip() for name in ("前置条件", "测试步骤", "预期结果"))
        if gwt in normalized_gwt: failures.append(f"{case['case_id']} duplicate/equivalent Given-When-Then")
        normalized_gwt.append(gwt)
        if any(term in gwt for term in vague_patterns): failures.append(f"{case['case_id']} vague assertion")
        quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", str(point["source_evidence"]))
        missing_quoted = [value for value in quoted if value not in str(point["then"])] if re.search(r"提示|文案|message|prompt", str(point["source_evidence"]), re.IGNORECASE) else []
        if missing_quoted: failures.append(f"{case['case_id']} does not preserve explicit requirement text: {missing_quoted}")
        if str(point["given"]).strip() != str(fields.get("前置条件", "")).strip() or str(point["when"]).strip() != str(fields.get("测试步骤", "")).strip() or str(point["then"]).strip() != str(fields.get("预期结果", "")).strip(): failures.append(f"{case['case_id']} GWT does not match matrix")
        if str(point["priority"]) != str(fields.get("用例等级", "")).strip(): failures.append(f"{case['case_id']} priority mismatch")
        if str(point["priority"]) == "P0" and not isinstance(point.get("risk_tags"), list): failures.append(f"{case['case_id']} P0 requires generic risk_tags")
        if isinstance(point.get("risk_tags"), list) and any(str(tag) not in allowed_risk_tags for tag in point["risk_tags"]): failures.append(f"{case['case_id']} has unsupported risk_tags")
    for item in matrix:
        if item["disposition"] == "case" and item["case_id"] not in case_ids: failures.append(f"{item['test_point_id']} references missing case")
        if item["disposition"] == "question" and not item["question_id"]: failures.append(f"{item['test_point_id']} missing question_id")
    matrix_text = " ".join(" ".join(str(item[field]) for field in ("rule_text", "given", "when", "then")) for item in matrix)
    for dimension, cues in scenario_cues.items():
        if any(cue in requirement for cue in cues) and not any(cue in matrix_text for cue in cues):
            failures.append(f"requirement has {dimension} rules but no matching test point")
    status = "FAILED" if failures else "PASSED"
    dimensions = {dimension: sum(1 for item in matrix if item["scenario_dimension"] == dimension) for dimension in sorted(DIMENSIONS)}
    priorities = {priority: sum(1 for item in matrix if item["priority"] == priority) for priority in ("P0", "P1", "P2")}
    report = "\n".join(["# Testcase Coverage Audit", "", f"- Status: {status}", f"- Function points: {len(known_fp)}", f"- BDD scenarios: {len(known_bdd)}", f"- Atomic rules: {len(set(atomic_ids))}", f"- Test points: {len(matrix)}", f"- Testcases: {len(cases)}", f"- Scenario dimensions: {dimensions}", f"- Priorities: {priorities}", "", "## Validation Failures", *([f"- {failure}" for failure in failures] or ["- None"])]) + "\n"
    return report, failures

def audit(output: Path | str, cases_text: str | None = None, existing_manifest: CoverageManifest | None = None) -> tuple[Any, ...]:
    if isinstance(output, Path):
        return audit_directory(output)
    manifest = extract_manifest(output, existing_manifest)
    failures: list[str] = []
    text = cases_text or ""
    known_fp = {item["id"] for item in manifest["function_points"]}
    known_bdd = {item["id"] for item in manifest["bdd_scenarios"]}
    referenced_fp: set[str] = set(); referenced_bdd: set[str] = set()
    for case in re.finditer(r"###\s+(TC\d{3})[\s\S]*?(?=###|\Z)", text):
        case_id = case.group(1); block = case.group(0)
        ref_match = re.search(r"(?:关联需求点|\u5173\u8054\u9700\u6c42\u70b9).*?((?:FP|BDD)-\d+(?:\s+(?:FP|BDD)-\d+)*)", block)
        refs = re.findall(r"(?:FP|BDD)-\d+", ref_match.group(1) if ref_match else "")
        fps = [ref for ref in refs if ref.startswith("FP-")]; bdds = [ref for ref in refs if ref.startswith("BDD-")]
        if len(fps) > 1: failures.append(f"{case_id} 关联了多个功能点: {fps}")
        if len(bdds) > 1: failures.append(f"{case_id} 关联了多个 BDD 场景: {bdds}")
        for ref in fps:
            if ref not in known_fp: failures.append(f"{case_id} 引用了未知功能点: {ref}")
            referenced_fp.add(ref)
        for ref in bdds:
            if ref not in known_bdd: failures.append(f"{case_id} 引用了未知 BDD 场景: {ref}")
            referenced_bdd.add(ref)
    failures.extend(f"未覆盖功能点: {item['id']}" for item in manifest["function_points"] if item["id"] not in referenced_fp)
    failures.extend(f"未覆盖 BDD 场景: {item['id']}" for item in manifest["bdd_scenarios"] if item["id"] not in referenced_bdd)
    return "# Testcase Coverage Audit\n", failures, manifest

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("output_directory", type=Path); args = parser.parse_args(argv)
    try:
        report, failures = audit_directory(args.output_directory)
        (args.output_directory / "testcase_coverage.md").write_text(report, encoding="utf-8")
        if failures:
            print("Coverage audit failed:\n" + "\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
            return 1
        print("Coverage audit passed."); return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Coverage audit failed: {error}", file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
