@echo off
cd /d "%~dp0"
python -m src.data_refinery
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [Error] Failed to launch Data Refinery.
    pause
)
