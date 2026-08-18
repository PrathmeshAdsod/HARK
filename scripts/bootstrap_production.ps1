[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AdminSecretFile,
    [string]$Region = "us-east-1",
    [Parameter(Mandatory = $true)]
    [string]$ClusterHost
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$secretPath = [System.IO.Path]::GetFullPath($AdminSecretFile)
$allowedRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
if (-not $secretPath.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The one-time credential must be staged in the operating-system temporary directory."
}
if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
    throw "The one-time CockroachDB credential is unavailable."
}

$adminPassword = [System.IO.File]::ReadAllText($secretPath)
Remove-Item -LiteralPath $secretPath -Force
if (-not $adminPassword) { throw "The one-time CockroachDB credential was empty." }

function New-HarkSecret {
    $bytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return ([Convert]::ToBase64String($bytes)).Replace("+", "A").Replace("/", "B").TrimEnd("=")
}

$memoryPassword = New-HarkSecret
$diagnosticPassword = New-HarkSecret
$adminUrl = "postgresql://hark_admin:$([Uri]::EscapeDataString($adminPassword))@${ClusterHost}:26257/defaultdb?sslmode=verify-full"
$env:HARK_ADMIN_DATABASE_URL = $adminUrl
$env:HARK_MEMORY_PASSWORD = $memoryPassword
$env:HARK_DIAGNOSTIC_PASSWORD = $diagnosticPassword

& (Join-Path $repo ".venv\Scripts\python.exe") (Join-Path $repo "backend\init_db.py")
if ($LASTEXITCODE -ne 0) { throw "CockroachDB initialization failed." }

$memoryUrl = "postgresql://hark_memory:$([Uri]::EscapeDataString($memoryPassword))@${ClusterHost}:26257/defaultdb?sslmode=verify-full"
$diagnosticUrl = "postgresql://hark_diagnostic:$([Uri]::EscapeDataString($diagnosticPassword))@${ClusterHost}:26257/defaultdb?sslmode=verify-full"
$config = @{
    memory_database_url = $memoryUrl
    diagnostic_database_url = $diagnosticUrl
} | ConvertTo-Json -Compress

aws ssm put-parameter --name "/hark/prod/database" --type SecureString --value $config --overwrite --region $Region --description "Hark least-privilege CockroachDB connection URLs" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "SSM database configuration write failed." }
aws ssm put-parameter --name "/hark/prod/execution-enabled" --type String --value "true" --overwrite --region $Region --description "Hark execution kill switch" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "SSM kill switch write failed." }

Write-Output "CockroachDB production schema, least-privilege roles, and encrypted AWS parameters are ready."
