[CmdletBinding()]
param(
    [string]$Region = "us-east-1",
    [string]$StackName = "hark-prod",
    [switch]$SkipPackage
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$archive = Join-Path $repo "dist\hark-lambda.zip"
if (-not $SkipPackage) {
    & (Join-Path $PSScriptRoot "package.ps1") -OutputPath $archive | Out-Null
}
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw "The Lambda package does not exist. Run scripts/package.ps1 first."
}

$accountId = aws sts get-caller-identity --query Account --output text
if ($LASTEXITCODE -ne 0 -or -not $accountId) { throw "AWS identity lookup failed." }
$bucket = "hark-deploy-$accountId-$Region"
$codeKey = "releases/hark-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()).zip"

aws s3api head-bucket --bucket $bucket 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    if ($Region -eq "us-east-1") {
        aws s3api create-bucket --bucket $bucket --region $Region | Out-Null
    } else {
        aws s3api create-bucket --bucket $bucket --region $Region --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw "Deployment bucket creation failed." }
    aws s3api put-public-access-block --bucket $bucket --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" | Out-Null
}

aws s3 cp $archive "s3://$bucket/$codeKey" --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "Artifact upload failed." }

aws cloudformation deploy `
  --template-file (Join-Path $repo "infra\template.yaml") `
  --stack-name $StackName `
  --region $Region `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides "CodeBucket=$bucket" "CodeKey=$codeKey" `
  --no-fail-on-empty-changeset
if ($LASTEXITCODE -ne 0) { throw "CloudFormation deployment failed." }

aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue" --output text
