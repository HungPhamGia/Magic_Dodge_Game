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

# Game YEU CAU dong ho nhip tim that (thiet ke goc): man bat dau doi den khi dong
# ho gui du lieu moi cho vao choi. Can bleak de quet/ket noi dong ho.
./.venv/bin/python -c "import bleak" 2>/dev/null || ./.venv/bin/pip install --quiet bleak
# Dat HR_NAME de thu hep quet theo ten dong ho, vi du:  export HR_NAME='Band'
# (Chi khi CAN test khong co dong ho moi them --sim-hr / --no-hr.)

echo "Dang mo game... (nhan Esc de thoat)"
# Camera BAT (nghieng nguoi de doi lan). Neu khong co webcam/quyen, game tu chuyen
# ve ban phim. Dat CAM=0 truoc khi chay de tat camera neu can.
CAM_FLAG=""; [ "$CAM" = "0" ] && CAM_FLAG="--no-camera"
# Doi dong ho nhip tim that (thiet ke goc). HR_NAME chi thu hep quet.
if [ -n "$HR_NAME" ]; then
    ./.venv/bin/python -m magicdodge.main $CAM_FLAG --no-wand --hr-name "$HR_NAME"
else
    ./.venv/bin/python -m magicdodge.main $CAM_FLAG --no-wand
fi

echo ""
echo "Game da dong. Nhan Enter de tat cua so nay."
read -r
