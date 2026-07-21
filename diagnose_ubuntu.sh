#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
LOG="diagnose_ubuntu.log"
exec > >(tee "$LOG") 2>&1

echo "=== PhoneBot startup diagnosis ==="
date
pwd
uname -a
command -v python3 || true
python3 --version 2>&1 || true
python3 -m pip --version 2>&1 || true
python3 -m venv --help >/dev/null 2>&1 && echo "python3-venv: available" || echo "python3-venv: missing or broken"
[ -x .venv/bin/python ] && .venv/bin/python --version || echo ".venv: missing or broken"
[ -x .venv/bin/python ] && .venv/bin/python -c "import fastapi,uvicorn,pydantic,dotenv; import app; print('Application imports: OK')" 2>&1 || true
python3 - <<'PY'
import socket
s=socket.socket()
try:
    s.bind(('127.0.0.1',8000))
    print('Port 8000: available')
except OSError as e:
    print('Port 8000: unavailable:', e)
finally:
    s.close()
PY

echo "Da luu ket qua tai $LOG"
read -r -p "Nhan Enter de dong..." _
