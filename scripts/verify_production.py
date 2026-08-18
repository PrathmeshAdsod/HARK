from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from hark.config import load_settings  # noqa: E402
from hark.store import Store, connection, failure_fingerprint  # noqa: E402


def main() -> None:
    settings = load_settings()
    store = Store(settings)
    checks: dict[str, object] = {"database_ping": store.ping()}

    with connection(settings.memory_database_url) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_user")
        checks["memory_identity"] = cursor.fetchone()[0] == "hark_memory"
        cursor.execute(
            "SELECT count(*) FROM information_schema.statistics "
            "WHERE table_schema='hark' AND table_name='experiences' "
            "AND index_name='experiences_memory_vector_idx'"
        )
        checks["vector_index_present"] = int(cursor.fetchone()[0]) > 0

    with connection(settings.diagnostic_database_url) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_user")
        checks["diagnostic_identity"] = cursor.fetchone()[0] == "hark_diagnostic"
        try:
            cursor.execute(
                "INSERT INTO hark_demo.customers (id,email) VALUES (%s,%s)",
                (str(uuid.uuid4()), "must-not-write@example.test"),
            )
            checks["diagnostic_write_blocked"] = False
        except Exception as exc:
            payload = exc.args[0] if exc.args else {}
            checks["diagnostic_write_blocked"] = (
                isinstance(payload, dict) and payload.get("C") == "42501"
            )
        finally:
            conn.rollback()

    denied = store.diagnostic_query("verify_statement_stats_setting")
    profiled = store.diagnostic_query("profile_statement_fingerprints")
    explained = store.diagnostic_query("explain_orders_lookup")
    indexes = store.diagnostic_query("inspect_order_indexes")
    checks["cold_boundary_sqlstate_42501"] = (
        denied.get("ok") is False and denied.get("sqlstate") == "42501"
    )
    checks["official_skill_statistics_query"] = profiled.get("ok") is True
    checks["safe_explain"] = explained.get("ok") is True
    checks["index_metadata"] = indexes.get("ok") is True

    demo_id = uuid.uuid4().hex
    store.create_demo(demo_id)
    run_id = store.reserve_run(
        demo_id, "Investigate why the orders lookup became slower after deployment."
    )
    denied["fingerprint"] = failure_fingerprint(
        store.skill_id, settings.environment_id, denied
    )
    vector = [1.0] + [0.0] * (settings.embedding_dimensions - 1)
    experience_id = store.persist_experience(
        run_id=run_id,
        demo_id=demo_id,
        task="Investigate why the orders lookup became slower after deployment.",
        embedding=vector,
        failure=denied,
        diagnosis="Production verification of the bounded recovery path.",
        actions=[
            "verify_statement_stats_setting",
            "profile_statement_fingerprints",
            "explain_orders_lookup",
            "inspect_order_indexes",
        ],
    )
    store.finish_run(
        run_id,
        status="succeeded",
        diagnosis="Production verification",
        metrics={"verification": True},
    )
    recalled = store.retrieve_experience(demo_id, vector, 0.99)
    reactive = store.find_failure_recovery(demo_id, denied["fingerprint"])
    checks["vector_retrieval"] = recalled is not None and recalled.id == experience_id
    checks["failure_fingerprint_retrieval"] = bool(
        reactive and reactive.get("experience_id") == experience_id
    )
    checks["invalidation"] = store.invalidate_experience(demo_id, experience_id)
    checks["invalidated_memory_excluded"] = (
        store.retrieve_experience(demo_id, vector, 0.1) is None
        and store.find_failure_recovery(demo_id, denied["fingerprint"]) is None
    )

    passed = all(value is True for value in checks.values())
    print(json.dumps({"passed": passed, "checks": checks}, indent=2, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
