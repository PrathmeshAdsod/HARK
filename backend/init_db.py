from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hark.store import connection  # noqa: E402


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    admin_url = os.environ.get("HARK_ADMIN_DATABASE_URL", "")
    if not admin_url:
        raise SystemExit("HARK_ADMIN_DATABASE_URL is required.")
    memory_password = os.environ.get("HARK_MEMORY_PASSWORD", "")
    diagnostic_password = os.environ.get("HARK_DIAGNOSTIC_PASSWORD", "")
    insecure_local = "sslmode=disable" in admin_url.lower()
    if not insecure_local and (not memory_password or not diagnostic_password):
        raise SystemExit(
            "HARK_MEMORY_PASSWORD and HARK_DIAGNOSTIC_PASSWORD are required. "
            "Generate them once, then store the same values in the application secret."
        )
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

    with connection(admin_url) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE USER IF NOT EXISTS hark_memory")
        cursor.execute("CREATE USER IF NOT EXISTS hark_diagnostic")
        if not insecure_local:
            cursor.execute(f"ALTER USER hark_memory WITH PASSWORD {_literal(memory_password)}")
            cursor.execute(f"ALTER USER hark_diagnostic WITH PASSWORD {_literal(diagnostic_password)}")
        conn.commit()

    with connection(admin_url) as conn:
        conn.autocommit = True
        cursor = conn.cursor()
        for statement in _statements(schema):
            cursor.execute(statement)

        cursor.execute("GRANT USAGE ON SCHEMA hark TO hark_memory")
        cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA hark TO hark_memory")
        cursor.execute("GRANT USAGE ON SCHEMA hark_demo TO hark_diagnostic")
        cursor.execute("GRANT SELECT ON TABLE hark_demo.orders, hark_demo.customers TO hark_diagnostic")
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM hark_memory")
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM hark_diagnostic")

    print("CockroachDB schema and least-privilege roles are ready.")
    print("Passwords were not printed.")


def _statements(script: str) -> list[str]:
    statements = []
    current = []
    for line in script.splitlines():
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    if "".join(current).strip():
        statements.append("\n".join(current).strip())
    return statements


if __name__ == "__main__":
    main()
