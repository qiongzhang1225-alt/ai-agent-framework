@echo off
REM yuki source-mode launcher (no rebuild needed after code changes).
REM Double-click to launch. No console window stays open.
REM
REM Difference vs yuki.exe:
REM   - yuki.exe (frozen): code baked in -> need build.bat after any change
REM   - launch.bat (source): reads launcher.py directly -> just restart to apply

cd /d "%~dp0"

REM Use pythonw.exe (windowless) so no black cmd box stays around.
REM start "" detaches the process so this .bat exits immediately.
start "" ".venv\Scripts\pythonw.exe" launcher.py

REM No pause / no echo - this window closes the moment yuki splash shows up.
