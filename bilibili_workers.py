"""
Bilibili QThread Workers - QR Polling + Download Worker.

Workers chạy trong thread riêng để không block UI:
- QRPollWorker: Polling trạng thái QR mỗi 2 giây
- BilibiliDownloadWorker: Tải video bằng native engine (fallback cho yt-dlp)
"""

import time

from PyQt5.QtCore import QThread, pyqtSignal

from bilibili_api import (
    BilibiliDownloader,
    BilibiliQRLogin,
    QR_STATUS_EXPIRED,
    QR_STATUS_NOT_SCANNED,
    QR_STATUS_SCANNED_WAIT_CONFIRM,
    QR_STATUS_SUCCESS,
)


# ============================================================================
# QR Code Polling Worker
# ============================================================================


class QRPollWorker(QThread):
    """
    Worker polling trạng thái QR code mỗi 2 giây.
    Timeout sau 180 giây (QR code hết hạn).
    """

    # Signals
    status_changed = pyqtSignal(int, str)   # (status_code, message_text)
    login_success = pyqtSignal(dict)        # cookies dict
    login_failed = pyqtSignal(str)          # error message

    POLL_INTERVAL = 2    # giây
    TIMEOUT = 180        # giây

    def __init__(self, qrcode_key, parent=None):
        super().__init__(parent)
        self.qrcode_key = qrcode_key
        self._stop = False
        self._login = BilibiliQRLogin()

    def stop(self):
        """Dừng polling."""
        self._stop = True

    def run(self):
        start_time = time.time()

        while not self._stop:
            elapsed = time.time() - start_time
            if elapsed >= self.TIMEOUT:
                self.status_changed.emit(QR_STATUS_EXPIRED, "QR code đã hết hạn")
                self.login_failed.emit("QR code đã hết hạn (180 giây)")
                return

            try:
                status_code, cookies = self._login.poll_status(self.qrcode_key)

                if status_code == QR_STATUS_SUCCESS:
                    self.status_changed.emit(status_code, "✅ Đăng nhập thành công!")
                    self.login_success.emit(cookies)
                    return

                elif status_code == QR_STATUS_SCANNED_WAIT_CONFIRM:
                    remaining = int(self.TIMEOUT - elapsed)
                    self.status_changed.emit(
                        status_code,
                        f"📱 Đã quét QR, vui lòng xác nhận trên điện thoại... ({remaining}s)"
                    )

                elif status_code == QR_STATUS_EXPIRED:
                    self.status_changed.emit(status_code, "⏰ QR code đã hết hạn")
                    self.login_failed.emit("QR code đã hết hạn")
                    return

                elif status_code == QR_STATUS_NOT_SCANNED:
                    remaining = int(self.TIMEOUT - elapsed)
                    self.status_changed.emit(
                        status_code,
                        f"📸 Dùng app Bilibili quét mã QR... ({remaining}s)"
                    )
                else:
                    self.status_changed.emit(status_code, f"Trạng thái: {status_code}")

            except Exception as e:
                self.login_failed.emit(f"Lỗi polling: {e}")
                return

            # Ngủ giữa các lần poll
            for _ in range(self.POLL_INTERVAL * 10):  # 0.1s intervals cho responsive stop
                if self._stop:
                    return
                time.sleep(0.1)


# ============================================================================
# Bilibili Download Worker (Native Engine)
# ============================================================================


class BilibiliDownloadWorker(QThread):
    """
    Worker tải video Bilibili bằng native download engine.
    Dùng làm fallback khi yt-dlp lỗi.
    """

    # Signals
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)    # (current_bytes, total_bytes)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(
        self,
        urls,
        out_dir,
        quality,
        cookies,
        ffmpeg_path,
        parent=None,
    ):
        super().__init__(parent)
        self.urls = urls if isinstance(urls, list) else [urls]
        self.out_dir = out_dir
        self.quality = quality
        self.cookies = cookies
        self.ffmpeg_path = ffmpeg_path
        self._stop = False

        # Thống kê
        self.total = len(self.urls)
        self.success_count = 0
        self.error_count = 0

    def stop(self):
        """Dừng download."""
        self._stop = True

    def run(self):
        downloader = BilibiliDownloader(ffmpeg_path=self.ffmpeg_path)

        for idx, url in enumerate(self.urls, 1):
            if self._stop:
                self.log.emit("⛔ Download bị dừng bởi người dùng")
                break

            self.log.emit(f"\n{'='*60}")
            self.log.emit(f"📥 [{idx}/{self.total}] Đang tải: {url}")
            self.log.emit(f"{'='*60}")

            try:
                # Map quality string → Bilibili quality ID
                quality_id = self._map_quality(self.quality)

                results = downloader.download_video(
                    url_or_bvid=url,
                    output_dir=self.out_dir,
                    quality=quality_id,
                    cookies=self.cookies,
                    progress_callback=self._on_progress,
                    log_callback=lambda msg: self.log.emit(msg),
                )

                self.success_count += 1
                for fp in results:
                    self.log.emit(f"✅ Đã lưu: {fp}")

            except Exception as e:
                self.error_count += 1
                self.log.emit(f"❌ Lỗi tải {url}: {e}")

        # Tổng kết
        self.log.emit(f"\n📊 Kết quả: {self.success_count} thành công, {self.error_count} lỗi")

        if self.error_count > 0 and self.success_count == 0:
            self.failed.emit(f"Tất cả {self.error_count} video đều lỗi")
        else:
            self.done.emit()

    def _on_progress(self, downloaded, total, phase):
        """Callback nhận progress từ download."""
        self.progress.emit(downloaded, total)

    def _map_quality(self, quality_str):
        """
        Map chất lượng từ UI string sang Bilibili quality ID.

        Args:
            quality_str: "best", "max1080", "max720", etc.

        Returns:
            int: Bilibili quality ID
        """
        quality_map = {
            "best": 127,       # Lấy cao nhất có thể
            "max4320": 127,    # 8K
            "max2160": 120,    # 4K
            "max1440": 116,    # 1080P60 (1440p không có trên Bilibili)
            "max1080": 80,     # 1080P
            "max720": 64,      # 720P
            "max480": 32,      # 480P
            "max360": 16,      # 360P
        }
        return quality_map.get(quality_str, 80)
