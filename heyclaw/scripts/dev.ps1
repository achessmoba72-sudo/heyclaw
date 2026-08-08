param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("setup", "kill", "backend", "satellite", "backend-api", "backend-ngrok", "clean")]
    [string] $Action
)

$ErrorActionPreference = "Stop"
$BackendRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path $BackendRoot -Parent))
$SatelliteRoot = Join-Path $ProjectRoot "satellite"

function Stop-HeyClaw {
    $portPids = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in @(8000, 3000, 3001, 8082, 8765) } |
        Select-Object -ExpandProperty OwningProcess -Unique

    $projectPids = Get-CimInstance Win32_Process |
        Where-Object {
            $_.ExecutablePath -and
            (
                $_.ExecutablePath.StartsWith(
                    "$BackendRoot\.venv\",
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                $_.ExecutablePath.StartsWith(
                    "$SatelliteRoot\.venv\",
                    [StringComparison]::OrdinalIgnoreCase
                )
            )
        } |
        Select-Object -ExpandProperty ProcessId

    $ngrokPids = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "ngrok.exe" -and
            $_.CommandLine -match "\s3001(?:\s|$)"
        } |
        Select-Object -ExpandProperty ProcessId

    $targets = @($portPids) + @($projectPids) + @($ngrokPids) |
        Where-Object { $_ } |
        Sort-Object -Unique

    foreach ($processId in $targets) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping $($process.ProcessName) (PID $processId)"
            Stop-Process -Id $processId -Force
        }
    }
}

function Install-HeyClaw {
    Set-Location $BackendRoot
    uv sync --all-groups --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "HeyClaw uv sync failed" }
    Set-Location $SatelliteRoot
    uv sync --all-groups --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "Satellite uv sync failed" }
    Write-Host "HeyClaw backend and satellite are ready." -ForegroundColor Green
}

function Get-NgrokDomain {
    $configFile = Join-Path $BackendRoot "config.json"
    if (-not (Test-Path -LiteralPath $configFile)) {
        throw "heyclaw/config.json not found"
    }
    $config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    $publicWsUrl = [string] $config.gateway.publicWsUrl
    if (-not $publicWsUrl) {
        throw "gateway.publicWsUrl is not configured in heyclaw/config.json"
    }
    $uri = [Uri] $publicWsUrl
    if ($uri.Scheme -ne "wss" -or -not $uri.Host -or $uri.AbsolutePath -ne "/ws") {
        throw "gateway.publicWsUrl must be a valid wss:// URL ending in /ws"
    }
    return $uri.Host
}

function Start-HeyClaw {
    Stop-HeyClaw
    Set-Location $BackendRoot
    $backendExe = Join-Path $BackendRoot ".venv\Scripts\heyclaw-serve.exe"
    if (-not (Test-Path -LiteralPath $backendExe)) {
        throw "Run this first: make -f Makefile.windows setup"
    }

    $ngrokDomain = Get-NgrokDomain
    Write-Host "Starting the HeyClaw backend..." -ForegroundColor Cyan
    $backend = Start-Process `
        -FilePath $backendExe `
        -WorkingDirectory $BackendRoot `
        -NoNewWindow `
        -PassThru
    $ngrok = $null

    try {
        $ready = $false
        foreach ($attempt in 1..300) {
            if ($backend.HasExited) {
                throw "The backend stopped with exit code $($backend.ExitCode)"
            }
            $ready = [bool] (
                Get-NetTCPConnection -State Listen -LocalPort 3001 -ErrorAction SilentlyContinue
            )
            if ($ready) { break }
            Start-Sleep -Milliseconds 200
        }
        if (-not $ready) { throw "Speech Engine unavailable on port 3001" }

        Write-Host "Starting the ngrok tunnel..." -ForegroundColor Cyan
        $ngrok = Start-Process `
            -FilePath "ngrok.exe" `
            -ArgumentList @("http", "--url=$ngrokDomain", "3001") `
            -NoNewWindow `
            -PassThru
        Write-Host "HeyClaw is ready. Press Ctrl+C to stop." -ForegroundColor Green
        while (-not $backend.HasExited -and -not $ngrok.HasExited) {
            Start-Sleep -Milliseconds 250
            $backend.Refresh()
            $ngrok.Refresh()
        }

        if ($backend.HasExited) {
            throw "HeyClaw backend stopped with exit code $($backend.ExitCode)"
        }
        throw "ngrok stopped with exit code $($ngrok.ExitCode)"
    }
    finally {
        if ($ngrok -and -not $ngrok.HasExited) {
            Stop-Process -Id $ngrok.Id -Force
        }
        if ($backend -and -not $backend.HasExited) {
            Stop-Process -Id $backend.Id -Force
        }
    }
}

function Start-Satellite {
    Set-Location $SatelliteRoot
    uv run heyclaw-satellite
    if ($LASTEXITCODE -ne 0) { throw "Satellite stopped with exit code $LASTEXITCODE" }
}

function Start-BackendApi {
    Set-Location $BackendRoot
    uv run uvicorn app.main:app --host localhost --port 8000
    if ($LASTEXITCODE -ne 0) { throw "FastAPI stopped with exit code $LASTEXITCODE" }
}

function Start-BackendNgrok {
    $ngrokDomain = Get-NgrokDomain
    ngrok.exe http "--url=$ngrokDomain" 3001
    if ($LASTEXITCODE -ne 0) { throw "ngrok stopped with exit code $LASTEXITCODE" }
}

function Clear-HeyClawGeneratedFiles {
    $directoryNames = @(
        "__pycache__",
        "logs",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache"
    )
    $backendVenv = Join-Path $BackendRoot ".venv"
    $satelliteVenv = Join-Path $SatelliteRoot ".venv"
    Get-ChildItem -LiteralPath $BackendRoot, $SatelliteRoot -Directory -Recurse -Force |
        Where-Object {
            -not $_.FullName.StartsWith(
                $backendVenv,
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            -not $_.FullName.StartsWith(
                $satelliteVenv,
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            ($_.Name -in $directoryNames -or $_.Name.EndsWith(".egg-info"))
        } |
        Sort-Object { $_.FullName.Length } -Descending |
        Remove-Item -Recurse -Force

    Get-ChildItem -LiteralPath $BackendRoot, $SatelliteRoot -File -Force |
        Where-Object { $_.Name -eq ".coverage" } |
        Remove-Item -Force
}

switch ($Action) {
    "setup" { Install-HeyClaw }
    "kill" { Stop-HeyClaw }
    "backend" { Start-HeyClaw }
    "satellite" { Start-Satellite }
    "backend-api" { Start-BackendApi }
    "backend-ngrok" { Start-BackendNgrok }
    "clean" { Clear-HeyClawGeneratedFiles }
}
