@echo off
set "PROJECT_DIR=%~dp0"
set "PYTHONPATH=%PROJECT_DIR%"
start "PDF AI Reader" /D "%PROJECT_DIR%" conda run -n pdf_ai_reader_3144 python src\main.py
