# Hướng dẫn Build Ứng dụng Video Downloader

## Yêu cầu

- Python 3.10 trở lên
- Đã cài đặt các thư viện từ `requirements.txt`:
  ```bash
  pip install -r requirements.txt
  ```
- PyInstaller đã được cài đặt (tự động khi chạy build)

## Build trên macOS (Intel x64)

> Lưu ý: phần mềm đã được cập nhật để tự nhận **ffmpeg** và **yt-dlp** trên macOS (không cần đuôi `.exe`).

1. Cài Python 3.10+ và `pip` trên macOS.
2. Cài các thư viện cần thiết:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. Tải binary cho mac x64:
   - **ffmpeg (macOS x64)**: tải bản static phù hợp và đặt tên file là `ffmpeg`
   - **yt-dlp (macOS)**: tải file thực thi và đặt tên là `yt-dlp`
   - Copy 2 file này vào **cùng thư mục với `app.py`** (thư mục `tool_dowload_video`)

4. Chạy PyInstaller (ví dụ):
   ```bash
   pyinstaller app.py \
     --clean \
     --windowed \
     --name "VideoDownloader" \
     --hidden-import "reup_tool_widget" \
     --icon "logo.png" \
     --add-data "logo.png:." \
     --add-data "logo.ico:." \
     --add-binary "ffmpeg:." \
     --add-data "yt-dlp:."
   ```

5. Sau khi build xong:
   - Bạn sẽ có app dạng `.app` trong thư mục `dist/VideoDownloader.app`
   - Khi chạy, app sẽ ưu tiên dùng:
     - `ffmpeg` / `yt-dlp` nằm trong bundle
     - Nếu không có, sẽ tìm trong `PATH` hệ thống (ví dụ cài qua `brew install ffmpeg yt-dlp`)
