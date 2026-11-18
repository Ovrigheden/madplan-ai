@echo off
echo Stopper Madplan AI...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo Madplan AI er stoppet.
timeout /t 2 >nul
