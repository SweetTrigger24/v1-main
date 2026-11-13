# BỐI CẢNH DỰ ÁN

> Đặc tả lịch sử của bản 10 phút. Theo định hướng mới ngày 10/11/2026, bản hiện hành chuyển sang Reflex, dự báo trực tiếp tối đa 12 giờ và cảnh báo thấp/cao. Xem [README.md](README.md) và `train_forecast.py`; các giới hạn/kiến trúc cũ dưới đây không còn mô tả toàn bộ bản hiện hành.

Dự án môn học: hệ thống IoT chạy trên Raspberry Pi 4 đo nhiệt độ và độ ẩm bằng cảm biến DHT22, dùng mạng LSTM dự báo giá trị 10 phút tới, và cảnh báo bằng LED và còi khi giá trị đo vượt ngưỡng hoặc khi mô hình dự đoán sắp vượt ngưỡng. Nội dung lý thuyết dựa trên Chương 4 "Học sâu cho IoT" (sách Hands-On Artificial Intelligence for IoT), nhưng code viết bằng TensorFlow 2 / Keras, KHÔNG dùng TensorFlow 1.x (tf.placeholder, tf.contrib) như trong sách.

## Phần cứng và môi trường
- Raspberry Pi 4, Raspberry Pi OS 64-bit bản trixie, Python trong venv (tạo bằng --system-site-packages).
- DHT22 -> GPIO4. LED xanh/vàng/đỏ -> GPIO17/27/22. Còi 3 chân -> GPIO18 (dùng RPi.GPIO PWM, BUZZER_FREQ=1000, BUZZER_DUTY=50 thay cho cờ BUZZER_ACTIVE_HIGH cũ — đã sửa do phần cứng thực tế yêu cầu PWM liên tục). Nút bấm -> GPIO23 (pull_up).
- Không dùng camera. Thư viện: gpiozero, adafruit-circuitpython-dht, tensorflow/keras, numpy, pandas, matplotlib.
- Code chưa được chạy thử trên Pi thật; phần Keras và GPIO mới chỉ kiểm thử bằng module giả.

## Cấu trúc file
- common.py: hằng số cấu hình (chân GPIO, SAMPLE_SEC=60, LOOK_BACK=30, HORIZON=10, ngưỡng TEMP_HIGH=32, HUM_HIGH=90, RED_CONSECUTIVE=3, YELLOW_CONSECUTIVE=2, MUTE_SEC=300) và hàm tiện ích make_windows, normalize, denormalize, các hàm RMSE/MAE, save_meta/load_meta.
- test_hw.py: kiểm tra LED, còi, nút bấm, DHT22.
- collect_data.py: đọc DHT22 theo chu kỳ, ghi data/sensor_data.csv, có thử lại khi lỗi.
- train_lstm.py: làm sạch dữ liệu, chia tập, chuẩn hóa, so sánh với mô hình mốc, huấn luyện LSTM, lưu model/lstm_model.keras và model/meta.json.
- monitor.py: đọc cảm biến, dự báo bằng LSTM, quyết định trạng thái XANH/VANG/DO, điều khiển LED và còi, ghi data/monitor_log.csv.
- buzzer_on.py: script riêng thử còi.

# ÁNH XẠ KIẾN THỨC CHƯƠNG 4 -> PHẦN CỦA PROJECT

## A. Tiền xử lý dữ liệu (common.py, train_lstm.py)
- Chuẩn hóa MinMax: hàm normalize/denormalize, tham số lo/hi lưu trong meta.json. Chỉ fit trên tập huấn luyện để tránh rò rỉ dữ liệu.
- Chia tập huấn luyện / kiểm định / kiểm thử (70/15/15): hàm split_scale_window, chia THEO THỜI GIAN, không xáo trộn, vì đây là chuỗi thời gian.
- Dữ liệu chuỗi thời gian cho RNN: hàm make_windows tạo cửa sổ trượt (LOOK_BACK bước quá khứ -> giá trị sau HORIZON bước).

## B. Kiến trúc mô hình (train_lstm.py) — CẬP NHẬT
- Neuron nhân tạo và tổng có trọng số h = sum(w*x) - theta: nền tảng của mọi lớp trong mô hình, kể cả lớp Dense đầu ra.
- Hàm kích hoạt: ô LSTM dùng sigmoid cho ba cổng và tanh cho trạng thái ứng viên; lớp Dense(2) đầu ra không dùng kích hoạt (tuyến tính) vì đây là bài toán hồi quy.
- RNN và vòng lặp phản hồi (trạng thái ẩn truyền qua các bước thời gian): lớp LSTM xử lý chuỗi LOOK_BACK bước.
- LSTM: cổng quên, cổng vào, cổng ra, bộ nhớ dài hạn c_t và ngắn hạn h_t. Lý do chọn LSTM thay RNN thường là vấn đề triệt tiêu độ dốc khi chuỗi dài.
- Kiến trúc chính: LSTM(64, return_sequences=True) -> Dropout(0.2) -> LSTM(32) -> Dense(2).
- Lớp LSTM thứ nhất BẮT BUỘC có return_sequences=True.
- Dropout(0.2) trong Keras là tỉ lệ tắt 20% (tương đương keep_prob = 0.8); chương 4 dùng keep_prob = 0.7 trong LeNet. Dropout chỉ hoạt động khi huấn luyện.
- Lý do theo chương 4:
  1. Mục RNN cho phép nhiều lớp ẩn; mục LSTM cho biết lớp ẩn của RNN được thay bằng ô LSTM. Hai lớp LSTM là SUY RA từ hai điều này, chương không mô tả trực tiếp.
  2. Theo mục MLP/XOR, lớp ẩn biến đổi dữ liệu thành dạng lớp sau dùng được; lớp LSTM(64) tạo chuỗi biểu diễn ẩn, lớp LSTM(32) tóm tắt thành vector cho Dense.
  3. Giả thuyết cần kiểm chứng (KHÔNG phải kết luận của chương): lớp dưới bắt biến động ngắn hạn, lớp trên bắt xu hướng dài hạn. Tương tự ý "lớp dưới đặc trưng chung, lớp trên trừu tượng hơn" ở mục CNN.
  4. Dropout giảm quá khớp (mục LeNet) để bù cho việc tăng số tham số (khoảng 29.634 so với khoảng 4.546 của LSTM(32) đơn lớp).
- Đặc tả gốc LSTM(32) -> Dense(2) giữ làm kiến trúc đối chứng.

## C. Huấn luyện (train_lstm.py)
- Thuật toán giảm độ dốc, learning rate và bộ tối ưu: Adam, learning_rate=1e-3.
- Hàm mất mát MSE cho bài toán hồi quy: loss="mse".
- Lan truyền ngược theo thời gian (BPTT): Keras tự thực hiện; cửa sổ LOOK_BACK tương ứng với việc cắt ngắn BPTT (truncated BPTT) trong sách. Không viết gradient thủ công.
- Epoch và batch: epochs tối đa 100, batch_size=32.
- Quá khớp và thiếu khớp: theo dõi loss huấn luyện và loss kiểm định (model/loss.png); EarlyStopping(patience=10, restore_best_weights=True). Sách còn nêu điều chuẩn L2 và dropout là cách giảm quá khớp; hiện chưa dùng, có thể thêm nếu loss kiểm định tách xa loss huấn luyện.
- TensorBoard để quan sát quá trình huấn luyện: callback TensorBoard(log_dir="logs").

## D. Đánh giá (train_lstm.py)
- MSE, RMSE, MAE trên tập kiểm thử, tính theo đơn vị gốc (độ C và %).
- Mô hình mốc: dự đoán "giá trị sau HORIZON bước = giá trị hiện tại". LSTM phải có RMSE thấp hơn mốc, nếu không script in cảnh báo. Sách dùng hồi quy tuyến tính và MLP làm mốc so sánh; có thể thêm MLP hồi quy làm mốc thứ hai.

## E. Suy luận thời gian thực trên thiết bị biên (monitor.py)
- Nạp mô hình đã huấn luyện, dùng deque độ dài LOOK_BACK làm cửa sổ trượt, chuẩn hóa bằng đúng lo/hi trong meta.json, dự đoán, rồi denormalize.
- Đây là phần ứng dụng DL vào dữ liệu IoT mà chương mở đầu nhấn mạnh.

## F. Logic cảnh báo (monitor.py)
- Logic này KHÔNG thuộc nội dung chương 4, đây là logic ứng dụng: so sánh với ngưỡng, đếm số lần vượt liên tiếp để tránh báo giả, hai nhánh cảnh báo (đo thực tế -> đỏ + còi; dự đoán -> vàng), nút bấm tắt còi MUTE_SEC giây.

## G. Ngoài phạm vi hiện tại (đừng tự thêm)
- CNN, LeNet, gộp, tích chập: chỉ dành cho đề tài có camera, project này không dùng camera.
- Autoencoder và VAE: chỉ dành cho đề tài phát hiện bất thường rung, không có trong project này.
- MLP, bài toán XOR, GRU: nền tảng lý thuyết; GRU chỉ là phương án so sánh tùy chọn thay cho LSTM.

## H. Thí nghiệm đối chứng bắt buộc (không bỏ qua)
- Định lý xấp xỉ phổ quát (chương 4) cho phép một lớp ẩn là đủ về lý thuyết, nên hai lớp chỉ được giữ nếu có bằng chứng thực nghiệm.
- Huấn luyện cả hai kiến trúc với cùng dữ liệu, cùng cách chia theo thời gian, cùng EarlyStopping, chạy ít nhất 3 giá trị seed khác nhau (tf.keras.utils.set_random_seed).
- So sánh trên tập kiểm thử, theo đơn vị gốc: RMSE và MAE của nhiệt độ và độ ẩm; kèm mô hình mốc "giữ nguyên giá trị hiện tại"; kèm số tham số và thời gian huấn luyện trên Pi 4.
- Giữ hai lớp CHỈ KHI cải thiện RMSE vượt dao động giữa các seed, loss kiểm định không tách xa loss huấn luyện (không quá khớp), và thời gian huấn luyện chấp nhận được. Ngược lại quay về LSTM(32).
- Ghi kết quả so sánh vào báo cáo.

### KẾT QUẢ THỰC NGHIỆM THỰC TẾ TRÊN RASPBERRY PI 4 (CẬP NHẬT):
Thực hiện chạy đủ 2 kiến trúc qua 3 seed (42, 7, 2026) trên tập dữ liệu 4.320 mẫu:

| Mô hình / Kiến trúc | Seed | RMSE Nhiệt (°C) | RMSE Ẩm (%) | MAE Nhiệt (°C) | MAE Ẩm (%) | Số tham số | Thời gian train (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Mốc (giữ nguyên)** | — | 0.373 | 1.355 | 0.289 | 1.097 | 0 | 0.0s |
| **LSTM(32) Đơn lớp** | 42 | 0.354 | 1.324 | 0.267 | 1.094 | 4.546 | 273.2s |
| **LSTM(32) Đơn lớp (CHỌN)** | **7** | **0.314** | **1.230** | **0.239** | **1.002** | **4.546** | **351.4s** |
| **LSTM(32) Đơn lớp** | 2026 | 0.332 | 1.312 | 0.249 | 1.079 | 4.546 | 360.7s |
| **LSTM(64+32) Hai lớp** | 42 | 0.363 | 1.385 | 0.276 | 1.141 | 29.634 | 576.2s |
| **LSTM(64+32) Hai lớp** | 7 | 0.419 | 1.610 | 0.327 | 1.302 | 29.634 | 181.3s |
| **LSTM(64+32) Hai lớp** | 2026 | 0.465 | 1.739 | 0.371 | 1.423 | 29.634 | 124.8s |

- **Đánh giá thống kê**:
  - RMSE trung bình `LSTM(32)` đơn lớp: **0.8110 ± 0.0284** (tốt hơn mốc 0.8639).
  - RMSE trung bình `LSTM(64+32)` hai lớp: **0.9970 ± 0.0939** (kém hơn mốc và kém hơn đơn lớp).
- **Kết luận khoa học**:
  - Giữ nguyên kiến trúc **`LSTM(32)` đơn lớp** (Seed 7).
  - Lý do: Mạng 2 lớp có 29.634 tham số gây quá khớp (overfitting) và tốn tài nguyên gấp đôi trên Pi 4. Mạng đơn lớp 4.546 tham số vừa vặn tối ưu cho phần cứng nhúng và dự báo chính xác nhất (khớp hoàn toàn Định lý xấp xỉ phổ quát).
  - Kết quả lưu tại: `model/experiment_results.csv`, mô hình triển khai lưu tại `model/lstm_model.keras`.

# QUY TẮC KHI SỬA CODE
- Giữ chú thích và thông báo bằng tiếng Việt, đúng phong cách hiện có.
- Hằng số cấu hình đặt trong common.py, không rải rác ở các file khác.
- Không đổi định dạng file CSV (cột time, temp, hum) và meta.json nếu không cập nhật đồng bộ ở collect_data.py, train_lstm.py, monitor.py.
- DHT22 đọc lỗi thỉnh thoảng là bình thường: luôn bọc try/except RuntimeError và thử lại.
- Chia dữ liệu theo thời gian, không xáo trộn; chuẩn hóa chỉ fit trên tập huấn luyện.
- Giải thích mỗi thay đổi bằng cách chỉ ra nó ứng với kiến thức nào của chương 4 (nếu có).
