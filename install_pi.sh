#!/usr/bin/env bash
# Cài gói đã được prepare_pi.py build trên PC; Pi không build JavaScript.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
if [[ "$(uname -s)" != Linux ]]; then
    echo "Script này dành cho Raspberry Pi OS 64-bit có systemd." >&2; exit 1
fi
if [[ "$EUID" -eq 0 ]]; then
    echo "Chạy bằng tài khoản thường: bash install_pi.sh (script tự dùng sudo)." >&2; exit 1
fi
if [[ "$(getconf LONG_BIT)" != 64 ]]; then
    echo "Cần Raspberry Pi OS 64-bit." >&2; exit 1
fi
INSTALL_ML=false
if [[ "${1:-}" == --with-ml && $# -eq 1 ]]; then INSTALL_ML=true;
elif [[ $# -gt 0 ]]; then echo "Tham số hỗ trợ: --with-ml" >&2; exit 1; fi
if [[ "$PROJECT_DIR" == *'"'* || "$PROJECT_DIR" == *$'\n'* || "$PROJECT_DIR" == *'%'* ]]; then
    echo "Đường dẫn dự án không được chứa dấu nháy kép, %, hoặc xuống dòng." >&2; exit 1
fi
# Kiểm tra trước mọi thay đổi hệ thống; thiếu bản build thì dừng và hướng dẫn build ở PC.
python3 prepare_pi.py --check
TARGET_USER="$(id -un)"
TARGET_GROUP="$(id -gn)"
sudo -v
NGINX_WAS_INSTALLED=false
if dpkg-query -W -f='${Status}' nginx 2>/dev/null | grep -q 'install ok installed'; then NGINX_WAS_INSTALLED=true; fi
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-pip python3-lgpio python3-rpi.gpio \
    libgpiod-dev gpiod libopenblas-dev build-essential nginx
if ! $NGINX_WAS_INSTALLED; then sudo systemctl disable --now nginx.service; fi
sudo usermod -aG gpio "$TARGET_USER"
python3 -m venv --system-site-packages venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements-pi.txt
if $INSTALL_ML; then venv/bin/python -m pip install -r requirements-inference.txt; fi
mkdir -p data model logs
if [[ ! -f .env ]]; then
    (umask 077; cat > .env <<'ENV'
# Cấu hình nền. Ngưỡng và email nhận đã lưu trên dashboard được SQLite ưu tiên.
# Sửa tệp này xong cần restart iot-monitor và iot-dashboard.
EMAIL_ENABLED=false
EMAIL_SMTP=smtp.gmail.com
EMAIL_PORT=465
EMAIL_FROM=
EMAIL_TO=
# Gmail App Password có khoảng trắng cần đặt trong dấu nháy kép.
EMAIL_PASS=
EMAIL_COOLDOWN=1800

TEMP_RED_LOW=10
TEMP_YELLOW_LOW=15
TEMP_YELLOW_HIGH=30
TEMP_RED_HIGH=35
HUM_RED_LOW=25
HUM_YELLOW_LOW=35
HUM_YELLOW_HIGH=75
HUM_RED_HIGH=85

MQTT_ENABLED=false
MQTT_HOST=localhost
MQTT_PORT=1883
# IOT_DB_FILE để trống dùng data/iot.sqlite3 trong thư mục dự án.
# IOT_DB_FILE=
ENV
    )
fi
venv/bin/python check_system.py --config-only
venv/bin/python -c 'from storage import Store; print("Đã nhập", Store().import_csv(), "mẫu lịch sử")'

BUILD_ID="$(python3 -c 'from pi_deploy import validate_frontend; from pathlib import Path; print(validate_frontend(Path.cwd())["build_id"])')"
if [[ ! "$BUILD_ID" =~ ^[0-9a-f]{16}$ ]]; then echo "Build ID không hợp lệ" >&2; exit 1; fi
WEB_ROOT=/var/www/iot-dashboard
sudo mkdir -p "$WEB_ROOT"
if [[ "$(realpath -- "$WEB_ROOT")" != /var/www/iot-dashboard ]]; then
    echo "Đường dẫn web không được trỏ ra ngoài /var/www/iot-dashboard" >&2; exit 1
fi
WEB_RELEASE="$WEB_ROOT/$BUILD_ID"
sudo mkdir -p "$WEB_RELEASE"
if [[ "$(realpath -- "$WEB_RELEASE")" != "$WEB_RELEASE" ]]; then echo "Đường dẫn bản web không hợp lệ" >&2; exit 1; fi
sudo cp -a "$PROJECT_DIR/deploy/frontend/." "$WEB_RELEASE/"
sudo find "$WEB_RELEASE" -type d -exec chmod 755 {} +
sudo find "$WEB_RELEASE" -type f -exec chmod 644 {} +
venv/bin/python - "$WEB_RELEASE" "$PROJECT_DIR/deploy/nginx.conf" <<'PY'
import sys
from pathlib import Path
from pi_deploy import nginx_config
Path(sys.argv[2]).write_text(nginx_config(sys.argv[1]), encoding="utf-8")
PY
sudo /usr/sbin/nginx -t -c "$PROJECT_DIR/deploy/nginx.conf"

# Dừng bản cũ trước khi bàn giao cổng 3000 cho Nginx, không tranh DHT22.
sudo systemctl disable --now collect.service monitor.service 2>/dev/null || true
sudo systemctl stop iot-web.service iot-dashboard.service iot-monitor.service 2>/dev/null || true
sudo tee /etc/systemd/system/iot-monitor.service >/dev/null <<EOF
[Unit]
Description=IoT DHT22 monitor and 12-hour inference
After=network.target time-sync.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
SupplementaryGroups=gpio
WorkingDirectory=$PROJECT_DIR
ExecStart=/bin/bash "$PROJECT_DIR/start_monitor.sh"
Restart=always
RestartSec=10
TimeoutStopSec=25
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/iot-dashboard.service >/dev/null <<EOF
[Unit]
Description=IoT Reflex backend (no frontend build)
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$PROJECT_DIR
Environment=TZ=Asia/Ho_Chi_Minh
Environment=PYTHONUNBUFFERED=1
Environment=GRANIAN_WORKERS=1
ExecStart=/bin/bash "$PROJECT_DIR/start_dashboard.sh"
Restart=always
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/iot-web.service >/dev/null <<EOF
[Unit]
Description=IoT prebuilt frontend and websocket proxy
Wants=iot-dashboard.service
After=network.target iot-dashboard.service
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStartPre=/usr/sbin/nginx -t -c "$PROJECT_DIR/deploy/nginx.conf"
ExecStart=/usr/sbin/nginx -c "$PROJECT_DIR/deploy/nginx.conf" -g "daemon off;"
Restart=always
RestartSec=10
TimeoutStopSec=15
KillSignal=SIGQUIT

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/iot-monitor.service \
    /etc/systemd/system/iot-dashboard.service /etc/systemd/system/iot-web.service
sudo systemctl enable iot-monitor.service iot-dashboard.service iot-web.service
sudo systemctl restart iot-monitor.service iot-dashboard.service iot-web.service
echo "Đang chờ dashboard phản hồi qua Nginx (tối đa 90 giây)..."
python3 -c 'from pi_deploy import wait_for_backend; wait_for_backend()'
sudo systemctl --no-pager --full status iot-monitor iot-dashboard iot-web || true
for service in iot-monitor iot-dashboard iot-web; do
    if ! sudo systemctl is-active --quiet "$service"; then
        echo "Dịch vụ $service chưa hoạt động. Xem journalctl -u $service -n 50 --no-pager." >&2
        exit 1
    fi
done
echo "Đã bật tự chạy. Khi khởi động Pi chỉ chạy monitor, backend và Nginx; không build frontend."
echo "Dashboard: http://<IP-của-Pi>:3000 (cùng Wi-Fi/LAN)"
echo "Ngưỡng, email nhận và mật khẩu chỉnh trong tab Cài đặt."
