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
- `config/connections.json`

## Stage 4 必出物

## 2. Token Gate

用户确认单个 API 环境后执行。脚本只按环境具备的 `token_probe` 或登录字段选择能力，不按名称、域名或业务码分支：

```powershell
$skillPath = Resolve-Path '.codex/skills/tapd-prepare-test-from'
python "$skillPath/scripts/validate_environment_token.py" `
  --config 'config/environments_config.json' `
  --environment-name '<用户确认的环境名>' `
  --request-timeout-seconds 10 `
  --retry-count 3 `
  --browser-timeout-ms 30000
```

此命令失败时停止。不得把 `api_domain` 根地址当作探测端点，不得猜测响应业务码、登录定位信息或 Token 格式。脚本成功续期时只原子更新所选环境的 `authorization`。

## 3. 确认输入快照
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

<<<<<<< HEAD
## 4. 初始化评估壳
=======
>>>>>>> 9975fae79a679d0938403de25a07f0a7b635f594
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

<<<<<<< HEAD
## 5. 生成并执行只读查询计划

按照 [assessment-contract.md](assessment-contract.md) 生成 `$preparationPath/query_plan.json`。每个表和字段必须来自 `table_information.md`，查询目的必须关联用例。禁止执行未经用户确认数据库平台的查询。

```powershell
=======
>>>>>>> 9975fae79a679d0938403de25a07f0a7b635f594
python "$skillPath/scripts/execute_read_query_plan.py" `
  --connections 'config/connections.json' `
  --connection-name '<confirmed read-only connection>' `
  --plan "$preparationPath/query_plan.json" `
  --output "$preparationPath/real_data_records.json" `
  --manifest 'output/test_data_manifest.md'

<<<<<<< HEAD
真实查询没有返回正向用例所需记录时，禁止构造假记录。按以下顺序处理：

1. 使用有源码证据的真实业务 API 生成 `api_create` setup，并提供对应 HTTP 或受控 SQL cleanup。
2. 没有稳定 API 且直接写入不绕过被测行为时，使用用户另行确认的 `controlled-write` 测试连接生成参数化单条 `sql_insert` setup 和按 `TEST_` 标识清理的 `sql_delete` cleanup。
3. 无法自动创建时输出 `manual_create` 步骤；人工完成后重新运行只读查询，只有查询返回真实记录才能继续。
4. 只有数据无法安全创建、无法清理或缺少接口/表证据时才标记 `blocked`。

禁止任何 Mock、Fake、Stub 或 Mock seed。新增功能测试不得预创建本次要由被测接口创建的目标对象，只准备真实上游依赖；更新、删除和状态流转用例应创建隔离的真实目标对象。

## 6. 生成评估映射

读取评估壳、用例、全部代码审查证据和真实查询记录，按照 [assessment-contract.md](assessment-contract.md) 生成 `$preparationPath/model_mapping.json`。不从 URL 名称猜测 HTTP Method，不新增需求或代码中不存在的断言。

```powershell
=======
>>>>>>> 9975fae79a679d0938403de25a07f0a7b635f594
python "$skillPath/scripts/build_assessment_from_model.py" `
  --assessment-shell "$preparationPath/preparation_assessment_shell.json" `
  --model-mapping "$preparationPath/model_mapping.json" `
  --real-data "$preparationPath/real_data_records.json" `
  --output "$preparationPath/preparation_assessment.json"

<<<<<<< HEAD
## 7. 校验并渲染

```powershell
=======
>>>>>>> 9975fae79a679d0938403de25a07f0a7b635f594
python "$skillPath/scripts/validate_preparation_assessment.py" `
  --assessment "$preparationPath/preparation_assessment.json" `
  --snapshot "$preparationPath/confirmed_input_snapshot.json" `
  --tapd-cases 'output/tapd_cases.json' `
  --report "$preparationPath/preparation_validation_report.json"

python "$skillPath/scripts/render_three_documents.py" `
  --assessment "$preparationPath/preparation_assessment.json" `
  --snapshot "$preparationPath/confirmed_input_snapshot.json" `
  --output-dir 'output'

<<<<<<< HEAD
## 8. 生成唯一执行计划

准备产物要移交第五步接口执行时，直接生成执行器消费的唯一计划：

```powershell
=======
>>>>>>> 9975fae79a679d0938403de25a07f0a7b635f594
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
