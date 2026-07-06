@echo off
set PYTHONPATH=%~dp0
cd /d "%~dp0"
conda run -n pdf_ai_reader_314 python src/main.py
pause
