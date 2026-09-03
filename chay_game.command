#!/bin/bash
# Double-click de choi MagicDodge (ban phim, toan man hinh).
# Di chuyen: mui ten trai/phai.  Niem chu: J = tam giac, K = tron, L = vuong.
# Nhan Esc de thoat, R de choi lai.
# Moi van choi tu dong day len Firebase (bien FIREBASE_DB_URL ben duoi).
cd "$(dirname "$0")" || exit 1

# Cloud that: Firebase Realtime Database. URL nay khong phai mat khau (test mode).
export FIREBASE_DB_URL='https://magicdodge-461ab-default-rtdb.asia-southeast1.firebasedatabase.app/'

# Key OpenRouter cho AI Coach nam trong file .env (da gitignore, khong len GitHub).
# Sua key trong .env; khong co thi coach chay che do offline (van co nhan xet).
[ -f .env ] && . ./.env

# Tao venv + cai pygame-ce lan dau neu chua co.
if [ ! -x ".venv/bin/python" ]; then
    echo "Lan dau chay: dang tao moi truong va cai pygame-ce..."
    python3 -m venv .venv || { echo "Loi tao venv"; read -r; exit 1; }
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet "pygame-ce>=2.5" || { echo "Loi cai pygame"; read -r; exit 1; }
fi

echo "Dang mo game toan man hinh... (nhan Esc de thoat)"
./.venv/bin/python -m magicdodge.main --no-camera --no-wand

echo ""
echo "Game da dong. Nhan Enter de tat cua so nay."
read -r
