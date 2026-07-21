@echo off
setlocal
cd /d "%~dp0"
set "LOG=diagnose_windows.log"
(
  echo === PhoneBot startup diagnosis ===
  echo %date% %time%
  echo Folder: %CD%
  where py 2^>nul
  where python 2^>nul
  py -3 --version 2^>nul
  python --version 2^>nul
  if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" --version
    ".venv\Scripts\python.exe" -c "import fastapi,uvicorn,pydantic,dotenv; import app; print('Application imports: OK')"
  ) else (
    echo .venv: missing or broken
  )
  powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',8000); 'Port 8000: in use'} catch {'Port 8000: available'} finally{$c.Dispose()}"
) > "%LOG%" 2>&1
type "%LOG%"
echo.
echo Da luu ket qua tai %LOG%
pause
endlocal
