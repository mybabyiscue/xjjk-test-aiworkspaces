"""Execute a validated read-only query plan and render real-data records."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ModuleNotFoundError:
    pymysql = None
    DictCursor = None


JsonObject = dict[str, object]
SAFE_DEFAULT_CHARSET: str = "utf8mb4"
SAFE_DEFAULT_CONNECT_TIMEOUT: int = 10
SAFE_DEFAULT_READ_TIMEOUT: int = 30
SAFE_DEFAULT_WRITE_TIMEOUT: int = 30
SAFE_DEFAULT_RETRY_ATTEMPTS: int = 1
SAFE_DEFAULT_RETRY_DELAY_SECONDS: float = 0.0
SAFE_DEFAULT_MAX_ROWS: int = 100
SUPPORTED_DATABASE_TYPES: frozenset[str] = frozenset({"mysql"})


class QueryPlanError(RuntimeError):
    """Raised when a query plan or connection configuration is invalid."""


class CursorProtocol(Protocol):
    def execute(self, query: str) -> object:
        ...

    def fetchall(self) -> object:
        ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> object:
        ...

    def begin(self) -> object:
        ...

    def rollback(self) -> object:
        ...

    def close(self) -> object:
        ...


def parse_arguments() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Execute a read-only database query plan.")
    parser.add_argument("--connections", required=True)
    parser.add_argument("--connection-name", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    return parser.parse_args()


def read_json_object(path: Path) -> JsonObject:
    value: object = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise QueryPlanError(f"JSON root must be an object: {path}")
    return value


def require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryPlanError(f"{field_name} must be a non-empty string")
    return value.strip()


def require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise QueryPlanError(f"{field_name} must be an array")
    return value


def require_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise QueryPlanError(f"{field_name} must be an integer")
    return value


def select_connection(payload: JsonObject, name: str) -> JsonObject:
    rows: list[object] = require_list(payload.get("connections"), "connections")
    for row in rows:
        if isinstance(row, dict) and row.get("name") == name:
            if row.get("enabled") is not True:
                raise QueryPlanError(f"Connection is not enabled: {name}")
            if row.get("access_mode") != "read-only":
                raise QueryPlanError(f"Read query requires access_mode=read-only: {name}")
            database_type: str = str(row.get("database_type", "mysql")).lower()
            if database_type not in SUPPORTED_DATABASE_TYPES:
                raise QueryPlanError(f"Unsupported database_type for read query: {database_type}")
            return row
    raise QueryPlanError(f"Enabled connection not found: {name}")


def validate_controlled_write_connection(config: JsonObject, environment_name: str, database: str, table: str) -> None:
    if config.get("enabled") is not True:
        raise QueryPlanError("Controlled write connection must be enabled")
    if config.get("access_mode") != "controlled-write":
        raise QueryPlanError("Controlled write connection requires access_mode=controlled-write")
    if require_string(config.get("environment_name"), "connection.environment_name") != environment_name:
        raise QueryPlanError("Controlled write connection is not bound to the selected environment")
    allowed_databases: list[object] = require_list(config.get("allowed_databases"), "connection.allowed_databases")
    allowed_tables: list[object] = require_list(config.get("allowed_tables"), "connection.allowed_tables")
    if database not in {item for item in allowed_databases if isinstance(item, str)}:
        raise QueryPlanError("Database is outside the controlled-write allowlist")
    if table not in {item for item in allowed_tables if isinstance(item, str)}:
        raise QueryPlanError("Table is outside the controlled-write allowlist")


def connect_with_retry(config: JsonObject, attempts: int, retry_delay_seconds: float) -> ConnectionProtocol:
    if pymysql is None or DictCursor is None:
        raise QueryPlanError("PyMySQL is required only when executing real MySQL queries. Install project dependency pymysql.")
    last_error: object | None = None
    ssl_config: object = config.get("ssl")
    if config.get("ssl_disabled") is True:
        raise QueryPlanError("ssl_disabled=true is not allowed. Configure TLS or explicitly use a non-production test database adapter.")
    charset: str = str(config.get("charset", SAFE_DEFAULT_CHARSET))
    connect_timeout: int = int(config.get("connect_timeout", SAFE_DEFAULT_CONNECT_TIMEOUT))
    read_timeout: int = int(config.get("read_timeout", SAFE_DEFAULT_READ_TIMEOUT))
    write_timeout: int = int(config.get("write_timeout", SAFE_DEFAULT_WRITE_TIMEOUT))
    for attempt in range(1, attempts + 1):
        try:
            return pymysql.connect(
                host=require_string(config.get("host"), "connection.host"),
                port=int(config.get("port")),
                user=require_string(config.get("username"), "connection.username"),
                password=require_string(config.get("password"), "connection.password"),
                charset=charset,
                autocommit=False,
                cursorclass=DictCursor,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                ssl=ssl_config if isinstance(ssl_config, dict) else None,
            )
        except pymysql.MySQLError as error:
            last_error = error
            logging.warning(json.dumps({"event": "db_connect_retry", "attempt": attempt, "connection": config.get("name")}, ensure_ascii=False))
            if attempt < attempts:
                time.sleep(retry_delay_seconds)
    if last_error is None:
        raise QueryPlanError("Database connection failed without an error")
    raise last_error


def validate_select(sql: str) -> None:
    normalized: str = sql.strip().lower()
    if re.match(r"^select\s", normalized) is None:
        raise QueryPlanError("Only SELECT statements are allowed")
    if re.search(r"\bselect\s+\*", normalized) is not None:
        raise QueryPlanError("SELECT * is forbidden")
    if re.search(r"\blimit\s+\d+\b", normalized) is None:
        raise QueryPlanError("SELECT queries must include an explicit LIMIT")
    if any(marker in normalized for marker in (";", "--", "#", "/*", "*/")):
        raise QueryPlanError("Query contains a forbidden delimiter or comment")
    forbidden_patterns: tuple[str, ...] = (
        r"\b(insert|update|delete|drop|alter|truncate|replace|call|execute|handler)\b",
        r"\binto\s+(outfile|dumpfile)\b",
        r"\bfor\s+update\b",
        r"\block\s+in\s+share\s+mode\b",
        r"\b(get_lock|release_lock|sleep|benchmark)\s*\(",
        r"\b(create|rename|grant|revoke)\b",
    )
    if any(re.search(pattern, normalized) is not None for pattern in forbidden_patterns):
        raise QueryPlanError("Query contains a forbidden operation")


def execute_query(connection: ConnectionProtocol, sql: str, attempts: int, retry_delay_seconds: float, query_reference: str) -> list[JsonObject]:
    if pymysql is None:
        raise QueryPlanError("PyMySQL is required only when executing real MySQL queries. Install project dependency pymysql.")
    last_error: object | None = None
    for attempt in range(1, attempts + 1):
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows: object = cursor.fetchall()
                if not isinstance(rows, list):
                    return list(rows)
                return rows
        except pymysql.MySQLError as error:
            last_error = error
            logging.warning(json.dumps({"event": "db_query_retry", "attempt": attempt, "query_reference": query_reference}, ensure_ascii=False))
            if attempt < attempts:
                time.sleep(retry_delay_seconds)
    if last_error is None:
        raise QueryPlanError(f"Query failed without an error: {query_reference}")
    raise last_error


def serialize_value(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def serialize_row(row: JsonObject) -> JsonObject:
    return {key: serialize_value(value) for key, value in row.items()}


def build_records(connection: ConnectionProtocol, connection_name: str, plan: JsonObject, attempts: int, retry_delay_seconds: float) -> list[JsonObject]:
    records: list[JsonObject] = []
    executed_at: str = datetime.now().astimezone().isoformat()
    for index, raw_query in enumerate(require_list(plan.get("queries"), "queries"), start=1):
        if not isinstance(raw_query, dict):
            raise QueryPlanError(f"queries[{index}] must be an object")
        query_reference: str = require_string(raw_query.get("query_reference"), f"queries[{index}].query_reference")
        database: str = require_string(raw_query.get("database"), f"queries[{index}].database")
        table: str = require_string(raw_query.get("table"), f"queries[{index}].table")
        purpose: str = require_string(raw_query.get("purpose"), f"queries[{index}].purpose")
        sql: str = require_string(raw_query.get("sql"), f"queries[{index}].sql")
        max_rows: int = int(raw_query.get("max_rows", SAFE_DEFAULT_MAX_ROWS))
        if max_rows < 1 or max_rows > SAFE_DEFAULT_MAX_ROWS:
            raise QueryPlanError(f"queries[{index}].max_rows must be between 1 and {SAFE_DEFAULT_MAX_ROWS}")
        validate_select(sql)
        rows: list[JsonObject] = [serialize_row(row) for row in execute_query(connection, sql, attempts, retry_delay_seconds, query_reference)]
        if len(rows) > max_rows:
            raise QueryPlanError(f"Query returned more rows than allowed: {query_reference}")
        fields: list[str] = sorted({key for row in rows for key in row})
        records.append(
            {
                "query_reference": query_reference,
                "connection": connection_name,
                "database": database,
                "table": table,
                "executed_at": executed_at,
                "purpose": purpose,
                "fields": fields,
                "filters": {"sql": sql},
                "row_count": len(rows),
                "records": rows,
            }
        )
    return records


def write_outputs(records: list[JsonObject], output_path: Path, manifest_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"real_data_records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    lines: list[str] = ["# 测试数据台账清单", "", "## 数据台账明细", ""]
    for record in records:
        database: str = str(record["database"])
        table: str = str(record["table"])
        rows: object = record["records"]
        if isinstance(rows, list):
            for row in rows:
                lines.append(f"{database}:{table}:【{json.dumps(row, ensure_ascii=False, separators=(',', ':'))}】")
    if len(lines) == 4:
        lines.append("无可用真实数据。")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    arguments: argparse.Namespace = parse_arguments()
    connections: JsonObject = read_json_object(Path(arguments.connections))
    plan: JsonObject = read_json_object(Path(arguments.plan))
    config: JsonObject = select_connection(connections, arguments.connection_name)
    attempts: int = int(config.get("retry_attempts", SAFE_DEFAULT_RETRY_ATTEMPTS))
    retry_delay_seconds: float = float(config.get("retry_delay_seconds", SAFE_DEFAULT_RETRY_DELAY_SECONDS))
    connection: ConnectionProtocol = connect_with_retry(config, attempts, retry_delay_seconds)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        connection.begin()
        records: list[JsonObject] = build_records(connection, arguments.connection_name, plan, attempts, retry_delay_seconds)
    finally:
        connection.rollback()
        connection.close()
    write_outputs(records, Path(arguments.output), Path(arguments.manifest))
    print(json.dumps({"query_count": len(records), "row_count": sum(int(item["row_count"]) for item in records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

