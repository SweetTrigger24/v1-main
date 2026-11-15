"""TOOL KIỂM TRA TOÀN DIỆN HỆ THỐNG VÀ PHẦN CỨNG (Raspberry Pi 4)

Công dụng:
  1. Kiểm tra môi trường phần mềm (OS, Python venv, thư viện, cấu trúc thư mục).
  2. Hiển thị sơ đồ chân vật lý (Physical Pin vs BCM) để đối chiếu dây nối.
  3. Kiểm tra phần cứng từng bước:
     - LED Xanh (GPIO17), Vàng (GPIO27), Đỏ (GPIO22)
     - Còi Buzzer 3 chân PWM (GPIO18 - 1000Hz, 50% duty)
     - Nút bấm (GPIO23 - pull-up) đọc trạng thái thời gian thực
     - Cảm biến DHT22 (GPIO4 - Pin 7) đọc 5 lần, tính tỷ lệ & kiểm tra tính hợp lệ
  4. Đưa ra chẩn đoán lỗi và hướng dẫn khắc phục chi tiết nếu chân bị sai.

Chạy:
  python check_system.py            # Chạy toàn bộ kiểm tra tương tác
  python check_system.py --env-only # Chỉ kiểm tra môi trường phần mềm
  python check_system.py --config-only # Kiểm tra .env/SQLite, không khởi tạo GPIO
  python check_system.py --hw-only  # Chỉ kiểm tra phần cứng
"""
import argparse
import os
import platform
import socket
import sys
import time
from pathlib import Path

# Đảm bảo terminal in được tiếng Việt UTF-8 (đặc biệt trên Windows/CP1252)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Màu ANSI terminal
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_banner(text):
    print(f"\n{BOLD}{CYAN}{'=' * 75}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 75}{RESET}")


def print_status(component, status, note=""):
    color = GREEN if status == "PASS" else (YELLOW if status == "WARN" else RED)
    status_str = f"[{color}{status:4s}{RESET}]"
    print(f" {status_str} {BOLD}{component:28s}{RESET} {note}")


# ==============================================================================
# 1. BẢNG TRA CỨU CHÂN DÂY NỐI (PIN MAPPING)
# ==============================================================================
PIN_MAP = [
    ("DHT22 (DATA)", "GPIO4", "Pin 7", "Chân tín hiệu cảm biến (cần trở kéo 4.7k-10k lên 3.3V)"),
    ("DHT22 (VCC)", "3.3V / 5V", "Pin 1 / 2", "Nguồn cấp cảm biến (khuyến nghị 3.3V)"),
    ("DHT22 (GND)", "GND", "Pin 6 / 9", "Mass cảm biến"),
    ("LED Xanh (Dương)", "GPIO17", "Pin 11", "Đèn báo bình thường / Heartbeat (qua trở 220Ω)"),
    ("LED Vàng (Dương)", "GPIO27", "Pin 13", "Đèn báo dự báo sắp vượt ngưỡng (qua trở 220Ω)"),
    ("LED Đỏ (Dương)", "GPIO22", "Pin 15", "Đèn báo đo thực tế vượt ngưỡng (qua trở 220Ω)"),
    ("LEDs (Âm)", "GND", "Pin 14 / 20", "Chân âm chung của các đèn LED"),
    ("Còi 3 chân (I/O)", "GPIO18", "Pin 12", "Chân điều khiển xung PWM 1000Hz (Hardware PWM0)"),
    ("Còi 3 chân (VCC)", "5V (hoặc 3.3V)", "Pin 2 / 4", "Nguồn cấp module còi"),
    ("Còi 3 chân (GND)", "GND", "Pin 6", "Mass module còi"),
    ("Nút bấm (Pin 1)", "GPIO23", "Pin 16", "Tín hiệu nút bấm tắt còi khẩn cấp (Pull-up nội)"),
    ("Nút bấm (Pin 2)", "GND", "Pin 20", "Nối đất nút bấm (khi bấm chân GPIO23 sẽ nối đất)"),
]


def display_pin_table():
    print(f"\n{BOLD}{'THIẾT BỊ / CHỨC NĂNG':20s} {'CHÂN BCM':12s} {'PIN VẬT LÝ':14s} {'LƯU Ý ĐẤU NỐI'}{RESET}")
    print("-" * 75)
    for comp, bcm, phys, note in PIN_MAP:
        print(f"{comp:20s} {BOLD}{bcm:12s}{RESET} {CYAN}{phys:14s}{RESET} {note}")
    print("-" * 75)


# ==============================================================================
# 2. KIỂM TRA MÔI TRƯỜNG PHẦN MỀM
# ==============================================================================
def check_environment():
    print_banner("1. KIỂM TRA MÔI TRƯỜNG HỆ THỐNG & PHẦN MỀM")
    valid = True

    # 1. Hệ điều hành & Kiến trúc
    os_name = platform.system()
    machine = platform.machine()
    is_rpi = False
    model_info = "Không rõ"
    rpi_model_file = Path("/proc/device-tree/model")
    if rpi_model_file.exists():
        try:
            model_info = rpi_model_file.read_text(encoding="utf-8").strip("\x00")
            is_rpi = True
        except Exception:
            pass

    if is_rpi:
        print_status("Phần cứng Host", "PASS", f"{model_info} ({machine})")
    else:
        print_status("Phần cứng Host", "WARN", f"{os_name} ({machine}) - Không phát hiện Raspberry Pi vật lý")

    # 2. Môi trường Python & Venv
    in_venv = sys.prefix != sys.base_prefix
    py_ver = platform.python_version()
    if in_venv:
        print_status("Python Virtualenv", "PASS", f"Python {py_ver} (trong venv: {sys.prefix})")
    else:
        print_status("Python Virtualenv", "WARN", f"Python {py_ver} (Đang chạy ngoài venv! Khuyến nghị dùng venv)")

    # 3. Kiểm tra các thư viện bắt buộc
    libs = [
        ("RPi.GPIO", "Điều khiển PWM còi và chân GPIO"),
        ("gpiozero", "Quản lý LED và Nút bấm"),
        ("adafruit_dht", "Đọc cảm biến nhiệt độ độ ẩm DHT22"),
        ("board", "Định danh chân theo bo mạch CircuitPython"),
        ("numpy", "Xử lý mảng số học và chuẩn hóa MinMax"),
        ("pandas", "Xử lý dữ liệu chuỗi thời gian CSV"),
        ("reflex", "Dashboard thời gian thực"),
        ("dotenv", "Cấu hình .env"),
    ]

    optional_libs = [
        ("tensorflow", "Huấn luyện mạng nơ-ron LSTM"),
        ("matplotlib", "Biểu đồ huấn luyện cũ (tùy chọn)"),
        ("paho.mqtt.client", "Truyền dữ liệu MQTT"),
    ]

    print("\n" + BOLD + " Thư viện cốt lõi:" + RESET)
    for lib_name, desc in libs:
        try:
            __import__(lib_name)
            print_status(lib_name, "PASS", desc)
        except Exception as exc:
            valid = False
            print_status(lib_name, "FAIL", f"Không nạp được thư viện: {exc}. Cài lại requirements-pi.txt.")

    print("\n" + BOLD + " Thư viện nâng cao / Tùy chọn:" + RESET)
    for lib_name, desc in optional_libs:
        try:
            __import__(lib_name)
            print_status(lib_name, "PASS", desc)
        except Exception as exc:
            print_status(lib_name, "WARN", f"Chưa nạp được thư viện tùy chọn - {desc}: {exc}")

    # 4. Kiểm tra cấu trúc thư mục & file dự án
    print("\n" + BOLD + " Cấu trúc thư mục & Tệp tin:" + RESET)
    required_paths = [
        ("common.py", "File", "Cấu hình chung GPIO & hàm tiện ích"),
        ("hardware.py", "File", "Đọc DHT22, điều khiển LED/còi và nút bấm"),
        ("train_forecast.py", "File", "Script huấn luyện mô hình LSTM 12 giờ"),
        ("monitor.py", "File", "Script giám sát thời gian thực"),
        ("install_pi.sh", "File", "Cài đặt và tạo dịch vụ tự khởi động"),
        ("data", "Thư mục", "Nơi chứa sensor_data.csv và log"),
        ("model", "Thư mục", "Nơi chứa mô hình forecast_12h.keras"),
    ]

    for p_name, p_type, desc in required_paths:
        p = Path(p_name)
        if p.exists():
            print_status(f"{p_name} ({p_type})", "PASS", desc)
        else:
            print_status(f"{p_name} ({p_type})", "WARN", f"Chưa tồn tại ({desc})")

    # 5. Kiểm tra dữ liệu & mô hình đã có chưa
    data_file = Path("data/sensor_data.csv")
    if data_file.exists():
        lines = len(data_file.read_text(encoding="utf-8", errors="ignore").splitlines()) - 1
        lines = max(0, lines)
        status = "PASS" if lines >= 300 else "WARN"
        from provenance import dataset_provenance
        provenance = dataset_provenance(data_file.resolve())
        simulated = provenance.get("weather_source_rows", provenance.get("weather_rows", 0)) + provenance.get("sample_rows", 0)
        print_status("data/sensor_data.csv", status, f"CSV có {lines} mẫu, gồm {simulated} mẫu mô phỏng đã nhận diện")
    else:
        print_status("data/sensor_data.csv", "WARN", "Chưa có CSV lịch sử; monitor.py sẽ ghi số đo cảm biến")

    model_keras = Path("model/forecast_12h.keras")
    if model_keras.exists():
        print_status("model/forecast_12h.keras", "PASS", "Có mô hình LSTM 12 giờ; xem quyết định lựa chọn trong forecast_12h.json")
    else:
        print_status("Mô hình LSTM", "WARN", "Chưa có mô hình 12 giờ; monitor vẫn chạy dự báo chu kỳ ngày")

    return check_configuration() and valid


def check_configuration():
    """Kiểm tra cấu hình hiệu lực, không in mật khẩu hoặc gửi thông báo."""
    print_banner("KIỂM TRA CẤU HÌNH .env VÀ SQLITE")
    try:
        import common as config
        from runtime_settings import THRESHOLD_FIELDS, load_settings, validate_settings
        from storage import Store
        settings = {"thresholds": {
            field: getattr(config, field.upper())
            for field in THRESHOLD_FIELDS
        }, "email_enabled": config.EMAIL_ENABLED, "email_to": config.EMAIL_TO}
        if config.DB_FILE.exists():
            settings = load_settings(Store(config.DB_FILE))
        settings = validate_settings(settings)
    except Exception as exc:
        print_status("Cấu hình", "FAIL", str(exc))
        return False

    valid = True
    env_exists = (config.BASE_DIR / ".env").is_file()
    print_status("Tệp .env", "PASS" if env_exists else "WARN",
                 "Đã nạp cấu hình" if env_exists else "Đang dùng mặc định và biến môi trường của tiến trình")
    print_status("SQLite", "PASS", str(config.DB_FILE))
    print_status("8 ngưỡng thấp/cao", "PASS", "Đúng dải cảm biến và thứ tự tăng dần")

    if settings["email_enabled"]:
        missing = [name for name in ("EMAIL_SMTP", "EMAIL_FROM", "EMAIL_PASS") if not getattr(config, name).strip()]
        if missing:
            print_status("Email", "FAIL", "Thiếu " + ", ".join(missing))
            valid = False
        elif config.EMAIL_PORT == 587:
            print_status("Email", "FAIL", "Chương trình dùng SMTP SSL; cấu hình máy chủ SSL, thường ở cổng 465")
            valid = False
        else:
            try:
                validate_settings({**settings, "email_to": config.EMAIL_FROM})
            except ValueError:
                print_status("Email", "FAIL", "EMAIL_FROM cần là địa chỉ email hợp lệ")
                valid = False
            else:
                print_status("Email", "PASS", "Đủ cấu hình SMTP SSL; cần xác minh gửi thực tế trên Pi")
    else:
        print_status("Email", "WARN", "Đang tắt; nhập tài khoản SMTP trong .env và bật tại tab Cài đặt")

    if config.MQTT_ENABLED and not config.MQTT_HOST:
        print_status("MQTT", "FAIL", "Thiếu MQTT_HOST")
        valid = False
    else:
        print_status("MQTT", "PASS" if config.MQTT_ENABLED else "WARN",
                     "Đã bật; cần broker đang chạy tại MQTT_HOST/MQTT_PORT" if config.MQTT_ENABLED else "Đang tắt")

    model_ready = config.FORECAST_MODEL.is_file() and config.FORECAST_META.is_file()
    print_status("Mô hình LSTM 12 giờ", "PASS" if model_ready else "WARN",
                 "Có cặp mô hình/metadata; monitor kiểm định định dạng khi nạp" if model_ready else
                 "Chưa có cặp forecast_12h.keras/json; dùng dự báo chu kỳ ngày")
    return valid


# ==============================================================================
# 3. KIỂM TRA PHẦN CỨNG TƯƠNG TÁC
# ==============================================================================
def check_hardware_interactive():
    print_banner("2. KIỂM TRA PHẦN CỨNG TỪNG CHÂN (INTERACTIVE HARDWARE TEST)")
    display_pin_table()

    # Kiểm tra xem có thư viện phần cứng không
    try:
        import RPi.GPIO as GPIO
        import adafruit_dht
        import board
        from gpiozero import LED, Button
    except ImportError as e:
        print(f"\n{RED}{BOLD}Không thể chạy kiểm tra phần cứng do thiếu thư viện GPIO: {e}{RESET}")
        print("Lưu ý: Nếu đang chạy trên máy tính Windows/Mac, chỉ có thể kiểm tra phần mềm.")
        print("Trên Raspberry Pi: Hãy kích hoạt venv và chạy lại.")
        return False

    from common import (BUZZER_DUTY, BUZZER_FREQ, PIN_BUTTON, PIN_BUZZER, PIN_DHT,
                        PIN_LED_GREEN, PIN_LED_RED, PIN_LED_YELLOW)

    results = {}

    # -------------------------------------------------------------------------
    # TEST 1: LED XANH (GPIO17 / Pin 11)
    # -------------------------------------------------------------------------
    print(f"\n{BOLD}[TEST 1/6] Kiểm tra LED XANH (GPIO{PIN_LED_GREEN} - Pin 11)...{RESET}")
    print(" -> Đèn LED Xanh sẽ nhấp nháy 3 lần (0.4s bật, 0.2s tắt)...")
    led_green = None
    try:
        led_green = LED(PIN_LED_GREEN)
        for _ in range(3):
            led_green.on()
            time.sleep(0.4)
            led_green.off()
            time.sleep(0.2)
        ans = input(" ? Bạn có thấy đèn LED XANH nhấp nháy không? (c/k hoặc y/n): ").strip().lower()
        if ans in ["c", "y", "yes", "co"]:
            results["LED Xanh (GPIO17)"] = "PASS"
            print_status("LED Xanh (GPIO17)", "PASS", "Hoạt động chính xác")
        else:
            results["LED Xanh (GPIO17)"] = "FAIL"
            print_status("LED Xanh (GPIO17)", "FAIL", "Kiểm tra lại: Chân Pin 11, cực dương LED, trở 220Ω và nối đất Pin 14")
    except Exception as e:
        results["LED Xanh (GPIO17)"] = "FAIL"
        print_status("LED Xanh (GPIO17)", "FAIL", f"Lỗi khởi tạo: {e}")
    finally:
        if led_green is not None:
            led_green.close()

    # -------------------------------------------------------------------------
    # TEST 2: LED VÀNG (GPIO27 / Pin 13)
    # -------------------------------------------------------------------------
    print(f"\n{BOLD}[TEST 2/6] Kiểm tra LED VÀNG (GPIO{PIN_LED_YELLOW} - Pin 13)...{RESET}")
    print(" -> Đèn LED Vàng sẽ nhấp nháy 3 lần...")
    led_yellow = None
    try:
        led_yellow = LED(PIN_LED_YELLOW)
        for _ in range(3):
            led_yellow.on()
            time.sleep(0.4)
            led_yellow.off()
            time.sleep(0.2)
        ans = input(" ? Bạn có thấy đèn LED VÀNG nhấp nháy không? (c/k hoặc y/n): ").strip().lower()
        if ans in ["c", "y", "yes", "co"]:
            results["LED Vàng (GPIO27)"] = "PASS"
            print_status("LED Vàng (GPIO27)", "PASS", "Hoạt động chính xác")
        else:
            results["LED Vàng (GPIO27)"] = "FAIL"
            print_status("LED Vàng (GPIO27)", "FAIL", "Kiểm tra lại: Chân Pin 13, cực dương LED, trở 220Ω và nối đất Pin 14")
    except Exception as e:
        results["LED Vàng (GPIO27)"] = "FAIL"
        print_status("LED Vàng (GPIO27)", "FAIL", f"Lỗi khởi tạo: {e}")
    finally:
        if led_yellow is not None:
            led_yellow.close()

    # -------------------------------------------------------------------------
    # TEST 3: LED ĐỎ (GPIO22 / Pin 15)
    # -------------------------------------------------------------------------
    print(f"\n{BOLD}[TEST 3/6] Kiểm tra LED ĐỎ (GPIO{PIN_LED_RED} - Pin 15)...{RESET}")
    print(" -> Đèn LED Đỏ sẽ nhấp nháy 3 lần...")
    led_red = None
    try:
        led_red = LED(PIN_LED_RED)
        for _ in range(3):
            led_red.on()
            time.sleep(0.4)
            led_red.off()
            time.sleep(0.2)
        ans = input(" ? Bạn có thấy đèn LED ĐỎ nhấp nháy không? (c/k hoặc y/n): ").strip().lower()
        if ans in ["c", "y", "yes", "co"]:
            results["LED Đỏ (GPIO22)"] = "PASS"
            print_status("LED Đỏ (GPIO22)", "PASS", "Hoạt động chính xác")
        else:
            results["LED Đỏ (GPIO22)"] = "FAIL"
            print_status("LED Đỏ (GPIO22)", "FAIL", "Kiểm tra lại: Chân Pin 15, cực dương LED, trở 220Ω và nối đất Pin 20")
    except Exception as e:
        results["LED Đỏ (GPIO22)"] = "FAIL"
        print_status("LED Đỏ (GPIO22)", "FAIL", f"Lỗi khởi tạo: {e}")
    finally:
        if led_red is not None:
            led_red.close()

    # -------------------------------------------------------------------------
    # TEST 4: CÒI BUZZER 3 CHÂN (GPIO18 / Pin 12 - PWM)
    # -------------------------------------------------------------------------
    print(f"\n{BOLD}[TEST 4/6] Kiểm tra CÒI BUZZER (GPIO{PIN_BUZZER} - Pin 12, PWM {BUZZER_FREQ}Hz, {BUZZER_DUTY}% duty)...{RESET}")
    print(" -> Còi sẽ phát 2 tiếng bíp rõ ràng (bíp 0.25s - ngắt 0.2s - bíp 0.25s)...")
    pwm = None
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIN_BUZZER, GPIO.OUT)
        pwm = GPIO.PWM(PIN_BUZZER, BUZZER_FREQ)
        pwm.start(0)

        # Tiếng bíp 1
        pwm.ChangeDutyCycle(BUZZER_DUTY)
        time.sleep(0.25)
        pwm.ChangeDutyCycle(0)
        time.sleep(0.2)
        # Tiếng bíp 2
        pwm.ChangeDutyCycle(BUZZER_DUTY)
        time.sleep(0.25)
        pwm.ChangeDutyCycle(0)

        ans = input(" ? Bạn có nghe thấy 2 tiếng còi bíp rõ ràng không? (c/k hoặc y/n): ").strip().lower()
        if ans in ["c", "y", "yes", "co"]:
            results["Còi Buzzer (GPIO18)"] = "PASS"
            print_status("Còi Buzzer (GPIO18)", "PASS", "Tín hiệu PWM phát âm thanh tốt")
        else:
            results["Còi Buzzer (GPIO18)"] = "FAIL"
            print_status("Còi Buzzer (GPIO18)", "FAIL", "Kiểm tra chân I/O nối Pin 12, VCC nối 5V (Pin 2/4), GND nối Pin 6")
    except Exception as e:
        results["Còi Buzzer (GPIO18)"] = "FAIL"
        print_status("Còi Buzzer (GPIO18)", "FAIL", f"Lỗi khởi tạo PWM: {e}")
    finally:
        if pwm is not None:
            pwm.stop()
            GPIO.cleanup(PIN_BUZZER)

    # -------------------------------------------------------------------------
    # TEST 5: NÚT BẤM (GPIO23 / Pin 16)
    # -------------------------------------------------------------------------
    print(f"\n{BOLD}[TEST 5/6] Kiểm tra NÚT BẤM (GPIO{PIN_BUTTON} - Pin 16, Pull-up nội)...{RESET}")
    print(" -> Vui lòng NHẤN THỬ NÚT BẤM trong vòng 8 giây tới...")
    button_pressed = False
    btn = None
    try:
        btn = Button(PIN_BUTTON, pull_up=True)
        start_t = time.monotonic()
        while time.monotonic() - start_t < 8.0:
            if btn.is_pressed:
                button_pressed = True
                print(f" {GREEN}{BOLD}>>> PHÁT HIỆN NÚT ĐÃ ĐƯỢC NHẤN THÀNH CÔNG! <<<{RESET}")
                time.sleep(0.5)
                break
            time.sleep(0.05)

        if button_pressed:
            results["Nút bấm (GPIO23)"] = "PASS"
            print_status("Nút bấm (GPIO23)", "PASS", "Nhận tín hiệu nhấn nút chuẩn xác")
        else:
            results["Nút bấm (GPIO23)"] = "FAIL"
            print_status("Nút bấm (GPIO23)", "FAIL", "Không thấy tín hiệu nhấn! Kiểm tra 1 chân vào Pin 16 (GPIO23), 1 chân vào Pin 20 (GND)")
    except Exception as e:
        results["Nút bấm (GPIO23)"] = "FAIL"
        print_status("Nút bấm (GPIO23)", "FAIL", f"Lỗi khởi tạo Button: {e}")
    finally:
        if btn is not None:
            btn.close()

    # -------------------------------------------------------------------------
    # TEST 6: CẢM BIẾN DHT22 (GPIO4 / Pin 7)
    # -------------------------------------------------------------------------
    print(f"\n{BOLD}[TEST 6/6] Kiểm tra CẢM BIẾN DHT22 (GPIO{PIN_DHT} - Pin 7)...{RESET}")
    print(" -> Đang đọc thử cảm biến 5 lần liên tiếp (chu kỳ 2.5s)...")
    ok_count = 0
    readings = []
    dht = None
    try:
        try:
            dht = adafruit_dht.DHT22(getattr(board, f"D{PIN_DHT}"), use_pulseio=False)
        except TypeError:
            dht = adafruit_dht.DHT22(getattr(board, f"D{PIN_DHT}"))

        for i in range(5):
            t_start = time.monotonic()
            try:
                t = dht.temperature
                h = dht.humidity
                elapsed = time.monotonic() - t_start
                if t is not None and h is not None and -40 <= t <= 80 and 0 <= h <= 100:
                    ok_count += 1
                    readings.append((t, h))
                    print(f"   Lần {i + 1}: {GREEN}Thành công{RESET} | Nhiệt độ: {t:.1f}°C, Độ ẩm: {h:.1f}% (mất {elapsed:.2f}s)")
                else:
                    print(f"   Lần {i + 1}: {YELLOW}Giá trị ngoài dải hợp lý{RESET} (t={t}, h={h})")
            except RuntimeError as err:
                print(f"   Lần {i + 1}: {YELLOW}Không nhận được phản hồi xung ({err.args[0]}){RESET}")
            except Exception as err:
                print(f"   Lần {i + 1}: {RED}Lỗi ngoại lệ ({err}){RESET}")
            time.sleep(2.5)

        if ok_count >= 3:
            avg_t = sum(r[0] for r in readings) / len(readings)
            avg_h = sum(r[1] for r in readings) / len(readings)
            results["Cảm biến DHT22 (GPIO4)"] = "PASS"
            print_status("Cảm biến DHT22 (GPIO4)", "PASS", f"Đạt {ok_count}/5 lần đọc (TB: {avg_t:.1f}°C, {avg_h:.1f}%)")
        elif ok_count > 0:
            results["Cảm biến DHT22 (GPIO4)"] = "WARN"
            print_status("Cảm biến DHT22 (GPIO4)", "WARN", f"Chỉ đọc được {ok_count}/5 lần! Dây tiếp xúc lỏng hoặc thiếu trở kéo 4.7k-10k")
        else:
            results["Cảm biến DHT22 (GPIO4)"] = "FAIL"
            print_status("Cảm biến DHT22 (GPIO4)", "FAIL", "Không đọc được lần nào! Kiểm tra: VCC 3.3V (Pin 1), DATA vào Pin 7 (GPIO4), GND vào Pin 6/9")
    except Exception as e:
        results["Cảm biến DHT22 (GPIO4)"] = "FAIL"
        print_status("Cảm biến DHT22 (GPIO4)", "FAIL", f"Lỗi khởi tạo driver DHT22: {e}")
    finally:
        if dht is not None:
            dht.exit()

    # =========================================================================
    # TỔNG KẾT PHẦN CỨNG
    # =========================================================================
    print_banner("3. BẢNG TỔNG KẾT ĐÁNH GIÁ PHẦN CỨNG")
    all_pass = True
    for comp, res in results.items():
        print_status(comp, res)
        if res != "PASS":
            all_pass = False

    if all_pass:
        print(f"\n{BOLD}{GREEN}Các thiết bị đều đạt bài kiểm tra.{RESET}")
        print("Hệ thống sẵn sàng chuyển sang bước Thu thập dữ liệu hoặc Chạy giám sát!")
    else:
        print(f"\n{BOLD}{RED}>>> CẢNH BÁO: PHÁT HIỆN MỘT SỐ CHÂN PHẦN CỨNG CHƯA ĐẠT! <<<{RESET}")
        print("Vui lòng kiểm tra các mục WARN/FAIL và đối chiếu bảng chân để kiểm tra dây nối.")

    return all_pass


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Tool kiểm tra toàn diện hệ thống & phần cứng Raspberry Pi")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--env-only", action="store_true", help="Chỉ kiểm tra môi trường phần mềm & thư viện")
    modes.add_argument("--hw-only", action="store_true", help="Chỉ kiểm tra phần cứng tương tác")
    modes.add_argument("--config-only", action="store_true", help="Kiểm tra .env và cài đặt SQLite, không khởi tạo GPIO")
    args = parser.parse_args()

    if args.config_only:
        raise SystemExit(0 if check_configuration() else 1)

    print(f"\n{BOLD}==========================================================================={RESET}")
    print(f"{BOLD}   TOOL KIỂM TRA TỔNG THỂ HỆ THỐNG & PHẦN CỨNG RASPBERRY PI 4 (v1)   {RESET}")
    print(f"{BOLD}==========================================================================={RESET}")

    environment_ok = True
    if not args.hw_only:
        environment_ok = check_environment()

    hardware_ok = True
    if not args.env_only:
        from common import GPIO_LOCK_PORT
        with socket.socket() as gpio_lock:
            try:
                gpio_lock.bind(("127.0.0.1", GPIO_LOCK_PORT))
            except OSError:
                print_status("Khóa GPIO", "FAIL", "Monitor hoặc bài test mạch đang chạy. Dừng monitor trước khi test.")
                raise SystemExit(1) from None
            hardware_ok = check_hardware_interactive()

    print("\nHoàn tất kiểm tra!")
    raise SystemExit(0 if environment_ok and hardware_ok else 1)


if __name__ == "__main__":
    main()
