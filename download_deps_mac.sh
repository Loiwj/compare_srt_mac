#!/bin/bash
set -e

echo ""
echo "============================================"
echo "  Download Dependencies (macOS x64)"
echo "  ffmpeg / yt-dlp / libmpv"
echo "============================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- yt-dlp ---
echo "[1/3] Downloading yt-dlp ..."
if [ -f "yt-dlp" ]; then
    echo "      Already exists, skipping."
else
    curl -L -o "yt-dlp" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
    if [ $? -ne 0 ]; then
        echo "      [ERROR] Failed to download yt-dlp"
    else
        chmod +x "yt-dlp"
        echo "      OK!"
    fi
fi
echo ""

# --- ffmpeg ---
echo "[2/3] Downloading ffmpeg ..."
if [ -f "ffmpeg" ]; then
    echo "      Already exists, skipping."
else
    echo "      Trying evermeet.cx (pre-built macOS binary)..."
    curl -L -o "ffmpeg.zip" "https://evermeet.cx/ffmpeg/getrelease/zip" 2>/dev/null
    if [ $? -eq 0 ] && [ -f "ffmpeg.zip" ]; then
        unzip -o "ffmpeg.zip" ffmpeg -d . 2>/dev/null
        if [ -f "ffmpeg" ]; then
            chmod +x "ffmpeg"
            rm -f "ffmpeg.zip"
            echo "      OK!"
        else
            rm -f "ffmpeg.zip"
            echo "      [ERROR] Could not extract ffmpeg from zip"
            echo "      Try: brew install ffmpeg"
        fi
    else
        echo "      [ERROR] Failed to download ffmpeg"
        echo "      Try: brew install ffmpeg"
    fi
fi
echo ""

# --- libmpv ---
echo "[3/3] Checking libmpv ..."
if [ -f "libmpv.dylib" ] || [ -f "libmpv.2.dylib" ]; then
    echo "      Already exists, skipping."
else
    # Kiểm tra nếu đã cài qua Homebrew
    BREW_MPV_LIB=""
    if command -v brew &> /dev/null; then
        BREW_PREFIX="$(brew --prefix 2>/dev/null || echo '/usr/local')"
        for candidate in \
            "$BREW_PREFIX/lib/libmpv.dylib" \
            "$BREW_PREFIX/lib/libmpv.2.dylib" \
            "$BREW_PREFIX/opt/mpv/lib/libmpv.dylib" \
            "$BREW_PREFIX/opt/mpv/lib/libmpv.2.dylib"; do
            if [ -f "$candidate" ]; then
                BREW_MPV_LIB="$candidate"
                break
            fi
        done
    fi

    if [ -n "$BREW_MPV_LIB" ]; then
        cp "$BREW_MPV_LIB" .
        echo "      Copied from Homebrew: $BREW_MPV_LIB"
        echo "      OK!"
    else
        echo "      [NOT FOUND] libmpv not found."
        echo ""
        echo "      To install libmpv on macOS, run:"
        echo "        brew install mpv"
        echo ""
        echo "      Then re-run this script to auto-copy the .dylib file."
    fi
fi
echo ""

# --- Summary ---
echo "============================================"
echo "  Status:"
[ -f "yt-dlp" ]                                           && echo "  [OK] yt-dlp"          || echo "  [MISSING] yt-dlp"
[ -f "ffmpeg" ]                                           && echo "  [OK] ffmpeg"           || echo "  [MISSING] ffmpeg"
[ -f "libmpv.dylib" ] || [ -f "libmpv.2.dylib" ]         && echo "  [OK] libmpv"           || echo "  [MISSING] libmpv"
echo "============================================"
echo ""
