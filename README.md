# Video Bulk Downloader (Qt5 + yt-dlp)

Ứng dụng desktop GUI (PyQt5) để tải video từ nhiều nguồn (YouTube, TikTok, v.v.) bằng `yt-dlp`.

## Tính năng

- Tải **nhiều URL** cùng lúc (dán mỗi URL một dòng).
- Mở rộng URL kênh/playlist thành danh sách video:
  - YouTube channel/playlist
  - TikTok profile/listing (nếu extractor hỗ trợ)
- Hỗ trợ cookie từ trình duyệt (`--cookies-from-browser`):
  - chrome, edge, firefox, brave, opera, chromium, safari
- Chọn chế độ tải:
  - Video tốt nhất + merge mp4
  - Chỉ audio mp3
- Giao diện Qt5 dễ dùng.

## Cài đặt

1. Cài Python 3.10+.
2. Mở terminal tại thư mục project.
3. Cài dependencies:

```bash
pip install -r requirements.txt
```

## Chạy app

```bash
python app.py
```

## Ghi chú

- Dự án đã clone `yt-dlp` vào thư mục con `./yt-dlp`.
- App sẽ gọi trực tiếp `yt_dlp/__main__.py` từ repo clone.
- Nếu video yêu cầu đăng nhập/độ tuổi/khu vực, hãy chọn cookie browser phù hợp.

## Cảnh báo pháp lý

Hãy chỉ tải nội dung khi bạn có quyền theo điều khoản nền tảng và luật hiện hành.
