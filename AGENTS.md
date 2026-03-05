# AGENTS.md - Video Downloader & SRT Compare Tool

## Project Overview

Đây là một ứng dụng desktop đa chức năng được phát triển bằng Python và PyQt5, cung cấp các công cụ:

1. **Tải video hàng loạt** (Video Downloader) - Sử dụng yt-dlp để tải video từ YouTube, TikTok, Bilibili và nhiều nguồn khác
2. **So sánh và chỉnh sửa file SRT** (SRT Compare) - So sánh thởi gian phụ đề giữa các file SRT
3. **Xử lý video** - Gộp video/audio, cắt nhỏ video, ghép nhiều video
4. **CapCut Integration** - Xuất phụ đề từ project CapCut, trim audio, kiểm tra overlap

Dự án được phát triển bởi Dương Quốc Lợi, với giao diện hỗ trợ cả Dark Mode và Light Mode.

## Technology Stack

- **Ngôn ngữ**: Python 3.10+
- **GUI Framework**: PyQt5 (>=5.15)
- **Video Download**: yt-dlp (embedded submodule)
- **Audio Processing**: pydub (>=0.25.1), FFmpeg
- **Cookie Handling**: browser-cookie3 (>=0.19.1)
- **Build Tool**: PyInstaller

## Project Structure

```
.
├── app.py                    # Entry point chính, chứa MainWindow và các Worker classes
├── main.py                   # Entry point wrapper để chạy app
├── reup_tool_widget.py       # SRT Compare Tool widget (tab SRT compare)
├── srt_parser.py             # Module xử lý parse và so sánh file SRT
├── capcut_srt_gui.py         # Module xử lý CapCut draft, audio trim, overlap detection
├── requirements.txt          # Python dependencies
├── build_mac.sh              # Script build cho macOS
├── BUILD_INSTRUCTIONS.md     # Hướng dẫn build chi tiết
├── logo.png                  # Logo ứng dụng
├── logo.ico                  # Icon Windows
├── cookie_profiles.json      # Lưu trữ cookie profiles
├── cookies_bilibili_debug.json  # Debug cookies
├── yt-dlp/                   # Submodule yt-dlp (clone trực tiếp)
├── .runtime/                 # Thư mục tạm cho cookie files
└── .thumb_cache/             # Cache thumbnail
```

## Main Components

### 1. Video Downloader (app.py)

Main window với các tab:
- **📥 Tải video**: Quét và tải video từ URL
- **🔗 Gộp Video**: Gộp video và audio riêng biệt (thường dùng cho Bilibili)
- **✂️ Cắt & Ghép Video**: Cắt nhỏ video hoặc ghép nhiều video

Các worker classes:
- `ExpandWorker`: Quét danh sách video từ URL/channel/playlist
- `DownloadWorker`: Tải video đã chọn
- `ThumbnailDownloadWorker`: Tải thumbnail riêng
- `MergeWorker`: Gộp video và audio
- `SplitWorker`: Cắt nhỏ video
- `FastConcatWorker`: Ghép nhiều video nhanh

### 2. SRT Compare Tool (reup_tool_widget.py)

Widget được nhúng vào tab "SRT compare" của app chính:
- So sánh thởi gian start/end giữa 2 file SRT
- Tự động sửa lỗi đồng bộ thởi gian
- Tạo file thaisub (giữ nguyên timing, xóa nội dung)
- Chia nhỏ file SRT thành nhiều phần để dễ copy

### 3. SRT Parser (srt_parser.py)

Module độc lập để parse và so sánh file SRT:
- `SubtitleEntry`: Class đại diện cho một subtitle entry
- `parse_srt_file()`: Parse file SRT thành list entries
- `compare_srt_files()`: So sánh 2 file SRT với tolerance

### 4. CapCut Integration (capcut_srt_gui.py)

Module xử lý CapCut draft:
- `load_draft_json()`: Đọc file draft_content.json hoặc draft_info.json
- `extract_subtitles_with_audio()`: Trích xuất phụ đề và audio mapping
- `trim_audio_file()`: Cắt im lặng ở cuối file audio
- `check_audio_overlap()`: Kiểm tra overlap giữa các audio clip

## Build & Run

### Development

```bash
# Cài dependencies
pip install -r requirements.txt

# Chạy ứng dụng
python app.py
# hoặc
python main.py
```

### Build trên macOS (Intel/Apple Silicon)

```bash
# Sử dụng script build
chmod +x build_mac.sh
./build_mac.sh

# Hoặc build thủ công với PyInstaller
pyinstaller --clean --windowed --name "VideoDownloader" \
  --add-binary "ffmpeg:." --add-binary "yt-dlp:." app.py
```

### Build qua GitHub Actions

Workflow `.github/workflows/build-macos.yml` tự động build khi push lên main/master:
- Chạy trên runner macos-15-intel
- Output: ZIP và DMG file

## Dependencies

Từ `requirements.txt`:
```
PyQt5>=5.15
browser-cookie3>=0.19.1
pydub>=0.25.1
```

External binaries cần thiết:
- `ffmpeg`: Xử lý video/audio (nên đặt cùng thư mục với app hoặc trong PATH)
- `yt-dlp`: Download video (embedded trong project hoặc trong PATH)

## Code Conventions

### Ngôn ngữ
- Code comments và UI labels sử dụng **tiếng Việt**
- Variable names dùng snake_case
- Class names dùng CamelCase
- Constants dùng UPPER_CASE

### UI Conventions
- Dark mode là mặc định
- Màu chủ đạo: `#2d8fe3` (xanh dương), `#d32f2f` (đỏ cho nút nguy hiểm)
- Font size: 14-15px cho text thường, 16-18px cho headers
- Các nhóm chức năng dùng QGroupBox với border radius 10px

### Worker Pattern
- Mọi tác vụ nặng (download, xử lý video) chạy trong QThread riêng
- Sử dụng pyqtSignal để communicate với main thread
- Hỗ trợ cancel/terminate khi cần

## Configuration Files

### Cookie Profiles (cookie_profiles.json)
```json
{
  "profile_name": "{\"url\":\"...\",\"cookies\":[...]}"
}
```

### Download Archive
File `downloaded.archive` trong thư mục download, lưu danh sách video đã tải (format: yt-dlp archive)

### QSettings
- Organization: "VideoDownloader"
- Application: "App"
- Lưu: download_out_dir, merge_out_dir, split_out_dir, fc_out_dir

## Security Considerations

- **Cookie handling**: Cookie từ browser được đọc read-only, không modify
- **FFmpeg execution**: Sử dụng subprocess với proper escaping, no shell=True
- **File paths**: Sử dụng Pathlib, normalize paths trước khi sử dụng
- **Temporary files**: Cookie files lưu trong `.runtime/` (gitignored)

## Platform Support

- **Primary**: macOS (Intel x64)
- **Secondary**: Windows (có code xử lý .exe unblock, nhưng không phải focus chính)
- **Linux**: Có thể chạy nhưng chưa được test kỹ

## Known Limitations

1. **yt-dlp**: Phải clone vào thư mục `yt-dlp/` hoặc cung cấp binary riêng
2. **FFmpeg**: Không tự động download, cần cài thủ công
3. **Cookie browser**: Một số trình duyệt (Safari) có thể cần quyền đặc biệt
4. **Large playlists**: Quét playlist lớn (>1000 videos) có thể chậm nếu không bật fast scan

## Testing

Không có unit tests. Testing chủ yếu thông qua:
1. Manual testing trên macOS
2. GitHub Actions build verification
3. Test với các URL thực tế từ YouTube, TikTok, Bilibili

## Troubleshooting

### Lỗi "Không tìm thấy FFmpeg"
- Kiểm tra ffmpeg có trong PATH hoặc cùng thư mục với app
- Log sẽ hiển thị đường dẫn đang tìm kiếm

### Lỗi cookie bị khoá
- Đóng trình duyệt trước khi lấy cookie
- Hoặc dùng JSON cookie thủ công

### Lỗi download thất bại
- Kiểm tra URL có thể truy cập không
- Thử đổi browser cookie
- Kiểm tra yt-dlp có được nhúng đúng không

## Future Enhancements

- Thêm support cho yt-dlp update tự động
- Thêm queue management cho download
- Thêm video preview trong bảng danh sách
- Support thêm nền tảng (Instagram, Facebook)
