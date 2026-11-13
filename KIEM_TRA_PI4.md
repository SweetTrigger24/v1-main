# Kết quả rà soát để triển khai Raspberry Pi 4

Ngày kiểm tra: **13/11/2026**, giờ Việt Nam.

Mã nguồn và bản frontend đã qua kiểm tra trên PC Windows. Đã tạo lại `../iot-pi4-2gb.zip` từ mã sau sửa. Chưa chạy trực tiếp trên CPU ARM hoặc mạch Raspberry Pi; kết quả này không thay thế bài kiểm tra thiết bị bên dưới.

## Phạm vi đã đọc

- Thu cảm biến, LED, còi, mute, khóa GPIO và vòng monitor.
- Cảnh báo thấp/cao, dự báo chu kỳ/LSTM, huấn luyện và metadata mô hình.
- SQLite, nhập CSV, nguồn dữ liệu mô phỏng, lịch sử và xuất CSV.
- SMTP, MQTT, cấu hình `.env`, cài đặt và xác thực dashboard.
- Toàn bộ package `iot_dashboard`, cấu hình Reflex, các script QA và bộ kiểm thử.
- Dependency, Bash/systemd, Nginx, kiểm tra frontend và đóng gói triển khai.

## Các lỗi đã sửa

| Tệp | Tình huống lỗi | Kết quả sau sửa |
| --- | --- | --- |
| `notifications.py` | Một đợt đỏ đã hết rồi xuất hiện đợt mới trong 30 phút; SMTP lỗi ở lần gửi đầu của đợt mới khiến cooldown cũ chặn thử lại | Chỉ đánh dấu đợt mới đã gửi khi SMTP thành công; lần đo tiếp theo được thử lại, kể cả sau restart |
| `forecasting.py` | Metadata thiếu/sai `validation_rmse` có thể làm hỏng cả kết quả dự phòng hoặc tạo dải sai số NaN | Kiểm tra đủ hai số hữu hạn không âm trước khi nhận kết quả LSTM; giữ nguyên cả đường dự báo dự phòng nếu lỗi |
| `monitor.py` | GPIO đã khởi tạo nhưng constructor của monitor lỗi trước khi vào khối dọn dẹp | Giải phóng phần cứng và socket khóa cả khi constructor lỗi; lỗi dọn phần cứng cũng không bỏ qua việc đóng socket |
| `pi_deploy.py`, `install_pi.sh` | systemd báo active trong lúc app còn khởi tạo nên installer có thể báo thành công trước khi backend dùng được | Đợi HTTP `/ping` qua Nginx, tối đa 90 giây, rồi kiểm tra cả ba dịch vụ; lỗi sẽ trả mã thoát khác 0 |
| `install_pi.sh` | Log cài trên Pi báo `bad unit file setting` khi hai dịch vụ được sinh với dấu nháy bao quanh giá trị `WorkingDirectory` | Sinh đường dẫn tuyệt đối trực tiếp, thêm kiểm tra unit bằng `systemd-analyze verify` trước khi bật dịch vụ |

Đã thêm 12 ca kiểm thử để tái hiện và ngăn các lỗi trên quay lại, gồm kiểm tra unit do Bash sinh ra với đường dẫn thông thường và đường dẫn có khoảng trắng. README và hướng dẫn triển khai đã đổi lệnh cài mặc định cho phù hợp mô hình hiện có.

## Kết quả kiểm tra

| Kiểm tra | Kết quả |
| --- | --- |
| `python -m pytest -q` | **105 passed**, gồm kiểm tra Keras huấn luyện/lưu/nạp/suy luận khi có TensorFlow |
| `python -m pip check` | Không có dependency bị thiếu hoặc xung đột trong venv PC |
| `python -m compileall` | Mã Python biên dịch được |
| `bash -n` cho ba script `.sh` | Đạt; các script dùng xuống dòng LF |
| `python prepare_pi.py --check` | Checksum frontend và mã liên quan hợp lệ |
| `python monitor.py --demo --db artifacts/review-demo.sqlite3 --once` | Nhập 43.200 mẫu; tạo số đo demo và dự báo; DB riêng, không ghi CSV vận hành hoặc gửi email |
| `python check_system.py --config-only` | Cấu hình hợp lệ; email và MQTT đang tắt |
| Nginx thật + Reflex backend-only | Đạt; không tạo frontend build/node_modules hoặc chạy Node/Bun khi vận hành |
| Giao diện desktop/mobile | Đạt: biểu đồ, dự báo 1 giờ, lọc lịch sử, tải CSV, không lỗi JavaScript |
| Cài đặt | Đạt: lưu ngưỡng/email, giữ bản nháp, validation và phát hiện hai phiên ghi đồng thời |
| Xác thực | Đạt: đăng nhập, đổi mật khẩu, thu hồi phiên cũ, đăng xuất, reload; dữ liệu riêng không gửi trước đăng nhập |
| Truy cập qua tên máy LAN mô phỏng | WebSocket và đăng nhập dùng đúng hostname truy cập |

Có 5 cảnh báo deprecation từ Keras/NumPy trong bài test mô hình; không làm bài test thất bại. Môi trường PC: Python 3.11.0, Reflex 0.9.13, TensorFlow 2.21.0, NumPy 2.4.6, pandas 2.3.3; Nginx QA 1.30.6.

Ảnh và báo cáo QA nằm ở `artifacts/`; thư mục này không được đóng gói lên Pi. Số RAM trong `artifacts/pi-deployment-validation.json` là đo trên Windows, không phải số đo RAM của Pi.

## Lựa chọn dự báo của bản hiện có

Đã huấn luyện lại trên PC ngày **13/11/2026**, với giới hạn 120 epoch. Early stopping dừng ở epoch 111 và khôi phục trọng số epoch 103. `model/forecast_12h.json` hiện ghi **`use_lstm=true`**, được chương trình tự quyết định từ validation.

| RMSE validation | LSTM mới | Baseline |
| --- | --- | --- |
| Nhiệt độ | 1,110°C | 1,327°C |
| Độ ẩm | 7,201 điểm phần trăm | 7,878 điểm phần trăm |

RMSE test của LSTM là **4,609°C / 13,187 điểm phần trăm độ ẩm**; baseline là **6,031°C / 12,582 điểm phần trăm**. Tại riêng mốc 12 giờ, RMSE LSTM là **6,181°C / 15,144 điểm phần trăm**. LSTM được chọn theo validation; độ ẩm trên test vẫn kém baseline và các sai số này chưa đủ để khẳng định dự báo thực tế chính xác. Tập test có nhiệt độ thấp nhất khoảng 15,35°C, trong khi train thấp nhất khoảng 23,01°C.

Đã xác nhận checksum cặp tệp, nạp lại mô hình Keras và chạy qua `Forecaster` tạo đủ 72 mốc hữu hạn bằng dữ liệu khí tượng lịch sử. Mô hình có 17.232 tham số, đầu vào `(144, 4)`, đầu ra `(72, 2)`. Log, kết quả xác minh và bản mô hình cũ được giữ trong `artifacts/training/20261113_145248/`, không đi vào ZIP triển khai. Đây là lần huấn luyện lại từ đầu với giới hạn epoch cao hơn, không tiếp tục cập nhật trọng số bản cũ.

Bộ 43.200 mẫu đi kèm là dữ liệu mô phỏng theo khí tượng, không phải DHT22 đo thật. Lần đo demo cũng không chứng minh độ chính xác dự báo thực tế. ZIP không chứa `.env`, DB vận hành, venv Windows hay các tệp mô hình; installer tạo cấu hình và DB trên Pi. Chuyển riêng cả `model/forecast_12h.keras` và `model/forecast_12h.json` để triển khai LSTM; chương trình cần đủ khoảng 24 giờ đầu vào gần nhất trước khi sử dụng mô hình.

## Đưa lên Pi

Chuyển `iot-pi4-2gb.zip` vào home của tài khoản Pi. Trên **Raspberry Pi OS Lite 64-bit** có systemd, bằng tài khoản thường có quyền sudo:

```bash
sudo apt update
sudo apt install -y unzip
unzip ~/iot-pi4-2gb.zip -d ~
cd ~/v1-main
bash install_pi.sh --with-ml
```

Installer cần Internet. Mở `http://<IP-Pi>:3000` từ cùng mạng LAN; mật khẩu mặc định của DB mới là `123456`, đổi trong Cài đặt. Nếu cập nhật Pi đang sử dụng, sao lưu DB/cấu hình và dừng ba dịch vụ trước khi giải nén đè mã.

Lệnh `bash install_pi.sh --with-ml` cài bộ suy luận LSTM. Chép đủ cặp `.keras`/`.json` cùng lần huấn luyện, theo `HUONG_DAN_TRIEN_KHAI.md`; dùng `bash install_pi.sh` nếu chỉ cần dự báo chu kỳ. Đã đối chiếu [wheel TensorFlow ARM64](https://pypi.org/project/tensorflow/2.21.0/) và [hướng dẫn venv của Raspberry Pi OS](https://www.raspberrypi.com/documentation/computers/os.html); cài đặt dependency trên chính Pi vẫn là bước cần xác nhận.

## Cần kiểm tra tại Pi thật

```bash
cd ~/v1-main
venv/bin/python check_system.py --env-only
sudo systemctl stop iot-monitor
venv/bin/python check_system.py --hw-only
sudo systemctl start iot-monitor
sudo systemctl status iot-monitor iot-dashboard iot-web
journalctl -u iot-monitor -n 50 --no-pager
free -h
```

Đối chiếu sơ đồ GPIO trong hướng dẫn; xác nhận số đo thật, LED vàng/đỏ, còi và mute. Sau đó reboot và xác nhận ba dịch vụ tự chạy, số đo tiếp tục cập nhật. Thử SMTP/MQTT nếu sử dụng, và đo RAM sau lần suy luận LSTM đầu tiên nếu bật mô hình. Chưa có kết quả phần cứng, SMTP/broker thật, mức RAM Pi hoặc reboot trong lần rà soát này.
