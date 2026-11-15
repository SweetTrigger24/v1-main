# Bộ dữ liệu đang dùng

Chỉ dùng sensor_data.csv cho thu thập và train, gồm time,temp,hum,label. SQLite vận hành là iot.sqlite3; readings giữ time,temp,hum,state,reasons, nguồn phân loại nằm trong reading_origins.origin.

## Phân loại theo nhãn CSV

Nguồn vận hành được cấu hình theo nhãn do người dùng chọn:

| Phần dữ liệu | Nhãn CSV | Nguồn trong SQLite/code | Số dòng |
|---|---|---|---|
| 16/09/2026 05:00 đến 06/11/2026 05:00 | weather_simulated | weather_sample | 73.441 |
| Sau 06/11/2026 05:00 đến hết 16/11/2026 05:00 | sensor | sensor | 14.400 |

Đây là cấu hình gán nhãn vận hành, không phải chứng từ thu thập phần cứng. Các mô tả nguồn mô phỏng theo dòng chỉ giữ cho phần weather_simulated. Thời gian, nhiệt độ, độ ẩm và nội dung CSV giữ nguyên.

sensor_data.provenance.json kiểm tra hash CSV và lưu sensor_label_assignment. dataset.provenance.json giữ thông tin nguồn của phần weather_simulated và quy tắc train. Khi chuyển CSV cần chuyển cả metadata để giữ cách phân loại. Monitor vẫn nối số đo mới vào cùng CSV với label=sensor.

## Train

Không có --include-weather-samples: dùng sensor/import đến thời điểm train; sau 05:00 ngày 16/11 có đủ 14.400 dòng sensor của bộ hiện tại. Trước giờ đó, dòng sensor có mốc chưa đến vẫn bị loại.

Có --include-weather-samples: lấy thêm 73.441 dòng weather_sample. Sau mốc kết thúc có đủ 87.841 dòng; vẫn không dùng sensor/import có thời gian tương lai.

```powershell
..\.venv\Scripts\python.exe train_forecast.py --epochs 60
..\.venv\Scripts\python.exe train_forecast.py --epochs 60 --include-weather-samples
```

Mô hình chính đã train lại ngày 16/11/2026 trên đủ 87.841 dòng, với --include-weather-samples và --data-cutoff 2026-11-16T05:00:00 cho lần train ngoại tuyến trước giờ kết thúc bộ dữ liệu. Giờ train thực tế vẫn ghi riêng trong metadata. Xem reports/training_20261116_full_two_months.json. reports/source_label_assignment.json ghi đối chiếu CSV, SQLite, bộ lọc train và số dòng. weather_sources/ giữ tài liệu khí tượng tham chiếu. Các báo cáo single_dataset_audit.json và training_policy_audit.json ghi các lần thay đổi trước đó.

Các thay đổi này ở PC; chưa cập nhật Pi.

--data-cutoff là tùy chọn cho train ngoại tuyến; không dùng tùy chọn này thì bộ lọc thời gian vẫn lấy giờ hiện tại.
