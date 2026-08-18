# Hark setup, workflow extension, operations, and teardown

[← README](README.md) · **[Add your workflow](ADD_WORKFLOW.md)** · [Live application](https://sdzlkokjx52kzobxgsl74riswa0afbzv.lambda-url.us-east-1.on.aws/)

Hark has two setup paths:

1. **Run the included production workflow** — reproduce the CockroachDB query-regression implementation end to end.
2. **Extend Hark with another workflow** — keep the same execution-memory plane while supplying a different Skill, scope, safe tool surface, and real environment adapter.

The repository's included workflow is a complete working template, not a separate memory architecture. Hark's reusable semantics—scoped recall, immutable evidence, experience derivation, failure recovery, source/use provenance, vector retrieval, invalidation, provider routing, and execution budgets—remain the same when another workflow is added.

For the shortest extension path, start with **[ADD_WORKFLOW.md](ADD_WORKFLOW.md)**.

---

## Workflow model

Every Hark workflow connects four workflow-specific pieces to the common memory plane:

```text
Skill
+ workflow / environment scope
+ safe tools
+ real environment adapter
              │
              ▼
         Hark runtime
              │
      execution evidence
              │
              ▼
       CockroachDB memory
              │
              ▼
    later related execution
```

A workflow should define:

- **Skill** — the procedure the agent follows.
- **Scope** — stable `skill_id`, `workflow`, and `environment_id` values.
- **Safe tools** — explicit operations the reasoning model may select.
- **Environment adapter** — implementation of those operations against the real target system.

Keep the scope distinct for every Skill/environment combination so memory from one context cannot silently influence another.

### Fast extension with a coding agent

A coding agent can inspect Hark's existing workflow and implement another one by following the same contract.

Example:

```text
Add a Kubernetes incident-diagnosis workflow to this Hark repository.
Inspect the existing query-regression workflow first. Preserve Hark's memory semantics, provenance, retrieval, invalidation, provider routing, budgets, and security boundaries. Add the Kubernetes Skill, a unique skill/workflow/environment scope, explicit read-only tools for pod status, recent events, deployment inspection and logs, the real Kubernetes environment adapter, task validation, tests, and setup docs. Keep the current workflow working.
```

The important boundary is simple: **extend the workflow layer; do not replace the Hark memory plane.**

For the manual implementation path, file locations, testing checklist, and more examples, see [ADD_WORKFLOW.md](ADD_WORKFLOW.md).

---

## Prerequisites

The checked-in production path targets PowerShell on Windows, AWS `us-east-1`, Python 3.12, and CockroachDB v26.2 or later.

- Python 3.12
- Docker Desktop
- Git
- AWS CLI v2 authenticated to the intended account
- CockroachDB Cloud account and Basic/free-tier cluster for production
- Google Gemini API key with access to the configured models
- Optional: [`ccloud`](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started) for cluster teardown

Confirm local tools and AWS identity:

```powershell
python --version
docker version
aws --version
aws sts get-caller-identity
```

Keep production credentials server-side. `.env` is ignored and `.env.example` contains placeholders only.

---

## Local setup: included CockroachDB workflow

Create the environment and start the pinned local CockroachDB image:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-dev.txt
docker compose up -d
docker compose ps
```

Initialize the schema and roles. Local Docker runs CockroachDB in insecure development mode, so passwords are accepted for parity but are not enforced by the local server:

```powershell
$env:HARK_ADMIN_DATABASE_URL = 'postgresql://root@localhost:26257/defaultdb?sslmode=disable'
$env:HARK_MEMORY_PASSWORD = 'local-memory-only'
$env:HARK_DIAGNOSTIC_PASSWORD = 'local-diagnostic-only'
.\.venv\Scripts\python.exe .\backend\init_db.py
```

Configure the included workflow and providers:

```powershell
$env:AWS_REGION = 'us-east-1'
$env:HARK_MEMORY_DATABASE_URL = 'postgresql://hark_memory@localhost:26257/defaultdb?sslmode=disable'
$env:HARK_DIAGNOSTIC_DATABASE_URL = 'postgresql://hark_diagnostic@localhost:26257/defaultdb?sslmode=disable'
$env:BEDROCK_MODEL_ID = 'amazon.nova-micro-v1:0'
$env:GEMINI_API_KEY = Read-Host 'Gemini API key'
$env:GEMINI_PRIMARY_MODEL_ID = 'gemini-3.5-flash-lite'
$env:GEMINI_TERTIARY_MODEL_ID = 'gemini-3.1-flash-lite'
$env:GEMINI_EMBEDDING_MODEL_ID = 'gemini-embedding-2'
$env:EMBEDDING_DIMENSIONS = '256'
$env:HARK_ENVIRONMENT_ID = 'restricted-orders-gemini-embedding-2-256-v1'
```

Start the backend and frontend together:

```powershell
.\.venv\Scripts\python.exe .\backend\local_server.py
```

Open:

```text
http://127.0.0.1:8080
```

---

## Local tests

Run the complete suite against the real local CockroachDB container:

```powershell
$env:HARK_TEST_DATABASE_URL = 'postgresql://root@localhost:26257/defaultdb?sslmode=disable'
.\.venv\Scripts\python.exe -m pytest -q -rs
```

The suite covers task validation, kill switch behavior, first/related orchestration, provider routing and fallback, Bedrock health caching, canonical embedding task types/dimensions, provider budgets, diagnosis safety, static/API behavior, real SQL permissions, SQLSTATE `42501`, official-Skill queries, vector retrieval, negative retrieval, structured recovery, invalidation, limits, and concurrency leases.

Test doubles are dependency-injected in tests only. Production has no fake-inference path.

### What to add when you create another workflow

Every additional workflow should add tests for:

- its task validation,
- its allowed-tool boundary,
- real or integration-level environment operations,
- a first run with no useful precedent,
- experience persistence,
- a related run that retrieves relevant experience,
- a case where unrelated memory is rejected,
- failure/recovery behavior where applicable,
- provenance and invalidation,
- isolation from other Skill/workflow/environment scopes.

The second run should still gather fresh evidence. Hark memory is execution guidance, not a cached answer.

---

## Add another workflow manually

The full guide is [ADD_WORKFLOW.md](ADD_WORKFLOW.md). At a high level:

### 1. Add the Skill

Place the Skill under:

```text
backend/skills/<your-skill>/SKILL.md
```

Pin or version external Skills so every run remains traceable to the procedure that produced it.

### 2. Define the workflow scope

Give the workflow distinct values for:

```text
skill_id
workflow
environment_id
```

These values participate in memory isolation before vector ranking.

### 3. Add the safe tool surface

Follow the bounded-tool pattern in:

```text
backend/hark/service.py
backend/hark/store.py
```

Expose only the operations the workflow needs. Tool output should be structured evidence. Avoid turning user text directly into privileged shell commands, SQL, cloud actions, or other unrestricted execution.

### 4. Connect the real environment

Implement the operations against the target system: Kubernetes, GitHub, CI/CD, another database, cloud APIs, security tooling, or another service.

Use server-side credentials and least privilege.

### 5. Reuse the Hark memory loop

Preserve:

```text
task
 -> scoped experience search
 -> Skill + Experience Brief
 -> bounded execution
 -> evidence / failure / recovery / outcome
 -> experience embedding
 -> CockroachDB persistence
 -> later retrieval
 -> explicit source/use provenance
```

### 6. Verify workflow isolation

A new workflow must not retrieve experience from another Skill or environment unless you explicitly design a compatible shared scope.

---

## CockroachDB production bootstrap

The commands below reproduce the included CockroachDB workflow.

1. In CockroachDB Cloud, create a Basic/free-tier cluster named `hark-prod` on AWS in `us-east-1`.
2. Configure a spending/resource limit in the Cloud console if the selected plan exposes one.
3. Create an initial SQL user with administrative setup privileges and copy its generated password once.
4. Record the General connection-string hostname. Do not commit the admin password.

The bootstrap creates two runtime identities:

- `hark_memory` — owns Hark's memory lifecycle.
- `hark_diagnostic` — read-only evidence identity for the included workflow.

It applies `backend/schema.sql`, grants minimum permissions, and stores only the resulting URLs in SSM SecureString.

Stage the one-time admin password in the OS temporary directory without echoing it:

```powershell
$credentialPath = Join-Path ([System.IO.Path]::GetTempPath()) 'hark-admin-one-time.txt'
$securePassword = Read-Host 'CockroachDB admin password' -AsSecureString
$passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    [System.IO.File]::WriteAllText(
        $credentialPath,
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
    )
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr)
}

.\scripts\bootstrap_production.ps1 `
  -AdminSecretFile $credentialPath `
  -ClusterHost 'YOUR-CLUSTER-HOST.cockroachlabs.cloud' `
  -Region 'us-east-1'
```

The included diagnostic identity has `USAGE` on `hark_demo` and `SELECT` only on the fixed `orders` and `customers` tables. It has no public-schema creation privilege and no cluster privileges.

That real permission boundary is why the Skill's cluster-setting preflight can return SQLSTATE `42501`, while the production-safe statistics view, `EXPLAIN`, and index metadata remain available.

For another workflow, keep `hark_memory` as the common memory identity if appropriate, and create/configure a separate least-privilege environment identity for that workflow's real tools.

---

## Store provider credentials

Production loads the Gemini key from `/hark/prod/providers`.

```powershell
@'
import getpass
import json
import boto3

key = getpass.getpass("Gemini API key: ")
if not key.strip():
    raise SystemExit("No key supplied")
boto3.client("ssm", region_name="us-east-1").put_parameter(
    Name="/hark/prod/providers",
    Description="Encrypted Hark provider credentials",
    Type="SecureString",
    Value=json.dumps({"gemini_api_key": key}),
    Overwrite=True,
)
print("Stored /hark/prod/providers as SecureString")
'@ | .\.venv\Scripts\python.exe -
```

Verify metadata only:

```powershell
aws ssm get-parameter `
  --name '/hark/prod/providers' `
  --region us-east-1 `
  --query 'Parameter.[Name,Type]' `
  --output table
```

---

## Provider behavior

Reasoning is provider-resilient:

1. **Amazon Bedrock Nova Micro** is the preferred primary route whenever the account-level availability check reports it authorized.
2. **Gemini 3.5 Flash-Lite** is the automatic fallback when the preferred route is unavailable.
3. **Gemini 3.1 Flash-Lite** is the tertiary fallback for recognized provider failures.

Bedrock availability is health-gated and cached for 300 seconds, so a known-unavailable route does not add a failed inference call to every run. If authorization becomes available later, the router returns to Bedrock automatically.

Invalid application requests are not disguised as provider failures.

### Canonical memory embeddings

Gemini Embedding 2 is the single canonical memory embedding route. Query and document embeddings use their respective retrieval task types, request exactly 256 values, and are rejected if the provider returns another shape.

This is deliberate: reasoning providers may change between runs, but semantic memory must remain in one comparable vector space.

Verify Bedrock independently:

```powershell
$env:AWS_REGION = 'us-east-1'
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

The command exits non-zero unless account authorization and a real Nova conversation succeed.

---

## AWS deployment

Validate, package, and deploy:

```powershell
aws cloudformation validate-template `
  --template-body file://infra/template.yaml `
  --region us-east-1

.\scripts\deploy.ps1 -Region 'us-east-1' -StackName 'hark-prod'
```

The deployment script packages the backend, frontend, included Skill, and dependencies; creates/reuses a private project artifact bucket with public access blocked; uploads a timestamped package; deploys CloudFormation with `CAPABILITY_NAMED_IAM`; and prints the public Function URL.

The stack creates the Lambda runtime, runtime role, Function URL/policies, and outputs. SSM parameters and the S3 artifact bucket are bootstrapped outside the stack and therefore appear explicitly in teardown.

Ordinary update:

```powershell
.\scripts\deploy.ps1
```

Inspect a package before upload:

```powershell
.\scripts\package.ps1
$env:PYTHONPATH = (Resolve-Path .\dist\lambda).Path
.\.venv\Scripts\python.exe -c "from hark.web import lambda_handler; print(lambda_handler({'requestContext':{'http':{'method':'GET'}},'rawPath':'/'},None)['statusCode'])"
.\scripts\deploy.ps1 -SkipPackage
```

If you add another workflow, make sure its Skill files and runtime dependencies are included by the packaging path before deployment.

---

## Production verification

Verify the real database and memory lifecycle without printing secrets:

```powershell
$env:AWS_REGION = 'us-east-1'
$env:HARK_DATABASE_PARAMETER = '/hark/prod/database'
.\.venv\Scripts\python.exe .\scripts\verify_production.py
```

The included verifier checks both identities and denied writes, executes all four CockroachDB diagnostic operations, inserts/searches a 256-dimensional verification vector through the production vector index, verifies deterministic recovery, invalidates the experience, and confirms that both retrieval paths exclude it.

Run the Bedrock verifier separately:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

A Bedrock authorization failure does not invalidate the provider-resilient runtime; it means the router will use its configured fallback until Bedrock becomes authorized.

Finally verify the live application in a real browser:

- landing page,
- fresh investigation,
- first run,
- related run,
- comparison metrics,
- experience provenance,
- invalidation,
- refresh/direct investigation URL,
- responsive layout,
- browser console.

For any new workflow, add an equivalent production verification path that proves the real environment tools and first→related memory behavior.

---

## Limits and kill switch

Current production defaults:

- 4 runs per investigation per rolling 24 hours
- 40 runs per UTC day
- 1,000 lifetime runs
- 3 active database leases
- 5 model/tool iterations
- 60-second application deadline
- 45-day anonymous-link lifetime
- Gemini 3.5 reasoning: 200 requests/day, 12/minute
- Gemini 3.1 reasoning: 100 requests/day, 12/minute
- Gemini Embedding 2: 150 requests/day, 90/minute

Daily and per-minute provider reservations are enforced atomically in CockroachDB and are hidden from the public execution trace.

Disable all new execution while keeping existing investigations readable:

```powershell
aws ssm put-parameter `
  --name '/hark/prod/execution-enabled' `
  --type String `
  --value 'false' `
  --overwrite `
  --region us-east-1
```

Re-enable after checking the reason for the pause:

```powershell
aws ssm put-parameter `
  --name '/hark/prod/execution-enabled' `
  --type String `
  --value 'true' `
  --overwrite `
  --region us-east-1
```

If the kill-switch read fails, Hark fails closed and blocks new runs.

---

## Troubleshooting

### Bedrock `Operation not allowed`

First inspect account-level availability and run the real verifier.

In the hackathon deployment account on 18 August 2026, agreement, entitlement, and region were available while Bedrock authorization was not. Hark therefore used its verified Gemini fallback for the acceptance run. Bedrock remains the preferred primary route and will be selected automatically when the account-level availability check reports authorization.

If the restriction persists, open an AWS Support account/access case with the current account, region, model ID, availability response, and the `ValidationException: Operation not allowed` runtime result.

Do not keep retrying the same blocked call. After AWS confirms an authorization change, rerun:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

Record Bedrock as successful only when the real verifier passes.

### Public URL returns 403

Lambda Function URLs require both public resource-policy actions in this stack: `lambda:InvokeFunctionUrl` with auth type `NONE`, and `lambda:InvokeFunction` with `InvokedViaFunctionUrl: true`.

Confirm both resources remain in `infra/template.yaml`, then redeploy.

### Database connection fails

```powershell
aws ssm get-parameter `
  --name '/hark/prod/database' `
  --region us-east-1 `
  --query 'Parameter.[Name,Type]' `
  --output table
```

This prints metadata only. Confirm the cluster is available and its CA-verifiable endpoint is reachable, then run `scripts/verify_production.py`.

### Deployment fails

Read the CloudFormation failure before changing configuration:

```powershell
aws cloudformation describe-stack-events `
  --stack-name hark-prod `
  --region us-east-1 `
  --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" `
  --output table
```

The current AWS account has a regional concurrency limit of 10 and requires all 10 to remain unreserved, so Hark uses its CockroachDB execution-lease limit instead of Lambda reserved concurrency.

---

## Teardown

Teardown is destructive. Disable execution first and verify the exact AWS account, stack, bucket, parameters, and cluster before proceeding.

```powershell
aws sts get-caller-identity

aws ssm put-parameter `
  --name '/hark/prod/execution-enabled' `
  --type String `
  --value 'false' `
  --overwrite `
  --region us-east-1

aws cloudformation delete-stack --stack-name 'hark-prod' --region us-east-1
aws cloudformation wait stack-delete-complete --stack-name 'hark-prod' --region us-east-1

aws ssm delete-parameters `
  --names '/hark/prod/database' '/hark/prod/providers' '/hark/prod/execution-enabled' `
  --region us-east-1

$accountId = aws sts get-caller-identity --query Account --output text
$artifactBucket = "hark-deploy-$accountId-us-east-1"
aws s3 rm "s3://$artifactBucket" --recursive --region us-east-1
aws s3api delete-bucket --bucket $artifactBucket --region us-east-1
```

Deleting `/hark/prod/providers` removes Hark's encrypted copy only. It does **not** revoke the underlying Google API key. Revoke that key separately only if it is dedicated to Hark and no other application uses it.

Delete the CockroachDB cluster with the official CLI:

```powershell
ccloud auth login
ccloud cluster delete hark-prod
```

Cluster deletion permanently removes its data. The console equivalent is **hark-prod → Actions → Delete cluster**, then enter the exact cluster name.

No project-specific AWS access key is created by this repository. If one was created manually, list and remove only that exact key:

```powershell
aws iam list-access-keys --user-name '<DEPLOY_USER>'
aws iam update-access-key --user-name '<DEPLOY_USER>' --access-key-id '<PROJECT_KEY_ID>' --status Inactive
aws iam delete-access-key --user-name '<DEPLOY_USER>' --access-key-id '<PROJECT_KEY_ID>'
```

Stop and remove only the local development container and its anonymous volume:

```powershell
docker compose down -v
```

Do not delete unrelated AWS, IAM, S3, SSM, Google, or CockroachDB resources.
