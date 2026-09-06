[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Storage = Join-Path $Root 'storage'
$Logs = Join-Path $Storage 'logs'
$PidFile = Join-Path $Storage 'rtd-windows.pid'
$PortFile = Join-Path $Storage 'rtd-windows.port'
$ServerScript = Join-Path $Root 'backend\server.py'

function Read-Port {
    $value = if ($env:RTD_PORT) { $env:RTD_PORT } else { '8765' }
    $port = 0
    if (-not [int]::TryParse($value, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw 'RTD_PORT must be a number between 1 and 65535.'
    }
    return $port
}

function Test-Health([int]$Port) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
        return $health.status -eq 'ok'
    }
    catch {
        return $false
    }
}

function Test-PortAvailable([int]$Port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function Open-RtdBrowser([int]$Port) {
    try {
        Start-Process "http://127.0.0.1:$Port" -ErrorAction Stop
    }
    catch {
        Write-Warning "The browser could not be opened automatically. Open http://127.0.0.1:$Port manually."
    }
}

function Resolve-Python {
    if ($env:RTD_SERVICE_PYTHON) {
        if (-not (Test-Path -LiteralPath $env:RTD_SERVICE_PYTHON -PathType Leaf)) {
            throw "RTD_SERVICE_PYTHON does not exist: $env:RTD_SERVICE_PYTHON"
        }
        return [pscustomobject]@{ File = (Resolve-Path -LiteralPath $env:RTD_SERVICE_PYTHON).Path; Prefix = @() }
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $valid = $false
        try {
            & $py.Source -3 -c 'import sys; assert sys.version_info >= (3, 9)' 2>$null
            $valid = $LASTEXITCODE -eq 0
        }
        catch {}
        if ($valid) {
            return [pscustomobject]@{ File = $py.Source; Prefix = @('-3') }
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $valid = $false
        try {
            & $python.Source -c 'import sys; assert sys.version_info >= (3, 9)' 2>$null
            $valid = $LASTEXITCODE -eq 0
        }
        catch {}
        if ($valid) {
            return [pscustomobject]@{ File = $python.Source; Prefix = @() }
        }
    }

    throw 'Python 3.9 or newer was not found. Install Python and enable Add Python to PATH.'
}

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Port = Read-Port

if (Test-Path -LiteralPath $PortFile -PathType Leaf) {
    $recordedPort = 0
    $recordedPortText = (Get-Content -LiteralPath $PortFile -Raw).Trim()
    if ([int]::TryParse($recordedPortText, [ref]$recordedPort) -and (Test-Health $recordedPort)) {
        Open-RtdBrowser $recordedPort
        Write-Host "RTD is already running: http://127.0.0.1:$recordedPort"
        exit 0
    }
}

if (Test-Health $Port) {
    Open-RtdBrowser $Port
    Write-Host "RTD is already running: http://127.0.0.1:$Port"
    exit 0
}

while (-not (Test-PortAvailable $Port)) {
    $Port++
    if ($Port -gt 65535) { throw 'No available TCP port was found.' }
}

$Python = Resolve-Python
$dependencyCheck = 'import pandas, numpy, openpyxl, matplotlib'
$dependenciesReady = $false
try {
    & $Python.File @($Python.Prefix) -c $dependencyCheck
    $dependenciesReady = $LASTEXITCODE -eq 0
}
catch {}
if (-not $dependenciesReady) {
    Write-Host ''
    Write-Host 'Python dependencies are missing. Run this command in the project directory:' -ForegroundColor Yellow
    Write-Host '  py -3 -m pip install -r requirements.txt' -ForegroundColor Yellow
    exit 1
}

if ($env:RTD_ALGORITHM_PYTHON) {
    if (-not (Test-Path -LiteralPath $env:RTD_ALGORITHM_PYTHON -PathType Leaf)) {
        throw "RTD_ALGORITHM_PYTHON does not exist: $env:RTD_ALGORITHM_PYTHON"
    }
}

$quotedServer = '"' + $ServerScript + '"'
$quotedStorage = '"' + $Storage + '"'
$arguments = @($Python.Prefix) + @('-B', $quotedServer, '--port', "$Port", '--storage', $quotedStorage)
$process = Start-Process -FilePath $Python.File -ArgumentList $arguments -WorkingDirectory $Root `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Logs 'launcher-windows.log') `
    -RedirectStandardError (Join-Path $Logs 'launcher-windows-error.log') -PassThru

$process.Refresh()
$pidRecord = [ordered]@{
    pid = $process.Id
    start_time_filetime_utc = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
}
[System.IO.File]::WriteAllText($PidFile, ($pidRecord | ConvertTo-Json -Compress), [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText($PortFile, "$Port", [System.Text.Encoding]::ASCII)

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if (Test-Health $Port) {
        Open-RtdBrowser $Port
        Write-Host "RTD started: http://127.0.0.1:$Port"
        Write-Host 'To stop RTD, double-click the Windows stop command.'
        exit 0
    }
    if ($process.HasExited) {
        Write-Host "RTD failed to start. See: $Logs\launcher-windows-error.log" -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 1
    $process.Refresh()
}

Write-Host "RTD startup timed out. See: $Logs\launcher-windows-error.log" -ForegroundColor Red
exit 1
