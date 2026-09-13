# Register the WNBA forecast scheduled tasks on Windows and disable legacy tasks.
#
# Run in an elevated PowerShell if the existing legacy tasks were registered
# under a different principal. Requires the desktop to be powered on and the
# user logged in (interactive tasks).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows_forecast_tasks.ps1
#
param(
    [string]$ProjectDir = "C:\Users\muski\wnba_props",
    [string]$PythonExe = "python",
    [string]$TaskUser = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = "Stop"

$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$ScriptsDir = Join-Path $ProjectDir "scripts"

function New-ForecastTask {
    param(
        [string]$Name,
        [string]$Script,
        [string[]]$ExtraArgs,
        [object[]]$Triggers
    )
    $argList = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $ScriptsDir $Script),
        "-ProjectDir", $ProjectDir,
        "-PythonExe", $PythonExe
    ) + $ExtraArgs
    $action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument ($argList -join ' ') -WorkingDirectory $ProjectDir
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Registered task: $Name"
}

$weekendTriggers = @(
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 12:36),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 12:36)
)
$eveningTriggers = @(New-ScheduledTaskTrigger -Daily -At 18:45)
$gradeTriggers = @(New-ScheduledTaskTrigger -Daily -At 06:17)

New-ForecastTask -Name "WNBA Forecast Weekend" -Script "run_wnba_forecast_task.ps1" `
    -ExtraArgs @("-Slot", "afternoon") -Triggers $weekendTriggers
New-ForecastTask -Name "WNBA Forecast Daily" -Script "run_wnba_forecast_task.ps1" `
    -ExtraArgs @("-Slot", "evening") -Triggers $eveningTriggers
New-ForecastTask -Name "WNBA Forecast Grade" -Script "run_wnba_forecast_grade_task.ps1" `
    -ExtraArgs @() -Triggers $gradeTriggers

foreach ($legacy in @("WNBA Props Daily", "WNBA Shadow Capture", "WNBA Shadow Grade")) {
    $existing = Get-ScheduledTask -TaskName $legacy -ErrorAction SilentlyContinue
    if ($existing) {
        Disable-ScheduledTask -TaskName $legacy | Out-Null
        Write-Host "Disabled legacy task: $legacy"
    }
}

Write-Host ""
Write-Host "Active WNBA tasks:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "WNBA*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
