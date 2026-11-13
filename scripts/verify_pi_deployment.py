"""QA: bản frontend đã build + Nginx thật + backend-only không có node_modules."""
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path

import psutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pi_deploy import nginx_config, validate_frontend
from storage import Store
from runtime_settings import default_thresholds, load_settings, save_settings
from dashboard_auth import ensure_auth
from common import now_local


def wait_http(url, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.25)
    raise RuntimeError(f"Không mở được {url}")


def stop_process(process):
    if process is None:
        return
    try:
        parent = psutil.Process(process.pid)
        for child in reversed(parent.children(recursive=True)):
            try: child.terminate()
            except psutil.NoSuchProcess: pass
        parent.terminate()
        psutil.wait_procs([parent], timeout=5)
    except psutil.NoSuchProcess:
        pass


def main():
    if os.name != "nt":
        raise SystemExit("Bài kiểm tra này dùng Nginx Windows cho QA; installer Pi dùng Nginx hệ thống.")
    nginx = Path(os.environ["IOT_QA_NGINX"]).resolve()
    validate_frontend(ROOT)
    # Thư mục riêng chỉ chứa mã Python và static build, không sao chép .web/node_modules.
    work = ROOT.parent / (".deployment-qa-" + uuid.uuid4().hex[:10])
    work.mkdir()
    for path in ROOT.glob("*.py"):
        shutil.copy2(path, work / path.name)
    shutil.copytree(ROOT / "iot_dashboard", work / "iot_dashboard", ignore=shutil.ignore_patterns("__pycache__"))
    static = work / "frontend"
    shutil.copytree(ROOT / "deploy/frontend", static)
    # Chỉ đổi cổng trong bản QA, không sửa frontend giao cho Pi (cổng 3000).
    for path in static.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".html", ".json"}:
            original = path.read_bytes()
            patched = original.replace(b"localhost:3000", b"localhost:3001")
            if patched != original:
                path.write_bytes(patched)
                compressed = path.with_name(path.name + ".gz")
                if compressed.exists(): compressed.write_bytes(gzip.compress(patched))
    (work / "logs").mkdir()
    (work / "temp").mkdir()
    config_file = work / "nginx.conf"
    config_file.write_text(nginx_config(static.as_posix(), port=3001, backend_port=8001,
        pid=(work / "nginx.pid").as_posix(), mime_file=(nginx.parent / "conf/mime.types").as_posix(),
        error_log=(work / "logs/nginx-error.log").as_posix(), worker_user=None), encoding="utf-8")
    store = Store(work / "qa.sqlite3")
    store.put("database_role", "qa")
    store.import_csv()
    with store.connection() as db:
        db.execute("DELETE FROM kv WHERE key='dashboard_auth' OR key LIKE 'login_attempt:%'")
    ensure_auth(store)
    settings = load_settings(store)
    save_settings(store, {"thresholds": default_thresholds(), "email_enabled": False,
                          "email_to": "settings-qa@example.invalid"}, settings["revision"])
    now = now_local().replace(microsecond=0)
    store.add_reading(now, 25, 65, source="demo")
    store.put("monitor", {"ok": True, "time": now.isoformat()})
    points = []
    for i in range(1, 73):
        stamp = now + timedelta(minutes=i * 10)
        points.append({"time": stamp.isoformat(), "label": stamp.strftime("%d/%m %H:%M"),
                       "temp": 25 + i / 24, "hum": 80 + i / 3.6, "temp_low": 24, "temp_high": 29,
                       "hum_low": 70, "hum_high": 100, "state": "XANH", "reasons": []})
    store.put("forecast", {"origin": now.isoformat(), "engine": "QA", "points": points})
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", REFLEX_DIR=str(ROOT.parent / ".reflex"), REFLEX_SSR="false",
               REFLEX_CHECK_LATEST_VERSION="false", REFLEX_TELEMETRY_ENABLED="false",
               REFLEX_API_URL="http://localhost:3001", IOT_DB_FILE=store.path, GRANIAN_WORKERS="1",
               IOT_QA_URL="http://127.0.0.1:3001", IOT_QA_PASSWORD="123456")
    backend = web = None
    backend_log = (work / "backend.log").open("w", encoding="utf-8")
    web_log = (work / "nginx-console.log").open("w", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW
    try:
        backend = subprocess.Popen([sys.executable, "-m", "reflex", "run", "--env", "prod", "--backend-only",
                                    "--backend-host", "127.0.0.1", "--backend-port", "8001"],
                                   cwd=work, env=env, stdout=backend_log, stderr=subprocess.STDOUT, creationflags=flags)
        wait_http("http://127.0.0.1:8001/ping")
        prefix = work.as_posix() + "/"
        subprocess.run([str(nginx), "-p", prefix, "-c", str(config_file), "-t"], check=True, creationflags=flags)
        web = subprocess.Popen([str(nginx), "-p", prefix, "-c", str(config_file), "-g", "daemon off;"],
                               cwd=work, stdout=web_log, stderr=subprocess.STDOUT, creationflags=flags)
        wait_http("http://127.0.0.1:3001/ping")
        wait_http("http://127.0.0.1:3001/")
        for script in ("verify_dashboard.py", "verify_settings_ui.py", "verify_auth_ui.py"):
            subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, env=env, check=True)
        # Tên máy khác localhost kiểm tra URL WebSocket khi mở từ máy khác trong LAN.
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True,
                args=["--host-resolver-rules=MAP iot-pi.test 127.0.0.1", "--no-proxy-server"])
            page = browser.new_page()
            sockets = []
            page.on("websocket", lambda websocket: sockets.append(websocket.url))
            page.goto("http://iot-pi.test:3001", wait_until="networkidle")
            page.locator("#login-password").fill("qa-new-pass-2026")
            page.get_by_role("button", name="Đăng nhập", exact=True).click()
            page.get_by_text("MÔ PHỎNG · không phải số đo thật", exact=True).wait_for(timeout=15000)
            assert any(url.startswith("ws://iot-pi.test:3001/_event") for url in sockets), sockets
            browser.close()
        print("PASS: đăng nhập và WebSocket dùng đúng tên máy truy cập qua LAN")
        children = psutil.Process(backend.pid).children(recursive=True)
        assert not any("node" in p.name().lower() or "bun" in p.name().lower() for p in children)
        assert not (work / ".web/node_modules").exists()
        assert not (work / ".web/build/client").exists()
        backend_log.flush()
        log = (work / "backend.log").read_text(encoding="utf-8")
        assert "Creating Production Build" not in log and "Compiling:" not in log
        report = {"frontend_prebuilt": True, "real_nginx_proxy": True, "websocket_and_login": True,
                  "lan_hostname_websocket": True,
                  "backend_no_node_or_build": True, "hardware_pi_tested": False,
                  "backend_rss_mib_windows": round(sum(p.memory_info().rss for p in [psutil.Process(backend.pid), *children]) / 1024 ** 2, 1),
                  "qa_directory": str(work)}
        (ROOT / "artifacts/pi-deployment-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PASS: frontend đã build + Nginx + backend-only; không Node/Bun/build khi chạy")
    finally:
        if web:
            subprocess.run([str(nginx), "-p", work.as_posix() + "/", "-c", str(config_file), "-s", "quit"],
                           check=False, creationflags=flags)
        stop_process(web)
        stop_process(backend)
        backend_log.close()
        web_log.close()
        print("QA logs:", work)


if __name__ == "__main__":
    main()
