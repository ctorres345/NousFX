# Runs the NousFX sync: a dry run first, then the real run once you confirm.
# All arguments go to scripts\nousfx_sync.py, for example: .\install.ps1 --tools claude,agy
# This script copies nothing itself; docs/sync.md says what the sync writes.

$sync = Join-Path $PSScriptRoot 'scripts\nousfx_sync.py'
$py = $null
foreach ($c in 'python', 'python3') {  # skip the Microsoft Store stub: it is on PATH but does not run
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        & $c -c 'import sys' 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    }
}
if (-not $py) { Write-Host 'Python 3 was not found on PATH as python or python3.'; exit 1 }
# PowerShell reads an unquoted claude,agy as an array; join it back so the sync gets one argument.
$fwd = @($args | ForEach-Object { if ($_ -is [array]) { $_ -join ',' } else { "$_" } })

& $py -B $sync --dry-run @fwd
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$answer = Read-Host 'Apply these changes? [y/N]'
if ($answer -match '^(y|yes)$') {
    & $py -B $sync @fwd
    exit $LASTEXITCODE
}
Write-Host 'Nothing written.'
