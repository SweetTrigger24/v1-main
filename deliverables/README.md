# Các gói hiện tại

- iot-pi4-2gb.zip: code và frontend đã build, không chứa .env, DB riêng hoặc mô hình.
- Du_lieu_hien_tai_20261116_0500.zip: CSV chung, metadata và SQLite đã bỏ cấu hình riêng.
- Model_LSTM_hien_tai.zip: cặp mô hình train lại ngày 16/11 trên đủ 87.841 dòng và log/kiểm tra tương ứng.

Nguồn vận hành của dữ liệu theo nhãn người dùng: 59.041 weather_sample và 28.800 sensor. Lần train mới dùng mốc cắt 05:00 ngày 16/11 cho train ngoại tuyến; giờ train thực tế ghi riêng. Đã cập nhật mô hình trên PC, chưa chuyển Pi. Chi tiết: ../reports/training_20261116_full_two_months.json.
