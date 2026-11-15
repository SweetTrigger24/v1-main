# Các gói hiện tại

- `Du_lieu_100_phan_tram_mo_phong_20261116_0500.zip`: dữ liệu hiện tại, nguồn khí tượng, CSV và hai SQLite. Các DB trong gói đã loại cấu hình/xác thực riêng tư trong `kv`, chỉ giữ định danh bộ dữ liệu. Giải nén thư mục `data/` vào thư mục dự án để dùng DB có sẵn.
- `Model_train_lai_100_phan_tram_mo_phong_20261115.zip`: cặp mô hình/metadata đã train và nhật ký, kết quả kiểm tra của lần train tương ứng. Cặp đang dùng cũng nằm trong `model/`.

Bộ dữ liệu là **100% mô phỏng**. Nhãn `label=sensor` trong 10 ngày cuối chỉ dùng chia kịch bản, không phải số đo DHT22 thật. Chi tiết tại `data/README_DATASET.md`.

SQLite dùng cấu trúc mới: `readings(time,temp,hum,state,reasons)` và metadata `reading_origins(time,origin)`. Dùng code cùng bản khi chuyển DB sang Pi. Pi chưa được cập nhật bởi lần đẩy GitHub này.

Báo cáo cuối kì hiện có tại `reports/Bao_cao_cuoi_ki_Nhom_11_LSTM.docx` (đường dẫn tính từ thư mục gốc repo).
