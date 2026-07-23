@echo off
setlocal
cd /d "%~dp0"
"C:\Users\31969\AppData\Local\Programs\Python\Python39\python.exe" -m hevc_lab web --host 127.0.0.1 --port 8000
