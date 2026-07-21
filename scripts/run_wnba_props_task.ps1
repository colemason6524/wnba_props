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
    Write-TaskLog "Running command: $FilePath $($Arguments -join ' ')"

    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo.FileName = $FilePath
    foreach ($Argument in $Arguments) {
        [void]$Process.StartInfo.ArgumentList.Add($Argument)
    }
    $Process.StartInfo.WorkingDirectory = $ProjectDir
    $Process.StartInfo.UseShellExecute = $false
    $Process.StartInfo.RedirectStandardOutput = $true
    $Process.StartInfo.RedirectStandardError = $true

    [void]$Process.Start()
    $StdOut = $Process.StandardOutput.ReadToEnd()
    $StdErr = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()

    if (-not [string]::IsNullOrWhiteSpace($StdOut)) {
        $StdOut -split "`r?`n" | ForEach-Object {
            if (-not [string]::IsNullOrWhiteSpace($_)) {
                Write-TaskLog $_
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($StdErr)) {
        $StdErr -split "`r?`n" | ForEach-Object {
            if (-not [string]::IsNullOrWhiteSpace($_)) {
                Write-TaskLog $_
            }
        }
    }
    return $Process.ExitCode
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
