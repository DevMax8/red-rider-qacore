param([ValidateRange(1024,65515)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run RUN_WINDOWS.cmd or scripts\setup.ps1 first.' }
$selectedPort = $null
foreach ($candidatePort in $Port..($Port + 20)) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidatePort)
    try {
        $listener.Start()
        $selectedPort = $candidatePort
    } catch [System.Net.Sockets.SocketException] {
        # Leave existing servers running and select the next free local port.
    } finally {
        $listener.Stop()
    }
    if ($null -ne $selectedPort) { break }
}
if ($null -eq $selectedPort) { throw 'No free local port found. Close an old server and try again.' }
Write-Host "Application: http://127.0.0.1:$selectedPort"
if ($selectedPort -ne $Port) { Write-Host "Port $Port is busy; using $selectedPort instead." }
Write-Host 'Keep this terminal open. Press Ctrl+C to stop.'
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $selectedPort
exit $LASTEXITCODE
