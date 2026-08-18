from __future__ import annotations

from dataclasses import replace

import pytest

from hark.config import Settings
from hark.service import InvalidTask, RunService, _validate_diagnosis
from hark.store import RetrievedExperience


def settings() -> Settings:
    return Settings(
        aws_region="us-east-1",
        memory_database_url="unused",
        diagnostic_database_url="unused",
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


class FakeGateway:
    def embed_query(self, run_id, text):
        return [1.0] + [0.0] * 255, 12

    def embed_document(self, run_id, text):
        return [1.0] + [0.0] * 255, 12

    def converse(self, *, tools=None, **kwargs):
        if tools:
            name = tools[0]["toolSpec"]["name"]
            return {
                "provider": "test-provider",
                "model": "test-model",
                "usage": {"inputTokens": 10, "outputTokens": 2},
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"toolUse": {"toolUseId": f"use-{name}", "name": name, "input": {}}}],
                    }
                },
            }
        return {
            "provider": "test-provider",
            "model": "test-model",
            "usage": {"inputTokens": 20, "outputTokens": 14},
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "The fixed orders lookup scans because its customer/status access path is absent. Review and add a scoped index after validation."}],
                }
            },
        }


class FakeStore:
    skill_id = "profiling-statement-fingerprints@1.0"

    def __init__(self):
        self.memory = None
        self.runs = []
        self.events = []

    def create_demo(self, demo_id):
        self.demo_id = demo_id

    def reserve_run(self, demo_id, task):
        run_id = f"run-{len(self.runs) + 1}"
        self.runs.append({"id": run_id, "task": task, "status": "running", "metrics": {}})
        return run_id

    def add_event(self, run_id, event_type, title, detail, payload=None):
        self.events.append((run_id, event_type, title, detail, payload or {}))

    def try_consume_provider_request(self, run_id, budget_key, daily_budget, per_minute_limit):
        return True

    def retrieve_experience(self, demo_id, embedding, threshold):
        return self.memory

    def link_experience(self, run_id, experience, use_type):
        self.events.append((run_id, "linked", use_type, experience.id, {}))

    def diagnostic_query(self, operation):
        if operation == "verify_statement_stats_setting":
            return {
                "ok": False,
                "operation": operation,
                "sqlstate": "42501",
                "category": "insufficient_privilege",
                "message": "VIEWACTIVITY is required",
                "duration_ms": 2.0,
            }
        return {
            "ok": True,
            "operation": operation,
            "columns": ["info"],
            "rows": [["scan"]],
            "duration_ms": 1.0,
        }

    def find_failure_recovery(self, demo_id, fingerprint):
        return None

    def persist_experience(self, **kwargs):
        self.memory = RetrievedExperience(
            id="experience-1",
            similarity=0.99,
            brief="Use EXPLAIN and index inspection; skip privileged activity statistics.",
            failure_fingerprint="fingerprint",
            recovery="Use EXPLAIN and index inspection.",
        )
        return self.memory.id

    def finish_run(self, run_id, *, status, diagnosis, metrics, **kwargs):
        run = next(item for item in self.runs if item["id"] == run_id)
        run.update(status=status, diagnosis=diagnosis, metrics=metrics)

    def get_demo(self, demo_id):
        return {"id": demo_id, "runs": self.runs, "experiences": []}


def test_task_validation_is_narrow():
    service = RunService(settings(), store=FakeStore(), gateway=FakeGateway())
    assert "orders" in service.validate_task("Investigate why the orders query became slower today.")
    with pytest.raises(InvalidTask):
        service.validate_task("Write a poem about databases and clouds.")
    with pytest.raises(InvalidTask):
        service.validate_task("slow query")


def test_cold_then_warm_uses_memory_and_fewer_tools():
    store = FakeStore()
    service = RunService(settings(), store=store, gateway=FakeGateway())
    demo_id = "A" * 32

    cold = service.execute(demo_id, "Investigate why the orders lookup became slower after deployment.")
    warm = service.execute(demo_id, "Checkout reads have regressed since the latest release.")

    cold_metrics = cold["runs"][0]["metrics"]
    warm_metrics = warm["runs"][1]["metrics"]
    assert cold_metrics["tool_calls"] == 4
    assert cold_metrics["failures"] == 1
    assert cold_metrics["memory_used"] is False
    assert warm_metrics["tool_calls"] == 3
    assert warm_metrics["failures"] == 0
    assert warm_metrics["memory_used"] is True


def test_execution_kill_switch_blocks_new_runs():
    service = RunService(replace(settings(), execution_enabled=False), store=FakeStore(), gateway=FakeGateway())
    with pytest.raises(Exception, match="paused"):
        service.execute("B" * 32, "Investigate why the orders lookup became slower after deployment.")


def test_diagnosis_read_only_boundary_rejects_ddl():
    _validate_diagnosis("The plan performs a full scan. Review the evidence with the database owner.")
    with pytest.raises(RuntimeError, match="read-only"):
        _validate_diagnosis("CREATE INDEX ON orders (customer_id)")
