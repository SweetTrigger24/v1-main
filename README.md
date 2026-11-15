# IoT · Giám sát & dự báo nhiệt độ, độ ẩm 12 giờ

Raspberry Pi 4 + DHT22, dashboard **Reflex 0.9.13**, lịch sử SQLite và cảnh báo LED/còi/email. Một dịch vụ đọc cảm biến mỗi 60 giây; dashboard nhận cập nhật mỗi 5 giây; đường dự báo được tính lại mỗi 10 phút, gồm 72 mốc cách nhau 10 phút, tối đa 12 giờ. Thời gian hiển thị là giờ Việt Nam.

Xem sơ đồ chân, mapping từng tệp và quy trình cài/test mạch tại [Hướng dẫn triển khai](HUONG_DAN_TRIEN_KHAI.md).

## Cài một lần, những lần sau chỉ cấp nguồn Pi

Bản triển khai cho Pi 4 RAM 2 GB dùng **frontend build sẵn trên PC + Nginx + Reflex backend một worker**. Pi không chạy Node/Bun hoặc build giao diện khi khởi động.

Trên PC, dùng đúng môi trường Python của dự án để build và đóng gói:

```powershell
cd C:\Users\hoang\Downloads\iot\v1-main
..\.venv\Scripts\python.exe prepare_pi.py
```

Chương trình tạo **`iot-pi4-2gb.zip`** ở thư mục cha, gồm mã nguồn và `deploy/frontend` đã build. Gói đã chuẩn bị sẵn có thể chép trực tiếp lên Pi; không cần build lại nếu không sửa mã giao diện. Môi trường ảo Windows, `node_modules`, DB đang chạy, `.env` và mô hình riêng không được đưa vào gói.

Giải nén gói, chép hai tệp mô hình PC đã huấn luyện vào `model/forecast_12h.keras` và `model/forecast_12h.json` nếu muốn chạy LSTM. Trên **Raspberry Pi OS Lite 64-bit có systemd**, kết nối mạng rồi chạy bằng tài khoản thường:

```bash
bash install_pi.sh --with-ml
```

Script kiểm tra checksum bản build trước khi sửa hệ thống, cài `venv`, thư viện GPIO, Reflex và Nginx; nhập CSV lịch sử rồi tạo/bật ba dịch vụ với đúng tên người dùng/đường dẫn. Installer chờ `/ping` qua Nginx phản hồi trước khi báo thành công. Dùng `bash install_pi.sh --with-ml` để cài thêm TensorFlow khi cần suy luận LSTM. Không huấn luyện mô hình trên Pi.

Cặp mô hình hiện có trong thư mục `model/` đã được huấn luyện lại ngày 13/11/2026, ghi `use_lstm=true`: LSTM thắng baseline trên cả hai biến ở validation. Chuyển riêng cả `forecast_12h.keras` và `forecast_12h.json` sang Pi, cài với `--with-ml` và thu đủ khoảng 24 giờ đầu vào gần nhất để dùng LSTM. Bỏ `--with-ml` nếu chỉ dùng dự báo chu kỳ. Dữ liệu huấn luyện vẫn là mô phỏng khí tượng, sai số test còn cao. Xem kết quả và giới hạn kiểm chứng tại [Kiểm tra Pi 4](KIEM_TRA_PI4.md).

`deploy/frontend` chỉ gồm HTML/JS/CSS dùng được trên các kiến trúc; không cần công cụ frontend Windows trên Pi. Nginx phục vụ bản static từ một thư mục release riêng trong `/var/www/iot-dashboard/`; không phục vụ `.env`, SQLite hoặc mã nguồn Python. WebSocket/API được chuyển tới backend loopback.

Sau khi cài, mỗi lần Pi khởi động/có nguồn, ba dịch vụ tự chạy và tự khởi động lại nếu gặp lỗi:

| Dịch vụ | Công việc |
| --- | --- |
| `iot-monitor` | Đọc cảm biến, suy luận, LED/còi/email; giới hạn thread TensorFlow/BLAS |
| `iot-dashboard` | Reflex backend một worker tại `127.0.0.1:8000`, không build frontend |
| `iot-web` | Nginx một worker, giao diện đã build và proxy WebSocket tại cổng 3000 |

Truy cập **http://<IP-của-Pi>:3000** từ điện thoại/PC cùng Wi-Fi/LAN. Dùng `hostname -I` để xem IP. Pi cần được cấu hình mạng và đấu cảm biến; chép mã vào máy chưa thay thế bước cài dịch vụ.

```bash
sudo systemctl status iot-monitor iot-dashboard iot-web
journalctl -u iot-monitor -f
journalctl -u iot-dashboard -f
journalctl -u iot-web -f
sudo systemctl restart iot-monitor iot-dashboard iot-web
```

Lần cài đầu cần Internet để tải thư viện Python/Nginx. Sau đó khởi động không tải công cụ frontend hay kiểm tra phiên bản Reflex trên mạng; email vẫn cần kết nối SMTP.

Khi sửa mã Python liên quan giao diện/State, chạy lại `prepare_pi.py` trên PC và triển khai gói mới. `python prepare_pi.py --check` chỉ kiểm tra bản build bằng thư viện chuẩn, không gọi compiler. Cài đặt ngưỡng, email và mật khẩu trên dashboard không làm thay đổi mã nguồn nên không cần build lại.

Trên Pi sử dụng các dịch vụ trên; `python dashboard.py` vẫn là lối chạy thử trên PC và còn bước build. Không dùng lệnh này trong dịch vụ Pi.

### Theo dõi RAM trên Pi sau khi cài

```bash
free -h
sudo systemctl show iot-monitor iot-dashboard iot-web -p Id -p MemoryCurrent -p MemoryPeak
```

Kiểm tra sau khi khởi động và sau lần LSTM suy luận đầu tiên, rồi mở dashboard để thử các thao tác. Tối ưu này loại bước build ở thời điểm vận hành và giới hạn worker/thread, chưa phải kết quả đo RAM trên Pi thật. Gói TensorFlow ARM64 cần phù hợp với phiên bản Python trên Pi và mô hình từ PC.

## Đấu phần cứng (BCM)

| Thiết bị | GPIO | Chân vật lý |
| --- | --- | --- |
| DHT22 DATA | 4 | 7 |
| LED xanh | 17 | 11 |
| LED vàng | 27 | 13 |
| LED đỏ | 22 | 15 |
| Còi PWM | 18 | 12 |
| Nút tắt tiếng, nối về GND | 23 | 16 |

Dùng điện trở hạn dòng cho LED, điện trở kéo DATA của DHT22 lên 3.3V nếu cảm biến chưa có sẵn; nối chung GND. Còi chính dùng PWM 1000Hz/50%. Chạy `python check_system.py --hw-only` để kiểm tra phần cứng tương tác.

## Dashboard

- Trước khi vào dashboard, nhập mật khẩu. **Mặc định: `123456`**. Không cần tên đăng nhập.
- Đổi mật khẩu ở **Cài đặt → Đổi mật khẩu**: nhập mật khẩu hiện tại, mật khẩu mới và xác nhận, rồi bấm **Đổi mật khẩu**. Mật khẩu mới cần 6–128 ký tự và được giữ sau khi restart.
- Phiên đăng nhập có hiệu lực 12 giờ; có nút **Đăng xuất**. Đổi mật khẩu thu hồi các phiên cũ trong lần cập nhật tiếp theo; phiên vừa đổi tiếp tục dùng được. Monitor vẫn thu cảm biến và gửi cảnh báo khi bạn đăng xuất.
- Thẻ nhiệt độ/độ ẩm hiện tại, thời điểm cập nhật và trạng thái kết nối cảm biến.
- Hai thẻ dự báo thể hiện giá trị thấp nhất–cao nhất của đường dự báo trong số giờ đã chọn, có ghi rõ thời gian. Trục độ ẩm ở cả lịch sử và dự báo cố định 0–100%.
- Tab **Dự báo**: chọn từ 1 đến 12 giờ, hai đồ thị nhiệt độ và độ ẩm, dải sai số tham khảo, cảnh báo mốc đỏ đầu tiên trong toàn bộ 12 giờ.
- Tab **Lịch sử**: chọn thời điểm bắt đầu/kết thúc, gộp 5 phút/theo giờ/theo ngày. Mỗi mốc có trung bình, thấp nhất và cao nhất; tải CSV đúng khoảng đã chọn. Để trống thời điểm kết thúc để tự theo hiện tại.
- Tab **Cài đặt**: chỉnh 8 ngưỡng thấp/cao của nhiệt độ và độ ẩm, đổi email nhận và bật/tắt email, sau đó bấm **Lưu cài đặt**. Cấu hình lưu trong SQLite và monitor áp dụng từ lần đo tiếp theo, không cần restart. Các ngưỡng đỏ trên đồ thị cập nhật theo cấu hình đã lưu.
- Khoảng truy vấn dài tự gộp lớn hơn để giữ khoảng 1.500 điểm; vẫn tính min/max từ toàn bộ số đo, không bỏ qua các đỉnh/đáy.
- Mất cập nhật trên 3 phút, hoặc cảm biến báo lỗi: hiển thị lỗi, không coi dữ liệu cũ là số đo trực tiếp; ẩn dự báo đang mất dữ liệu đầu vào. Số đo cuối vẫn có thời gian đi kèm để tra cứu.
- Chỉ nhập CSV vào lịch sử, không hiển thị dữ liệu nhập là đang đo realtime. Bộ hiện có phủ **11/10–10/11/2026**, theo thời tiết Hà Đông.

## Mức vàng / đỏ mặc định

Các mức sau là **ngưỡng vận hành khởi đầu cho môi trường phòng thông thường**, không phải tiêu chuẩn chuyên ngành. Nếu đặt trong nhà kính, kho bảo quản hay chuồng nuôi, cần đổi theo mục đích thực tế.

| Đại lượng | Đỏ thấp | Vàng thấp | Vàng cao | Đỏ cao |
| --- | --- | --- | --- | --- |
| Nhiệt độ | ≤10°C | ≤15°C | ≥30°C | ≥35°C |
| Độ ẩm | ≤25% | ≤35% | ≥75% | ≥85% |

Đỏ được ưu tiên hơn vàng; chỉ cần **một** đại lượng vượt ngưỡng. Mức thấp và mức cao dùng cùng cách xác nhận:

- Chạm ngưỡng vàng: đèn vàng.
- Chạm ngưỡng đỏ: ngay **số đo hợp lệ đầu tiên**, đèn đỏ + còi và email đỏ hiện tại được xử lý. Email cần bật trong Cài đặt và tuân theo thời gian chống gửi lặp. Cảm biến được đọc mỗi 60 giây.
- Dự báo có mốc đỏ trong 12 giờ: sau 2 lần đọc liên tiếp thấy nguy cơ, đèn vàng. Email dự báo gửi ngay khi đường dự báo mới cho thấy mốc đỏ; không đợi hai lần đọc và không bật còi chỉ vì dự báo.
- Trở về bình thường: xóa cảnh báo hiện tại. Nút bấm tắt tiếng 5 phút, đèn đỏ vẫn phản ánh trạng thái; còi tự bật lại nếu còn đỏ sau 5 phút.
- Lỗi cảm biến: đèn vàng, tắt còi; dashboard ghi rõ lỗi thay vì coi là bình thường.

Sửa ngưỡng trong tab **Cài đặt**. Thứ tự hợp lệ: đỏ thấp < vàng thấp < vàng cao < đỏ cao; nhiệt độ trong -40–80°C, độ ẩm trong 0–100%. Không ghi đè ô đang nhập khi dashboard tự làm mới. Nếu hai trình duyệt cùng sửa, bản lưu sau từ cấu hình cũ sẽ báo xung đột và yêu cầu **Tải lại**.

Các giá trị `.env` là mặc định ban đầu; sau khi lưu trên giao diện, cấu hình SQLite được ưu tiên và giữ qua lần khởi động lại. Đổi ngưỡng sẽ bắt đầu lại chuỗi xác nhận cảnh báo để không cộng dồn số đo theo hai bộ ngưỡng khác nhau.

Mật khẩu đăng nhập được lưu riêng trong SQLite dưới dạng PBKDF2-SHA256 có salt ngẫu nhiên; không trả bản băm hay dữ liệu phiên xác thực về trình duyệt. Các thao tác đọc dữ liệu, tải CSV, lưu cài đặt và đổi mật khẩu đều kiểm tra phiên ở phía server. Nhập sai liên tiếp 5 lần sẽ tạm chờ 30 giây. Mật khẩu gắn với DB mà dashboard đang dùng: DB demo và DB vận hành có mật khẩu riêng.

## Bật email

Installer tự tạo `.env` với các giá trị mặc định nếu tệp chưa có. Mặc định email và MQTT đều tắt, các ngưỡng đã có giá trị khởi đầu. Kiểm tra cấu hình hiệu lực bằng lệnh sau trong thư mục dự án:

```bash
venv/bin/python check_system.py --config-only
```

Lệnh kiểm tra các giá trị `.env`, ngưỡng/email đã lưu trong SQLite và sự hiện diện của cặp mô hình 12 giờ, không in mật khẩu. Installer chạy bước này trước khi kích hoạt dịch vụ. Sửa `.env` cần restart `iot-monitor` và `iot-dashboard`; các thay đổi lưu trên dashboard được áp dụng tự động. `IOT_DB_FILE` để trống dùng DB mặc định; đường dẫn tương đối được tính từ thư mục dự án.

Trong tab **Cài đặt**, nhập **Email nhận cảnh báo**, bật công tắc email rồi bấm **Lưu cài đặt**. Địa chỉ mới và công tắc được monitor tự đọc; không cần sửa `.env` mỗi lần đổi người nhận.

Tài khoản gửi SMTP cần thiết lập **một lần** trong `.env` trên máy chạy monitor. Điền:

```dotenv
EMAIL_FROM=dia_chi_gui@gmail.com
EMAIL_PASS=app_password
EMAIL_COOLDOWN=1800
```

Dùng Gmail App Password, không dùng mật khẩu đăng nhập. Sau đó:

```bash
sudo systemctl restart iot-monitor iot-dashboard
```

Email gửi khi **đỏ hiện tại đã xác nhận** hoặc khi **dự báo trung tâm có mốc chạm đỏ trong 12 giờ tiếp theo**, kể cả nhiệt độ thấp/độ ẩm thấp. Email dự báo có thời điểm, giá trị và lý do cảnh báo. Nếu đang đỏ hiện tại thì ưu tiên email hiện tại, tránh gửi hai email cùng lúc. Dải sai số chỉ để tham khảo, chưa dùng làm điều kiện gửi email.

Cùng loại cảnh báo lặp tối đa một lần mỗi 30 phút; đợt mới sau khi đã hết cảnh báo có thể gửi ngay. Trạng thái chống lặp lưu trong SQLite nên restart không gửi lại vô cớ. Đổi người nhận hoặc bật/tắt email sẽ xóa trạng thái gửi cũ để đánh giá lại ở lần đọc tiếp theo. SMTP lỗi không đánh dấu gửi thành công và tự thử lại ở lần đọc tiếp theo. Tab Cài đặt hiển thị trạng thái gửi; không cần mở web để email hoạt động. Mật khẩu SMTP không được lưu trong cài đặt dashboard hay trả về trình duyệt. Chế độ demo luôn không gửi email thật.

## Dự báo 12 giờ và dữ liệu hiện có

CSV hiện có **87.841 mẫu**, từ **16/09/2026 05:00 đến 16/11/2026 05:00**, theo giờ Việt Nam, cách nhau một phút. Bộ train có **86.041 mẫu**, đã loại 1.800 mẫu dùng dự báo hoặc chưa xảy ra tại thời điểm lấy nguồn. Nguồn gồm mô hình khí tượng tại Hà Đông và 314 bản tin trạm WMO 48825 để hiệu chỉnh khi đủ dữ liệu; URL, thời điểm tải và hash được lưu cùng bộ dữ liệu.

Đây là **dữ liệu 100% mô phỏng dựa trên khí tượng ngoài trời**. Nhãn `label=sensor` trong 10 ngày cuối là nhãn kịch bản, không phải số đo DHT22 thật. Xem [mô tả bộ dữ liệu và nguồn](data/README_DATASET.md). CSV có `time,temp,hum,label`; SQLite có `readings(time,temp,hum,state,reasons)` và bảng nguồn riêng `reading_origins(time,origin)`.

Hai DB SQLite đã loại cấu hình/xác thực riêng tư được đóng gói trong [gói dữ liệu hiện tại](deliverables/Du_lieu_100_phan_tram_mo_phong_20261116_0500.zip). Giải nén thư mục `data/` vào repo để dùng DB có sẵn. Mô hình nằm trong `model/`; [báo cáo cuối kì](reports/Bao_cao_cuoi_ki_Nhom_11_LSTM.docx) và [các gói bàn giao](deliverables/README.md) cũng được lưu trong repo. Tệp `.env`, DB vận hành chứa cấu hình cá nhân và lịch sử bảo trì chỉ giữ trên máy.

```bash
# Ví dụ tạo một bộ 30 ngày khác; lệnh này sẽ thay CSV hiện tại:
python generate_weather_data.py --days 30
sudo systemctl restart iot-monitor
```

Hệ thống đọc manifest, gắn `source=weather_sample`, cập nhật lịch sử khi bộ CSV đổi và giữ các số đo `source=sensor` đang có. Không hiển thị mẫu nhập thành số đo realtime. Dự báo ban đầu dùng **trung vị chu kỳ ngày có hiệu chỉnh theo số đo mới**; dashboard/email ghi rõ giới hạn của dữ liệu mô phỏng.

Đường LSTM mới là **dự báo trực tiếp 72 mốc** từ 24 giờ đầu vào, lấy mẫu gộp 10 phút; đầu vào gồm nhiệt độ, độ ẩm, sin/cos giờ trong ngày. Đây là ứng dụng cửa sổ chuỗi thời gian, LSTM, hồi quy nhiều đầu ra, chuẩn hóa, dropout và early stopping trong kiến thức học sâu cho IoT. Không dùng các mô hình `.keras`/`.tflite` 10 phút cũ.

Huấn luyện chính thức yêu cầu tối thiểu **7 ngày số đo hợp lệ không bị gắn nhãn mô phỏng**, khuyến nghị **14–30 ngày dữ liệu thực**. Mặc định loại `source=demo/sample/weather_sample`; các CSV khác vẫn cần xác minh nguồn gốc. Có thể thử nghiệm với bộ khí tượng bằng cờ `--include-weather-samples`; metadata mô hình và dashboard sẽ ghi rõ đã dùng dữ liệu mô phỏng. Sai số trên bộ này không chứng minh độ chính xác của cảm biến thật.

```bash
# Cài phần học sâu tùy chọn ngay từ đầu:
bash install_pi.sh --with-ml

# Hoặc cài bổ sung sau:
source venv/bin/activate
pip install -r requirements-ml.txt
python train_forecast.py --epochs 60
# Thử nghiệm trên DB mô phỏng hiện tại đã loại forecast:
python train_forecast.py --epochs 60 --db data/training_data.sqlite3 --skip-csv-import --include-weather-samples
```

Có thể huấn luyện trên PC với bản sao dữ liệu SQLite rồi chép cả `model/forecast_12h.keras` và `model/forecast_12h.json` về Pi (Pi cần TensorFlow để suy luận LSTM). Chia train/validation/test theo thời gian 70/15/15, fit MinMax trên train, không cho nhãn 12 giờ vượt ranh giới tập. Chỉ điền tiến tối đa 20 phút khi thiếu dữ liệu; khoảng trống dài giữ nguyên và cửa sổ lỗi bị loại.

Chọn LSTM bằng **validation**, chỉ dùng khi RMSE tốt hơn dự báo chu kỳ trên **cả nhiệt độ và độ ẩm**; test chỉ báo cáo, có RMSE riêng ở mốc 12 giờ. Nếu không thắng baseline hoặc chưa có 24 giờ số đo gần đây, monitor dùng dự báo chu kỳ. Kết quả/metadata nằm trong `model/forecast_12h.json`. Mô hình được kiểm tra cập nhật ở lần tính dự báo tiếp theo; restart monitor cũng nạp lại. Không tự huấn luyện nặng lúc Pi vừa cấp nguồn.

## Phát triển và kiểm thử trên PC

```bash
python -m venv venv
# Linux: source venv/bin/activate
# Windows PowerShell: .\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q
```

Chạy mô phỏng riêng, không thao tác GPIO, không ghi vào CSV thật và không gửi email:

```bash
python monitor.py --demo --db data/demo.sqlite3 --interval 5
```

Trong terminal khác, chọn cùng DB rồi mở dashboard:

```bash
# Linux:
IOT_DB_FILE="$PWD/data/demo.sqlite3" python dashboard.py
# PowerShell:
# $env:IOT_DB_FILE="$PWD/data/demo.sqlite3"; python dashboard.py
```

Dashboard sẽ ghi rõ **MÔ PHỎNG**. Không trộn demo với DB vận hành. `pytest.ini` chỉ thu bộ test tự động; kiểm tra phần cứng tương tác bằng `python check_system.py --hw-only`.

Bạn có thể đặt `IOT_DB_FILE` trỏ tới `data/demo.sqlite3` trong cả hai terminal. Monitor nhận diện DB vận hành bằng đường dẫn mặc định, dấu vai trò và số đo thật đã lưu; không từ chối nhầm chỉ vì dashboard đang dùng cùng DB demo.

## Thành phần chính

| Tệp | Vai trò |
| --- | --- |
| `monitor.py`, `hardware.py` | Chủ duy nhất của GPIO; đọc, xác nhận cảnh báo, LED/còi và nút mute |
| `storage.py` | SQLite WAL, nhập CSV một lần, truy vấn lịch sử |
| `forecasting.py`, `train_forecast.py` | Dự báo chu kỳ và LSTM nhiều bước, kiểm định mô hình |
| `alerts.py`, `notifications.py` | Ngưỡng thấp/cao, cảnh báo hiện tại/tương lai, email chống lặp |
| `iot_dashboard/`, `rxconfig.py` | Dashboard Reflex/Recharts |
| `common.py`, `.env` | Chân GPIO, cấu hình, thời gian, ngưỡng và thông tin SMTP |
| `install_pi.sh`, `start_dashboard.sh` | Cài một lần, sinh dịch vụ tự chạy |

SQLite là nguồn dùng cho dashboard. Bộ dữ liệu hiện tại trên PC là bộ hai tháng **100% mô phỏng** trong `data/sensor_data.csv` và `data/iot.sqlite3`; xem `data/README_DATASET.md`. Các nhãn nhóm `label=sensor` trong 10 ngày cuối là nhãn kiểm thử; nguồn `weather_sample` và trạng thái 100% mô phỏng được ghi trong metadata và SQLite. CSV chỉ còn `time,temp,hum,label`. Để train bộ đã loại forecast, dùng `--db data/training_data.sqlite3 --skip-csv-import --include-weather-samples`. Khi chạy phần cứng thật, monitor tạo `data/live_sensor_data.csv` và `data/monitor_log.csv`; trong log, `pred_temp/pred_hum` là **mốc dự báo kế tiếp** (tối đa 10 phút), không phải giá trị ở 12 giờ. Toàn bộ 72 mốc nằm trong snapshot SQLite. Sao lưu DB bằng SQLite backup API hoặc dừng dịch vụ trước khi sao chép; không chỉ copy tệp DB đang mở mà bỏ qua WAL.

`monitor.py` quản lý việc thu dữ liệu và giám sát chung. `markdown.md` và `NHUNG_GI_DA_LAM.md` lưu bối cảnh học thuật/thực nghiệm của bản 10 phút; README này mô tả phiên bản hiện hành.

Tài liệu framework: [Reflex Background Events](https://reflex.dev/docs/events/background-events/), [Recharts Line Chart](https://reflex.dev/docs/library/graphing/charts/linechart/), [Self Hosting](https://reflex.dev/docs/hosting/self-hosting/). Dự án ghim Reflex 0.9.13; script dùng cờ `--single-port` có trong phiên bản này.
