from __future__ import annotations

import hashlib
import json
import ssl
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import pg8000.dbapi

from .config import Settings


class CapacityError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


@dataclass
class RetrievedExperience:
    id: str
    similarity: float
    brief: str
    failure_fingerprint: str | None
    recovery: str


def _connect_url(url: str):
    if not url:
        raise RuntimeError("CockroachDB connection is not configured.")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    sslmode = query.get("sslmode", ["verify-full"])[0]
    ssl_context: ssl.SSLContext | bool | None
    if sslmode == "disable":
        ssl_context = False
    else:
        ssl_context = ssl.create_default_context()
        if sslmode == "require":
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
    return pg8000.dbapi.connect(
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        host=parsed.hostname or "localhost",
        port=parsed.port or 26257,
        database=(parsed.path or "/defaultdb").lstrip("/"),
        ssl_context=ssl_context,
        timeout=10,
        application_name="hark",
    )


@contextmanager
def connection(url: str) -> Iterator[Any]:
    conn = _connect_url(url)
    try:
        yield conn
    finally:
        conn.close()


def rows(cursor) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


class Store:
    skill_id = "profiling-statement-fingerprints@1.0"
    workflow = "query-regression-diagnosis"

    def __init__(self, settings: Settings):
        self.settings = settings

    def ping(self) -> bool:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1

    def create_demo(self, demo_id: str) -> None:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO hark.demos (id, expires_at) VALUES (%s, now() + (%s || ' days')::INTERVAL)",
                (demo_id, self.settings.retention_days),
            )
            conn.commit()

    def demo_exists(self, demo_id: str) -> bool:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM hark.demos WHERE id=%s AND expires_at > now())",
                (demo_id,),
            )
            return bool(cursor.fetchone()[0])

    def reserve_run(self, demo_id: str, task: str) -> str:
        run_id = str(uuid.uuid4())
        lease_id = str(uuid.uuid4())
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT id FROM hark.usage_guard WHERE id=1 FOR UPDATE")
                cursor.execute("DELETE FROM hark.execution_leases WHERE expires_at <= now()")
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM hark.demos WHERE id=%s AND expires_at > now())",
                    (demo_id,),
                )
                if not cursor.fetchone()[0]:
                    raise NotFoundError("This demo link is invalid or has expired.")
                cursor.execute(
                    "SELECT count(*) FROM hark.runs WHERE demo_id=%s AND created_at >= now() - INTERVAL '24 hours'",
                    (demo_id,),
                )
                if cursor.fetchone()[0] >= self.settings.demo_run_limit_per_day:
                    raise CapacityError("This demo has reached its 24-hour run limit.")
                cursor.execute(
                    "SELECT count(*) FROM hark.runs WHERE created_at >= date_trunc('day', now())"
                )
                if cursor.fetchone()[0] >= self.settings.global_run_limit_per_day:
                    raise CapacityError("Hark is currently at demo capacity. Please try again shortly.")
                cursor.execute("SELECT count(*) FROM hark.runs")
                if cursor.fetchone()[0] >= self.settings.global_total_run_limit:
                    raise CapacityError("Hark is currently at demo capacity. Please try again shortly.")
                cursor.execute("SELECT count(*) FROM hark.execution_leases")
                if cursor.fetchone()[0] >= self.settings.max_concurrent_runs:
                    raise CapacityError("Hark is currently at demo capacity. Please try again shortly.")
                cursor.execute(
                    """
                    INSERT INTO hark.runs
                      (id, demo_id, skill_id, skill_source, environment_id, workflow, task, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'running')
                    """,
                    (
                        run_id,
                        demo_id,
                        self.skill_id,
                        "https://github.com/cockroachlabs/cockroachdb-skills/tree/e14e86d23ce8ee2e7e40a34ce2944c2502b6eadd/skills/cockroachdb-observability-and-diagnostics/profiling-statement-fingerprints",
                        self.settings.environment_id,
                        self.workflow,
                        task,
                    ),
                )
                cursor.execute(
                    "INSERT INTO hark.execution_leases (id, run_id, expires_at) VALUES (%s,%s,now() + (%s || ' seconds')::INTERVAL)",
                    (lease_id, run_id, self.settings.run_timeout_seconds + 10),
                )
                conn.commit()
                return run_id
            except Exception:
                conn.rollback()
                raise

    def add_event(
        self,
        run_id: str,
        event_type: str,
        title: str,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        safe_payload = json.dumps(payload or {}, separators=(",", ":"), default=str)[:12000]
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO hark.run_events (run_id, sequence, event_type, title, detail, payload)
                SELECT %s, COALESCE(max(sequence),0)+1, %s, %s, %s, %s::JSONB
                FROM hark.run_events WHERE run_id=%s
                """,
                (run_id, event_type, title[:120], detail[:1200], safe_payload, run_id),
            )
            conn.commit()

    def try_consume_provider_request(
        self,
        run_id: str,
        budget_key: str,
        daily_budget: int,
        per_minute_limit: int,
    ) -> bool:
        """Atomically reserve one provider request in the existing immutable event ledger."""
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT id FROM hark.usage_guard WHERE id=1 FOR UPDATE")
                cursor.execute(
                    """
                    SELECT count(*) FROM hark.run_events
                    WHERE event_type='provider_request'
                      AND created_at >= date_trunc('day', now())
                      AND payload->>'budget_key'=%s
                    """,
                    (budget_key,),
                )
                if int(cursor.fetchone()[0]) >= daily_budget:
                    conn.rollback()
                    return False
                cursor.execute(
                    """
                    SELECT count(*) FROM hark.run_events
                    WHERE event_type='provider_request'
                      AND created_at >= date_trunc('minute', now())
                      AND payload->>'budget_key'=%s
                    """,
                    (budget_key,),
                )
                if int(cursor.fetchone()[0]) >= per_minute_limit:
                    conn.rollback()
                    return False
                cursor.execute(
                    """
                    INSERT INTO hark.run_events (run_id,sequence,event_type,title,detail,payload)
                    SELECT %s, COALESCE(max(sequence),0)+1, 'provider_request',
                           'Provider request reserved', 'Server-side provider budget reservation',
                           %s::JSONB
                    FROM hark.run_events WHERE run_id=%s
                    """,
                    (
                        run_id,
                        json.dumps({"budget_key": budget_key}, separators=(",", ":")),
                        run_id,
                    ),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def retrieve_experience(
        self, demo_id: str, embedding: list[float], threshold: float
    ) -> RetrievedExperience | None:
        literal = vector_literal(embedding)
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, 1 - (embedding <=> CAST(%s AS VECTOR)) AS similarity,
                       experience_brief, failure_fingerprint, recovery
                FROM hark.experiences
                WHERE demo_id=%s AND skill_id=%s AND environment_id=%s AND workflow=%s
                  AND status='succeeded' AND invalidated_at IS NULL AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(%s AS VECTOR)
                LIMIT 1
                """,
                (
                    literal,
                    demo_id,
                    self.skill_id,
                    self.settings.environment_id,
                    self.workflow,
                    literal,
                ),
            )
            row = cursor.fetchone()
            if not row or float(row[1]) < threshold:
                return None
            return RetrievedExperience(
                id=str(row[0]),
                similarity=float(row[1]),
                brief=str(row[2]),
                failure_fingerprint=str(row[3]) if row[3] else None,
                recovery=str(row[4]),
            )

    def find_failure_recovery(self, demo_id: str, fingerprint: str) -> dict[str, Any] | None:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT fr.recovery, fr.experience_id
                FROM hark.failure_recoveries fr
                JOIN hark.experiences e ON e.id=fr.experience_id
                WHERE fr.demo_id=%s AND fr.failure_fingerprint=%s AND e.invalidated_at IS NULL
                """,
                (demo_id, fingerprint),
            )
            row = cursor.fetchone()
            return {"recovery": row[0], "experience_id": str(row[1])} if row else None

    def link_experience(self, run_id: str, experience: RetrievedExperience, use_type: str) -> None:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO hark.run_experience_links
                  (run_id, experience_id, use_type, similarity, brief_snapshot)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (run_id, experience_id, use_type) DO NOTHING
                """,
                (run_id, experience.id, use_type, experience.similarity, experience.brief),
            )
            conn.commit()

    def persist_experience(
        self,
        *,
        run_id: str,
        demo_id: str,
        task: str,
        embedding: list[float] | None,
        failure: dict[str, Any] | None,
        diagnosis: str,
        actions: list[str],
    ) -> str:
        experience_id = str(uuid.uuid4())
        brief, recovery, avoid = experience_brief(failure)
        if embedding is not None and len(embedding) != self.settings.embedding_dimensions:
            raise ValueError("The experience embedding does not match the canonical dimensions.")
        literal = vector_literal(embedding) if embedding else None
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO hark.experiences
                  (id,demo_id,source_run_id,skill_id,environment_id,workflow,original_task,
                   experience_brief,what_happened,failure_fingerprint,failure_detail,recovery,
                   paths_to_avoid,outcome,confidence,status,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::JSONB,%s,%s,%s,0.95,'succeeded',CAST(%s AS VECTOR))
                """,
                (
                    experience_id,
                    demo_id,
                    run_id,
                    self.skill_id,
                    self.settings.environment_id,
                    self.workflow,
                    task,
                    brief,
                    diagnosis[:3000],
                    failure.get("fingerprint") if failure else None,
                    json.dumps(failure or {}, separators=(",", ":")),
                    recovery,
                    avoid,
                    "diagnosis_completed",
                    literal,
                ),
            )
            if failure:
                cursor.execute(
                    """
                    UPSERT INTO hark.failure_recoveries
                      (demo_id,failure_fingerprint,skill_id,environment_id,tool_name,sqlstate,
                       failure_category,recovery,experience_id,last_seen_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    """,
                    (
                        demo_id,
                        failure["fingerprint"],
                        self.skill_id,
                        self.settings.environment_id,
                        failure.get("tool") or failure.get("operation") or "unknown",
                        failure.get("sqlstate"),
                        failure["category"],
                        recovery,
                        experience_id,
                    ),
                )
            conn.commit()
        return experience_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        diagnosis: str | None,
        metrics: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE hark.runs SET status=%s, diagnosis=%s, metrics=%s::JSONB,
                    error_code=%s, error_message=%s, completed_at=now()
                WHERE id=%s
                """,
                (
                    status,
                    diagnosis,
                    json.dumps(metrics, separators=(",", ":"), default=str),
                    error_code,
                    error_message,
                    run_id,
                ),
            )
            cursor.execute("DELETE FROM hark.execution_leases WHERE run_id=%s", (run_id,))
            conn.commit()

    def get_demo(self, demo_id: str) -> dict[str, Any]:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id,created_at,expires_at FROM hark.demos WHERE id=%s AND expires_at > now()",
                (demo_id,),
            )
            demo_row = cursor.fetchone()
            if not demo_row:
                raise NotFoundError("This demo link is invalid or has expired.")
            cursor.execute(
                """
                SELECT id,task,status,diagnosis,metrics,error_code,error_message,created_at,completed_at
                FROM hark.runs WHERE demo_id=%s ORDER BY created_at
                """,
                (demo_id,),
            )
            run_rows = rows(cursor)
            for run in run_rows:
                run["id"] = str(run["id"])
                run["metrics"] = _json_value(run.get("metrics"))
                run["created_at"] = _iso(run.get("created_at"))
                run["completed_at"] = _iso(run.get("completed_at"))
                cursor.execute(
                    """
                    SELECT sequence,event_type,title,detail,payload,created_at
                    FROM hark.run_events
                    WHERE run_id=%s AND event_type <> 'provider_request'
                    ORDER BY sequence
                    """,
                    (run["id"],),
                )
                event_rows = rows(cursor)
                for event in event_rows:
                    event["payload"] = _json_value(event.get("payload"))
                    event["created_at"] = _iso(event.get("created_at"))
                run["events"] = event_rows
            cursor.execute(
                """
                SELECT e.id,e.source_run_id,e.original_task,e.experience_brief,e.failure_fingerprint,
                       e.recovery,e.paths_to_avoid,e.outcome,e.confidence,e.invalidated_at,e.created_at,
                       (SELECT count(*) FROM hark.run_experience_links l WHERE l.experience_id=e.id) AS times_used
                FROM hark.experiences e WHERE e.demo_id=%s ORDER BY e.created_at
                """,
                (demo_id,),
            )
            experience_rows = rows(cursor)
            for experience in experience_rows:
                experience["id"] = str(experience["id"])
                experience["source_run_id"] = str(experience["source_run_id"])
                experience["invalidated_at"] = _iso(experience.get("invalidated_at"))
                experience["created_at"] = _iso(experience.get("created_at"))
                experience["confidence"] = float(experience["confidence"])
            cursor.execute(
                "SELECT count(*) FROM hark.runs "
                "WHERE demo_id=%s AND created_at >= now() - INTERVAL '24 hours'",
                (demo_id,),
            )
            runs_used_24h = int(cursor.fetchone()[0])
            return {
                "id": demo_id,
                "created_at": _iso(demo_row[1]),
                "expires_at": _iso(demo_row[2]),
                "runs": run_rows,
                "experiences": experience_rows,
                "limits": {
                    "runs_used_24h": runs_used_24h,
                    "runs_allowed_24h": self.settings.demo_run_limit_per_day,
                },
            }

    def invalidate_experience(self, demo_id: str, experience_id: str) -> bool:
        with connection(self.settings.memory_database_url) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE hark.experiences SET invalidated_at=now(), invalidation_reason='user_invalidated'
                WHERE id=%s AND demo_id=%s AND invalidated_at IS NULL
                """,
                (experience_id, demo_id),
            )
            changed = cursor.rowcount == 1
            conn.commit()
            return changed

    def diagnostic_query(self, operation: str) -> dict[str, Any]:
        statements = {
            "verify_statement_stats_setting": """
                SHOW CLUSTER SETTING sql.stats.automatic_collection.enabled
            """,
            "profile_statement_fingerprints": """
                SELECT fingerprint_id, metadata->>'query' AS query_text,
                       (statistics->'statistics'->'runLat'->>'mean')::FLOAT8 AS mean_latency,
                       (metadata->>'fullScan')::BOOL AS full_scan,
                       metadata->'index_recommendations' AS index_recommendations
                FROM crdb_internal.statement_statistics
                WHERE aggregated_ts > now() - INTERVAL '24 hours'
                ORDER BY mean_latency DESC LIMIT 5
            """,
            "explain_orders_lookup": """
                EXPLAIN SELECT id, customer_id, status, total_cents, created_at
                FROM hark_demo.orders
                WHERE customer_id = '11111111-1111-4111-8111-111111111111'
                  AND status = 'paid'
                ORDER BY created_at DESC LIMIT 25
            """,
            "inspect_order_indexes": """
                SELECT index_name, column_name, seq_in_index, direction, storing, implicit
                FROM information_schema.statistics
                WHERE table_schema='hark_demo' AND table_name='orders'
                ORDER BY index_name, seq_in_index
            """,
        }
        if operation not in statements:
            raise ValueError("Unsupported diagnostic operation.")
        started = time.perf_counter()
        try:
            with connection(self.settings.diagnostic_database_url) as conn:
                cursor = conn.cursor()
                cursor.execute(statements[operation])
                result_rows = cursor.fetchmany(40)
                columns = [column[0] for column in cursor.description]
                conn.rollback()
                return {
                    "ok": True,
                    "operation": operation,
                    "columns": columns,
                    "rows": [[_safe_cell(value) for value in row] for row in result_rows],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
        except Exception as exc:
            sqlstate, message = _database_error(exc)
            return {
                "ok": False,
                "operation": operation,
                "sqlstate": sqlstate,
                "category": "insufficient_privilege" if sqlstate == "42501" else "database_error",
                "message": message,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }


def experience_brief(failure: dict[str, Any] | None) -> tuple[str, str, str]:
    limitation = failure["message"] if failure else "No database limitation was encountered."
    avoid = (failure.get("tool") or failure.get("operation")) if failure else "None"
    recovery = (
        "Skip the unavailable cluster-setting preflight; use the production-safe "
        "statement statistics view, EXPLAIN, and index metadata."
    )
    brief = (
        "Environment: restricted CockroachDB diagnostic role. "
        f"Known limitation: {limitation} "
        f"Successful path: {recovery} Avoid: {avoid}."
    )
    return brief, recovery, str(avoid)


def failure_fingerprint(skill_id: str, environment_id: str, result: dict[str, Any]) -> str:
    normalized = "|".join(
        [
            skill_id,
            environment_id,
            str(result.get("operation", "unknown")),
            str(result.get("sqlstate", "unknown")),
            str(result.get("category", "unknown")),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _database_error(exc: Exception) -> tuple[str | None, str]:
    sqlstate = None
    message = "CockroachDB rejected the diagnostic operation."
    if getattr(exc, "args", None):
        first = exc.args[0]
        if isinstance(first, dict):
            sqlstate = str(first.get("C")) if first.get("C") else None
            message = str(first.get("M") or message)
        elif isinstance(first, str):
            message = first
    return sqlstate, message[:500]


def _safe_cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)[:1500]


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
