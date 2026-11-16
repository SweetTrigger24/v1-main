# Bố cục dự án hiện tại

## Dữ liệu dùng chung

Chỉ dùng `data/sensor_data.csv` làm đầu vào thu thập và train. Mặc định train đọc CSV trực tiếp, không tạo một bản CSV hoặc SQLite riêng. `data/iot.sqlite3` vẫn cần cho dashboard, lịch sử, cấu hình và trạng thái vận hành.

Manifest CSV kiểm tra hash và cấu hình phân loại theo nhãn người dùng: 59.041 dòng weather_simulated có nguồn weather_sample, 28.800 dòng cuối có nguồn sensor. Metadata mô phỏng theo dòng chỉ giữ cho weather_simulated. Nhãn vận hành không phải chứng từ thu thập phần cứng. Monitor vẫn nối số đo mới vào cùng CSV.

Đã đối chiếu chính xác dữ liệu sau lọc cũ và sau gộp 10 phút với bản train cũ trước khi xóa. Xem single_dataset_audit.json và training_policy_audit.json cho các lần thay đổi trước; source_label_assignment.json ghi cấu hình nhãn hiện tại và cách chọn dữ liệu khi train. Mô hình 24 giờ, một lớp đã train lại trên đủ 87.841 dòng ngày 16/11/2026. Xem training_20261116_full_two_months.json.

## Các phần được giữ

| Thành phần | Vai trò |
|---|---|
| common, hardware, monitor, alerts, notifications | Cấu hình, thu DHT22, LED/còi/mute và thông báo |
| storage, provenance, runtime_settings | SQLite, nguồn dữ liệu và cấu hình dashboard |
| forecasting, train_forecast | Dự báo 12 giờ và huấn luyện từ CSV chung |
| dashboard, dashboard_data, dashboard_auth, rxconfig, iot_dashboard/ | Backend/giao diện và đăng nhập |
| prepare_pi, pi_deploy, install_pi.sh, start_*.sh | Build trên PC và triển khai systemd/Nginx trên Pi |
| check_system, test_*.py, scripts/ | Kiểm tra cấu hình, phần cứng và QA giao diện/triển khai |
| generate_weather_data, generate_station_dataset | Tái lập mô phỏng từ nguồn khí tượng, xuất CSV bốn cột |
| data/weather_sources/ | Nguồn gốc khí tượng; reference/ chứa bản tin và thống kê tham chiếu |
| model/ | Cặp mô hình hiện tại |
| artifacts/training/20261116_full_two_months/ | Log, mô hình và kiểm tra của lần train hiện tại |
| artifacts/training/20261115_current_simulation/ | Mô hình trước lần train mới, dùng để đối chiếu và khôi phục |
| artifacts/training/20261113_145248/ | Kết quả lịch sử được báo cáo cuối kì trích dẫn |
| artifacts/dashboard-*.png, scripts/ | Ảnh và công cụ QA được nguồn tạo báo cáo sử dụng |
| reports/ và thư mục bao_cao_cuoi_ki ở workspace | Bản báo cáo bàn giao cùng nguồn, hình và dữ liệu để tái tạo báo cáo |
| .venv, .reflex, .web, reflex.lock, .qa-tools ở workspace | Môi trường/công cụ/lockfile dùng để chạy và build, không phải bản dữ liệu thừa |
| .env, .states, .vscode, .weather-work/known_hosts | Cấu hình riêng, phiên ứng dụng, thiết lập editor và xác minh máy Pi |

## Đã dọn

- CSV/DB train riêng và các tệp journal đi kèm sau khi checkpoint DB.
- Thư mục backups cũ, mô hình trung gian của bộ ghép và previous_model không còn dùng.
- Công cụ merge_pi_dataset của định dạng CSV cũ; kiểm thử hiện tại vẫn kiểm tra ưu tiên mẫu thật, tránh trùng và chặn mẫu tương lai.
- Tài liệu bản 10 phút, bản QA cũ gây nhầm với hướng dẫn hiện tại và biên nhận đồng bộ cũ.
- Bản ZIP xuất trùng ở thư mục cha, cache Python và lock Word không còn được dùng.

Các gói hiện tại tập trung trong `deliverables/`. Gói Pi không chứa .env, SQLite riêng, backup hoặc báo cáo. Gói dữ liệu chỉ có CSV chung và một DB vận hành đã bỏ cấu hình riêng tư.

Lần thay đổi này được thực hiện trên PC; dữ liệu đang thu trên Pi không bị thay thế.
