from __future__ import annotations

import os
import uuid
from dataclasses import replace
from urllib.parse import quote, urlparse

import pytest

from hark.config import Settings
from hark.store import CapacityError, Store, connection, failure_fingerprint


ADMIN_URL = os.getenv("HARK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="Set HARK_TEST_DATABASE_URL for real CockroachDB tests.")


def _role_url(role: str, password: str) -> str:
    parsed = urlparse(ADMIN_URL)
    return (
        f"postgresql://{role}:{quote(password, safe='')}@{parsed.hostname}:{parsed.port or 26257}"
        f"/{(parsed.path or '/defaultdb').lstrip('/')}?sslmode=disable"
    )


def _settings() -> Settings:
    return Settings(
        aws_region="us-east-1",
        memory_database_url=_role_url("hark_memory", "MemoryTest-2026!"),
        diagnostic_database_url=_role_url("hark_diagnostic", "DiagnosticTest-2026!"),
        bedrock_model_id="amazon.nova-micro-v1:0",
        gemini_api_key="test-key",
        gemini_primary_model_id="gemini-3.5-flash-lite",
        gemini_tertiary_model_id="gemini-3.1-flash-lite",
        embedding_model_id="gemini-embedding-2",
        embedding_dimensions=256,
        similarity_threshold=0.73,
        bedrock_health_ttl_seconds=300,
        gemini_primary_request_budget_per_day=200,
        gemini_tertiary_request_budget_per_day=100,
        gemini_embedding_request_budget_per_day=150,
        gemini_primary_request_limit_per_minute=12,
        gemini_tertiary_request_limit_per_minute=12,
        gemini_embedding_request_limit_per_minute=90,
        demo_run_limit_per_day=4,
        global_run_limit_per_day=40,
        global_total_run_limit=1000,
        max_concurrent_runs=3,
        max_agent_iterations=5,
        run_timeout_seconds=60,
        execution_enabled=True,
        kill_switch_parameter="",
        retention_days=45,
        environment_id="restricted-orders-demo-v1",
    )


def test_real_permissions_recovery_vector_retrieval_and_invalidation():
    store = Store(_settings())
    demo_id = uuid.uuid4().hex
    store.create_demo(demo_id)
    run_id = store.reserve_run(demo_id, "Investigate why the orders lookup became slower after deployment.")

    privileged = store.diagnostic_query("verify_statement_stats_setting")
    profiled = store.diagnostic_query("profile_statement_fingerprints")
    explained = store.diagnostic_query("explain_orders_lookup")
    indexes = store.diagnostic_query("inspect_order_indexes")
    assert privileged["ok"] is False
    assert privileged["sqlstate"] == "42501"
    assert profiled["ok"] is True
    assert explained["ok"] is True
    assert indexes["ok"] is True

    privileged["fingerprint"] = failure_fingerprint(store.skill_id, store.settings.environment_id, privileged)
    vector = [1.0] + [0.0] * 255
    experience_id = store.persist_experience(
        run_id=run_id,
        demo_id=demo_id,
        task="Investigate why the orders lookup became slower after deployment.",
        embedding=vector,
        failure=privileged,
        diagnosis="EXPLAIN and index metadata show the safe diagnostic path.",
        actions=["verify_statement_stats_setting", "profile_statement_fingerprints", "explain_orders_lookup", "inspect_order_indexes"],
    )
    store.finish_run(run_id, status="succeeded", diagnosis="Verified", metrics={"successful": True})

    recalled = store.retrieve_experience(demo_id, vector, 0.95)
    assert recalled is not None
    assert recalled.id == experience_id
    unrelated = [0.0, 1.0] + [0.0] * 254
    assert store.retrieve_experience(demo_id, unrelated, 0.73) is None
    weak_match = [0.70, 0.714142842] + [0.0] * 254
    assert store.retrieve_experience(demo_id, weak_match, 0.73) is None
    reactive = store.find_failure_recovery(demo_id, privileged["fingerprint"])
    assert reactive and reactive["experience_id"] == experience_id

    assert store.invalidate_experience(demo_id, experience_id) is True
    assert store.retrieve_experience(demo_id, vector, 0.5) is None
    assert store.find_failure_recovery(demo_id, privileged["fingerprint"]) is None


def test_diagnostic_identity_cannot_write_demo_data():
    with connection(_settings().diagnostic_database_url) as conn:
        cursor = conn.cursor()
        with pytest.raises(Exception) as exc_info:
            cursor.execute(
                "INSERT INTO hark_demo.customers (id,email) VALUES (%s,%s)",
                (str(uuid.uuid4()), "blocked@example.test"),
            )
        payload = exc_info.value.args[0]
        assert isinstance(payload, dict)
        assert payload.get("C") == "42501"


def test_per_demo_limit_and_database_lease_concurrency_guard():
    limited_store = Store(replace(_settings(), demo_run_limit_per_day=1))
    demo_id = uuid.uuid4().hex
    limited_store.create_demo(demo_id)
    run_id = limited_store.reserve_run(
        demo_id, "Investigate why the orders lookup became slower after deployment."
    )
    budget_key = f"test-budget-{uuid.uuid4().hex}"
    assert limited_store.try_consume_provider_request(run_id, budget_key, 1, 1) is True
    assert limited_store.try_consume_provider_request(run_id, budget_key, 1, 1) is False
    assert all(
        event["event_type"] != "provider_request"
        for event in limited_store.get_demo(demo_id)["runs"][0]["events"]
    )
    limited_store.finish_run(run_id, status="succeeded", diagnosis="verified", metrics={})
    with pytest.raises(CapacityError, match="24-hour run limit"):
        limited_store.reserve_run(
            demo_id, "Investigate why the orders lookup became slower after deployment."
        )

    lease_store = Store(replace(_settings(), max_concurrent_runs=1))
    first_demo, second_demo = uuid.uuid4().hex, uuid.uuid4().hex
    lease_store.create_demo(first_demo)
    lease_store.create_demo(second_demo)
    active_run = lease_store.reserve_run(
        first_demo, "Investigate why the orders lookup became slower after deployment."
    )
    try:
        with pytest.raises(CapacityError, match="demo capacity"):
            lease_store.reserve_run(
                second_demo, "Investigate why the orders lookup became slower after deployment."
            )
    finally:
        lease_store.finish_run(
            active_run, status="failed", diagnosis=None, metrics={"test_cleanup": True}
        )
