# Bộ dữ liệu thời tiết tham khảo · Hà Đông, Hà Nội

- Khoảng thời gian: **2026-10-11T22:21:00 → 2026-11-10T22:20:00**, giờ Việt Nam.
- **43,200 mẫu**, chu kỳ 60 giây; đây là dữ liệu phút **nội suy/mô phỏng theo khí tượng**, không phải số đo DHT22 thực.
- Điểm yêu cầu: 20.9714°B, 105.7788°Đ; ô lưới trả về: 20.984182°B, 105.79108°Đ, cao độ 13.0 m.
- Nguồn: [Open-Meteo Historical Weather / ECMWF IFS](https://open-meteo.com/en/docs/historical-weather-api), dữ liệu theo giờ, lưới khoảng 9 km. [URL yêu cầu cụ thể](https://archive-api.open-meteo.com/v1/archive?latitude=20.9714&longitude=105.7788&start_date=2026-10-11&end_date=2026-11-10&hourly=temperature_2m%2Crelative_humidity_2m%2Cprecipitation&models=ecmwf_ifs&timezone=Asia%2FHo_Chi_Minh).
- Nhiệt độ: **15.3–33.6°C**, trung bình 26.86°C.
- Độ ẩm: **42.4–100.0%**, trung bình 77.96%.

Giữ diễn biến nguồn từng giờ theo từng ngày, không lặp một đường sin ngày/đêm. Nội suy tuyến tính về từng phút; thêm nhiễu AR(1) nhỏ có tương quan nhiệt/ẩm, bằng 0 tại đúng mốc giờ nguồn; làm tròn 0,1 như giao diện DHT22. Phần cuối sau mốc nguồn 2026-11-10T22:00:00 giữ nền giờ cuối thêm 20 phút, có nhiễu nhỏ. Không lấy mốc tương lai để hoàn tất phần đuôi.

Các mẫu phút không tạo thêm thông tin quan trắc độc lập so với nguồn giờ. Lưới khí tượng không tái hiện chính xác ban công, phòng kín hoặc điều hòa. Không dùng sai số kiểm thử trên bộ này để công bố độ chính xác dự báo cảm biến thật.

## Các tệp

- `sensor_data.csv`: định dạng tương thích `time,temp,hum`.
- `sensor_data.provenance.json`: tọa độ, URL, phương pháp, seed, SHA-256 và phạm vi mẫu.
- `weather_hourly_reference.csv`: số liệu giờ gốc, trước khi thêm nhiễu.
- `weather_daily_summary.csv`: thống kê từng ngày (ngày đầu/cuối là ngày không đủ 24 giờ).
- `weather_sources/`: JSON API gốc và bản tin METAR Nội Bài để đối chiếu vùng.
- Khi sinh bộ dữ liệu mới, chương trình tự tạo bản sao CSV cũ trong `backups/`.

Nguồn ECMWF IFS là dữ liệu phân tích/mô hình khí tượng, không được gắn nhãn quan trắc trạm Hà Đông. Nguồn quan trắc Nội Bài qua [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/request/download.phtml?network=VN__ASOS) là đối chiếu độc lập ở nơi khác, không được nhập thành số đo Hà Đông. Kết quả đối chiếu lưu trong `weather_sources/regional_comparison.json` nếu có; không dùng để khẳng định sai số tại điểm đặt cảm biến.

Tải lại 30 ngày mới: `python generate_weather_data.py --days 30`. Tái lập bộ đã giao: `python generate_weather_data.py --days 30 --end 2026-11-10T22:20:00 --seed 20261110 --source-file "data/weather_sources/ha_dong_ecmwf_ifs_raw.json"`. Chương trình tự sao lưu CSV cũ. Sau khi đổi dữ liệu trên Pi, restart `iot-monitor` để nhập bộ mới.

Muốn thử huấn luyện trên dữ liệu này: `python train_forecast.py --include-weather-samples`. Metadata mô hình sẽ ghi rõ nguồn mô phỏng; mặc định huấn luyện vẫn loại dữ liệu mô phỏng.

Dữ liệu Open-Meteo cần ghi nguồn theo [CC BY 4.0](https://open-meteo.com/en/licence). Không thay nhãn nguồn khi xuất báo cáo.
