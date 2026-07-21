@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title PhoneBot FA Console v0.9.5 - Timestamp Domain Fix
set "LOG_FILE=%CD%\launcher_windows.log"

echo ==================================================
echo  PhoneBot FA Console v0.9.5 - Timestamp Domain Fix
echo ==================================================
echo Thu muc: %CD%
echo Log khoi dong: %LOG_FILE%
echo.

where py >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 goto :no_python
  set "PY=python"
)

%PY% --version
if errorlevel 1 goto :no_python

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 goto :old_python

if exist ".venv" if not exist ".venv\Scripts\python.exe" (
  echo Phat hien .venv bi loi. Dang tao lai...
  rmdir /s /q ".venv"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Dang tao moi truong ao .venv...
  %PY% -m venv .venv >> "%LOG_FILE%" 2>&1
  if errorlevel 1 goto :venv_error
) else (
  echo [1/5] Da co .venv.
)

set "VPY=%CD%\.venv\Scripts\python.exe"

echo [2/5] Dang cap nhat pip...
"%VPY%" -m pip install --upgrade pip >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :pip_error

echo [3/5] Dang cai thu vien...
"%VPY%" -m pip install -r requirements.txt >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :requirements_error

echo [4/5] Dang chuan bi cau hinh va database...
if not exist ".env" copy ".env.example" ".env" >nul
if not exist "data" mkdir "data"

echo [5/5] Dang kiem tra ung dung...
"%VPY%" -c "import fastapi, uvicorn, pydantic, dotenv; import app; print('Kiem tra import: OK')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :import_error

"%VPY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=1)" >nul 2>&1
if not errorlevel 1 (
  echo Server da chay san tai http://127.0.0.1:8000
  start "" "http://127.0.0.1:8000/?v=0.8.1"
  goto :keep_open
)

echo.
echo Dang khoi dong server...
echo Khong dong cua so nay khi dang su dung tool.
echo Dia chi: http://127.0.0.1:8000
echo.
start "" cmd /c "timeout /t 3 /nobreak ^>nul ^& start ^"^" ^"http://127.0.0.1:8000/?v=0.8.1^""
"%VPY%" app.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :server_error

echo Server da dung.
goto :keep_open

:no_python
echo [LOI] Khong tim thay Python 3.
echo Tai Python 3.9 tro len va danh dau tuy chon "Add Python to PATH" khi cai dat.
goto :error_end

:old_python
echo [LOI] Can Python 3.9 tro len.
goto :error_end

:venv_error
echo [LOI] Khong tao duoc .venv.
goto :error_end

:pip_error
echo [LOI] Khong cap nhat duoc pip. Kiem tra ket noi Internet.
goto :error_end

:requirements_error
echo [LOI] Khong cai duoc thu vien. Xem launcher_windows.log de biet package bi loi.
goto :error_end

:import_error
echo [LOI] Ung dung khong import duoc. Xem launcher_windows.log.
goto :error_end

:server_error
echo [LOI] Server dung bat thuong. Xem launcher_windows.log.
echo Neu log co "Address already in use", hay dong server cu hoac khoi dong lai may.
goto :error_end

:error_end
echo.
echo Chi tiet da duoc luu tai:
echo %LOG_FILE%

:keep_open
echo.
pause
endlocal
