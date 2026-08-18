from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from .bedrock import InferenceUnavailable, tool_spec
from .config import Settings
from .providers import ProviderRouter
from .store import (
    CapacityError,
    RetrievedExperience,
    Store,
    experience_brief,
    failure_fingerprint,
)


DEMO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
QUERY_CONTEXT_RE = re.compile(r"\b(order|orders|checkout|customer|query|read|lookup|sql)\b", re.I)
REGRESSION_RE = re.compile(r"\b(slow|slower|latency|regress|degrad|performance|timeout)\w*\b", re.I)


class InvalidTask(ValueError):
    pass


class ExecutionDisabled(RuntimeError):
    pass


class RunService:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        gateway: Any | None = None,
    ):
        self.settings = settings
        self.store = store or Store(settings)
        self.gateway = gateway or ProviderRouter(
            settings,
            self.store.try_consume_provider_request,
        )

    def create_demo(self) -> str:
        demo_id = secrets.token_urlsafe(24)
        self.store.create_demo(demo_id)
        return demo_id

    def validate_demo_id(self, demo_id: str) -> None:
        if not DEMO_ID_RE.fullmatch(demo_id):
            raise InvalidTask("Invalid demo identifier.")

    def validate_task(self, task: str) -> str:
        normalized = " ".join(task.split())
        if not 20 <= len(normalized) <= 400:
            raise InvalidTask("Describe the query regression in 20 to 400 characters.")
        if not QUERY_CONTEXT_RE.search(normalized) or not REGRESSION_RE.search(normalized):
            raise InvalidTask(
                "This deployment supports CockroachDB query-regression diagnosis. "
                "Try: Investigate why our orders lookup became slower after today's deployment."
            )
        return normalized

    def execute(self, demo_id: str, task: str) -> dict[str, Any]:
        self.validate_demo_id(demo_id)
        task = self.validate_task(task)
        if not self._execution_enabled():
            raise ExecutionDisabled("Hark is currently paused. Existing demos remain available.")

        run_id = self.store.reserve_run(demo_id, task)
        started = time.perf_counter()
        tool_calls = 0
        failures = 0
        input_tokens = 0
        output_tokens = 0
        embedding_tokens = 0
        reasoning_route: list[dict[str, str]] = []
        actions: list[str] = []
        evidence: dict[str, Any] = {}
        failure: dict[str, Any] | None = None
        memory: RetrievedExperience | None = None
        query_embedding: list[float] | None = None
        document_embedding: list[float] | None = None

        try:
            self.store.add_event(run_id, "accepted", "Task accepted", task)
            skill_text = _load_skill_text()
            skill_guidance = _skill_guidance(skill_text)
            self.store.add_event(
                run_id,
                "skill_loaded",
                "Official Agent Skill loaded",
                "profiling-statement-fingerprints v1.0, pinned to Cockroach Labs commit e14e86d.",
                {"skill": self.store.skill_id, "source_commit": "e14e86d23ce8ee2e7e40a34ce2944c2502b6eadd"},
            )

            self.store.add_event(
                run_id,
                "memory_search",
                "Searching execution experience",
                "Structured scope: demo, skill, environment, workflow, active successful memories.",
            )
            try:
                query_embedding, query_tokens = self.gateway.embed_query(run_id, task)
                embedding_tokens += query_tokens
                self.store.add_event(
                    run_id,
                    "query_embedding_generated",
                    "Canonical query embedding generated",
                    f"Generated {self.settings.embedding_dimensions}-dimensional retrieval evidence for compatible execution memory.",
                    {
                        "embedding_provider": "google-gemini",
                        "embedding_model": self.settings.embedding_model_id,
                        "embedding_dimensions": self.settings.embedding_dimensions,
                    },
                )
                memory = self.store.retrieve_experience(
                    demo_id, query_embedding, self.settings.similarity_threshold
                )
            except InferenceUnavailable as exc:
                self.store.add_event(
                    run_id,
                    "memory_fallback",
                    "Vector recall unavailable",
                    "Continuing without proactive memory; structured failure recall remains available.",
                    {"category": "embedding_unavailable", "provider_error": str(exc)[:240]},
                )

            if memory:
                self.store.link_experience(run_id, memory, "proactive")
                self.store.add_event(
                    run_id,
                    "memory_found",
                    "Relevant precedent found",
                    memory.brief,
                    {"experience_id": memory.id, "similarity": round(memory.similarity, 6)},
                )
            else:
                self.store.add_event(
                    run_id,
                    "memory_none",
                    "No useful precedent",
                    "No active experience cleared the configured precision threshold.",
                    {"threshold": self.settings.similarity_threshold},
                )

            system_text = _system_prompt(skill_guidance, memory)
            operations = [] if memory else ["verify_statement_stats_setting"]
            operations.extend(
                ["profile_statement_fingerprints", "explain_orders_lookup", "inspect_order_indexes"]
            )

            while operations:
                self._check_deadline(started)
                if tool_calls >= self.settings.max_agent_iterations:
                    raise RuntimeError("The bounded agent iteration limit was reached.")
                available = [
                    tool_spec(operation, _tool_description(operation)) for operation in operations
                ]
                response = self.gateway.converse(
                    run_id=run_id,
                    system_text=system_text,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "text": _action_prompt(
                                        task=task,
                                        actions=actions,
                                        evidence=evidence,
                                    )
                                }
                            ],
                        }
                    ],
                    tools=available,
                    max_tokens=260,
                )
                _record_route(reasoning_route, response)
                usage = response.get("usage", {})
                input_tokens += int(usage.get("inputTokens", 0))
                output_tokens += int(usage.get("outputTokens", 0))
                message = response.get("output", {}).get("message", {})
                tool_use = _first_tool_use(message)
                if not tool_use or tool_use.get("name") not in operations:
                    raise RuntimeError("The model did not select a permitted diagnostic action.")

                operation = str(tool_use["name"])
                tool_calls += 1
                actions.append(operation)
                result = self.store.diagnostic_query(operation)
                evidence[operation] = result
                operations.remove(operation)

                if result.get("ok"):
                    self.store.add_event(
                        run_id,
                        "tool_succeeded",
                        _tool_title(operation),
                        f"CockroachDB completed the allowlisted operation in {result['duration_ms']} ms.",
                        result,
                    )
                else:
                    failures += 1
                    fingerprint = failure_fingerprint(
                        self.store.skill_id, self.settings.environment_id, result
                    )
                    result["fingerprint"] = fingerprint
                    failure = result
                    self.store.add_event(
                        run_id,
                        "tool_failed",
                        "Database-enforced limitation encountered",
                        result.get("message", "CockroachDB rejected the operation."),
                        result,
                    )
                    reactive = self.store.find_failure_recovery(demo_id, fingerprint)
                    if reactive:
                        reactive_experience = RetrievedExperience(
                            id=reactive["experience_id"],
                            similarity=1.0,
                            brief=reactive["recovery"],
                            failure_fingerprint=fingerprint,
                            recovery=reactive["recovery"],
                        )
                        self.store.link_experience(run_id, reactive_experience, "reactive")
                        self.store.add_event(
                            run_id,
                            "reactive_memory",
                            "Known failure recovery found",
                            reactive["recovery"],
                            {"failure_fingerprint": fingerprint},
                        )
                    else:
                        self.store.add_event(
                            run_id,
                            "recovery_selected",
                            "Safe recovery selected",
                            "Continue with the production-safe statistics view, EXPLAIN, and index inspection, without elevated privileges.",
                        )

            self._check_deadline(started)
            response = self.gateway.converse(
                run_id=run_id,
                system_text=system_text
                + (
                    "\nReturn a concise diagnosis grounded only in the tool evidence. "
                    "State the observed plan/index finding and a safe next action. "
                    "The answer must be plain text, not Markdown. Do not output SQL or DDL, "
                    "and do not tell the operator to create, alter, or drop an index."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": _diagnosis_prompt(
                                    task=task,
                                    actions=actions,
                                    evidence=evidence,
                                )
                            }
                        ],
                    }
                ],
                tools=None,
                max_tokens=420,
            )
            _record_route(reasoning_route, response)
            usage = response.get("usage", {})
            input_tokens += int(usage.get("inputTokens", 0))
            output_tokens += int(usage.get("outputTokens", 0))
            diagnosis = _message_text(response.get("output", {}).get("message", {}))
            if not diagnosis:
                raise RuntimeError("The reasoning provider returned no diagnosis.")
            _validate_diagnosis(diagnosis)

            self.store.add_event(
                run_id,
                "diagnosis_completed",
                "Diagnosis completed",
                diagnosis,
            )
            brief, _, _ = experience_brief(failure)
            try:
                document_embedding, document_tokens = self.gateway.embed_document(
                    run_id,
                    _experience_document(task, brief, diagnosis, actions),
                )
                embedding_tokens += document_tokens
            except InferenceUnavailable as exc:
                self.store.add_event(
                    run_id,
                    "experience_embedding_unavailable",
                    "Semantic retention unavailable",
                    "The execution provenance will be retained without a semantic vector.",
                    {"category": "embedding_unavailable", "provider_error": str(exc)[:240]},
                )
            persistence_warning = None
            try:
                experience_id = self.store.persist_experience(
                    run_id=run_id,
                    demo_id=demo_id,
                    task=task,
                    embedding=document_embedding,
                    failure=failure,
                    diagnosis=diagnosis,
                    actions=actions,
                )
                self.store.add_event(
                    run_id,
                    "experience_persisted",
                    "Execution experience persisted",
                    "Derived memory and immutable run provenance are linked in CockroachDB.",
                    {
                        "experience_id": experience_id,
                        "embedding_persisted": document_embedding is not None,
                        "embedding_provider": "google-gemini" if document_embedding else None,
                        "embedding_model": self.settings.embedding_model_id
                        if document_embedding
                        else None,
                        "embedding_dimensions": self.settings.embedding_dimensions
                        if document_embedding
                        else None,
                    },
                )
            except Exception:
                persistence_warning = "Diagnosis completed, but execution memory persistence did not complete."
                self.store.add_event(
                    run_id,
                    "persistence_failed",
                    "Memory persistence incomplete",
                    persistence_warning,
                )

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            metrics = {
                "duration_ms": duration_ms,
                "tool_calls": tool_calls,
                "failures": failures,
                "memory_retrieved": bool(memory),
                "memory_used": bool(memory),
                "memory_similarity": round(memory.similarity, 6) if memory else None,
                "embedding_tokens": embedding_tokens,
                "embedding_provider": "google-gemini" if document_embedding else None,
                "embedding_model": self.settings.embedding_model_id
                if document_embedding
                else None,
                "embedding_dimensions": self.settings.embedding_dimensions
                if document_embedding
                else None,
                "model_input_tokens": input_tokens,
                "model_output_tokens": output_tokens,
                "reasoning_provider": reasoning_route[-1]["provider"]
                if reasoning_route
                else None,
                "reasoning_model": reasoning_route[-1]["model"] if reasoning_route else None,
                "reasoning_route": reasoning_route,
                "successful": True,
                "persistence_warning": persistence_warning,
            }
            self.store.finish_run(run_id, status="succeeded", diagnosis=diagnosis, metrics=metrics)
            return self.store.get_demo(demo_id)
        except (InferenceUnavailable, TimeoutError) as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            public_message = (
                "Reasoning capacity is temporarily unavailable. Hark did not fabricate a diagnosis."
            )
            self.store.add_event(
                run_id,
                "run_failed",
                "Inference unavailable",
                public_message,
                {"provider_error": str(exc)[:240]},
            )
            self.store.finish_run(
                run_id,
                status="failed",
                diagnosis=None,
                metrics={
                    "duration_ms": duration_ms,
                    "tool_calls": tool_calls,
                    "failures": failures,
                    "memory_retrieved": bool(memory),
                    "memory_used": bool(memory),
                    "reasoning_route": reasoning_route,
                    "successful": False,
                },
                error_code="INFERENCE_UNAVAILABLE",
                error_message=public_message,
            )
            return self.store.get_demo(demo_id)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            public_message = "The diagnostic could not complete. No result was fabricated."
            try:
                self.store.add_event(run_id, "run_failed", "Run failed", public_message)
                self.store.finish_run(
                    run_id,
                    status="failed",
                    diagnosis=None,
                    metrics={
                        "duration_ms": duration_ms,
                        "tool_calls": tool_calls,
                        "failures": failures,
                        "reasoning_route": reasoning_route,
                        "successful": False,
                    },
                    error_code="RUN_FAILED",
                    error_message=public_message,
                )
            finally:
                return self.store.get_demo(demo_id)

    def _execution_enabled(self) -> bool:
        if not self.settings.execution_enabled:
            return False
        if not self.settings.kill_switch_parameter:
            return True
        try:
            import boto3

            value = boto3.client("ssm", region_name=self.settings.aws_region).get_parameter(
                Name=self.settings.kill_switch_parameter
            )["Parameter"]["Value"]
            return str(value).lower() == "true"
        except Exception:
            return False

    def _check_deadline(self, started: float) -> None:
        if time.perf_counter() - started >= self.settings.run_timeout_seconds:
            raise TimeoutError("The bounded run timeout was reached.")


def _action_prompt(*, task: str, actions: list[str], evidence: dict[str, Any]) -> str:
    evidence_text = json.dumps(evidence, separators=(",", ":"), default=str)[:12000]
    return (
        f"Diagnose this supported task: {task}\n"
        f"Completed diagnostic actions: {json.dumps(actions)}\n"
        f"Observed evidence: {evidence_text}\n"
        "Select exactly one of the supplied remaining diagnostic tools. "
        "Use only the tools supplied by Hark and never invent tool results."
    )


def _diagnosis_prompt(*, task: str, actions: list[str], evidence: dict[str, Any]) -> str:
    evidence_text = json.dumps(evidence, separators=(",", ":"), default=str)[:18000]
    return (
        f"Task: {task}\n"
        f"Completed diagnostic actions: {json.dumps(actions)}\n"
        f"Actual tool evidence: {evidence_text}\n"
        "Return exactly two short plain-text paragraphs: first the evidence-grounded diagnosis, "
        "then a safe read-only next action. Do not use Markdown, code blocks, SQL, or DDL. "
        "Do not recommend creating, altering, or dropping an index. Do not invent measurements "
        "or claim that an action was executed when it was not."
    )


def _experience_document(
    task: str,
    brief: str,
    diagnosis: str,
    actions: list[str],
) -> str:
    return (
        f"Original task: {task}\n"
        f"Execution experience: {brief}\n"
        f"Observed diagnosis: {diagnosis}\n"
        f"Successful diagnostic actions: {', '.join(actions)}"
    )[:30000]


def _validate_diagnosis(diagnosis: str) -> None:
    if re.search(r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\s+", diagnosis, re.I):
        raise RuntimeError("The reasoning response crossed Hark's read-only answer boundary.")


def _record_route(route: list[dict[str, str]], response: dict[str, Any]) -> None:
    provider = str(response.get("provider") or "unknown")
    model = str(response.get("model") or "unknown")
    item = {"provider": provider, "model": model}
    if not route or route[-1] != item:
        route.append(item)


def _load_skill_text() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "profiling-statement-fingerprints"
        / "SKILL.md"
    )
    return path.read_text(encoding="utf-8")


def _skill_guidance(text: str) -> str:
    sections = []
    for heading in ["## When to Use This Skill", "### Workflow 1: Slowness Investigation", "## Safety Considerations"]:
        start = text.find(heading)
        if start < 0:
            continue
        next_heading = text.find("\n## ", start + len(heading))
        sections.append(text[start : next_heading if next_heading > 0 else len(text)])
    return "\n\n".join(sections)[:9000]


def _system_prompt(skill_guidance: str, memory: RetrievedExperience | None) -> str:
    memory_text = memory.brief if memory else "No relevant execution experience was retrieved."
    return (
        "You are Hark's bounded CockroachDB query-regression diagnostic agent. "
        "Follow the pinned official CockroachDB Agent Skill. Use only tools supplied by the harness. "
        "Never propose or execute writes, DDL, privilege escalation, or arbitrary SQL. "
        "Treat tool output as evidence and do not invent metrics.\n\n"
        f"OFFICIAL SKILL GUIDANCE:\n{skill_guidance}\n\n"
        f"EXPERIENCE BRIEF:\n{memory_text}"
    )


def _tool_description(operation: str) -> str:
    return {
        "verify_statement_stats_setting": "Verify whether automatic SQL statistics collection is enabled. This cluster-setting preflight requires VIEWCLUSTERSETTING and may be rejected by the restricted role.",
        "profile_statement_fingerprints": "Read the statement fingerprint statistics visible to the restricted role using the production-safe CockroachDB view from the official Skill.",
        "explain_orders_lookup": "Run EXPLAIN for the fixed, allowlisted orders lookup without executing arbitrary user SQL.",
        "inspect_order_indexes": "Inspect information_schema metadata for indexes on the fixed orders demo table.",
    }[operation]


def _tool_title(operation: str) -> str:
    return {
        "verify_statement_stats_setting": "Statement statistics setting verified",
        "profile_statement_fingerprints": "Statement fingerprints inspected",
        "explain_orders_lookup": "Orders lookup plan explained",
        "inspect_order_indexes": "Orders indexes inspected",
    }[operation]


def _first_tool_use(message: dict[str, Any]) -> dict[str, Any] | None:
    for content in message.get("content", []):
        if "toolUse" in content:
            return content["toolUse"]
    return None


def _message_text(message: dict[str, Any]) -> str:
    return "\n".join(
        str(content["text"]).strip()
        for content in message.get("content", [])
        if content.get("text")
    ).strip()
