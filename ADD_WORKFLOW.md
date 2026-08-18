# Add your workflow to Hark

[← README](README.md) · [Setup & operations](SETUP.md)

Hark is built around a reusable idea:

> **Agent Skills provide procedure. Hark carries forward what execution taught the agent.**

A workflow connects that memory plane to a concrete Skill, environment, and safe tool surface. The repository includes a fully working CockroachDB query-regression workflow; you can extend the same architecture to additional operational workflows without changing Hark's core memory semantics.

```text
Skill + scope + safe tools + real environment adapter
                         │
                         ▼
                    Hark runtime
                         │
                 execution evidence
                         │
                         ▼
                  CockroachDB memory
                 ├─ run/event history
                 ├─ derived experience
                 ├─ vector retrieval
                 ├─ failure recovery
                 ├─ provenance
                 └─ invalidation
                         │
                         ▼
                 next related execution
```

## What a workflow provides

Every workflow needs four pieces:

1. **Skill** — the `SKILL.md` or equivalent procedure the agent follows.
2. **Scope** — stable `skill_id`, `workflow`, and `environment_id` values so memory stays where it belongs.
3. **Safe tools** — explicit operations the reasoning model may choose from.
4. **Environment adapter** — code that executes those operations against the real target system and returns structured evidence.

Everything below that boundary can preserve the same Hark behavior:

- scoped memory search before acting,
- immutable execution events,
- experience derivation from real outcomes,
- semantic vector retrieval,
- deterministic failure recovery,
- source-run and later-use provenance,
- invalidation without deleting audit history,
- provider routing and execution budgets.

A deployment can add additional workflow implementations while keeping memory isolated by Skill, workflow, and environment.

---

## Fastest path: use a coding agent

Because the repository already contains one complete production implementation, a coding agent can inspect that pattern and build another workflow around the same Hark core.

### Example prompt

```text
Add a Kubernetes incident-diagnosis workflow to this Hark repository.

Inspect the existing query-regression workflow first. Preserve Hark's memory semantics, scoped retrieval, provenance, invalidation, provider routing, budgets, and security boundaries.

Add the Kubernetes Skill, a unique skill/workflow/environment scope, explicit read-only tools for pod status, recent events, deployment inspection and logs, and a real Kubernetes environment adapter. Add task validation, tests, and setup documentation. Keep the existing workflow working.
```

That is usually enough for a capable coding agent to understand where the workflow-specific code ends and Hark's reusable memory plane begins.

The key instruction is:

> **Extend the workflow layer; preserve the memory plane.**

---

## Manual path

### 1. Add the Skill

Place the Skill under:

```text
backend/skills/<your-skill>/SKILL.md
```

Pin or version externally sourced Skills so each execution remains traceable to the exact procedure used.

### 2. Give the workflow its own scope

Define distinct values for:

```text
skill_id
workflow
environment_id
```

Hark scopes eligible experience before vector ranking. That keeps memories from unrelated workflows or environments from contaminating one another.

### 3. Define the safe tool surface

Follow the current bounded-tool pattern in:

```text
backend/hark/service.py
backend/hark/store.py
```

A good workflow tool should:

- perform one explicit operation,
- return structured evidence,
- have a clear description for the reasoning model,
- use least privilege,
- avoid turning arbitrary user text directly into privileged execution.

For example, a Kubernetes workflow might expose:

```text
get_pod_status
read_recent_events
inspect_deployment
read_container_logs
```

A CI/CD workflow might expose:

```text
get_failed_stage
read_job_logs
inspect_pipeline_config
read_recent_runs
```

The model can choose dynamically among allowed tools, while the workflow controls what can actually execute.

### 4. Connect the real environment

Implement those operations against the target system:

- Kubernetes,
- GitHub,
- CI/CD,
- another database,
- cloud APIs,
- security tooling,
- repository tooling,
- or another operational environment.

Keep credentials server-side and give the workflow only the permissions its tools require.

### 5. Preserve the Hark memory loop

The workflow should continue through the same semantic sequence:

```text
incoming task
    ↓
scoped experience search
    ↓
Skill + Experience Brief
    ↓
bounded tool execution
    ↓
evidence / failure / recovery / outcome
    ↓
derived experience + canonical embedding
    ↓
CockroachDB persistence
    ↓
related future task retrieves experience
    ↓
explicit source/use provenance
```

The related run must still gather fresh evidence. Retrieved memory guides execution; it is not a cached answer.

### 6. Test both sides of memory

For each workflow, verify at minimum:

- a first run with no useful precedent,
- experience persistence,
- a related run that retrieves the right experience,
- changed execution or recovery behavior when memory is useful,
- unrelated tasks below the retrieval threshold,
- workflow/environment isolation,
- provenance,
- invalidation.

---

## Example workflow directions

### Kubernetes incident response

```text
Skill: deployment / pod triage
Environment: production cluster
Experience: known RBAC boundary, successful evidence path, recovery sequence
```

### CI/CD failure diagnosis

```text
Skill: pipeline investigation
Environment: repository / pipeline
Experience: flaky stage, cache failure, successful fallback, project-specific command
```

### Security investigation

```text
Skill: alert triage
Environment: security platform
Experience: noisy signal source, validated evidence path, safe escalation route
```

### Repository maintenance

```text
Skill: codebase diagnosis
Environment: repository
Experience: test command, generated-file boundary, known tooling failure, successful recovery
```

### Data / database operations

```text
Skill: performance or pipeline diagnosis
Environment: database / warehouse / ETL system
Experience: permissions, query/tool constraints, known safe fallback, evidence path
```

---

## The extension rule

Different workflows may use completely different Skills and tools, but the Hark contract stays recognizable:

> **Remember what actually happened, scope it correctly, preserve its provenance, and make relevant experience available to the next execution.**

That is the layer Hark adds around Agent Skills.
