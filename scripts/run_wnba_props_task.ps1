param(
    [string]$ProjectDir = "C:\Users\muski\wnba_props",
    [string]$PythonExe = "python",
    [string]$PlayerPropsBook = "FANDUEL",
    [string]$RunNote = "scheduled WNBA props run"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "outputs\logs") | Out-Null
$LogPath = Join-Path $ProjectDir "outputs\logs\wnba_props_task.log"

function Write-TaskLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

function Write-TaskLogBlock {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) {
        return
    }
    $Message -split "`r?`n" | ForEach-Object {
        if (-not [string]::IsNullOrWhiteSpace($_)) {
            Write-TaskLog $_
        }
    }
}

function Invoke-LoggedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )
    $SafeName = [System.IO.Path]::GetFileNameWithoutExtension($FilePath) -replace "[^A-Za-z0-9_-]", "_"
    $TempOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("wnba_props_{0}_{1}.log" -f $SafeName, [guid]::NewGuid().ToString("N"))
    try {
        Write-TaskLog "Running command: $FilePath $($Arguments -join ' ')"
        & $FilePath @Arguments > $TempOutput 2>&1
        $CommandExitCode = $LASTEXITCODE
        if (Test-Path $TempOutput) {
            Get-Content -Path $TempOutput | ForEach-Object {
                Write-TaskLog $_
            }
        }
        return $CommandExitCode
    }
    finally {
        Remove-Item -Path $TempOutput -Force -ErrorAction SilentlyContinue
    }
}

function Write-FailureDetails {
    param($ErrorRecord)
    Write-TaskLog "FAILED: $($ErrorRecord.Exception.Message)"
    Write-TaskLogBlock (($ErrorRecord | Format-List * -Force | Out-String).TrimEnd())
    if ($ErrorRecord.ScriptStackTrace) {
        Write-TaskLogBlock ("Script stack trace:`n$($ErrorRecord.ScriptStackTrace)")
    }
    if ($ErrorRecord.InvocationInfo -and $ErrorRecord.InvocationInfo.PositionMessage) {
        Write-TaskLogBlock ("Invocation:`n$($ErrorRecord.InvocationInfo.PositionMessage)")
    }
}

try {
    Write-TaskLog "Starting WNBA props task"
    Write-TaskLog "User: $env:USERNAME"
    Write-TaskLog "ProjectDir: $ProjectDir"
    Write-TaskLog "PythonExe: $PythonExe"
    Write-TaskLog "PlayerPropsBook: $PlayerPropsBook"
    Write-TaskLog "RunNote: $RunNote"

    if (-not (Test-Path $ProjectDir)) {
        throw "Project directory does not exist: $ProjectDir"
    }

    Set-Location $ProjectDir

    $env:PYTHONPYCACHEPREFIX = ".pycache"
    $env:LINE_SOURCE = "playerprops"
    $env:PLAYERPROPS_BOOK = $PlayerPropsBook
    $env:RUN_NOTE = $RunNote

    Write-TaskLog "Python version:"
    $VersionExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments @("--version")
    if ($VersionExitCode -ne 0) {
        throw "Python version check failed with exit code $VersionExitCode"
    }

    Write-TaskLog "Running WNBA props"
    $ExitCode = Invoke-LoggedCommand -FilePath $PythonExe -Arguments @("run_nightly.py")
    Write-TaskLog "Finished WNBA props with exit code $ExitCode"
    exit $ExitCode
}
catch {
    Write-FailureDetails $_
    exit 1
}
