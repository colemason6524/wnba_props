param(
    [string]$ProjectDir = "C:\Users\muski\wnba_props",
    [string]$PythonExe = "python",
    [ValidateSet("afternoon", "evening")]
    [string]$Slot = "evening",
    [string]$EnvFile = "",
    [switch]$NoDiscord
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $env:USERPROFILE ".config\wnba_props\env"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "outputs\logs") | Out-Null
$LogPath = Join-Path $ProjectDir "outputs\logs\wnba_forecast.log"

function Write-TaskLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

function Import-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Write-TaskLog "Env file not found (using process environment): $Path"
        return
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $index = $line.IndexOf("=")
            $name = $line.Substring(0, $index).Trim()
            $value = $line.Substring($index + 1).Trim().Trim('"')
            if ($name) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

function Invoke-LoggedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )
    Write-TaskLog "Running command: $FilePath $($Arguments -join ' ')"
    $SafeName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath) -replace "[^A-Za-z0-9_-]", "_"
    $TempBase = Join-Path ([System.IO.Path]::GetTempPath()) ("wnba_forecast_{0}_{1}" -f $SafeName, [guid]::NewGuid().ToString("N"))
    $TempOut = "$TempBase.out.log"
    $TempErr = "$TempBase.err.log"
    try {
        $Process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $Arguments `
            -WorkingDirectory $ProjectDir `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $TempOut `
            -RedirectStandardError $TempErr
        foreach ($Path in @($TempOut, $TempErr)) {
            if (Test-Path $Path) {
                Get-Content -Path $Path | ForEach-Object {
                    if (-not [string]::IsNullOrWhiteSpace($_)) { Write-TaskLog $_ }
                }
            }
        }
        return $Process.ExitCode
    }
    finally {
        Remove-Item -Path $TempOut -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $TempErr -Force -ErrorAction SilentlyContinue
    }
}

try {
    Write-TaskLog "Starting WNBA forecast task (slot=$Slot)"
    Write-TaskLog "User: $env:USERNAME"
    Write-TaskLog "ProjectDir: $ProjectDir"
    Write-TaskLog "PythonExe: $PythonExe"

    if (-not (Test-Path $ProjectDir)) {
        throw "Project directory does not exist: $ProjectDir"
    }

    Set-Location $ProjectDir
    $env:PYTHONPYCACHEPREFIX = ".pycache"
    $env:TZ = "America/Detroit"

    Import-EnvFile -Path $EnvFile

    Write-TaskLog "Python version:"
    $VersionExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments @("--version")
    if ($VersionExitCode -ne 0) {
        throw "Python version check failed with exit code $VersionExitCode"
    }

    $TaskArgs = @("run_forecast_pipeline.py", "--slot", $Slot)
    if (-not $NoDiscord) {
        $TaskArgs += "--send-discord"
    }
    $ExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments $TaskArgs
    Write-TaskLog "Finished WNBA forecast with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-TaskLog "FAILED: $($_.Exception.Message)"
    Write-TaskLog (($_.ScriptStackTrace) -replace "`r?`n", " | ")
    exit 1
}
