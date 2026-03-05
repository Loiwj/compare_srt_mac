#!/usr/bin/env bash
set -euo pipefail

# Script build cho macOS (Intel/Apple Silicon) cho tool_dowload_video
# - Tự tạo venv
# - Tự tải ffmpeg cho macOS
# - Chuẩn bị yt-dlp (dùng file yt-dlp_macos nếu có)
# - Đóng gói bằng PyInstaller vào dạng .app

cd "$(dirname "$0")"

echo "==> Tạo virtualenv và cài thư viện..."
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "==> Chuẩn bị yt-dlp cho macOS..."
# Nếu đã có file yt-dlp_macos trong repo thì copy sang yt-dlp
if [ -f "yt-dlp_macos" ]; then
  cp "yt-dlp_macos" "yt-dlp"
  chmod +x "yt-dlp" || true
  echo "   - Đã copy yt-dlp_macos -> yt-dlp"
else
  # Nếu không có, thử tải bản mới nhất từ GitHub (macOS binary)
  echo "   - Không tìm thấy yt-dlp_macos, thử tải từ GitHub..."
  curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos -o yt-dlp
  chmod +x "yt-dlp"
  echo "   - Đã tải yt-dlp (macOS) về file ./yt-dlp"
fi

echo "==> Tải ffmpeg cho macOS..."
# Chọn một bản ffmpeg static phổ biến cho macOS.
# Ở đây dùng bản từ evermeet.cx (thường dùng trong cộng đồng).
FFMPEG_URL="https://evermeet.cx/ffmpeg/ffmpeg-6.1.1.zip"
TMP_ZIP="$(mktemp /tmp/ffmpeg-macos-XXXXXX.zip)"

curl -L "$FFMPEG_URL" -o "$TMP_ZIP"

echo "   - Giải nén ffmpeg..."
unzip -j "$TMP_ZIP" -d .
rm -f "$TMP_ZIP"

if [ -f "ffmpeg" ]; then
  chmod +x "ffmpeg" || true
  echo "   - Đã chuẩn bị ffmpeg tại: $(pwd)/ffmpeg"
else
  echo "!!! Không tìm thấy file 'ffmpeg' sau khi giải nén."
  echo "    Vui lòng kiểm tra lại URL hoặc tự tải ffmpeg cho macOS và đặt file 'ffmpeg' cạnh app.py."
fi

echo "==> Đóng gói ứng dụng với PyInstaller..."
python -m PyInstaller \
  --clean \
  --windowed \
  --name "VideoDownloader" \
  --add-binary "ffmpeg:." \
  --add-binary "yt-dlp:." \
  app.py

echo
echo "✅ Build xong: dist/VideoDownloader.app"
echo "   - ffmpeg và yt-dlp đã được nhúng vào bundle (thư mục .app)"

