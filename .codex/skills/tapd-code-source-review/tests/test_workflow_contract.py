"""Regression tests for workflow gates and evidence isolation."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from scripts.analyze_testcase_evidence import (
    analyze_route_consistency,
    apply_gateway_prefixes,
    build_http_mapping_pattern,
    confirmed_tables,
    entry_intersects_changed_ranges,
    find_block_end,
    map_cases_to_entries,
    parse_test_cases,
    scan_java_entries,
    select_business_entries,
    unresolved_tables,
)
from scripts.prepare_review_run import parse_gateway_prefix_candidates, validate_gateway_evidence, validate_source_scopes
from scripts.workflow_contract import validate_gateway_evidence_integrity
from scripts.workflow_contract import (
    CORE_PROCESS_INTERFACE_HEADERS,
    TABLE_INFORMATION_HEADERS,
    UNIT_TEST_INTERFACE_HEADERS,
    markdown_table_lines,
    parse_code_url,
    sha256_file,
    validate_approved_source_run,
    validate_review_document_schemas,
    write_json,
)


def test_review_document_schemas_accept_canonical_first_tables(tmp_path: Path) -> None:
    documents = {
        "unit_test_interfaces.md": UNIT_TEST_INTERFACE_HEADERS,
        "core_process_interfaces.md": CORE_PROCESS_INTERFACE_HEADERS,
        "table_information.md": TABLE_INFORMATION_HEADERS,
    }
    for filename, headers in documents.items():
        header, separator = markdown_table_lines(headers)
        (tmp_path / filename).write_text(
            f"# Evidence\n\n{header}\n{separator}\n",
            encoding="utf-8",
        )

    validate_review_document_schemas(tmp_path)


def test_review_document_schemas_reject_legacy_table(tmp_path: Path) -> None:
    documents = {
        "unit_test_interfaces.md": UNIT_TEST_INTERFACE_HEADERS,
        "core_process_interfaces.md": CORE_PROCESS_INTERFACE_HEADERS,
        "table_information.md": TABLE_INFORMATION_HEADERS,
    }
    for filename, headers in documents.items():
        header, separator = markdown_table_lines(headers)
        (tmp_path / filename).write_text(
            f"# Evidence\n\n{header}\n{separator}\n",
            encoding="utf-8",
        )
    (tmp_path / "unit_test_interfaces.md").write_text(
        "# Legacy\n\n| HTTP | 完整路由 | 参数 | 用途 | 源码证据 |\n"
        "|---|---|---|---|---|\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unit_test_interfaces.md"):
        validate_review_document_schemas(tmp_path)


def test_parse_code_url_requires_explicit_git_branch() -> None:
    url, source_type, branch = parse_code_url(
        "https://git.example/team/service.git#feature-123", ""
    )

    assert url == "https://git.example/team/service.git"
    assert source_type == "git"
    assert branch == "feature-123"

    with pytest.raises(ValueError, match="explicit #branch"):
        parse_code_url("https://git.example/team/service.git", "")

    url, source_type, branch = parse_code_url(
        "https://cnb.cool/xjjk/sharkcloud/services/mall4cloud/-/tree/feature-sku-75483", ""
    )
    assert url == "https://cnb.cool/xjjk/sharkcloud/services/mall4cloud.git"
    assert source_type == "git"
    assert branch == "feature-sku-75483"

    url, source_type, branch = parse_code_url(
        "https://cnb.cool/xjjk/sharkcloud/services/mall4cloud", "feature-sku-75483"
    )
    assert url == "https://cnb.cool/xjjk/sharkcloud/services/mall4cloud.git"
    assert source_type == "git"
    assert branch == "feature-sku-75483"


def test_source_approval_is_bound_to_manifest_and_codegraph(tmp_path: Path) -> None:
    run_dir = tmp_path / "source_run"
    cache_dir = tmp_path / "cache"
    (cache_dir / ".codegraph").mkdir(parents=True)
    (cache_dir / ".codegraph" / "codegraph.db").write_bytes(b"index")
    run_dir.mkdir()
    manifest_path = run_dir / "source_manifest.json"
    write_json(manifest_path, {
        "source_run_id": "source-1",
        "code_sources": [{
            "service_id": "service_001",
            "source_type": "git",
            "branch": "feature-123",
            "commit": "abc123",
            "cache_path": str(cache_dir),
            "fetch_status": "success",
            "codegraph_status": "healthy",
        }],
    })
    write_json(run_dir / "code_source_confirmation.json", {
        "approved": True,
        "source_run_id": "source-1",
        "manifest_sha256": sha256_file(manifest_path),
    })

    validate_approved_source_run(run_dir)

    manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after approval"):
        validate_approved_source_run(run_dir)


def test_source_gate_rejects_failed_service(tmp_path: Path) -> None:
    run_dir = tmp_path / "source_run"
    run_dir.mkdir()
    manifest_path = run_dir / "source_manifest.json"
    write_json(manifest_path, {
        "source_run_id": "source-1",
        "code_sources": [{
            "service_id": "service_001",
            "source_type": "git",
            "branch": "feature-123",
            "commit": "",
            "cache_path": "",
            "fetch_status": "failed",
            "codegraph_status": "",
        }],
    })
    write_json(run_dir / "code_source_confirmation.json", {
        "approved": True,
        "source_run_id": "source-1",
        "manifest_sha256": sha256_file(manifest_path),
    })

    with pytest.raises(ValueError, match="not fetched successfully"):
        validate_approved_source_run(run_dir)


def test_source_scope_is_repository_relative_and_existing(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    module = repository / "module-a"
    module.mkdir(parents=True)
    sources = [{"service_id": "service_001", "cache_path": str(repository)}]

    assert validate_source_scopes(sources, {"service_001": "module-a"}) == {
        "service_001": "module-a"
    }
    assert validate_source_scopes(sources, {}) == {"service_001": ""}
    with pytest.raises(ValueError, match="repository-relative"):
        validate_source_scopes(sources, {"service_001": "../outside"})
    with pytest.raises(FileNotFoundError, match="does not exist"):
        validate_source_scopes(sources, {"service_001": "missing"})


def test_gateway_prefix_and_table_resolution_are_isolated() -> None:
    entries = [{"service_id": "service_001", "route": "/mp/activity"}]
    services = [{"service_id": "service_001", "gateway_prefix": "/product"}]
    tables = [
        {"table_name": "confirmed_table", "status": "confirmed", "grade": "B"},
        {"table_name": "unknown_table", "status": "metadata_unresolved", "grade": "C"},
    ]

    updated = apply_gateway_prefixes(entries, services)

    assert updated[0]["route"] == "/product/mp/activity"
    assert updated[0]["controller_route"] == "/mp/activity"
    assert [table["table_name"] for table in confirmed_tables(tables)] == ["confirmed_table"]
    assert [table["table_name"] for table in unresolved_tables(tables)] == ["unknown_table"]


def test_gateway_evidence_requires_exact_normalized_prefix(tmp_path: Path) -> None:
    evidence_path = tmp_path / "gateway.yml"
    evidence_path.write_text("Path=/wx/mp/**\n", encoding="utf-8")

    assert parse_gateway_prefix_candidates("Path=/wx/mp/**") == ["/wx/mp"]
    validate_gateway_evidence("/wx/mp", f"{evidence_path}:1", "service_alpha", "service", "")
    with pytest.raises(ValueError, match="gateway_evidence_unresolved"):
        validate_gateway_evidence("/wx", f"{evidence_path}:1", "service_alpha", "service", "")
    with pytest.raises(ValueError, match="gateway_evidence_unresolved"):
        validate_gateway_evidence("/mp", f"{evidence_path}:1", "service_alpha", "service", "")


def test_route_consistency_detects_conflict_match_ambiguity_and_unverified() -> None:
    entries = [
        {"service_id": "service_alpha", "controller_route": "/cp/externalInfo/init", "applied_gateway_prefix": "/wx", "gateway_source": "service", "gateway_rule_path_fragment": "", "gateway_evidence": "gateway.yml:1", "composed_route": "/wx/cp/externalInfo/init", "file": "src/Controller.java", "line": 10},
        {"service_id": "service_alpha", "controller_route": "/cp/group/list", "applied_gateway_prefix": "/wx/mp", "gateway_source": "rule", "gateway_rule_path_fragment": "module_alpha", "gateway_evidence": "gateway.yml:2", "composed_route": "/wx/mp/cp/group/list", "file": "module_alpha/Controller.java", "line": 20},
        {"service_id": "service_alpha", "controller_route": "/health", "applied_gateway_prefix": "/api", "gateway_source": "service", "gateway_rule_path_fragment": "", "gateway_evidence": "gateway.yml:1", "composed_route": "/api/health", "file": "HealthController.java", "line": 30},
    ]
    consumers = [
        {"service_id": "service_alpha", "route": "/wx/mp/cp/externalInfo/init", "file": "src/api.ts", "line": 5},
        {"service_id": "service_alpha", "route": "/wx/mp/cp/group/list", "file": "module_alpha/api.ts", "line": 6},
        {"service_id": "service_alpha", "route": "/wx/cp/group/list", "file": "legacy/api.ts", "line": 7},
    ]

    result = analyze_route_consistency(entries, consumers)
    statuses = [str(item["status"]) for item in result["routes"]]
    assert statuses == ["gateway_route_conflict", "ambiguous_gateway_route", "unverified"]
    assert result["route_conflict_count"] == 1
    assert result["ambiguous_route_count"] == 1
    assert result["route_unverified_count"] == 1


def test_route_consistency_matches_confirmed_prefix() -> None:
    result = analyze_route_consistency(
        [{"service_id": "service_alpha", "controller_route": "/cp/externalInfo/init", "applied_gateway_prefix": "/wx/mp", "gateway_source": "service", "gateway_rule_path_fragment": "", "gateway_evidence": "gateway.yml:1", "composed_route": "/wx/mp/cp/externalInfo/init", "file": "Controller.java", "line": 10}],
        [{"service_id": "service_alpha", "route": "/wx/mp/cp/externalInfo/init", "file": "api.ts", "line": 5}],
    )
    assert result["routes"][0]["status"] == "matched"
    assert result["routes"][0]["candidate_prefixes"] == ["/wx/mp"]


def test_gateway_evidence_integrity_detects_file_and_line_changes(tmp_path: Path) -> None:
    evidence_path = tmp_path / "gateway.yml"
    evidence_path.write_text("Path=/api/**\n", encoding="utf-8")
    record = validate_gateway_evidence("/api", f"{evidence_path}:1", "service_alpha", "service", "")
    context = {"gateway_evidence": {"service_alpha": record}, "gateway_evidence_rules": {}}
    manifest = {"code_sources": []}
    validate_gateway_evidence_integrity(context, manifest)
    evidence_path.write_text("Path=/changed/**\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Gateway evidence file changed"):
        validate_gateway_evidence_integrity(context, manifest)
    record["evidence_file_sha256"] = sha256_file(evidence_path)
    with pytest.raises(ValueError, match="Gateway evidence line changed"):
        validate_gateway_evidence_integrity(context, manifest)


def test_gateway_prefix_uses_longest_matching_module_rule() -> None:
    entries = [
        {
            "service_id": "service_001",
            "file": "cache/mall4cloud-product/src/StartLimitActivityController.java",
            "route": "/mp/start_limit_activity",
        },
        {
            "service_id": "service_001",
            "file": "cache/mall4cloud-order/src/OrderController.java",
            "route": "/order/confirm",
        },
        {
            "service_id": "service_001",
            "file": "cache/other/src/HealthController.java",
            "route": "/health",
        },
    ]
    services = [{
        "service_id": "service_001",
        "gateway_prefix": "",
        "gateway_prefix_rules": [
            {"path_fragment": "mall4cloud-product", "prefix": "/product"},
            {"path_fragment": "mall4cloud-order", "prefix": "/order"},
        ],
    }]

    updated = apply_gateway_prefixes(entries, services)

    assert [entry["route"] for entry in updated] == [
        "/product/mp/start_limit_activity",
        "/order/order/confirm",
        "/health",
    ]


def test_business_tokens_select_entries_when_cases_have_no_routes() -> None:
    entries = [
        {
            "service_id": "service_001",
            "route": "/mp/start_limit_activity",
            "tokens": ["起售限购活动", "活动保存"],
        },
        {
            "service_id": "service_001",
            "route": "/health",
            "tokens": ["健康检查"],
        },
    ]
    cases = [{"case_id": "TC001", "routes": [], "tokens": ["起售限购活动", "活动保存"]}]
    policy = {
        "minimum_score": 3,
        "max_inferred_cases_per_entry": 5,
        "max_inferred_entries_per_case": 3,
        "minimum_token_document_frequency": 1,
        "maximum_token_document_frequency_ratio": 1.0,
        "minimum_scored_token_length": 4,
        "minimum_chinese_scored_token_length": 2,
        "long_token_length": 5,
        "short_token_weight": 2,
        "long_token_weight": 3,
        "chinese_token_weight": 6,
        "action_only_alias_keys": ["save", "update"],
        "unmatched_identifier_penalty": 15,
        "identifier_aliases": {},
    }

    selected = select_business_entries(entries, cases, set())
    mapped = map_cases_to_entries(selected, cases, policy)

    assert [entry["route"] for entry in mapped] == ["/mp/start_limit_activity"]
    assert mapped[0]["case_ids"] == ["TC001"]


def test_identifier_aliases_map_changed_purchase_routes_to_chinese_cases() -> None:
    entries = [
        {"service_id": "service_001", "route": "/product/ma/spu/prod_info", "tokens": ["prod", "info"]},
        {"service_id": "service_001", "route": "/product/shop_cart/info", "tokens": ["shop", "cart", "info"]},
        {"service_id": "service_001", "route": "/order/order/confirm", "tokens": ["order", "confirm"]},
        {"service_id": "service_001", "route": "/order/order/submit", "tokens": ["order", "submit"]},
        {"service_id": "service_001", "route": "/product/inner/start_limit_activity/validate_submit", "tokens": ["start", "limit", "validate", "submit"]},
        {"service_id": "service_001", "route": "/product/inner/start_limit_activity/rules", "tokens": ["start", "limit", "rules"]},
    ]
    cases = [
        {"case_id": "TC001", "routes": [], "tokens": ["商品详情"]},
        {"case_id": "TC002", "routes": [], "tokens": ["购物车"]},
        {"case_id": "TC003", "routes": [], "tokens": ["结算"]},
        {"case_id": "TC004", "routes": [], "tokens": ["提交订单", "提交"]},
        {"case_id": "TC005", "routes": [], "tokens": ["起售", "限购", "校验"]},
        {"case_id": "TC006", "routes": [], "tokens": ["起售", "限购", "规则"]},
    ]
    policy = {
        "minimum_score": 6,
        "max_inferred_cases_per_entry": 5,
        "max_inferred_entries_per_case": 3,
        "minimum_token_document_frequency": 1,
        "maximum_token_document_frequency_ratio": 1.0,
        "minimum_scored_token_length": 4,
        "minimum_chinese_scored_token_length": 2,
        "long_token_length": 5,
        "short_token_weight": 2,
        "long_token_weight": 3,
        "chinese_token_weight": 6,
        "action_only_alias_keys": ["save", "update"],
        "unmatched_identifier_penalty": 15,
        "identifier_aliases": {
            "cart": ["购物车"],
            "confirm": ["结算"],
            "info": ["商品详情"],
            "limit": ["限购"],
            "rules": ["规则"],
            "start": ["起售"],
            "submit": ["提交"],
            "validate": ["校验"],
        },
    }

    mapped = map_cases_to_entries(entries, cases, policy)

    assert {entry["route"] for entry in mapped} == {entry["route"] for entry in entries}


def test_action_only_alias_does_not_map_unrelated_save_entry() -> None:
    entries = [
        {"route": "/warehouse", "tokens": ["warehouse", "save", "保存"], "identifier_tokens": ["warehouse", "save"]},
        {"route": "/spu", "tokens": ["sku", "save", "保存"], "identifier_tokens": ["sku", "save"]},
        {"route": "/spu_price_log", "tokens": ["sku", "save", "保存"], "identifier_tokens": ["sku", "price", "log", "save"]},
    ]
    cases = [{"case_id": "TC001", "routes": [], "tokens": ["sku", "保存"]}]
    policy = {
        "minimum_score": 2,
        "max_inferred_cases_per_entry": 5,
        "max_inferred_entries_per_case": 3,
        "minimum_token_document_frequency": 1,
        "maximum_token_document_frequency_ratio": 1.0,
        "minimum_scored_token_length": 3,
        "minimum_chinese_scored_token_length": 2,
        "long_token_length": 5,
        "short_token_weight": 2,
        "long_token_weight": 3,
        "chinese_token_weight": 6,
        "action_only_alias_keys": ["save", "update"],
        "unmatched_identifier_penalty": 15,
        "identifier_aliases": {"save": ["保存"], "update": ["更新", "保存"]},
    }

    mapped = map_cases_to_entries(entries, cases, policy)

    assert [entry["route"] for entry in mapped] == ["/spu"]


def test_route_free_cases_keep_all_source_entries(tmp_path: Path) -> None:
    controller = tmp_path / "OrderController.java"
    controller.write_text("class OrderController {}\n", encoding="utf-8")
    entries = [
        {"route": "/order/confirm", "file": str(controller), "line": 20, "end_line": 80},
        {"route": "/order/pay_info", "file": str(controller), "line": 100, "end_line": 120},
    ]
    changed_ranges = {str(controller.resolve()).casefold(): [(45, 50)]}

    selected = select_business_entries(entries, [{"routes": []}], changed_ranges)

    assert [entry["route"] for entry in selected] == ["/order/confirm", "/order/pay_info"]
    assert entry_intersects_changed_ranges(entries[0], changed_ranges)
    assert not entry_intersects_changed_ranges(entries[1], changed_ranges)


def test_method_declaration_pattern_handles_throws_clause() -> None:
    policy_path = Path(__file__).resolve().parents[1] / "assets" / "review-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    pattern = re.compile(policy["interface_detection"]["method_declaration_pattern"])
    source = """
public String confirm(OrderDTO request) throws ExecutionException, InterruptedException {
    if (request != null) {
        return "ok";
    }
    return "empty";
}
""".strip()

    declaration = pattern.search(source)

    assert declaration is not None
    assert declaration.group(2) == "confirm"
    assert find_block_end(source, declaration.end() - 1) == len(source)


def test_interface_tokens_exclude_method_body_business_noise(tmp_path: Path) -> None:
    policy_path = Path(__file__).resolve().parents[1] / "assets" / "review-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    interface_policy = policy["interface_detection"]
    source = """
@RestController
@RequestMapping("/order")
public class OrderController {
    /** 订单结算。 */
    @PostMapping("/confirm")
    public String confirm(OrderDTO request) {
        String unrelated = "退款售后库存优惠券";
        return unrelated;
    }
}
""".strip()
    controller = tmp_path / "OrderController.java"
    controller.write_text(source, encoding="utf-8")

    entries = scan_java_entries(
        [{"service_id": "service_001", "path": str(controller), "text": source}],
        {},
        build_http_mapping_pattern(interface_policy["http_mapping_annotations"]),
        re.compile(interface_policy["method_declaration_pattern"]),
        interface_policy,
        set(policy["case_matching"]["generic_tokens"]),
        2,
        6,
        3,
    )

    assert "结算" in entries[0]["tokens"]
    assert "退款" not in entries[0]["tokens"]


def test_testcase_parser_stops_before_next_section(tmp_path: Path) -> None:
    test_cases = tmp_path / "test_cases.md"
    test_cases.write_text(
        "# 测试用例\n\n## 三、P2\n\n### TC001 - 活动查询\n"
        "- **预期结果**：返回活动\n\n## BLAST 测试点矩阵\n\n"
        "| TP001 | TC001 | unrelated appendix token |\n",
        encoding="utf-8",
    )

    cases = parse_test_cases(
        test_cases,
        re.compile(r"^###\s+(TC\d{3})\s+-\s+(.+)$", re.MULTILINE),
        re.compile(r"(/[A-Za-z0-9_./{}-]+)"),
        set(),
        2,
        6,
        3,
    )

    assert "unrelated appendix token" not in cases[0]["body"]


def test_http_mapping_pattern_accepts_annotations_without_arguments() -> None:
    pattern = build_http_mapping_pattern(["GetMapping", "PostMapping"])

    no_arguments = pattern.search("@GetMapping\npublic String detail()")
    with_arguments = pattern.search('@PostMapping("/save")')

    assert no_arguments is not None
    assert no_arguments.group(1) == "GetMapping"
    assert no_arguments.group(2) is None
    assert with_arguments is not None
    assert with_arguments.group(2) == '"/save"'
