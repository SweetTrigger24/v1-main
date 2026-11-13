"""Kiểm tra bản frontend và tạo cấu hình Nginx, chỉ dùng thư viện chuẩn."""
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REFLEX_VERSION = "0.9.13"
COMPILE_INPUTS = ("common.py", "rxconfig.py", "dashboard_auth.py", "runtime_settings.py",
                  "dashboard_data.py", "alerts.py", "storage.py", "provenance.py", "requirements.txt")


def wait_for_backend(url="http://127.0.0.1:3000/ping", timeout=90, interval=1):
    """Chờ HTTP qua Nginx và backend; systemd active chưa đảm bảo app đã sẵn sàng."""
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Dashboard chưa sẵn sàng. Xem journalctl -u iot-dashboard -u iot-web -n 50 --no-pager.")
        try:
            with opener.open(url, timeout=min(2, remaining)) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def source_digest(root):
    root = Path(root).resolve()
    files = [root / name for name in COMPILE_INPUTS] + sorted((root / "iot_dashboard").rglob("*.py"))
    if not (root / "iot_dashboard/iot_dashboard.py").is_file():
        raise ValueError("Thiếu mã nguồn dashboard")
    digest = hashlib.sha256()
    for path in files:
        if not path.is_file():
            raise ValueError(f"Thiếu tệp {path.name}")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        # Windows/Linux có thể khác xuống dòng khi giải nén qua Git.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def validate_frontend(root):
    root = Path(root).resolve()
    folder = root / "deploy/frontend"
    manifest_path = root / "deploy/frontend_manifest.json"
    if not manifest_path.is_file() or not (folder / "index.html").is_file():
        raise ValueError("Thiếu frontend đã build. Chạy python prepare_pi.py trên PC rồi chép gói iot-pi4-2gb.zip lên Pi.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("reflex_version") != REFLEX_VERSION:
        raise ValueError("Bản frontend không đúng phiên bản Reflex của dự án; cần build lại trên PC.")
    if manifest.get("source_sha256") != source_digest(root):
        raise ValueError("Mã dashboard đã thay đổi sau lần build. Chạy lại python prepare_pi.py trên PC.")
    if manifest.get("api_url") != "http://localhost:3000" or "index.html" not in manifest.get("files", {}):
        raise ValueError("Frontend không được cấu hình cho cổng 3000 cùng origin.")
    for name, expected in manifest["files"].items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frontend thiếu hoặc sai checksum: {name}")
    build_id = manifest.get("build_id", "")
    if len(build_id) != 16 or any(c not in "0123456789abcdef" for c in build_id):
        raise ValueError("Mã bản build không hợp lệ")
    return manifest


def nginx_config(frontend, *, port=3000, backend_port=8000, pid="/run/iot-web.pid",
                 mime_file="/etc/nginx/mime.types", error_log="/var/log/nginx/iot-error.log", worker_user="www-data"):
    paths = [str(value).replace("\\", "/") for value in (frontend, pid, mime_file, error_log)]
    if any('"' in p or "\n" in p or "$" in p for p in paths):
        raise ValueError("Đường dẫn Nginx không hợp lệ")
    frontend, pid, mime_file, error_log = paths
    if not (1 <= int(port) <= 65535 and 1 <= int(backend_port) <= 65535):
        raise ValueError("Cổng không hợp lệ")
    user = f"user {worker_user};" if worker_user else ""
    return f'''{user}
worker_processes 1;
pid "{pid}";
error_log "{error_log}" warn;
events {{ worker_connections 128; }}
http {{
    include "{mime_file}";
    default_type application/octet-stream;
    access_log off;
    sendfile on;
    keepalive_timeout 15;
    gzip_static on;
    map $http_upgrade $iot_connection_upgrade {{ default upgrade; '' close; }}
    server {{
        listen {int(port)};
        server_name _;
        root "{frontend}";
        index index.html;
        location ~ ^/(_event|ping|_upload|_health|_all_routes|auth-codespace)(/|$) {{
            proxy_pass http://127.0.0.1:{int(backend_port)};
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $iot_connection_upgrade;
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 3600;
            proxy_buffering off;
        }}
        location /assets/ {{
            try_files $uri =404;
            expires 30d;
            add_header Cache-Control "public, immutable";
        }}
        location / {{
            try_files $uri $uri/ /index.html;
            add_header Cache-Control "no-cache";
        }}
    }}
}}
'''
