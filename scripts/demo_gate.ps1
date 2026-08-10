# Demo script for acceptance criterion #1: a broken contract is rejected with
# reasons, a valid one passes. Runs the exact two checks contract-gate.yml runs,
# so what the audience sees is what CI does.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo

function Write-Banner($text) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkGray
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkGray
    Write-Host ''
}

try {
    Write-Banner 'A partner submits a broken contract -- structural check'
    uv run python scripts/validate_contracts.py --dir tests/fixtures/invalid --expect-invalid
    if ($LASTEXITCODE -ne 0) { throw 'Rejection demo failed: a bad fixture passed structural validation.' }

    Write-Banner 'Same submissions -- semantic review (LLM-as-judge)'
    uv run python scripts/eval_contracts.py --dir tests/fixtures/invalid --expect-fail
    if ($LASTEXITCODE -ne 0) { throw 'Rejection demo failed: a bad fixture passed semantic review.' }

    Write-Banner 'The approved contracts -- structural check'
    uv run python scripts/validate_contracts.py
    if ($LASTEXITCODE -ne 0) { throw 'A seed contract failed structural validation.' }

    Write-Banner 'The approved contracts -- semantic review'
    uv run python scripts/eval_contracts.py
    if ($LASTEXITCODE -ne 0) { throw 'A seed contract failed semantic review.' }

    Write-Host ''
    Write-Host 'Gate demo complete: bad contracts rejected with reasons, good ones merged.' -ForegroundColor Green
    if (-not $env:OPENAI_API_KEY) {
        Write-Host 'Note: semantic review ran on deterministic heuristics (no OPENAI_API_KEY set).' -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
