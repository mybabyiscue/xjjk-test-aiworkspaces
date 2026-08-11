"""Negative and cross-domain regression tests for the atomic audit contract."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_testcase_coverage import audit_directory

def make_output(case_title: str, expected: str, duplicate_point: bool = False) -> Path:
    root = Path(tempfile.mkdtemp(prefix="tapd-audit-"))
    requirement = """## Core function points\n1. **Inventory**: every item is listed\n## Acceptance Criteria\n### Scenario: inventory\n- Given data\n- When viewing\n- Then item is listed\n"""
    md = f"""# Testcases\n## P0\n### TC001 - {case_title}\n- **用例名称**：{case_title}\n- **用例目录**：inventory\n- **需求ID**：1\n- **用例类型**：功能测试\n- **用例状态**：正常\n- **用例等级**：P0\n- **所属端/角色/系统**：operator\n- **功能模块**：inventory\n- **前置条件**：data\n- **测试步骤**：\n  1. view\n- **预期结果**：\n  1. {expected}\n- **关联需求点**：TP-001\n- **备注**：risk\n"""
    root.joinpath("requirement.md").write_text(requirement, encoding="utf-8")
    root.joinpath("test_cases.md").write_text(md, encoding="utf-8")
    point_id = "TP-001" if not duplicate_point else "TP-002"
    matrix = [{"test_point_id": point_id, "function_point_id": "FP-001", "atomic_rule_id": "FP-001-R01", "bdd_scenario_id": "BDD-001", "rule_text": case_title, "source_section": "Core function points", "source_evidence": "1. **Inventory**: every item is listed", "active_layer": "UI", "scenario_dimension": "Happy Path", "given": "data", "when": "view", "then": expected, "priority": "P0", "risk_tags": ["data_consistency"], "disposition": "case", "question_id": None, "case_id": "TC999" if duplicate_point else "TC001"}]
    root.joinpath("testpoint_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
    return root

class AtomicAuditTests(unittest.TestCase):
    def test_vague_assertion_fails(self) -> None:
        report, failures = audit_directory(make_output("Inventory", "显示正常"))
        self.assertTrue(any("vague" in failure for failure in failures))

    def test_explicit_assertion_passes_for_unrelated_domain(self) -> None:
        report, failures = audit_directory(make_output("Invoice", "页面显示编号 INV-001"))
        self.assertEqual(failures, [])

    def test_missing_case_mapping_fails(self) -> None:
        report, failures = audit_directory(make_output("Inventory", "页面显示编号 INV-001", True))
        self.assertTrue(any("no test point" in failure for failure in failures))

    def test_duplicate_given_when_then_fails(self) -> None:
        root = make_output("Inventory", "页面显示编号 INV-001")
        text = root.joinpath("test_cases.md").read_text(encoding="utf-8")
        root.joinpath("test_cases.md").write_text(text + text.replace("TC001", "TC002"), encoding="utf-8")
        matrix = json.loads(root.joinpath("testpoint_matrix.json").read_text(encoding="utf-8"))
        matrix.append({**matrix[0], "test_point_id": "TP-002", "atomic_rule_id": "FP-001-R02", "case_id": "TC002"})
        root.joinpath("testpoint_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
        report, failures = audit_directory(root)
        self.assertTrue(any("duplicate/equivalent" in failure for failure in failures))

    def test_untrusted_source_evidence_fails(self) -> None:
        root = make_output("Inventory", "页面显示编号 INV-001")
        matrix = json.loads(root.joinpath("testpoint_matrix.json").read_text(encoding="utf-8"))
        matrix[0]["source_evidence"] = "invented evidence"
        root.joinpath("testpoint_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
        report, failures = audit_directory(root)
        self.assertTrue(any("source evidence" in failure for failure in failures))

    def test_wrong_bdd_mapping_fails(self) -> None:
        root = make_output("Inventory", "页面显示编号 INV-001")
        matrix = json.loads(root.joinpath("testpoint_matrix.json").read_text(encoding="utf-8"))
        matrix[0]["bdd_scenario_id"] = "BDD-999"
        root.joinpath("testpoint_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
        report, failures = audit_directory(root)
        self.assertTrue(any("unknown BDD" in failure for failure in failures))

    def test_p0_without_risk_tags_fails(self) -> None:
        root = make_output("Inventory", "页面显示编号 INV-001")
        matrix = json.loads(root.joinpath("testpoint_matrix.json").read_text(encoding="utf-8"))
        del matrix[0]["risk_tags"]
        root.joinpath("testpoint_matrix.json").write_text(json.dumps(matrix), encoding="utf-8")
        report, failures = audit_directory(root)
        self.assertTrue(any("risk_tags" in failure for failure in failures))

    def test_markdown_mapping_mismatch_fails(self) -> None:
        root = make_output("Inventory", "页面显示编号 INV-001")
        text = root.joinpath("test_cases.md").read_text(encoding="utf-8").replace("TP-001", "TP-999")
        root.joinpath("test_cases.md").write_text(text, encoding="utf-8")
        report, failures = audit_directory(root)
        self.assertTrue(any("mapping mismatch" in failure for failure in failures))

if __name__ == "__main__":
    unittest.main()
