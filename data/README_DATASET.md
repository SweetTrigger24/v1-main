# Bộ dữ liệu hiện tại trên PC

Bộ hai tháng 100% mô phỏng, từ 05:00 ngày 16/09 đến 05:00 ngày 16/11/2026, giờ Việt Nam.

## CSV đã rút gọn

Cả sensor_data.csv và training_data.csv chỉ có 4 cột:

| Cột | Ý nghĩa |
|---|---|
| time | Thời gian Việt Nam, dạng YYYY-MM-DDTHH:MM:SS |
| temp | Nhiệt độ, °C |
| hum | Độ ẩm tương đối, %RH |
| label | Nhóm kịch bản kiểm thử |

sensor_data.csv có 87.841 mẫu. training_data.csv có 86.041 mẫu, đã loại 1.800 mẫu dùng dự báo hoặc chưa xảy ra tại thời điểm lấy nguồn. Hai cột temp/hum và thời gian không đổi so với bộ đã train.

Tất cả dòng vẫn là mô phỏng. Nguồn weather_sample được ghi trong metadata và SQLite, không lặp lại thành cột trong CSV. Đúng 14.400 dòng trong (05:00 ngày 06/11, 05:00 ngày 16/11/2026] có label=sensor; nhãn này không có nghĩa số đo DHT22 thực. Các dòng còn lại có label=weather_simulated.

## Metadata và SQLite

- dataset.provenance.json ghi all_rows_are_simulated=true và source_counts thay cho các cột mô tả lặp lại; training_time_ranges ghi khoảng đủ điều kiện train; row_metadata_segments giữ chi tiết nguồn khí tượng và cờ dự báo theo các đoạn thời gian liên tiếp.
- sensor_data.provenance.json giữ nguồn, cách tạo và hash CSV, tương thích với importer dự án.
- iot.sqlite3 và training_data.sqlite3 có bảng readings với 5 cột: time, temp, hum, state, reasons; đã bỏ cột source. Nguồn được lưu riêng trong reading_origins(time, origin), liên kết theo time và tự xóa khi xóa số đo. Code Store.frame() ghép metadata thành trường source khi cần lọc dữ liệu; nhãn CSV không được dùng làm bằng chứng cảm biến thật.
- weather_sources/, các tệp tham chiếu theo giờ, thống kê ngày và bản tin trạm thuộc cùng bộ hiện tại.

Không cần train lại vì chỉ rút gọn cột mô tả. Mô hình PC vẫn là bản đã train tại artifacts/training/20261115_current_simulation; data_format_change.json trong thư mục đó đối chiếu hash cũ/mới và xác nhận giá trị dùng để train không đổi. Pi không bị thay đổi.

## Lệnh train khi cần

```powershell
..\.venv\Scripts\python.exe -X utf8 train_forecast.py --epochs 120 --db data/training_data.sqlite3 --skip-csv-import --include-weather-samples --output-dir artifacts/training/current_simulation
```
