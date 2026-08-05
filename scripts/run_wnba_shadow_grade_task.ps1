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

function Invoke-ShadowCommand {
    param([string[]]$Arguments)
    $Output = & $PythonExe $Arguments 2>&1
    $ExitCode = $LASTEXITCODE
    foreach ($Line in $Output) {
        Write-ShadowLog ([string]$Line)
    }
    if ($ExitCode -ne 0) {
        throw "Command failed with exit code $ExitCode`: $PythonExe $($Arguments -join ' ')"
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
