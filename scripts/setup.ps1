param([switch]$ForceInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $candidates = @()
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') { $candidates += $pythonCommand.Source }
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $candidates += $bundledPython }
    $created = $false
    foreach ($candidate in $candidates) {
        & $candidate -c "import sys; assert sys.version_info >= (3,11)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $candidate -m venv .venv
            if ($LASTEXITCODE -eq 0) { $created = $true; break }
        }
    }
    if (-not $created) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) { & $launcher.Source -3 -m venv .venv }
        if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Install Python 3.11+ from python.org, then run RUN_WINDOWS.cmd again.' }
    }
}
& $venvPython -c "import fastapi,uvicorn,httpx,pydantic,dotenv; assert int(pydantic.__version__.split('.')[0]) == 2" 2>$null
if ($ForceInstall -or $LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your connection and run again.' }
}
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Host 'Setup complete.'
