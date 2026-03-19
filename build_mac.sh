#!/bin/bash
set -e

echo "========================================"
echo "  Building Video Downloader for macOS"
echo "========================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/4] Cleaning old build files..."
rm -rf build dist
echo "Done."
echo ""

echo "[2/4] Preparing icon..."
if [ -f "logo.png" ]; then
    echo "  - Creating logo.icns from logo.png..."
    # Tạo iconset directory
    ICONSET_DIR="logo.iconset"
    mkdir -p "$ICONSET_DIR"
    
    # Kiểm tra xem sips có sẵn không (macOS built-in)
    if command -v sips &> /dev/null; then
        sips -z 16 16     logo.png --out "$ICONSET_DIR/icon_16x16.png"      2>/dev/null || true
        sips -z 32 32     logo.png --out "$ICONSET_DIR/icon_16x16@2x.png"   2>/dev/null || true
        sips -z 32 32     logo.png --out "$ICONSET_DIR/icon_32x32.png"      2>/dev/null || true
        sips -z 64 64     logo.png --out "$ICONSET_DIR/icon_32x32@2x.png"   2>/dev/null || true
        sips -z 128 128   logo.png --out "$ICONSET_DIR/icon_128x128.png"    2>/dev/null || true
        sips -z 256 256   logo.png --out "$ICONSET_DIR/icon_128x128@2x.png" 2>/dev/null || true
        sips -z 256 256   logo.png --out "$ICONSET_DIR/icon_256x256.png"    2>/dev/null || true
        sips -z 512 512   logo.png --out "$ICONSET_DIR/icon_256x256@2x.png" 2>/dev/null || true
        sips -z 512 512   logo.png --out "$ICONSET_DIR/icon_512x512.png"    2>/dev/null || true
        sips -z 1024 1024 logo.png --out "$ICONSET_DIR/icon_512x512@2x.png" 2>/dev/null || true
        
        iconutil -c icns "$ICONSET_DIR" -o logo.icns 2>/dev/null || true
    else
        echo "  - sips not available, trying Python Pillow..."
        python3 -c "
from PIL import Image
import pathlib
p = pathlib.Path('logo.png')
im = Image.open(p)
im.save('logo.icns', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])
" 2>/dev/null || echo "  - Warning: Could not create .icns icon"
    fi
    
    rm -rf "$ICONSET_DIR"
    echo "  - Icon ready."
fi
echo "Done."
echo ""

echo "[3/4] Building application with PyInstaller..."
pyinstaller build_mac.spec --clean
if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Build failed!"
    exit 1
fi
echo "Done."
echo ""

echo "[4/4] Copying required files to dist folder..."
if [ -f "ffmpeg" ]; then
    cp -f "ffmpeg" "dist/"
    chmod +x "dist/ffmpeg"
    echo "  - ffmpeg copied"
fi
if [ -f "yt-dlp" ]; then
    cp -f "yt-dlp" "dist/"
    chmod +x "dist/yt-dlp"
    echo "  - yt-dlp copied"
fi
# libmpv dylib - tìm cả .dylib và .2.dylib
for lib in libmpv.dylib libmpv.2.dylib; do
    if [ -f "$lib" ]; then
        cp -f "$lib" "dist/"
        echo "  - $lib copied"
    fi
done
if [ -f "logo.png" ]; then
    cp -f "logo.png" "dist/"
    echo "  - logo.png copied"
fi
echo ""

echo "========================================"
echo "  Build completed successfully!"
echo "========================================"
echo ""
echo "Output location: $SCRIPT_DIR/dist/VideoDownloader.app"
echo ""
echo "You can now distribute the 'dist' folder or the .app bundle."
echo ""
