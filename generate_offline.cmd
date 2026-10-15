@echo off
python "%~dp0generate_offline.py" %*
if errorlevel 1 pause
