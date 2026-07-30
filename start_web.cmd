@echo off
setlocal
cd /d "%~dp0"
title HEVC Realtime Stream V2.2.0 Web
echo Starting V2.2.0 local realtime web at http://127.0.0.1:8000/
echo Press Ctrl+C to stop the service.
echo.
"C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe" -m hevc_lab web --host 127.0.0.1 --port 8000
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo V2.2.0 service exited with code: %EXIT_CODE%
echo Review the error above if startup failed.
pause
exit /b %EXIT_CODE%
