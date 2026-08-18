# Hark setup, operations, and teardown

These instructions reproduce the checked-in Python/Lambda architecture. Commands target PowerShell on Windows, AWS `us-east-1`, and CockroachDB v26.2 or later.

## Prerequisites

- Python 3.12
- Docker Desktop
- Git
- AWS CLI v2 authenticated to the intended account
- A CockroachDB Cloud account and Basic/free-tier cluster for production
- A Google Gemini API key with access to the configured models
- Optional: [`ccloud`](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started) for cluster teardown

Confirm local tools and AWS identity:

```powershell
python --version
docker version
aws --version
aws sts get-caller-identity
```

Never paste database passwords, Gemini keys, or AWS credentials into Git, `.env`, browser storage, issue text, screenshots, or chat. `.env` is ignored and `.env.example` contains placeholders only.

## Local setup

Create the environment and start the pinned local CockroachDB image:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-dev.txt
docker compose up -d
docker compose ps
```

Initialize the schema and roles. Local Docker runs CockroachDB in insecure development mode, so passwords are accepted for parity but are not applied by the server:

```powershell
$env:HARK_ADMIN_DATABASE_URL = 'postgresql://root@localhost:26257/defaultdb?sslmode=disable'
$env:HARK_MEMORY_PASSWORD = 'local-memory-only'
$env:HARK_DIAGNOSTIC_PASSWORD = 'local-diagnostic-only'
.\.venv\Scripts\python.exe .\backend\init_db.py
```

Configure local identities and providers. `Read-Host` prevents the Gemini key from appearing in shell history:

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

Start the backend and frontend together, then open `http://127.0.0.1:8080`:

```powershell
.\.venv\Scripts\python.exe .\backend\local_server.py
```

## Local tests

Run the complete suite against the real local CockroachDB container:

```powershell
$env:HARK_TEST_DATABASE_URL = 'postgresql://root@localhost:26257/defaultdb?sslmode=disable'
.\.venv\Scripts\python.exe -m pytest -q -rs
```

The suite covers task validation, kill switch, first/related orchestration, provider routing and fallback, Bedrock health caching, canonical embedding task types/dimensions, provider budgets, diagnosis safety, static/API behavior, real SQL permissions, `42501`, official-Skill queries, vector retrieval, negative retrieval, structured recovery, invalidation, limits, and concurrency leases. Test doubles are dependency-injected in tests only; production has no fake-inference mode.

## CockroachDB production bootstrap

1. In CockroachDB Cloud, create a Basic/free-tier cluster named `hark-prod` on AWS in `us-east-1`.
2. Configure a spending or resource limit in the Cloud console if the selected plan exposes one. Check the current CockroachDB pricing page rather than relying on a historical amount.
3. Create an initial SQL user with administrative setup privileges and copy its generated password once.
4. Record the General connection-string hostname, but do not save the admin password in the repository.

The bootstrap creates `hark_memory` and `hark_diagnostic` with random passwords, applies `backend/schema.sql`, grants minimum permissions, and stores only the resulting URLs in SSM SecureString.

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

The script validates the temporary-file location and deletes the file after reading it. The runtime roles are separate:

- `hark_memory`: CRUD on Hark tables; no public-schema creation.
- `hark_diagnostic`: `USAGE` on `hark_demo` and `SELECT` only on fixed `orders` and `customers` tables; no public-schema creation and no cluster privileges.

The cluster-setting preflight consequently fails with SQLSTATE `42501`, while the production-safe statistics view, `EXPLAIN`, and `information_schema.statistics` remain usable.

## Store the provider secret

Production loads the Gemini key from `/hark/prod/providers`. The following prompt stores it without echoing it or writing it to disk:

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

Verify metadata only—never request or print the decrypted value:

```powershell
aws ssm get-parameter `
  --name '/hark/prod/providers' `
  --region us-east-1 `
  --query 'Parameter.[Name,Type]' `
  --output table
```

## Provider behavior

Reasoning order is Bedrock Nova Micro → Gemini 3.5 Flash-Lite → Gemini 3.1 Flash-Lite. Bedrock is attempted only when the account-level availability response is authorized and that health result is cached for 300 seconds. The tertiary Gemini route is used only after a recognized provider failure; invalid application requests surface as errors.

Gemini Embedding 2 is the only memory embedding route. Query and document embeddings use their distinct retrieval task types, request exactly 256 values, and are rejected if the provider returns another shape.

Check Bedrock without assuming access:

```powershell
$env:AWS_REGION = 'us-east-1'
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

The command exits non-zero unless account authorization and a real Nova conversation both succeed. Gemini model IDs and embedding dimensionality should be checked against Google's current model documentation before changing configuration.

## AWS deployment

Validate, package, and deploy:

```powershell
aws cloudformation validate-template `
  --template-body file://infra/template.yaml `
  --region us-east-1

.\scripts\deploy.ps1 -Region 'us-east-1' -StackName 'hark-prod'
```

The deployment script packages the backend, frontend, Skill, and dependencies; creates or reuses a private project artifact bucket with public access blocked; uploads a timestamped package; deploys CloudFormation with `CAPABILITY_NAMED_IAM`; and prints the public Function URL.

The stack creates `hark-prod`, `hark-prod-lambda-role`, its Function URL and policies, and outputs. The three SSM parameters and S3 artifact bucket are bootstrapped outside the stack and therefore appear explicitly in teardown.

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

## Production verification

Verify the real database and memory lifecycle without printing secrets:

```powershell
$env:AWS_REGION = 'us-east-1'
$env:HARK_DATABASE_PARAMETER = '/hark/prod/database'
.\.venv\Scripts\python.exe .\scripts\verify_production.py
```

The verifier uses an isolated anonymous demo, checks both identities and denied writes, executes all four diagnostic operations, inserts and searches a 256-dimensional verification vector through the production vector index, verifies deterministic recovery, invalidates the experience, and confirms that both retrieval paths exclude it.

Then run the Bedrock verifier separately. A Bedrock failure does not invalidate the bounded Gemini fallback, but it must be reported truthfully:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

Finally, use Chrome to verify the landing page, a fresh investigation, first and related tasks, comparison metrics, memory provenance/invalidation, refresh, direct demo URL, mobile layout, and console output. Do not treat deployment success as browser verification.

## Limits and kill switch

Server-side production defaults:

- 4 runs per demo per rolling 24 hours
- 40 runs per UTC day
- 1,000 lifetime runs
- 3 active database leases
- 5 model/tool iterations
- 60-second application deadline
- 45-day anonymous-link lifetime
- Gemini 3.5 reasoning: 200 requests/day, 12/minute
- Gemini 3.1 reasoning: 100 requests/day, 12/minute
- Gemini Embedding 2: 150 requests/day, 90/minute

Daily and per-minute provider reservations are enforced atomically in CockroachDB and are hidden from the public trace.

Disable all new execution while keeping existing demos readable:

```powershell
aws ssm put-parameter `
  --name '/hark/prod/execution-enabled' `
  --type String `
  --value 'false' `
  --overwrite `
  --region us-east-1
```

Re-enable only after checking the reason for the pause:

```powershell
aws ssm put-parameter `
  --name '/hark/prod/execution-enabled' `
  --type String `
  --value 'true' `
  --overwrite `
  --region us-east-1
```

If the kill-switch read fails, Hark fails closed and blocks new runs.

## Troubleshooting

### Bedrock `Operation not allowed`

Inspect current account-level availability and run the verifier. In the deployment account on 18 August 2026, agreement, entitlement, and region were `AVAILABLE`, while `authorizationStatus` was `NOT_AUTHORIZED`; the runtime returned `ValidationException: Operation not allowed`.

If that remains true, open an AWS Support account/access case with:

> Account `<ACCOUNT_ID>`, region `us-east-1`: Bedrock availability for `amazon.nova-micro-v1:0` reports `authorizationStatus: NOT_AUTHORIZED`, and Converse/InvokeModel returns `ValidationException: Operation not allowed`. Agreement, entitlement, region, and the documented IAM permissions have been checked. Please review and remove the account-level Bedrock authorization restriction.

Do not keep retrying the same call. Hark's known-unavailable health cache will use the configured Gemini route. After AWS confirms a change, rerun the verifier and a fresh production investigation; record Bedrock as successful only if the real response passes.

### Public URL returns 403

Lambda Function URLs require both public resource-policy actions in this stack: `lambda:InvokeFunctionUrl` with auth type `NONE`, and `lambda:InvokeFunction` with `InvokedViaFunctionUrl: true`. Confirm both resources remain in `infra/template.yaml`, then redeploy.

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

The current AWS account has a regional concurrency limit of 10 and requires all 10 to remain unreserved, so the template uses the CockroachDB lease limit instead of Lambda reserved concurrency.

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

Deleting `/hark/prod/providers` removes Hark's encrypted copy only. It does not revoke the underlying Google API key; revoke that key separately in the Google project only if it is dedicated to Hark and no other application uses it.

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
