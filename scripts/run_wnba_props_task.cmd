@echo off
setlocal

set "PROJECT_DIR=C:\Users\%USERNAME%\wnba_props"
set "PYTHON_EXE=python"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "LOG_DIR=%PROJECT_DIR%\outputs\logs"
set "BOOT_LOG=%LOG_DIR%\wnba_props_cmd_bootstrap.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%DATE% %TIME%] Starting WNBA props CMD wrapper>> "%BOOT_LOG%"
echo [%DATE% %TIME%] PROJECT_DIR=%PROJECT_DIR%>> "%BOOT_LOG%"
echo [%DATE% %TIME%] PYTHON_EXE=%PYTHON_EXE%>> "%BOOT_LOG%"
echo [%DATE% %TIME%] POWERSHELL_EXE=%POWERSHELL_EXE%>> "%BOOT_LOG%"

if not exist "%PROJECT_DIR%" (
    echo [%DATE% %TIME%] Project directory not found>> "%BOOT_LOG%"
    exit /b 1
)

if not exist "%POWERSHELL_EXE%" (
    echo [%DATE% %TIME%] PowerShell executable not found>> "%BOOT_LOG%"
    exit /b 1
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\run_wnba_props_task.ps1" -ProjectDir "%PROJECT_DIR%" -PythonExe "%PYTHON_EXE%" >> "%BOOT_LOG%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo [%DATE% %TIME%] Finished with exit code %EXIT_CODE%>> "%BOOT_LOG%"
exit /b %EXIT_CODE%
