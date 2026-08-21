$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = if ($env:WNBA_SHADOW_PYTHON_EXE) { $env:WNBA_SHADOW_PYTHON_EXE } else { "python" }
$LogDir = Join-Path $ProjectDir "outputs\logs"
$LogPath = Join-Path $LogDir "wnba_shadow_grade.log"

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

function Invoke-ShadowCommand {
    param([string[]]$Arguments)

    # Capture native stdout/stderr through temp files so ordinary Python
    # progress on stderr cannot become a terminating PowerShell error.
    $TempBase = Join-Path ([System.IO.Path]::GetTempPath()) ("wnba_shadow_grade_{0}" -f [guid]::NewGuid().ToString("N"))
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

        if ($Process.ExitCode -ne 0) {
            throw "Command failed with exit code $($Process.ExitCode): $PythonExe $($Arguments -join ' ')"
        }
    }
    finally {
        Remove-Item -Path $TempOut -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $TempErr -Force -ErrorAction SilentlyContinue
    }
}

Write-ShadowLog "Starting isolated WNBA shadow grading and rollup"
Write-ShadowLog "ProjectDir: $ProjectDir"

Push-Location $ProjectDir
try {
    Invoke-ShadowCommand -Arguments @("grade_projection_shadow.py", "--all-pending")
    Invoke-ShadowCommand -Arguments @("shadow_rollup.py")
    Write-ShadowLog "Finished isolated WNBA shadow grading and rollup with exit code 0"
    exit 0
}
catch {
    Write-ShadowLog "Shadow grading failed: $($_.Exception.Message)"
    exit 1
}
finally {
    Pop-Location
}
