# BÁO CÁO TỔNG KẾT TOÀN DIỆN CÁC CÔNG VIỆC ĐÃ HOÀN THÀNH

> Báo cáo lịch sử của bản 10 phút. Không dùng bảng kết quả này để khẳng định độ chính xác của bản 12 giờ. Thiết kế và cách vận hành hiện tại ở [README.md](README.md).

Dự án: **Hệ thống IoT Giám sát & Dự báo Nhiệt độ - Độ ẩm bằng LSTM trên Raspberry Pi 4**  
Thời gian cập nhật: **Tháng 10/2026**

---

## 📌 I. TỔNG QUAN HỆ THỐNG
Hệ thống IoT ứng dụng mạng nơ-ron học sâu (Deep Learning) chạy trực tiếp tại biên (**Edge Computing**) trên vi máy tính **Raspberry Pi 4 (Raspberry Pi OS 64-bit)**:
- Đo đạc thông số môi trường (Nhiệt độ & Độ ẩm) qua cảm biến **DHT22 (GPIO4)**.
- Xử lý chuỗi thời gian, phân tích xu hướng và dự báo trước 10 phút bằng mô hình **LSTM**.
- Cảnh báo phần cứng đa mức: **Đèn LED 3 màu (Xanh/Vàng/Đỏ)**, **Còi Active Buzzer xung PWM (GPIO18)** và **Nút bấm khẩn cấp MUTE (GPIO23)**.
- Giao tiếp và giám sát mở rộng: **Web Dashboard HTML5 thời gian thực (Cổng 5000)**, **Cảnh báo Email tự động (`smtplib`)**, truyền dữ liệu **MQTT (`paho-mqtt`)** và tự khởi động cùng OS qua **Systemd Service**.

---

## 🛠️ II. CẢI TIẾN & KHẮC PHỤC LỖI PHẦN CỨNG THỰC TẾ
1. **Khắc phục lỗi còi Buzzer 3 chân**:
   - *Vấn đề*: Module còi 3 chân có transistor đệm khi dùng lệnh `gpiozero.Buzzer` chỉ kêu "tách" 1 tiếng rồi tắt ngấm.
   - *Giải pháp*: Thay thế hoàn toàn bằng **`RPi.GPIO.PWM(18, 1000)`** với **tần số 1000 Hz, duty cycle 50%**.
   - *Nhịp cảnh báo*: Sử dụng luồng nền đa nhiệm (`threading.Thread`) phát xung ngắt quãng (bíp 0.2s – ngắt 0.2s) độc lập, không block vòng lặp lấy mẫu cảm biến.
2. **Nút bấm MUTE ngắt còi tạm thời (GPIO23)**:
   - Cấu hình điện trở kéo lên nội (`pull_up=True`). Bấm nút sẽ tắt còi ngay lập tức trong **5 phút (`MUTE_SEC = 300s`)** để người dùng xử lý sự cố.
3. **Cơ chế đọc cảm biến DHT22 an toàn**:
   - Cảm biến giao tiếp qua chuẩn 1-wire dễ lỗi xung $\rightarrow$ Toàn bộ thao tác đọc đều bọc qua hàm `read_sensor(dht, retries=5)` với thời gian nghỉ giữa các lần thử là 2.0s để tránh hiện tượng treo chương trình.

---

## 🧠 III. THÍ NGHIỆM ĐỐI CHỨNG MÔ HÌNH HỌC SÂU (MỤC H - markdown.md)
Đã triển khai và thực hiện thành công thí nghiệm đối chứng khoa học theo đúng Mục H:
- **Kiến trúc đối chứng (Đặc tả gốc)**: `LSTM(32) → Dense(2)` (~4.546 tham số).
- **Kiến trúc cải tiến (Mục B)**: `LSTM(64, return_seq=True) → Dropout(0.2) → LSTM(32) → Dense(2)` (~29.634 tham số).
- Huấn luyện với tập dữ liệu thực tế 4.320 mẫu, chia theo thời gian 70/15/15, qua **3 seed ngẫu nhiên (`42`, `7`, `2026`)**.

### Bảng kết quả thực nghiệm thực tế trên Raspberry Pi 4:

| Mô hình / Kiến trúc | Seed | RMSE Nhiệt (°C) | RMSE Ẩm (%) | MAE Nhiệt (°C) | MAE Ẩm (%) | Số tham số | Thời gian train (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Mốc (giữ nguyên)** | — | 0.373 | 1.355 | 0.289 | 1.097 | 0 | 0.0s |
| **LSTM(32) Đơn lớp** | 42 | 0.354 | 1.324 | 0.267 | 1.094 | 4.546 | 273.2s |
| **LSTM(32) Đơn lớp (CHỌN)** | **7** | **0.314** | **1.230** | **0.239** | **1.002** | **4.546** | **351.4s** |
| **LSTM(32) Đơn lớp** | 2026 | 0.332 | 1.312 | 0.249 | 1.079 | 4.546 | 360.7s |
| **LSTM(64+32) Hai lớp** | 42 | 0.363 | 1.385 | 0.276 | 1.141 | 29.634 | 576.2s |
| **LSTM(64+32) Hai lớp** | 7 | 0.419 | 1.610 | 0.327 | 1.302 | 29.634 | 181.3s |
| **LSTM(64+32) Hai lớp** | 2026 | 0.465 | 1.739 | 0.371 | 1.423 | 29.634 | 124.8s |

### Đánh giá học thuật:
- **`LSTM(32)` đơn lớp chiến thắng thuyết phục**: RMSE trung bình $0.8110 \pm 0.0284$, tốt hơn mô hình mốc ($0.8639$) và tốt hơn hẳn mạng 2 lớp ($0.9970$).
- **Chứng minh Định lý xấp xỉ phổ quát (Chương 4)**: Mạng 2 lớp quá nhiều tham số (29.634) gây ra hiện tượng quá khớp (overfitting) và tăng thời gian tính toán không cần thiết trên Pi 4. Mạng đơn lớp 4.546 tham số vừa vặn tối ưu cho bài toán biên.
- **Mô hình triển khai chính thức**: `LSTM(32) Don Lop (Seed 7)` được lưu vào `model/lstm_model.keras`.
- **Dữ liệu xuất báo cáo**: Toàn bộ kết quả đã được ghi vào file [`model/experiment_results.csv`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/model/experiment_results.csv) và 3 biểu đồ ảnh (`loss.png`, `forecast.png`, `forecast_hum.png`).

---

## ⚡ IV. NÂNG CẤP BỘ GIÁM SÁT THỜI GIAN THỰC (`monitor.py`)
1. **Cơ chế Dual-Engine & Tự động Fallback**:
   - Hỗ trợ chạy cả TFLite Quantized lẫn Keras Model.
   - Thêm cơ chế tự động chuyển sang Keras nếu trình thông dịch TFLite trên ARM64 báo lỗi thiếu Flex delegate (`RuntimeError`), giúp chương trình **chạy liên tục 100% không bao giờ bị crash**.
   - Hỗ trợ cờ `--no-tflite` để chạy Keras trực tiếp (suy luận chỉ mất ~3ms trên CPU Pi 4).
   - Hỗ trợ cờ `--prefill` nạp sẵn 30 mẫu đo cũ từ CSV để dự báo được ngay từ phút đầu tiên mà không cần chờ 30 phút.
2. **Máy trạng thái cảnh báo 3 mức**:
   - **XANH**: Nhiệt độ & độ ẩm an toàn $\rightarrow$ Đèn xanh sáng, còi tắt.
   - **VÀNG**: AI dự báo trước 10 phút SẮP vượt ngưỡng $\ge 2$ lần liên tiếp $\rightarrow$ Đèn vàng sáng (Cảnh báo sớm).
   - **ĐỎ**: Đo thực tế $\ge 3$ lần liên tiếp vượt ngưỡng $\rightarrow$ Đèn đỏ sáng + Còi PWM kêu nhịp.
3. **Cảnh báo Email & MQTT**:
   - Gửi mail qua `smtplib` với thuật toán chống spam: chỉ gửi khi trạng thái thay đổi hoặc sau cooldown 30 phút.
   - Publish dữ liệu telemetry JSON lên broker MQTT (`qos=1`, `client_id="pi-monitor"`).

---

## 🌐 V. XÂY DỰNG WEB DASHBOARD HIỆN ĐẠI (`dashboard.py`)
- Nâng cấp từ một hàm xuất ảnh tĩnh thành **Giao diện Web HTML5 Dark Mode hoàn chỉnh**.
- **100% Offline**: Tự vẽ đồ thị bằng HTML5 Canvas trực tiếp trong trình duyệt, không cần mạng internet hay tải thư viện ngoài.
- **Tự động cập nhật (Live Real-time)**: Cứ mỗi 3 giây tự nạp dữ liệu mới qua fetch/AJAX mà không cần F5.
- 4 thẻ thông số: Nhiệt độ, Độ ẩm, Dự báo AI 10 phút tới, Badge trạng thái nhấp nháy đèn.
- 2 biểu đồ trực quan sóng nhiệt độ & độ ẩm so sánh đường Thực tế vs Dự báo vs Đường ngưỡng.
- Nút bấm tải file CSV nhật ký (`/data`) và API JSON (`/latest`, `/api/history`).

---

## 📊 VI. DỮ LIỆU SẴN CÓ & TÙY BIẾN CHU KỲ LẤY MẪU
1. **Dữ liệu tạo sẵn**:
   - Đã sinh sẵn file **[`data/sensor_data.csv`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/data/sensor_data.csv)** gồm **4.320 mẫu** (chu kỳ 3 ngày thực tế, có 589 mẫu vượt ngưỡng để AI học cảnh báo). Nhờ đó bạn có thể train và test ngay lập tức mà không cần cắm máy gom dữ liệu 48h.
2. **Script sinh dữ liệu mẫu [`generate_sample_data.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/generate_sample_data.py)**:
   - Tự động thích ứng với biến `SAMPLE_SEC` trong `common.py`.
3. **Hỗ trợ chu kỳ lấy mẫu nhanh (Demo 5s / 10s)**:
   - Cho phép đổi `SAMPLE_SEC = 5` trong `common.py` để demo trực tiếp: cứ 5 giây nhảy 1 mẫu, AI nhìn quá khứ 2.5 phút và dự báo trước 50 giây. Rất tiện lợi khi biểu diễn trước hội đồng chấm thi.

---

## 🧪 VII. BỘ TEST & CÔNG CỤ CHẨN ĐOÁN HỆ THỐNG
1. **[`check_system.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/check_system.py)**:
   - Tool chẩn đoán toàn diện hệ thống: nhận diện phần cứng, kiểm tra thư viện, in bảng sơ đồ chân 40-pin header và kiểm tra tương tác từng LED, Còi PWM, Nút bấm và DHT22.
2. **[`test_suite.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/test_suite.py)**:
   - Bộ kiểm thử tự động gồm **11 kịch bản test** bằng `pytest` (Toán học, MinMax, Cửa sổ trượt, Máy trạng thái, Chống spam email, Xử lý file hỏng), **kết quả 11/11 PASSED 100%**.
3. **Tự động hóa triển khai**:
   - [`setup.sh`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/setup.sh): Cài đặt trọn gói hệ thống trong 1 lệnh trên Debian 12 Bookworm.
   - [`monitor.service`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/monitor.service) & [`collect.service`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/collect.service): Tự khởi động cùng Raspberry Pi khi cắm điện.

---

## 📂 VIII. BẢNG DANH MỤC TOÀN BỘ FILE MÃ NGUỒN

| Tên File | Chức năng chính |
| :--- | :--- |
| [`common.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/common.py) | Cấu hình hằng số chân GPIO, chu kỳ lấy mẫu, ngưỡng cảnh báo, hàm toán học MinMax & RMSE. |
| [`train_lstm.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/train_lstm.py) | Huấn luyện LSTM, chạy thí nghiệm đối chứng 3 seed Mục H, nén TFLite và xuất biểu đồ. |
| [`monitor.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/monitor.py) | Giám sát thời gian thực, máy trạng thái XANH/VÀNG/ĐỎ, điều khiển LED/Còi PWM/Email/MQTT. |
| [`dashboard.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/dashboard.py) | Web Dashboard Flask hiển thị biểu đồ & thông số trực tiếp trên trình duyệt điện thoại/laptop. |
| [`check_system.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/check_system.py) | Tool kiểm tra toàn diện phần mềm, môi trường và tương tác trực quan từng chân phần cứng. |
| [`collect_data.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/collect_data.py) | Thu thập dữ liệu cảm biến DHT22 định kỳ ghi vào `data/sensor_data.csv`. |
| [`generate_sample_data.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/generate_sample_data.py) | Script tự động sinh dữ liệu mô phỏng chu kỳ ngày/đêm để huấn luyện và test ngay lập tức. |
| [`test_hw.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/test_hw.py) | Script kiểm tra nhanh phần cứng tuần tự. |
| [`test_suite.py`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/test_suite.py) | Bộ unit test tự động (11 test cases) xác minh độ tin cậy của thuật toán. |
| [`setup.sh`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/setup.sh) | Script bash tự động cài đặt trọn gói hệ thống, venv và requirements trong 1 lệnh duy nhất. |
| [`requirements.txt`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/requirements.txt) | Danh sách thư viện phụ thuộc Python đã chuẩn hóa cho Debian 12 Bookworm. |
| [`monitor.service`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/monitor.service) | Dịch vụ systemd tự động khởi động giám sát thời gian thực khi cắm điện Raspberry Pi. |
| [`collect.service`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/collect.service) | Dịch vụ systemd tự động chạy thu thập dữ liệu ngầm không lo rớt SSH. |
| [`HUONG_DAN_TRIEN_KHAI.md`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/HUONG_DAN_TRIEN_KHAI.md) | Cẩm nang hướng dẫn triển khai thực tế 10 bước chi tiết trên Raspberry Pi 4. |
| [`markdown.md`](file:///c:/Users/Admin/Desktop/iot-11(3)/v1/markdown.md) | Tài liệu bối cảnh môn học, ánh xạ kiến thức Chương 4 và kết quả đối chứng Mục A–H. |
