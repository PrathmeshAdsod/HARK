# Hark setup, operations, and teardown

These instructions reproduce the checked-in Python/Lambda architecture. Commands target PowerShell on Windows, AWS `us-east-1`, and CockroachDB v26.2 or later.

## Prerequisites

- Python 3.12
- Docker Desktop
- Git
- AWS CLI v2 authenticated to the intended account
- A CockroachDB Cloud account for production
- Optional: [`ccloud`](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started) for cluster teardown

Confirm the tools and AWS identity before making changes:

```powershell
python --version
docker version
aws --version
aws sts get-caller-identity
```

Never paste database passwords or AWS keys into Git, `.env`, browser storage, issue text, or chat. `.env` is ignored; `.env.example` contains placeholders only.

## Local setup

Create the environment and start the pinned local CockroachDB image:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-dev.txt
docker compose up -d
docker compose ps
```

Initialize the schema and roles. Local Docker runs CockroachDB in insecure development mode, so passwords are accepted as inputs for parity but are not applied by the server:

```powershell
$env:HARK_ADMIN_DATABASE_URL = 'postgresql://root@localhost:26257/defaultdb?sslmode=disable'
$env:HARK_MEMORY_PASSWORD = 'local-memory-only'
$env:HARK_DIAGNOSTIC_PASSWORD = 'local-diagnostic-only'
.\.venv\Scripts\python.exe .\backend\init_db.py
```

Configure the two local identities and AWS region:

```powershell
$env:AWS_REGION = 'us-east-1'
$env:HARK_MEMORY_DATABASE_URL = 'postgresql://hark_memory@localhost:26257/defaultdb?sslmode=disable'
$env:HARK_DIAGNOSTIC_DATABASE_URL = 'postgresql://hark_diagnostic@localhost:26257/defaultdb?sslmode=disable'
$env:BEDROCK_MODEL_ID = 'amazon.nova-micro-v1:0'
$env:BEDROCK_EMBEDDING_MODEL_ID = 'amazon.titan-embed-text-v2:0'
$env:EMBEDDING_DIMENSIONS = '256'
```

The local process uses the normal AWS credential chain for Bedrock. Start the backend and static frontend together:

```powershell
.\.venv\Scripts\python.exe .\backend\local_server.py
```

Open `http://127.0.0.1:8080`.

## Local tests

Run the complete suite against the real local CockroachDB container:

```powershell
$env:HARK_TEST_DATABASE_URL = 'postgresql://root@localhost:26257/defaultdb?sslmode=disable'
.\.venv\Scripts\python.exe -m pytest -q -rs
```

This covers task narrowing, the execution kill switch, Cold/Warm orchestration with a deterministic test gateway, static/API behavior, real SQL permissions, the `42501` boundary, official-Skill query, safe recovery operations, vector retrieval, negative/weak retrieval, structured failure recall, invalidation, per-demo limits, and database-backed concurrency leases.

The deterministic gateway exists only in test source. Production has no fake-inference switch.

## CockroachDB production

1. In CockroachDB Cloud, create a **Basic** cluster named `hark-prod` on AWS in `us-east-1`.
2. Set a hard usage cap before loading data. The current deployment was configured for 60 million RUs and 6 GiB, displayed as a $15 monthly hard cap at creation time.
3. Create an initial SQL user with administrative setup privileges and copy its generated password once.
4. Record the General connection-string hostname, but do not save the admin password in the repository.

The bootstrap creates `hark_memory` and `hark_diagnostic` with random passwords, applies `backend/schema.sql`, grants the minimum table/schema permissions, and stores only the two resulting URLs in SSM SecureString.

Stage the admin password in an OS temporary file without echoing it, then run the bootstrap. The script validates that the file is under the temp directory and deletes it immediately after reading:

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

The runtime roles are intentionally separate:

- `hark_memory`: CRUD on tables in schema `hark`; no public-schema creation.
- `hark_diagnostic`: `USAGE` on `hark_demo` and `SELECT` only on the fixed `orders` and `customers` tables; no public-schema creation and no cluster privileges.

The cluster-setting preflight consequently fails with SQLSTATE `42501`, while the production-safe statistics view, `EXPLAIN`, and `information_schema.statistics` remain usable.

## Bedrock access

Hark uses these current IDs in `us-east-1`:

- `amazon.nova-micro-v1:0`
- `amazon.titan-embed-text-v2:0`

The Lambda role in `infra/template.yaml` grants `bedrock:InvokeModel` only for those two regional foundation-model ARNs. Nova is called through the Converse API with bounded output and at most five agent tool iterations. Titan is called with 256 dimensions and normalization enabled.

Before deployment, verify the account rather than assuming access:

```powershell
$env:AWS_REGION = 'us-east-1'
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

The command exits non-zero if either the embedding or conversation call fails.

## AWS deployment

Validate, package, and deploy:

```powershell
aws cloudformation validate-template `
  --template-body file://infra/template.yaml `
  --region us-east-1

.\scripts\deploy.ps1 -Region 'us-east-1' -StackName 'hark-prod'
```

The script:

1. creates `dist/hark-lambda.zip` from the backend, frontend, Skill, and pinned dependencies;
2. obtains the current account ID;
3. creates or reuses the private `hark-deploy-<account>-us-east-1` S3 bucket with public access blocked;
4. uploads a timestamped artifact;
5. deploys `infra/template.yaml` with `CAPABILITY_NAMED_IAM`;
6. prints the public Function URL.

The stack creates only project-named resources: Lambda function `hark-prod`, role `hark-prod-lambda-role`, its Function URL/policies, and outputs. The two SSM parameters and S3 deployment bucket are bootstrapped outside the stack so teardown lists them explicitly.

## Update deployment

Ordinary update:

```powershell
.\scripts\deploy.ps1
```

To inspect a package before uploading it:

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

The verifier uses an isolated anonymous demo, checks both identities and denied writes, executes all four diagnostic operations, inserts/searches a 256-dimensional verification vector through the production vector index, verifies deterministic recovery, invalidates the experience, and confirms both retrieval paths exclude it.

Then test Bedrock separately:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_bedrock.py
```

Finally open the Function URL in Chrome and verify landing, new demo, Cold run, Warm run, memory inspection/invalidation, fresh demo, refresh, direct demo URL, mobile layout, and console errors. Do not treat a successful deploy command as browser verification.

## Limits and kill switch

The server-side defaults are in `infra/template.yaml` and `backend/hark/config.py`:

- 4 runs per demo per rolling 24 hours
- 200 runs per UTC day
- 1,000 total runs
- 3 active database leases
- 5 model/tool iterations
- 60-second application deadline
- 45-day anonymous link lifetime

Disable all new execution while keeping existing demos readable:

```powershell
aws ssm put-parameter `
  --name '/hark/prod/execution-enabled' `
  --type String `
  --value 'false' `
  --overwrite `
  --region us-east-1
```

Re-enable only after verifying the reason for the pause:

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

Confirm region, IDs, and runtime permission, then run `scripts/verify_bedrock.py`. If the model catalog is active and the direct probe still returns `ValidationException: Operation not allowed`, open an AWS Support account/access case with:

> Account `<ACCOUNT_ID>`, region `us-east-1`: Bedrock InvokeModel/Converse for `amazon.nova-micro-v1:0` and `amazon.titan-embed-text-v2:0` returns `ValidationException: Operation not allowed`. Model availability and `bedrock:InvokeModel` IAM permission are confirmed. Please remove the account-specific Bedrock runtime restriction.

The hackathon manager gives the same AWS Support escalation in the [official Devpost discussion](https://cockroachdb-ai.devpost.com/forum_topics/44642-operation-not-allowed-error). Do not add a fake provider fallback.

After AWS confirms the change, run the Bedrock verifier, deploy if configuration changed, create a fresh demo in Chrome, and complete two differently worded production runs. Record only the values the live UI returns.

### Public URL returns 403

Lambda Function URLs created after October 2025 require both public resource-policy actions. Confirm both resources exist in `infra/template.yaml`: `lambda:InvokeFunctionUrl` with auth type `NONE`, and `lambda:InvokeFunction` with `InvokedViaFunctionUrl: true`. Redeploy the stack.

### Database connection fails

```powershell
aws ssm get-parameter `
  --name '/hark/prod/database' `
  --with-decryption `
  --region us-east-1 `
  --query 'Parameter.Type' `
  --output text
```

This prints only `SecureString`, not its value. Confirm the cluster is Available and its CA-verifiable endpoint is reachable. Re-run `scripts/verify_production.py` for a safe diagnosis.

### Deployment fails

Read the actual CloudFormation event before changing anything:

```powershell
aws cloudformation describe-stack-events `
  --stack-name hark-prod `
  --region us-east-1 `
  --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" `
  --output table
```

The current AWS account has a regional concurrency limit of 10 and requires all 10 to remain unreserved, so the template intentionally relies on the CockroachDB lease limit instead of Lambda reserved concurrency.

## Teardown

Teardown is destructive and CockroachDB cluster deletion is irreversible. Disable execution first and verify the exact account/stack/cluster names before running these commands.

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
  --names '/hark/prod/database' '/hark/prod/execution-enabled' `
  --region us-east-1

$accountId = aws sts get-caller-identity --query Account --output text
$artifactBucket = "hark-deploy-$accountId-us-east-1"
aws s3 rm "s3://$artifactBucket" --recursive --region us-east-1
aws s3api delete-bucket --bucket $artifactBucket --region us-east-1
```

Delete the CockroachDB cluster with the official CLI:

```powershell
ccloud auth login
ccloud cluster delete hark-prod
```

The command permanently deletes the cluster and its data. The console equivalent is **hark-prod → Actions → Delete cluster**, then enter the exact cluster name.

No project-specific AWS access key is created by this repository. If one was created manually for deployment, list it and remove only that exact key:

```powershell
aws iam list-access-keys --user-name '<DEPLOY_USER>'
aws iam update-access-key --user-name '<DEPLOY_USER>' --access-key-id '<PROJECT_KEY_ID>' --status Inactive
aws iam delete-access-key --user-name '<DEPLOY_USER>' --access-key-id '<PROJECT_KEY_ID>'
```

Stop and remove only the local development container and its anonymous volume:

```powershell
docker compose down -v
```

Do not delete unrelated AWS, IAM, S3, SSM, or CockroachDB resources.
