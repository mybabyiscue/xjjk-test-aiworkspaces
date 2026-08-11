# 执行工作流

从工作区根目录运行。默认路径兼容五步流水线；所有输入、输出和 skill 路径都可以通过命令参数覆盖。示例中的环境名、连接名和域名都是占位。

## 前置输入

- `output/test_cases.md`
- `output/tapd_cases.json`
- `output/latest/testcase_confirmation.json`
- `output/code_review/latest/evidence_index.json`
- `output/code_review/latest/unit_test_interfaces.md`
- `output/code_review/latest/table_information.md`
- `output/code_review/latest/core_process_interfaces.md`
- `output/code_review/latest/source_manifest.json`
- `config/environments_config.json`
- `config/credentials.local.json`
- `config/connections.json`

## Stage 4 必出物

- `output/test_preparation/query_plan.json`：由 `scripts/generate_query_plan.py` 生成。
- `output/test_preparation/model_mapping.json`：由 `scripts/generate_model_mapping.py` 生成。
- `output/test_preparation/preparation_assessment.json`
- `output/interface_test_preparation.md`
- `output/non_interface_cases.md`
- `output/integration_test_flow.md`
- `output/test_data_manifest.md`

如果 `query_plan.json` 或 `model_mapping.json` 的 `generation_report.status` 不是 `ready`，初始化阶段必须阻断，并打印缺少的证据项。

## PowerShell 示例

```powershell
$skillPath = Resolve-Path '.codex/skills/tapd-prepare-test-from'
$preparationPath = 'output/test_preparation'
New-Item -ItemType Directory -Force -Path $preparationPath | Out-Null

python "$skillPath/scripts/validate_confirmed_input.py" `
  --confirmation 'output/latest/testcase_confirmation.json' `
  --test-cases 'output/test_cases.md' `
  --tapd-cases 'output/tapd_cases.json' `
  --evidence-index 'output/code_review/latest/evidence_index.json' `
  --unit-interface-evidence 'output/code_review/latest/unit_test_interfaces.md' `
  --core-interface-evidence 'output/code_review/latest/core_process_interfaces.md' `
  --table-evidence 'output/code_review/latest/table_information.md' `
  --code-evidence 'output/code_review/latest/source_manifest.json' `
  --environment-name '<confirmed environment>' `
  --api-domain '<configured api domain>' `
  --healthcheck-url '<configured healthcheck url>' `
  --token-error-codes '<configured token code>' `
  --output "$preparationPath/confirmed_input_snapshot.json"

python "$skillPath/scripts/generate_query_plan.py" `
  --evidence-index 'output/code_review/latest/evidence_index.json' `
  --unit-interface-evidence 'output/code_review/latest/unit_test_interfaces.md' `
  --core-interface-evidence 'output/code_review/latest/core_process_interfaces.md' `
  --table-evidence 'output/code_review/latest/table_information.md' `
  --connection-name '<confirmed read-only connection>' `
  --max-rows 20 `
  --output "$preparationPath/query_plan.json"

python "$skillPath/scripts/generate_model_mapping.py" `
  --evidence-index 'output/code_review/latest/evidence_index.json' `
  --tapd-cases 'output/tapd_cases.json' `
  --unit-interface-evidence 'output/code_review/latest/unit_test_interfaces.md' `
  --core-interface-evidence 'output/code_review/latest/core_process_interfaces.md' `
  --table-evidence 'output/code_review/latest/table_information.md' `
  --output "$preparationPath/model_mapping.json"

python "$skillPath/scripts/initialize_preparation_assessment.py" `
  --snapshot "$preparationPath/confirmed_input_snapshot.json" `
  --tapd-cases 'output/tapd_cases.json' `
  --query-plan "$preparationPath/query_plan.json" `
  --model-mapping "$preparationPath/model_mapping.json" `
  --output "$preparationPath/preparation_assessment_shell.json"

python "$skillPath/scripts/execute_read_query_plan.py" `
  --connections 'config/connections.json' `
  --connection-name '<confirmed read-only connection>' `
  --plan "$preparationPath/query_plan.json" `
  --output "$preparationPath/real_data_records.json" `
  --manifest 'output/test_data_manifest.md'

python "$skillPath/scripts/build_assessment_from_model.py" `
  --assessment-shell "$preparationPath/preparation_assessment_shell.json" `
  --model-mapping "$preparationPath/model_mapping.json" `
  --real-data "$preparationPath/real_data_records.json" `
  --output "$preparationPath/preparation_assessment.json"

python "$skillPath/scripts/validate_preparation_assessment.py" `
  --assessment "$preparationPath/preparation_assessment.json" `
  --snapshot "$preparationPath/confirmed_input_snapshot.json" `
  --tapd-cases 'output/tapd_cases.json' `
  --report "$preparationPath/preparation_validation_report.json"

python "$skillPath/scripts/render_three_documents.py" `
  --assessment "$preparationPath/preparation_assessment.json" `
  --snapshot "$preparationPath/confirmed_input_snapshot.json" `
  --output-dir 'output'

python "$skillPath/scripts/build_api_execution_plan.py" `
  --assessment "$preparationPath/preparation_assessment.json" `
  --plan 'output/test_execution/execution_plan.json' `
  --report "$preparationPath/api_execution_plan_report.json"

python "$skillPath/scripts/check_no_business_hardcoding.py" `
  --skill-dir "$skillPath" `
  --report "$preparationPath/hardcoding_scan_report.json"
```

## Bash 示例

```bash
skill_path="$(pwd)/.codex/skills/tapd-prepare-test-from"
preparation_path="output/test_preparation"
mkdir -p "$preparation_path"

python "$skill_path/scripts/validate_confirmed_input.py" \
  --confirmation output/latest/testcase_confirmation.json \
  --test-cases output/test_cases.md \
  --tapd-cases output/tapd_cases.json \
  --evidence-index output/code_review/latest/evidence_index.json \
  --unit-interface-evidence output/code_review/latest/unit_test_interfaces.md \
  --core-interface-evidence output/code_review/latest/core_process_interfaces.md \
  --table-evidence output/code_review/latest/table_information.md \
  --code-evidence output/code_review/latest/source_manifest.json \
  --environment-name '<confirmed environment>' \
  --api-domain '<configured api domain>' \
  --healthcheck-url '<configured healthcheck url>' \
  --token-error-codes '<configured token code>' \
  --output "$preparation_path/confirmed_input_snapshot.json"

python "$skill_path/scripts/generate_query_plan.py" \
  --evidence-index output/code_review/latest/evidence_index.json \
  --unit-interface-evidence output/code_review/latest/unit_test_interfaces.md \
  --core-interface-evidence output/code_review/latest/core_process_interfaces.md \
  --table-evidence output/code_review/latest/table_information.md \
  --connection-name '<confirmed read-only connection>' \
  --max-rows 20 \
  --output "$preparation_path/query_plan.json"

python "$skill_path/scripts/generate_model_mapping.py" \
  --evidence-index output/code_review/latest/evidence_index.json \
  --tapd-cases output/tapd_cases.json \
  --unit-interface-evidence output/code_review/latest/unit_test_interfaces.md \
  --core-interface-evidence output/code_review/latest/core_process_interfaces.md \
  --table-evidence output/code_review/latest/table_information.md \
  --output "$preparation_path/model_mapping.json"

python "$skill_path/scripts/initialize_preparation_assessment.py" \
  --snapshot "$preparation_path/confirmed_input_snapshot.json" \
  --tapd-cases output/tapd_cases.json \
  --query-plan "$preparation_path/query_plan.json" \
  --model-mapping "$preparation_path/model_mapping.json" \
  --output "$preparation_path/preparation_assessment_shell.json"

python "$skill_path/scripts/execute_read_query_plan.py" \
  --connections config/connections.json \
  --connection-name '<confirmed read-only connection>' \
  --plan "$preparation_path/query_plan.json" \
  --output "$preparation_path/real_data_records.json" \
  --manifest output/test_data_manifest.md

python "$skill_path/scripts/build_assessment_from_model.py" \
  --assessment-shell "$preparation_path/preparation_assessment_shell.json" \
  --model-mapping "$preparation_path/model_mapping.json" \
  --real-data "$preparation_path/real_data_records.json" \
  --output "$preparation_path/preparation_assessment.json"

python "$skill_path/scripts/validate_preparation_assessment.py" \
  --assessment "$preparation_path/preparation_assessment.json" \
  --snapshot "$preparation_path/confirmed_input_snapshot.json" \
  --tapd-cases output/tapd_cases.json \
  --report "$preparation_path/preparation_validation_report.json"

python "$skill_path/scripts/render_three_documents.py" \
  --assessment "$preparation_path/preparation_assessment.json" \
  --snapshot "$preparation_path/confirmed_input_snapshot.json" \
  --output-dir output

python "$skill_path/scripts/build_api_execution_plan.py" \
  --assessment "$preparation_path/preparation_assessment.json" \
  --plan output/test_execution/execution_plan.json \
  --report "$preparation_path/api_execution_plan_report.json"

python "$skill_path/scripts/check_no_business_hardcoding.py" \
  --skill-dir "$skill_path" \
  --report "$preparation_path/hardcoding_scan_report.json"
```

## 处理规则

真实查询没有返回所需记录时，禁止构造假记录。按顺序选择：真实 API 创建、受控 SQL 创建、人工创建后复查、阻断。

生成 `output/test_execution/execution_plan.json` 后，第五步必须直接消费该计划，并使用同一构建函数从当前 assessment 重建比较；不得从 Markdown 或聊天上下文修补计划。
