# Reup Tool

Ứng dụng so sánh và chỉnh sửa file phụ đề SRT với giao diện Qt5.

## Tính năng chính

- ✅ So sánh 2 file SRT để kiểm tra lệch thời gian
- ✅ Tự động sửa lỗi lệch thời gian (đồng bộ từ file này sang file kia)
- ✅ Tạo file thaisub từ file nguồn
- ✅ Mở nhanh file SRT bằng ứng dụng mặc định của hệ điều hành

## Cài đặt

```bash
pip install -r requirements.txt
```

## Chạy trực tiếp

```bash
python main.py
```

## Build cho macOS

```bash
chmod +x build_mac.sh
./build_mac.sh
```

Output: `dist/ReupTool.app`

## Cấu trúc tối thiểu

- `main.py`: file giao diện/chức năng chính
- `srt_parser.py`: xử lý parse/so sánh/sửa SRT
- `requirements.txt`: dependencies để chạy/build
- `build_mac.sh`: script build cho macOS (PyInstaller)
