from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    aws_region: str
    memory_database_url: str
    diagnostic_database_url: str
    bedrock_model_id: str
    embedding_model_id: str
    embedding_dimensions: int
    similarity_threshold: float
    demo_run_limit_per_day: int
    global_run_limit_per_day: int
    global_total_run_limit: int
    max_concurrent_runs: int
    max_agent_iterations: int
    run_timeout_seconds: int
    execution_enabled: bool
    kill_switch_parameter: str
    retention_days: int
    environment_id: str


def _load_ssm_config(region: str) -> dict[str, str]:
    parameter_name = os.getenv("HARK_DATABASE_PARAMETER", "")
    if not parameter_name:
        return {}
    import boto3

    response = boto3.client("ssm", region_name=region).get_parameter(
        Name=parameter_name, WithDecryption=True
    )
    value = json.loads(response["Parameter"]["Value"])
    return {str(key): str(item) for key, item in value.items()}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    secure = _load_ssm_config(region)
    return Settings(
        aws_region=region,
        memory_database_url=os.getenv(
            "HARK_MEMORY_DATABASE_URL", secure.get("memory_database_url", "")
        ),
        diagnostic_database_url=os.getenv(
            "HARK_DIAGNOSTIC_DATABASE_URL", secure.get("diagnostic_database_url", "")
        ),
        bedrock_model_id=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0"),
        embedding_model_id=os.getenv(
            "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
        ),
        embedding_dimensions=_int("EMBEDDING_DIMENSIONS", 256),
        similarity_threshold=_float("MEMORY_SIMILARITY_THRESHOLD", 0.72),
        demo_run_limit_per_day=_int("DEMO_RUN_LIMIT_PER_DAY", 4),
        global_run_limit_per_day=_int("GLOBAL_RUN_LIMIT_PER_DAY", 200),
        global_total_run_limit=_int("GLOBAL_TOTAL_RUN_LIMIT", 1000),
        max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 3),
        max_agent_iterations=_int("MAX_AGENT_ITERATIONS", 5),
        run_timeout_seconds=_int("RUN_TIMEOUT_SECONDS", 60),
        execution_enabled=os.getenv("EXECUTION_ENABLED", "true").lower() == "true",
        kill_switch_parameter=os.getenv("HARK_KILL_SWITCH_PARAMETER", ""),
        retention_days=_int("DEMO_RETENTION_DAYS", 45),
        environment_id=os.getenv("HARK_ENVIRONMENT_ID", "restricted-orders-demo-v1"),
    )
