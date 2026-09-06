[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Storage = Join-Path $Root 'storage'
$PidFile = Join-Path $Storage 'rtd-windows.pid'
$PortFile = Join-Path $Storage 'rtd-windows.port'
if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Write-Host 'No RTD service started by the Windows launcher is recorded.'
    exit 0
}

try {
    $pidRecord = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    $serverPid = [int]$pidRecord.pid
    $recordedStartTime = [int64]$pidRecord.start_time_filetime_utc
    if ($serverPid -le 0 -or $recordedStartTime -le 0) { throw 'invalid record' }
}
catch {
    Remove-Item -LiteralPath $PidFile -Force
    Remove-Item -LiteralPath $PortFile -Force -ErrorAction SilentlyContinue
    throw 'The invalid PID file was removed; no process was stopped.'
}

$processInfo = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if ($processInfo) {
    $actualStartTime = $processInfo.StartTime.ToUniversalTime().ToFileTimeUtc()
    if ($actualStartTime -ne $recordedStartTime) {
        throw "PID $serverPid has been reused by another process. Nothing was stopped."
    }

    Stop-Process -Id $serverPid
    try {
        Wait-Process -Id $serverPid -Timeout 10 -ErrorAction Stop
    }
    catch {
        Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
    }
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PortFile -Force -ErrorAction SilentlyContinue
Write-Host 'The RTD Windows service has stopped.'
