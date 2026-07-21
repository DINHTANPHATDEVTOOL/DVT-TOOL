#!/usr/bin/env bash
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

LOG_FILE="$APP_DIR/launcher_ubuntu.log"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$APP_DIR/.venv"
PORT="${PORT:-8000}"

pause_and_exit() {
    local code="${1:-1}"
    echo
    read -r -p "Nhan Enter de dong cua so..."
    exit "$code"
}

log() {
    echo "$1" | tee -a "$LOG_FILE"
}

fail() {
    log ""
    log "[LOI] $1"
    log "Chi tiet: $LOG_FILE"
    pause_and_exit 1
}

: > "$LOG_FILE"

log "=================================================="
log " PhoneBot FA Console v0.9.5 - Timestamp Domain Fix"
log "=================================================="
log "Thu muc: $APP_DIR"
log ""

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "Khong tim thay python3. Hay cai: sudo apt install python3 python3-pip python3-venv"
fi

PY_VER="$("$PYTHON_BIN" --version 2>&1)"
log "$PY_VER"

TMP_VENV="$APP_DIR/.venv_test_$$"
rm -rf "$TMP_VENV"

log "[1/6] Kiem tra python venv..."
if ! "$PYTHON_BIN" -m venv "$TMP_VENV" >>"$LOG_FILE" 2>&1; then
    rm -rf "$TMP_VENV"
    log ""
    log "Ubuntu dang thieu goi venv cho dung phien ban Python."
    log "Hay chay:"
    log "  sudo apt update"
    log "  sudo apt install python3.10-venv python3-pip"
    log ""
    log "Neu van loi, chay:"
    log "  sudo apt install python3-full"
    pause_and_exit 1
fi

if [ ! -x "$TMP_VENV/bin/python" ] || ! "$TMP_VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    rm -rf "$TMP_VENV"
    log ""
    log "Venv duoc tao nhung khong co pip."
    log "Hay chay:"
    log "  sudo apt update"
    log "  sudo apt install python3.10-venv python3-pip python3-full"
    pause_and_exit 1
fi
rm -rf "$TMP_VENV"

if [ -d "$VENV_DIR" ]; then
    if [ ! -x "$VENV_DIR/bin/python" ] || ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
        log "[2/6] Phat hien .venv bi hong/thieu pip. Dang xoa va tao lai..."
        rm -rf "$VENV_DIR" || fail "Khong xoa duoc .venv cu."
    else
        log "[2/6] .venv hien tai hop le."
    fi
else
    log "[2/6] Chua co .venv."
fi

if [ ! -d "$VENV_DIR" ]; then
    log "[3/6] Dang tao .venv moi..."
    "$PYTHON_BIN" -m venv "$VENV_DIR" >>"$LOG_FILE" 2>&1 \
        || fail "Khong tao duoc .venv."
else
    log "[3/6] Khong can tao lai .venv."
fi

VENV_PY="$VENV_DIR/bin/python"

log "[4/6] Dang cap nhat pip..."
"$VENV_PY" -m pip install --upgrade pip 2>&1 | tee -a "$LOG_FILE" \
    || fail "Khong cap nhat duoc pip. Kiem tra ket noi Internet."

if [ ! -f "$APP_DIR/requirements.txt" ]; then
    fail "Khong tim thay requirements.txt trong thu muc project."
fi

log "[5/6] Dang cai/kiem tra thu vien..."
"$VENV_PY" -m pip install -r requirements.txt 2>&1 | tee -a "$LOG_FILE" \
    || fail "Cai requirements that bai."

if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

log "[6/6] Dang kiem tra ung dung..."
"$VENV_PY" -c "import app; print('Kiem tra import: OK')" 2>&1 | tee -a "$LOG_FILE" \
    || fail "Ung dung khong import duoc."

if command -v fuser >/dev/null 2>&1 && fuser "${PORT}/tcp" >/dev/null 2>&1; then
    log ""
    log "Port $PORT dang duoc su dung."
    log "Dong server cu bang lenh:"
    log "  fuser -k ${PORT}/tcp"
    pause_and_exit 1
fi

URL="http://127.0.0.1:${PORT}/?v=repair"
log ""
log "Dang khoi dong server..."
log "Dia chi: $URL"
log "Khong dong terminal nay trong khi dang su dung tool."

(
    sleep 2
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 || true
    fi
) &

exec "$VENV_PY" app.py
