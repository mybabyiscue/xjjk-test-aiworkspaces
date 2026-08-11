"""Regression tests for structured testcase coverage validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIRECTORY: Path = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from audit_testcase_coverage import CoverageManifest, audit, extract_manifest
from validate_outputs import (
    JsonObject,
    StoryIdentity,
    extract_story_identity,
    validate_story_identity,
)


BASE_REQUIREMENT: str = """## 八、核心功能点与可测性要求
1. 功能甲
2. 功能乙

## 十三、BDD 验收标准
- **场景一：成功路径**
- **场景二：拒绝路径**
"""


class CoverageAuditTests(unittest.TestCase):
    def test_heading_bdd_scenario_is_extracted(self) -> None:
        requirement: str = """## Core functions
1. Function A

## Acceptance Criteria
### Scenario: success
- Given a condition
- When an action occurs
- Then a result is visible
"""

        manifest: CoverageManifest = extract_manifest(requirement, None)

        self.assertEqual(
            manifest["bdd_scenarios"],
            [{"id": "BDD-001", "title": "Scenario: success"}],
        )

    def test_empty_bdd_section_fails(self) -> None:
        requirement: str = """## 核心功能点
1. 功能甲

## BDD 验收标准
- Given 条件
- When 操作
- Then 结果
"""

        with self.assertRaisesRegex(ValueError, "未提取到任何场景"):
            extract_manifest(requirement, None)

    def test_one_case_cannot_claim_multiple_function_and_bdd_ids(self) -> None:
        cases: str = """### TC001 - 单用例覆盖全部需求
- **关联需求点**：FP-001 FP-002 BDD-001 BDD-002
"""

        _report, failures, _manifest = audit(BASE_REQUIREMENT, cases, None)

        self.assertIn("TC001 关联了多个功能点: ['FP-001', 'FP-002']", failures)
        self.assertIn("TC001 关联了多个 BDD 场景: ['BDD-001', 'BDD-002']", failures)

    def test_ids_outside_reference_field_do_not_count_as_coverage(self) -> None:
        cases: str = """### TC001 - 仅覆盖一个功能点
- **关联需求点**：FP-001
- **备注**：FP-002 BDD-001 BDD-002
"""

        _report, failures, _manifest = audit(BASE_REQUIREMENT, cases, None)

        self.assertIn("未覆盖功能点: FP-002", failures)
        self.assertIn("未覆盖 BDD 场景: BDD-001", failures)
        self.assertIn("未覆盖 BDD 场景: BDD-002", failures)

    def test_unknown_reference_id_fails(self) -> None:
        cases: str = """### TC001 - 引用未知功能点
- **关联需求点**：FP-999
"""

        _report, failures, _manifest = audit(BASE_REQUIREMENT, cases, None)

        self.assertIn("TC001 引用了未知功能点: FP-999", failures)

    def test_existing_ids_survive_insertion(self) -> None:
        original_manifest: CoverageManifest = extract_manifest(BASE_REQUIREMENT, None)
        revised_requirement: str = BASE_REQUIREMENT.replace(
            "1. 功能甲\n2. 功能乙",
            "1. 新功能\n2. 功能甲\n3. 功能乙",
        )

        revised_manifest: CoverageManifest = extract_manifest(
            revised_requirement,
            original_manifest,
        )

        ids_by_title: dict[str, str] = {
            item["title"]: item["id"]
            for item in revised_manifest["function_points"]
        }
        self.assertEqual(ids_by_title["功能甲"], "FP-001")
        self.assertEqual(ids_by_title["功能乙"], "FP-002")
        self.assertEqual(ids_by_title["新功能"], "FP-003")

    def test_story_identity_must_match_requirement(self) -> None:
        story: JsonObject = {
            "workspace_id": "10000000",
            "id": "1110000000000000001",
            "short_id": "1000001",
            "name": "错误需求",
        }
        expected: StoryIdentity = {
            "workspace_id": "10000000",
            "id": "1110000000000000002",
            "short_id": "1000002",
            "name": "真实需求",
        }

        with self.assertRaisesRegex(ValueError, "story.id 与 requirement.md 不一致"):
            validate_story_identity(story, expected)

    def test_requirement_story_identifiers_must_be_numeric(self) -> None:
        requirement: str = """## 一、需求来源
- workspace_id：workspace-a
- TAPD 需求完整 ID：story-a
- TAPD 需求短号：short-a

## 二、需求基础信息
| 字段 | 内容 |
|---|---|
| 原始需求标题 | 测试需求 |
"""

        with self.assertRaisesRegex(ValueError, "workspace_id 必须是纯数字标识"):
            extract_story_identity(requirement)


if __name__ == "__main__":
    unittest.main()
