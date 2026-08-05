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

Write-ShadowLog "Starting isolated WNBA shadow capture"
Write-ShadowLog "ProjectDir: $ProjectDir"

Push-Location $ProjectDir
try {
    $Output = & $PythonExe "run_projection_shadow.py" 2>&1
    $ExitCode = $LASTEXITCODE
    foreach ($Line in $Output) {
        Write-ShadowLog ([string]$Line)
    }
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
