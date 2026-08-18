# Add a workflow to Hark

Hark's core idea is independent of a specific incident type: **procedure comes from an Agent Skill; execution creates experience; CockroachDB carries the relevant experience into later runs.**

A workflow connects that memory plane to a concrete Skill, environment, and safe tool surface. The repository ships with a CockroachDB query-regression workflow, and the same pattern can be repeated for infrastructure, CI/CD, security, data, coding, or other operational agents.

## The workflow contract

Every Hark workflow needs four things:

1. **Skill** — the `SKILL.md` or equivalent procedure the agent should follow.
2. **Scope** — stable `skill_id`, `workflow`, and `environment_id` values so memories are retrieved only where they belong.
3. **Safe tools** — explicit operations the model may choose from; user text should never become arbitrary privileged execution.
4. **Environment adapter** — the code that runs those operations against the real target system and returns structured evidence.

The memory semantics stay the same:

```text
task
  -> scoped memory search
  -> Skill + retrieved experience
  -> bounded tool execution
  -> evidence / failure / recovery / outcome
  -> experience embedding + CockroachDB persistence
  -> later related task retrieves that experience
  -> provenance records exactly what influenced the run
```

Hark's existing CockroachDB tables already separate experience by Skill, workflow, environment, source run, and later use. Each additional workflow should preserve that isolation.

## Fast path: use a coding agent

A coding agent can inspect the repository and extend the existing pattern. Give it the new Skill, the target environment, and the operations you are willing to expose.

Example prompt:

```text
Extend this Hark repository with a Kubernetes incident-diagnosis workflow.

First inspect the existing query-regression workflow and preserve Hark's memory semantics, provenance, retrieval, invalidation, provider routing, budgets, and security boundaries.

Add the Kubernetes Skill under backend/skills, give the workflow its own skill/workflow/environment scope, and implement only explicit read-only tools such as pod status, recent events, deployment inspection, and container logs. Wire task validation, tool descriptions, execution evidence, tests, and documentation by following the existing Hark pattern. Do not weaken or remove the current workflow.

Finish by running the test suite and documenting how to configure the target environment safely.
```

The important instruction is **preserve Hark's memory plane; replace or add only the workflow-specific procedure, scope, tools, and environment integration.**

## Manual path

If you prefer to add a workflow yourself:

### 1. Add the Skill

Place the Skill under:

```text
backend/skills/<your-skill>/SKILL.md
```

Pin or version external Skills so an execution can always be traced back to the procedure that produced it.

### 2. Give it a memory scope

Define a distinct:

```text
skill_id
environment_id
workflow
```

Hark uses these fields before vector ranking so experience from one environment or workflow does not silently influence another.

### 3. Define the allowed tools

Follow the existing bounded-tool pattern in `backend/hark/service.py` and `backend/hark/store.py`:

- expose only operations the workflow genuinely needs,
- describe them clearly for the reasoning model,
- return structured evidence,
- keep destructive or privileged actions outside the surface unless the workflow explicitly requires and governs them.

### 4. Connect the real environment

Implement the operation layer against the target system: Kubernetes, GitHub, a database, cloud APIs, CI/CD, or another service. Credentials stay server-side and should be least-privilege.

### 5. Keep the Hark memory loop

Reuse the existing sequence:

- embed the incoming task,
- retrieve scoped prior experience,
- inject the Experience Brief,
- record immutable run events,
- derive experience from the completed execution,
- persist its embedding and provenance,
- link every later use back to its source experience,
- preserve invalidation.

### 6. Test first-run and related-run behavior

For every workflow, test both sides of the idea:

- a first run with no useful precedent,
- a related run where retrieved experience changes the execution path or recovery behavior.

The second run should still gather fresh evidence; memory is guidance from prior execution, not a cached answer.

## Example workflow ideas

```text
Kubernetes incident response
Skill: deployment / pod triage
Experience: known RBAC boundary, successful log path, recovery sequence

CI/CD failure diagnosis
Skill: pipeline investigation
Experience: environment-specific flaky stage, cache failure, successful fallback

Security investigation
Skill: alert triage
Experience: noisy signal source, validated evidence path, safe escalation route

Repository maintenance
Skill: codebase diagnosis
Experience: project-specific test command, generated-file boundary, recovery from known tooling failure
```

Each workflow can have different tools and environments while keeping the same Hark semantics: **remember what actually happened, scope it correctly, and make that experience available to the next related execution.**
