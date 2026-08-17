"""Attach requirement-scoped DTO bytecode evidence to a prepared review run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "SpuDTO": ("spuId", "skuList"),
    "SkuDTO": (
        "skuId",
        "spuId",
        "weight",
        "volume",
        "dosage",
        "singleBottleNumber",
        "expirationControl",
        "shelfLife",
    ),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_fields(javap_path: Path, jar_path: Path, class_name: str) -> list[dict[str, object]]:
    result = subprocess.run(
        [str(javap_path), "-classpath", str(jar_path), "-private", class_name],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    simple_name = class_name.rsplit(".", 1)[-1]
    selected = set(TARGET_FIELDS.get(simple_name, ()))
    fields: list[dict[str, object]] = []
    pattern = re.compile(r"^\s+private\s+(?!static\s)([^;]+?)\s+([A-Za-z_$][\w$]*);$")
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match is None or match.group(2) not in selected:
            continue
        fields.append({"name": match.group(2), "type": match.group(1)})
    missing = selected - {str(field["name"]) for field in fields}
    if missing:
        raise ValueError(f"DTO bytecode fields are missing from {class_name}: {', '.join(sorted(missing))}")
    return fields


def append_evidence_section(path: Path, coordinate: str, jar_hash: str, classes: dict[str, list[dict[str, object]]]) -> None:
    marker = "## 三、Maven DTO 字节码证据"
    text = path.read_text(encoding="utf-8-sig")
    if marker in text:
        text = text.split(marker, 1)[0].rstrip() + "\n"
    lines = [
        "",
        marker,
        "",
        f"- 依赖坐标：`{coordinate}`",
        f"- JAR SHA-256：`{jar_hash}`",
        "- 证据方式：JDK `javap -private` 读取发布制品字段签名；未推测源码字段。",
        "",
        "| DTO字段路径 | Java类型 | 必填证据 | 证据位置 |",
        "|---|---|---|---|",
    ]
    for class_name, fields in classes.items():
        for field in fields:
            lines.append(f"| {class_name}.{field['name']} | {field['type']} | 当前证据不判定必填性 | {coordinate}#{class_name}.{field['name']} |")
    path.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach DTO bytecode evidence to a code-review run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--jar", required=True)
    parser.add_argument("--javap", required=True)
    parser.add_argument("--coordinate", required=True)
    arguments = parser.parse_args()

    run_dir = Path(arguments.run_dir).resolve()
    jar_path = Path(arguments.jar).resolve()
    javap_path = Path(arguments.javap).resolve()
    if not run_dir.is_dir() or not jar_path.is_file() or not javap_path.is_file():
        raise ValueError("Run directory, dependency JAR, and javap executable must exist.")

    class_names = {
        "SpuDTO": "com.mall4j.cloud.common.product.dto.SpuDTO",
        "SkuDTO": "com.mall4j.cloud.common.product.dto.SkuDTO",
    }
    classes = {simple: parse_fields(javap_path, jar_path, qualified) for simple, qualified in class_names.items()}
    sku_fields = [dict(field) for field in classes["SkuDTO"]]
    spu_fields = [dict(field) for field in classes["SpuDTO"]]
    for field in spu_fields:
        if field["name"] == "skuList":
            field["fields"] = sku_fields

    interface_path = run_dir / "raw" / "testcase_interface_evidence.json"
    interface_payload = read_json(interface_path)
    interfaces = interface_payload.get("interfaces")
    if not isinstance(interfaces, list):
        raise ValueError("testcase_interface_evidence.json.interfaces must be an array.")
    updated = 0
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        params = interface.get("params")
        if not isinstance(params, list):
            continue
        for parameter in params:
            if isinstance(parameter, dict) and parameter.get("type") == "SpuDTO":
                parameter["fields"] = spu_fields
                parameter["bytecode_evidence"] = "raw/dependency_dto_evidence.json#SpuDTO"
                updated += 1
    if updated == 0:
        raise ValueError("No SpuDTO interface parameter was found to enrich.")
    write_json(interface_path, interface_payload)

    jar_hash = file_sha256(jar_path)
    evidence = {
        "coordinate": arguments.coordinate,
        "jar_path": str(jar_path),
        "jar_sha256": jar_hash,
        "extraction_command": "javap -private",
        "classes": {
            simple: {"qualified_name": class_names[simple], "fields": fields}
            for simple, fields in classes.items()
        },
        "updated_interface_parameter_count": updated,
    }
    write_json(run_dir / "raw" / "dependency_dto_evidence.json", evidence)
    append_evidence_section(run_dir / "unit_test_interfaces.md", arguments.coordinate, jar_hash, classes)
    print(json.dumps({"updated_interfaces": updated, "jar_sha256": jar_hash}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
