[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\dist\hark-lambda.zip")
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dist = Join-Path $repo "dist"
$stage = Join-Path $dist "lambda"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Create the project virtual environment before packaging. See SETUP.md."
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null

& $python -m pip install --disable-pip-version-check --no-compile --target $stage -r (Join-Path $repo "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency packaging failed." }

Copy-Item -LiteralPath (Join-Path $repo "backend\hark") -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repo "backend\skills") -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repo "frontend") -Destination $stage -Recurse

Get-ChildItem -Path $stage -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $stage -File -Recurse -Include "*.pyc" | Remove-Item -Force

New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($resolvedOutput)) -Force | Out-Null
if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -LiteralPath $resolvedOutput -Force
}
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $resolvedOutput -CompressionLevel Optimal
Write-Output $resolvedOutput
