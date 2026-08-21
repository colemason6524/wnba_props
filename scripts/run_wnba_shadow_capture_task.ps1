param(
    [string[]]$PyArgs = @("run_projection_shadow.py")
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = if ($env:WNBA_SHADOW_PYTHON_EXE) { $env:WNBA_SHADOW_PYTHON_EXE } else { "python" }
$LogDir = Join-Path $ProjectDir "outputs\logs"
$LogPath = Join-Path $LogDir "wnba_shadow_capture.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-ShadowLog {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogPath -Value "$Timestamp  $Message"
}

function Write-ShadowLogBlock {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return }
    $Message -split "`r?`n" | ForEach-Object {
        if (-not [string]::IsNullOrWhiteSpace($_)) {
            Write-ShadowLog $_
        }
    }
}

function Invoke-ShadowPython {
    param([string[]]$Arguments)

    # Native stderr must never become a PowerShell terminating error under
    # $ErrorActionPreference = "Stop". Capture stdout/stderr through temp files
    # (the same proven pattern the production wrapper uses) and return Python's
    # real exit code.
    $TempBase = Join-Path ([System.IO.Path]::GetTempPath()) ("wnba_shadow_capture_{0}" -f [guid]::NewGuid().ToString("N"))
    $TempOut = "$TempBase.out.log"
    $TempErr = "$TempBase.err.log"

    try {
        $Process = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList $Arguments `
            -WorkingDirectory $ProjectDir `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $TempOut `
            -RedirectStandardError $TempErr

        foreach ($Path in @($TempOut, $TempErr)) {
            if (Test-Path $Path) {
                Get-Content -Path $Path | ForEach-Object {
                    if (-not [string]::IsNullOrWhiteSpace($_)) {
                        Write-ShadowLog $_
                    }
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

Write-ShadowLog "Starting isolated WNBA shadow capture"
Write-ShadowLog "ProjectDir: $ProjectDir"

Push-Location $ProjectDir
try {
    $ExitCode = Invoke-ShadowPython -Arguments $PyArgs
    Write-ShadowLog "Finished isolated WNBA shadow capture with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-ShadowLog "Shadow capture failed: $($_.Exception.Message)"
    exit 1
}
finally {
    Pop-Location
}
