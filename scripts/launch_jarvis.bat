@echo off
REM Launches JARVIS from source via python.exe (not the packaged
REM JARVIS.exe) - deliberately, since python.exe is already a trusted,
REM signed system executable that Windows Smart App Control does not
REM block, unlike a newly-built .exe with no reputation history (see
REM RELEASE.md for the full Smart App Control finding on this machine).
REM This is the recommended way to run JARVIS on this machine until
REM Smart App Control is no longer set to "On" (which requires a full
REM Windows reinstall to change - not attempted here).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m jarvis.gui.app
if errorlevel 1 (
    echo.
    echo JARVIS exited with an error. Press any key to close this window.
    pause >nul
)
