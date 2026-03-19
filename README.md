<div align="center">

<img src="logo.png" alt="Logo" width="100">

# 🎬 Video Downloader Tool

**Ứng dụng desktop đa năng để tải, xử lý và quản lý video**

Xây dựng bằng Python + PyQt5 · Tích hợp yt-dlp & FFmpeg

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](#)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.15+-41CD52?logo=qt&logoColor=white)](#)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-FF0000?logo=youtube&logoColor=white)](#)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-integrated-007808?logo=ffmpeg&logoColor=white)](#)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6?logo=windows&logoColor=white)](#)

</div>

---

## 📥 Tính năng

- 🎥 **Tải Video** — Tải nhiều URL cùng lúc từ YouTube, TikTok, Bilibili và nhiều nguồn khác
- 🎯 **Chọn chất lượng** — Chọn best / 1080p / 720p, hỗ trợ MP4 hoặc MP3
- 📋 **Quét playlist** — Tự động quét channel / playlist thành danh sách video
- 🍪 **Cookie browser** — Hỗ trợ Chrome, Edge, Firefox, Brave, Opera hoặc dán JSON thủ công
- 📝 **SRT Compare** — So sánh, chỉnh sửa 2 file phụ đề SRT song song
- 🤖 **Dịch phụ đề AI** — Tích hợp Gemini AI dịch tự động phụ đề
- 🔗 **Gộp Video** — Gộp video + audio riêng biệt thành 1 file (hữu ích cho Bilibili)
- ✂️ **Cắt & Ghép** — Cắt video theo thời gian, ghép nhiều video thành 1 file
- ▶️ **MPV Player** — Xem trước video trực tiếp trong app

---

## 🚀 Hướng dẫn cài đặt

### Bước 1 — Clone repo

```bash
git clone https://github.com/Loiwj/tool_dowload_video.git
cd tool_dowload_video
```

### Bước 2 — Cài Python dependencies

```bash
pip install -r requirements.txt
```

### Bước 3 — Tải binary dependencies

Chạy script tự động tải `yt-dlp.exe`, `ffmpeg.exe`, `libmpv-2.dll`:

```bash
.\download_deps.bat
```

> [!NOTE]
> `libmpv-2.dll` cần [7-Zip](https://7-zip.org/) để giải nén.
> Nếu không có 7-Zip, script sẽ tải file `.7z` và bạn cần giải nén thủ công.

**Hoặc tải thủ công:**

- `yt-dlp.exe` → [github.com/yt-dlp/yt-dlp/releases](https://github.com/yt-dlp/yt-dlp/releases/latest)
- `ffmpeg.exe` → [github.com/BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases)
- `libmpv-2.dll` → [sourceforge.net/mpv-player-windows](https://sourceforge.net/projects/mpv-player-windows/files/libmpv/)

### Bước 4 — Chạy ứng dụng

```bash
python app.py
```

---

## 📦 Dependencies

| Package | Version | Mục đích |
|---------|---------|----------|
| `PyQt5` | >= 5.15 | UI Framework |
| `python-mpv` | >= 1.0.6 | Video player tích hợp |
| `browser-cookie3` | >= 0.19.1 | Lấy cookie từ trình duyệt |
| `pydub` | >= 0.25.1 | Xử lý audio |
| `qrcode[pil]` | >= 7.0 | Tạo QR code |
| `requests` | >= 2.28 | HTTP requests |
| `cryptography` | >= 41.0 | Mã hóa |
| `deep-translator` | >= 1.11 | Dịch thuật |

---

## 🏗️ Build Executable

```bash
# Build nhanh (khuyến nghị)
.\build.bat

# Hoặc build thủ công
pyinstaller build.spec --clean
```

Output sau khi build:

```
dist/
├── VideoDownloader.exe    # File chính
├── ffmpeg.exe
├── yt-dlp.exe
└── runtime/
```

> Xem chi tiết tại [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md)

---

## 📂 Cấu trúc dự án

```
tool_dowload_video/
├── app.py                         # Entry point chính
├── reup_tool_widget.py            # SRT Compare Tool
├── video_trim_widget.py           # Cắt & Ghép Video
├── srt_parser.py                  # Module xử lý file SRT
├── capcut_srt_gui.py              # Tích hợp CapCut project
├── bilibili_api.py                # Bilibili API wrapper
├── bilibili_workers.py            # Bilibili download workers
├── patch_mpv_shortcut_layout.py   # Patch MPV keyboard shortcuts
├── patch_zoom_ux.py               # Patch zoom UX
├── download_deps.bat              # Script tải binary dependencies
├── build.bat                      # Script build tự động
├── requirements.txt               # Python dependencies
├── cookie_profiles.json           # Profiles cookie đã lưu
├── logo.png / logo.ico            # Logo app
└── README.md
```

---

## 🍪 Cookie

Hỗ trợ 3 chế độ cho video yêu cầu đăng nhập:

- **Không dùng** — mặc định, tải video công khai
- **Từ trình duyệt** — tự động lấy cookie từ Chrome / Edge / Firefox / Brave / Opera
- **JSON thủ công** — dán cookie JSON vào text area trong app

---

## ⚠️ Lưu ý

- Chỉ hỗ trợ **Windows 10/11**
- Cần `ffmpeg.exe`, `yt-dlp.exe` trong thư mục gốc hoặc PATH
- Video Bilibili có thể cần dùng tính năng **Gộp Video** (video/audio tách riêng)
- Một số video yêu cầu cookie để tải — cấu hình trong app

---

## 📜 Cảnh báo pháp lý

Hãy chỉ tải nội dung khi bạn có quyền theo điều khoản của nền tảng và luật pháp hiện hành.

---

<div align="center">

Made with ❤️ by [Loiwj](https://github.com/Loiwj)

</div>

