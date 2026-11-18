@echo off
cd /d "%~dp0"

netstat -ano | findstr ":8501 " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    start "" "http://localhost:8501"
    exit /b 0
)

start "" ".venv\Scripts\pythonw.exe" -m streamlit run app.py
exit /b 0
