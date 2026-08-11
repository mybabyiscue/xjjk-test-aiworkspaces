"""Build the auditable one-test-point-to-one-case matrix."""
from __future__ import annotations
import json, re, sys
from pathlib import Path

def main() -> int:
    output = Path(sys.argv[1])
    rules_path = Path(__file__).resolve().parents[1] / "references" / "audit-rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    scenario_cues = {str(key): tuple(str(item) for item in value) for key, value in dict(rules["scenario_cues"]).items()}
    requirement = (output / "requirement.md").read_text(encoding="utf-8")
    payload = json.loads((output / "tapd_cases.json").read_text(encoding="utf-8"))
    markdown = (output / "test_cases.md").read_text(encoding="utf-8")
    blocks = list(re.finditer(r"^###\s+(TC\d{3})\s+-\s+(.+)$", markdown, re.M))
    cases = payload["cases"]
    md_by_id = {}
    for index, heading in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(markdown)
        block = markdown[heading.end():end]
        fields = {m.group(1).strip(): m.group(2).strip() for m in re.finditer(r"^-\s+\*\*([^*]+)\*\*\s*[：:]\s*(.*?)\s*$", block, re.M)}
        def list_field(name: str) -> list[str]:
            match = re.search(rf"^-\s+\*\*{re.escape(name)}\*\*\s*[：:]\s*\n([\s\S]*?)(?=^-\s+\*\*|\Z)", block, re.M)
            return [item.strip() for item in re.findall(r"^\s*\d+\.\s+(.+)$", match.group(1), re.M)] if match else []
        md_by_id[heading.group(1)] = {"title": heading.group(2).strip(), "fields": fields, "steps": list_field("测试步骤"), "expected": list_field("预期结果")}
    for case in cases:
        md = md_by_id.get(case["case_id"])
        if md:
            case["title"] = md["title"]
            if md["steps"]: case["steps"] = md["steps"]
            if md["expected"]: case["expected_results"] = md["expected"]
    previous = {}
    matrix_path = output / "testpoint_matrix.json"
    if matrix_path.exists():
        previous = {str(x["case_id"]): x for x in json.loads(matrix_path.read_text(encoding="utf-8"))}
    fp_lines = re.findall(r"^\d+\. \*\*(FP[^*：:]+|[^*]+)\*\*：(.+)$", requirement, re.M)
    evidence = {f"FP-{i+1:03d}": f"{title}：{body}" for i, (title, body) in enumerate(fp_lines)}
    bdd_matches = list(re.finditer(r"^###\s+(?:场景|Scenario)[^\n]+", requirement, re.M | re.I))
    bdd_blocks = []
    for bdd_index, match in enumerate(bdd_matches):
        end = bdd_matches[bdd_index + 1].start() if bdd_index + 1 < len(bdd_matches) else len(requirement)
        bdd_blocks.append((f"BDD-{bdd_index + 1:03d}", requirement[match.start():end]))
    def grams(value: str) -> set[str]:
        compact = re.sub(r"\s+", "", value)
        return {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}
    section_match = re.search(r"^##\s+([^\n]*(?:核心功能点|core function)[^\n]*)$", requirement, re.M | re.I)
    source_section = section_match.group(1).strip() if section_match else "Core function points"
    dimensions = {"功能测试": "Happy Path", "异常测试": "Alternative/Error", "兼容性测试": "Compatibility"}
    matrix = []
    for index, case in enumerate(cases, 1):
        refs = str(case["requirement_points"][0]).split()
        prior_fp = str(previous.get(case["case_id"], {}).get("function_point_id") or "")
        referenced_fp = next((x for x in refs if x.startswith("FP-")), "")
        fp = prior_fp or referenced_fp
        if not fp:
            raise ValueError(f"missing explicit function-point mapping: {case['case_id']}")
        bdd = previous.get(case["case_id"], {}).get("bdd_scenario_id") or next((x for x in refs if x.startswith("BDD-")), None)
        if bdd is None and bdd_blocks:
            case_grams = grams(" ".join([str(case["title"]), str(case["precondition"]), " ".join(case["steps"]), " ".join(case["expected_results"])]))
            scored = [(len(case_grams & grams(block)), bdd_id) for bdd_id, block in bdd_blocks]
            best_score, best_bdd = max(scored)
            bdd = best_bdd if best_score >= 2 else None
        text_blob = " ".join([str(case["title"]), str(case["precondition"]), " ".join(case["steps"]), " ".join(case["expected_results"])])
        if any(token in text_blob for token in scenario_cues.get("Alternative/Error", ())):
            dim = "Alternative/Error"
        elif any(token in text_blob for token in scenario_cues.get("Edge Cases", ())):
            dim = "Edge Cases"
        else:
            dim = dimensions.get(case["case_type"], "Happy Path")
        tp = str(previous.get(case["case_id"], {}).get("test_point_id") or f"TP-{index:03d}")
        source_line = next((line.strip() for line in requirement.splitlines() if line.strip().startswith(f"{int(fp.split('-')[-1])}.")), "")
        if not source_line:
            raise ValueError(f"missing source evidence for {fp}")
        source_line = source_line.split("（依据", 1)[0].rstrip()
        prior_atomic = str(previous.get(case["case_id"], {}).get("atomic_rule_id") or "")
        atomic_id = prior_atomic if prior_atomic else f"{fp}-R{index:02d}"
        prior_layer = str(previous.get(case["case_id"], {}).get("active_layer") or "")
        if not prior_layer:
            raise ValueError(f"missing explicit active layer: {case['case_id']}")
        prior_tags = previous.get(case["case_id"], {}).get("risk_tags")
        if case["priority"] == "P0" and (not isinstance(prior_tags, list) or not prior_tags):
            raise ValueError(f"missing explicit P0 risk_tags: {case['case_id']}")
        risk_tags = prior_tags if isinstance(prior_tags, list) else []
        matrix.append({"test_point_id": tp, "function_point_id": fp, "atomic_rule_id": atomic_id, "bdd_scenario_id": bdd, "rule_text": case["title"], "source_section": source_section, "source_evidence": source_line, "active_layer": prior_layer, "scenario_dimension": dim, "given": case["precondition"], "when": "；".join(case["steps"]), "then": "；".join(case["expected_results"]), "priority": case["priority"], "risk_tags": risk_tags, "disposition": "case", "question_id": None, "case_id": case["case_id"]})
        case["requirement_points"] = [tp]
    (output / "testpoint_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "function_points": [{"id": fp, "title": fp} for fp in sorted({str(item["function_point_id"]) for item in matrix})],
        "bdd_scenarios": [{"id": bdd, "title": bdd} for bdd in sorted({str(item["bdd_scenario_id"]) for item in matrix if item["bdd_scenario_id"]})],
        "atomic_rules": [{"id": str(item["atomic_rule_id"]), "function_point_id": str(item["function_point_id"]), "rule_text": str(item["rule_text"]), "source_evidence": str(item["source_evidence"])} for item in matrix],
    }
    (output / "coverage_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "tapd_cases.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = (output / "test_cases.md").read_text(encoding="utf-8")
    for case in cases:
        pattern = rf"(###\s+{case['case_id']}\s+-[\s\S]*?- \*\*关联需求点\*\*：)\s*[^\r\n]*"
        md, count = re.subn(pattern, rf"\g<1>{case['requirement_points'][0]}", md, count=1)
        if count != 1: raise ValueError(f"无法更新 Markdown 映射: {case['case_id']}")
    (output / "test_cases.md").write_text(md, encoding="utf-8")
    return 0
if __name__ == "__main__": raise SystemExit(main())
