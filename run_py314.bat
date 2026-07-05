@echo off
set PYTHONPATH=%~dp0
conda run -n pdf_ai_reader_3144 python src\main.py
