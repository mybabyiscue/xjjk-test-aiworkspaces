"""Validate generated testcase artifacts against the skill contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence, TypedDict, TypeAlias
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from export_test_cases_excel import HEADERS, SHEET_NAME, ExcelRow, build_excel_rows
from audit_testcase_coverage import audit


JsonObject: TypeAlias = dict[str, object]
MarkdownCase: TypeAlias = dict[str, object]


class StoryIdentity(TypedDict):
    workspace_id: str
    id: str
    short_id: str
    name: str

CASE_HEADING_PATTERN: re.Pattern[str] = re.compile(
    r"^###\s+(TC\d{3})\s+-\s+(.+)$",
    re.MULTILINE,
)
FIELD_PATTERN: re.Pattern[str] = re.compile(r"^- \*\*([^*]+)\*\*：(.*)$", re.MULTILINE)
SECTION_PATTERN: re.Pattern[str] = re.compile(r"^##\s+[一二三]、(P[012])\b", re.MULTILINE)
LIST_ITEM_PATTERN: re.Pattern[str] = re.compile(r"^\s+\d+\.\s+(.+)$", re.MULTILINE)

REQUIRED_MARKDOWN_FIELDS: tuple[str, ...] = (
    "用例名称",
    "用例目录",
    "需求ID",
    "用例类型",
    "用例状态",
    "用例等级",
    "所属端/角色/系统",
    "功能模块",
    "前置条件",
    "测试步骤",
    "预期结果",
    "关联需求点",
    "备注",
)
REQUIRED_QUESTION_HEADER: str = (
    "| 编号 | 关联功能点/模块 | 问题类型 | 具体问题描述 | 来源 | 建议确认方 |"
)
ROOT_KEYS: frozenset[str] = frozenset({"story", "total_count", "cases"})
STORY_KEYS: frozenset[str] = frozenset({"workspace_id", "id", "short_id", "name"})
CASE_KEYS: frozenset[str] = frozenset(
    {
        "case_id",
        "title",
        "directory",
        "requirement_id",
        "case_type",
        "case_status",
        "priority",
        "system_scope",
        "module",
        "precondition",
        "steps",
        "expected_results",
        "requirement_points",
        "remarks",
    }
)
CASE_TYPES: frozenset[str] = frozenset({"功能测试", "性能测试", "安全性测试"})
CASE_STATUSES: frozenset[str] = frozenset({"正常"})
PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2"})
PLACEHOLDERS: tuple[str, ...] = (
    "TODO",
    "[Given]",
    "[When]",
    "[Then]",
    "[业务目录]",
    "[功能模块]",
    "[TAPD short_id]",
)
WORKSPACE_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^- workspace_id\s*[：:]\s*(\S+)\s*$",
    re.MULTILINE,
)
FULL_STORY_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^- TAPD 需求完整 ID\s*[：:]\s*(\S+)\s*$",
    re.MULTILINE,
)
SHORT_STORY_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^- TAPD 需求短号\s*[：:]\s*(\S+)\s*$",
    re.MULTILINE,
)
STORY_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^\|\s*原始需求标题\s*\|\s*(.*?)\s*\|\s*$",
    re.MULTILINE,
)


def read_text(path: Path) -> str:
    try:
        content: str = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ValueError(f"无法读取文件 {path}: {error}") from error
    if not content.strip():
        raise ValueError(f"文件不能为空: {path}")
    return content


def read_json_object(path: Path) -> JsonObject:
    content: str = read_text(path)
    try:
        value: object = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON 格式错误 {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return value


def require_exact_keys(value: JsonObject, expected: frozenset[str], field_name: str) -> None:
    actual: frozenset[str] = frozenset(value.keys())
    missing: list[str] = sorted(expected - actual)
    extra: list[str] = sorted(actual - expected)
    if missing or extra:
        raise ValueError(
            f"{field_name} 字段不符合契约；缺失={missing}，额外={extra}。"
        )


def require_object(value: object, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象。")
    return value


def require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是数组。")
    return value


def require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串。")
    normalized: str = value.strip()
    for placeholder in PLACEHOLDERS:
        if placeholder in normalized:
            raise ValueError(f"{field_name} 包含占位符 {placeholder}。")
    return normalized


def require_string_list(value: object, field_name: str) -> list[str]:
    raw_items: list[object] = require_list(value, field_name)
    if not raw_items:
        raise ValueError(f"{field_name} 不能为空数组。")
    return [
        require_string(item, f"{field_name}[{index}]")
        for index, item in enumerate(raw_items)
    ]


def extract_single_value(
    requirement: str,
    pattern: re.Pattern[str],
    field_name: str,
) -> str:
    matches: list[re.Match[str]] = list(pattern.finditer(requirement))
    if len(matches) != 1:
        raise ValueError(
            f"requirement.md 中 {field_name} 必须且只能出现一次，实际 {len(matches)} 次。"
        )
    return require_string(matches[0].group(1), f"requirement.md.{field_name}")


def extract_numeric_identifier(
    requirement: str,
    pattern: re.Pattern[str],
    field_name: str,
) -> str:
    value: str = extract_single_value(requirement, pattern, field_name)
    if not value.isdecimal():
        raise ValueError(f"requirement.md.{field_name} 必须是纯数字标识，实际={value!r}。")
    return value


def extract_story_identity(requirement: str) -> StoryIdentity:
    return {
        "workspace_id": extract_numeric_identifier(
            requirement,
            WORKSPACE_ID_PATTERN,
            "workspace_id",
        ),
        "id": extract_numeric_identifier(
            requirement,
            FULL_STORY_ID_PATTERN,
            "TAPD 需求完整 ID",
        ),
        "short_id": extract_numeric_identifier(
            requirement,
            SHORT_STORY_ID_PATTERN,
            "TAPD 需求短号",
        ),
        "name": extract_single_value(
            requirement,
            STORY_NAME_PATTERN,
            "原始需求标题",
        ),
    }


def validate_story_identity(story: JsonObject, expected: StoryIdentity) -> None:
    for field_name in ("workspace_id", "id", "short_id", "name"):
        actual_value: str = require_string(story.get(field_name), f"story.{field_name}")
        expected_value: str = expected[field_name]
        if actual_value != expected_value:
            raise ValueError(
                f"story.{field_name} 与 requirement.md 不一致；"
                f"期望={expected_value!r}，实际={actual_value!r}。"
            )


def section_priority(markdown: str, position: int) -> str:
    matches: list[re.Match[str]] = [
        match for match in SECTION_PATTERN.finditer(markdown) if match.start() < position
    ]
    if not matches:
        raise ValueError(f"位置 {position} 的用例不在 P0/P1/P2 章节中。")
    return matches[-1].group(1)


def extract_numbered_list(block: str, field_name: str, case_id: str) -> list[str]:
    marker: str = f"- **{field_name}**："
    start: int = block.find(marker)
    if start < 0:
        raise ValueError(f"{case_id} 缺少字段 {field_name}。")
    remainder: str = block[start + len(marker) :]
    next_field: int = remainder.find("\n- **")
    section: str = remainder if next_field < 0 else remainder[:next_field]
    items: list[str] = [match.group(1).strip() for match in LIST_ITEM_PATTERN.finditer(section)]
    if not items:
        raise ValueError(f"{case_id}.{field_name} 必须包含非空有序列表。")
    return items


def parse_markdown_cases(markdown: str) -> list[MarkdownCase]:
    headings: list[re.Match[str]] = list(CASE_HEADING_PATTERN.finditer(markdown))
    if not headings:
        raise ValueError("test_cases.md 未找到任何 `### TCxxx - 标题` 用例。")

    cases: list[MarkdownCase] = []
    for index, heading in enumerate(headings):
        block_end: int = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        block: str = markdown[heading.end() : block_end]
        fields: dict[str, str] = {
            match.group(1).strip(): match.group(2).strip()
            for match in FIELD_PATTERN.finditer(block)
        }
        case_id: str = heading.group(1)
        for field_name in REQUIRED_MARKDOWN_FIELDS:
            if field_name not in fields:
                raise ValueError(f"{case_id} 缺少字段 {field_name}。")
        for field_name in REQUIRED_MARKDOWN_FIELDS:
            if field_name not in {"测试步骤", "预期结果"}:
                require_string(fields[field_name], f"{case_id}.{field_name}")

        title: str = heading.group(2).strip()
        if fields["用例名称"] != title:
            raise ValueError(f"{case_id}.用例名称必须与标题完全一致。")
        priority: str = fields["用例等级"]
        if priority != section_priority(markdown, heading.start()):
            raise ValueError(f"{case_id}.用例等级与所在章节不一致。")

        cases.append(
            {
                "case_id": case_id,
                "title": title,
                "directory": fields["用例目录"],
                "requirement_id": fields["需求ID"],
                "case_type": fields["用例类型"],
                "case_status": fields["用例状态"],
                "priority": priority,
                "system_scope": fields["所属端/角色/系统"],
                "module": fields["功能模块"],
                "precondition": fields["前置条件"],
                "steps": extract_numbered_list(block, "测试步骤", case_id),
                "expected_results": extract_numbered_list(block, "预期结果", case_id),
                "requirement_points": fields["关联需求点"],
                "remarks": fields["备注"],
            }
        )
    return cases


def require_markdown_string(case: MarkdownCase, field_name: str, case_id: str) -> str:
    return require_string(case.get(field_name), f"{case_id}.{field_name}")


def require_markdown_list(case: MarkdownCase, field_name: str, case_id: str) -> list[str]:
    return require_string_list(case.get(field_name), f"{case_id}.{field_name}")


def validate_case_sequence(markdown_cases: list[MarkdownCase]) -> None:
    for index, case in enumerate(markdown_cases, start=1):
        expected: str = f"TC{index:03d}"
        actual: str = require_markdown_string(case, "case_id", expected)
        if actual != expected:
            raise ValueError(f"用例编号不连续；期望 {expected}，实际 {actual}。")


def validate_json_case(
    raw_case: object,
    markdown_case: MarkdownCase,
    index: int,
    expected_requirement_id: str,
) -> None:
    field_prefix: str = f"cases[{index}]"
    case: JsonObject = require_object(raw_case, field_prefix)
    require_exact_keys(case, CASE_KEYS, field_prefix)
    case_id: str = require_string(case.get("case_id"), f"{field_prefix}.case_id")

    scalar_fields: tuple[str, ...] = (
        "case_id",
        "title",
        "directory",
        "requirement_id",
        "case_type",
        "case_status",
        "priority",
        "system_scope",
        "module",
        "precondition",
        "remarks",
    )
    for field_name in scalar_fields:
        json_value: str = require_string(case.get(field_name), f"{field_prefix}.{field_name}")
        markdown_value: str = require_markdown_string(markdown_case, field_name, case_id)
        if json_value != markdown_value:
            raise ValueError(
                f"{case_id}.{field_name} 在 Markdown 与 JSON 中不一致。"
            )

    case_type: str = require_string(case.get("case_type"), f"{field_prefix}.case_type")
    case_status: str = require_string(case.get("case_status"), f"{field_prefix}.case_status")
    priority: str = require_string(case.get("priority"), f"{field_prefix}.priority")
    if case_type not in CASE_TYPES:
        raise ValueError(f"{case_id}.case_type 不合法: {case_type}")
    if case_status not in CASE_STATUSES:
        raise ValueError(f"{case_id}.case_status 不合法: {case_status}")
    if priority not in PRIORITIES:
        raise ValueError(f"{case_id}.priority 不合法: {priority}")
    requirement_id: str = require_string(
        case.get("requirement_id"),
        f"{field_prefix}.requirement_id",
    )
    if requirement_id != expected_requirement_id:
        raise ValueError(
            f"{case_id}.requirement_id 与 story.short_id 不一致；"
            f"期望={expected_requirement_id!r}，实际={requirement_id!r}。"
        )

    for field_name in ("steps", "expected_results"):
        json_values: list[str] = require_string_list(
            case.get(field_name),
            f"{field_prefix}.{field_name}",
        )
        markdown_values: list[str] = require_markdown_list(markdown_case, field_name, case_id)
        if json_values != markdown_values:
            raise ValueError(
                f"{case_id}.{field_name} 在 Markdown 与 JSON 中不一致。"
            )

    requirement_points: list[str] = require_string_list(
        case.get("requirement_points"),
        f"{field_prefix}.requirement_points",
    )
    markdown_requirement_point: str = require_markdown_string(
        markdown_case,
        "requirement_points",
        case_id,
    )
    if requirement_points != [markdown_requirement_point]:
        raise ValueError(
            f"{case_id}.requirement_points 在 Markdown 与 JSON 中不一致；"
            f"Markdown={[markdown_requirement_point]!r}，JSON={requirement_points!r}。"
        )


def validate_json_payload(
    payload: JsonObject,
    markdown_cases: list[MarkdownCase],
    story_identity: StoryIdentity,
) -> None:
    require_exact_keys(payload, ROOT_KEYS, "tapd_cases.json")
    story: JsonObject = require_object(payload.get("story"), "story")
    require_exact_keys(story, STORY_KEYS, "story")
    validate_story_identity(story, story_identity)

    raw_total_count: object = payload.get("total_count")
    if isinstance(raw_total_count, bool) or not isinstance(raw_total_count, int):
        raise ValueError("total_count 必须是整数。")
    raw_cases: list[object] = require_list(payload.get("cases"), "cases")
    if raw_total_count != len(raw_cases):
        raise ValueError("total_count 与 cases 数量不一致。")
    if len(raw_cases) != len(markdown_cases):
        raise ValueError("test_cases.md 与 tapd_cases.json 用例数量不一致。")
    if not raw_cases:
        raise ValueError("cases 不能为空。")

    for index, raw_case in enumerate(raw_cases):
        validate_json_case(
            raw_case,
            markdown_cases[index],
            index,
            story_identity["short_id"],
        )


def validate_questions(markdown: str) -> None:
    if not markdown.startswith("# 待确认问题清单"):
        raise ValueError("questions.md 缺少固定标题 `# 待确认问题清单`。")
    if REQUIRED_QUESTION_HEADER not in markdown:
        raise ValueError("questions.md 表头不符合唯一契约。")
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        if not line.startswith("|") or line.startswith("|---") or line == REQUIRED_QUESTION_HEADER:
            continue
        cells: list[str] = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 6:
            rows.append(cells)
    if not rows:
        raise ValueError("questions.md 必须包含问题行或‘无待确认问题’行。")
    no_question_rows: list[list[str]] = [row for row in rows if row[2] == "无待确认问题"]
    if no_question_rows:
        if len(rows) != 1 or no_question_rows[0][0] != "无":
            raise ValueError("‘无待确认问题’行不能与实际问题混用。")
        return
    ids: list[str] = [row[0] for row in rows]
    expected_ids: list[str] = [f"Q{index:03d}" for index in range(1, len(rows) + 1)]
    if ids != expected_ids:
        raise ValueError(f"questions.md 问题编号必须连续且唯一，期望={expected_ids}，实际={ids}。")
    allowed_types: set[str] = {"业务规则", "异常规则", "边界规则", "提示文案", "数据规则", "交互规则", "依赖范围", "技术实现"}
    for row in rows:
        if row[2] not in allowed_types:
            raise ValueError(f"{row[0]} 问题类型不合法: {row[2]}")
        if not row[1] or not row[3] or not row[4]:
            raise ValueError(f"{row[0]} 关联点、问题描述和来源不能为空。")


def require_excel_row(raw_row: tuple[object, ...], row_number: int) -> ExcelRow:
    if len(raw_row) != len(HEADERS):
        raise ValueError(
            f"test_cases.xlsx 第 {row_number} 行列数不符合契约；"
            f"期望 {len(HEADERS)}，实际 {len(raw_row)}。"
        )
    return tuple(
        require_string(value, f"test_cases.xlsx[{row_number},{column_number}]")
        for column_number, value in enumerate(raw_row, start=1)
    )


def validate_excel_workbook(path: Path, payload: JsonObject) -> None:
    if not path.is_file():
        raise ValueError(f"缺少 Excel 产物: {path}")
    try:
        workbook: Workbook = load_workbook(path, read_only=False, data_only=True)
    except (OSError, ValueError, BadZipFile, InvalidFileException) as error:
        raise ValueError(f"无法读取 Excel 文件 {path}: {error}") from error

    try:
        if workbook.sheetnames != [SHEET_NAME]:
            raise ValueError(
                f"test_cases.xlsx 工作表不符合契约；"
                f"期望={[SHEET_NAME]}，实际={workbook.sheetnames}。"
            )
        worksheet: Worksheet = workbook[SHEET_NAME]
        raw_header: tuple[object, ...] = tuple(
            worksheet.cell(row=1, column=index).value
            for index in range(1, len(HEADERS) + 1)
        )
        if raw_header != HEADERS:
            raise ValueError(
                f"test_cases.xlsx 表头不符合契约；期望={HEADERS}，实际={raw_header}。"
            )
        if worksheet.max_column != len(HEADERS):
            raise ValueError(
                f"test_cases.xlsx 包含额外列；"
                f"期望 {len(HEADERS)}，实际 {worksheet.max_column}。"
            )
        if str(worksheet.freeze_panes) != "A2":
            raise ValueError(
                f"test_cases.xlsx 必须冻结首行；实际 freeze_panes={worksheet.freeze_panes}。"
            )
        expected_filter: str = f"A1:N{worksheet.max_row}"
        if worksheet.auto_filter.ref != expected_filter:
            raise ValueError(
                f"test_cases.xlsx 自动筛选范围不符合契约；"
                f"期望={expected_filter}，实际={worksheet.auto_filter.ref}。"
            )
        if worksheet.merged_cells.ranges:
            raise ValueError("test_cases.xlsx 不允许包含合并单元格。")

        expected_rows: list[ExcelRow] = build_excel_rows(payload)
        if worksheet.max_row - 1 != len(expected_rows):
            raise ValueError(
                f"test_cases.xlsx 与 tapd_cases.json 用例数量不一致；"
                f"Excel={worksheet.max_row - 1}，JSON={len(expected_rows)}。"
            )

        for index, expected_row in enumerate(expected_rows, start=2):
            raw_row: tuple[object, ...] = tuple(
                worksheet.cell(row=index, column=column).value
                for column in range(1, len(HEADERS) + 1)
            )
            actual_row: ExcelRow = require_excel_row(raw_row, index)
            for column in range(1, len(HEADERS) + 1):
                cell: Cell = worksheet.cell(row=index, column=column)
                if cell.alignment.wrap_text is not True or cell.alignment.vertical != "top":
                    raise ValueError(
                        f"test_cases.xlsx[{index},{column}] 必须启用自动换行和顶端对齐。"
                    )
            if actual_row == expected_row:
                continue
            for column, (actual, expected) in enumerate(
                zip(actual_row, expected_row, strict=True),
                start=1,
            ):
                if actual != expected:
                    case_id: str = expected_row[0]
                    raise ValueError(
                        f"{case_id}.{HEADERS[column - 1]} 在 Excel 与 JSON 中不一致；"
                        f"Excel={actual!r}，JSON={expected!r}。"
                    )
    finally:
        workbook.close()


def validate_output_directory(output_directory: Path) -> None:
    test_cases_path: Path = output_directory / "test_cases.md"
    questions_path: Path = output_directory / "questions.md"
    tapd_cases_path: Path = output_directory / "tapd_cases.json"
    test_cases_excel_path: Path = output_directory / "test_cases.xlsx"
    requirement_path: Path = output_directory / "requirement.md"

    requirement_text: str = read_text(requirement_path)
    test_cases_markdown: str = read_text(test_cases_path)
    questions_markdown: str = read_text(questions_path)
    payload: JsonObject = read_json_object(tapd_cases_path)
    story_identity: StoryIdentity = extract_story_identity(requirement_text)
    markdown_cases: list[MarkdownCase] = parse_markdown_cases(test_cases_markdown)
    validate_case_sequence(markdown_cases)
    validate_questions(questions_markdown)
    validate_json_payload(payload, markdown_cases, story_identity)
    validate_excel_workbook(test_cases_excel_path, payload)
    coverage_report, coverage_failures = audit(output_directory)
    (output_directory / "testcase_coverage.md").write_text(coverage_report, encoding="utf-8")
    if coverage_failures:
        raise ValueError("coverage audit failed: " + "; ".join(coverage_failures))


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Validate tapd-testcase-generation output artifacts."
    )
    parser.add_argument("output_directory", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    arguments: argparse.Namespace = parse_arguments(argv)
    try:
        validate_output_directory(arguments.output_directory)
    except ValueError as error:
        print(f"Validation failed: {error}", file=sys.stderr)
        return 1
    print("Validation passed: Markdown, questions, JSON, and Excel artifacts match the contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
