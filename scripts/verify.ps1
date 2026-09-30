[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
$uv = (Get-Command uv -ErrorAction Stop).Source

function Invoke-Checked {
    param(
        [string]$Label,
        [string[]]$Arguments
    )
    Write-Host "==> $Label"
    & $uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Invoke-Checked 'uv environment' @('--version')
Invoke-Checked 'Locked dependencies' @(
    'sync', '--python', '3.11', '--locked', '--extra', 'dev', '--extra', 'semantic',
    '--no-install-project'
)
Invoke-Checked 'Editable project install' @(
    'sync', '--python', '3.11', '--locked', '--extra', 'dev', '--extra', 'semantic',
    '--no-build-isolation'
)
Invoke-Checked 'Python version' @('run', '--no-sync', 'python', '--version')
Invoke-Checked 'Lint' @('run', '--no-sync', 'ruff', 'check', 'src', 'tests', 'scripts', 'benchmarks')
Invoke-Checked 'Format' @(
    'run', '--no-sync', 'ruff', 'format', '--check', 'src', 'tests', 'scripts', 'benchmarks'
)
Invoke-Checked 'Types' @('run', '--no-sync', 'mypy', 'src/prism')

$work = Join-Path $repoRoot 'work'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$runName = 'verify-{0}-{1}' -f $PID, [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$testRoot = Join-Path $work $runName
Invoke-Checked 'Tests' @(
    'run', '--no-sync', 'pytest', '-q', '--basetemp', $testRoot,
    '-o', "cache_dir=$testRoot-cache"
)
Invoke-Checked 'Wheel build' @(
    'run', '--no-sync', 'python', '-m', 'build', '--wheel', '--no-isolation'
)
Invoke-Checked 'HTTP/search smoke' @('run', '--no-sync', 'python', 'scripts/smoke.py')

if (-not (Test-Path '.cache/model/prism-model.json')) {
    Write-Host 'Local model assets are absent; semantic tests may have been skipped.'
}
Write-Host 'Prism local verification passed.'
