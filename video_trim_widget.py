"""
Video Trim Widget — Xem trước video và cắt trực tiếp bằng giao diện visual.

Bao gồm:
- Video player (QMediaPlayer + QVideoWidget)
- Range slider kéo thả 2 đầu
- Danh sách segment
- Export ra file
"""

import os
import sys
import struct
import subprocess
from pathlib import Path

from PyQt5.QtCore import (
    Qt,
    QUrl,
    QTimer,
    pyqtSignal,
    QRect,
    QThread,
    QPoint,
)
from PyQt5.QtGui import QPainter, QColor, QFont, QBrush, QPen, QLinearGradient, QPolygon, QKeySequence
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSlider,
    QStyle,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QProgressBar,
    QFrame,
    QAbstractItemView,
    QLineEdit,
    QShortcut,
    QDialog,
    QScrollArea,
)

# mpv player — phát được mọi format, cần libmpv-2.dll (Windows) hoặc libmpv.dylib (macOS)
# Fix locale cho macOS: libmpv yêu cầu LC_NUMERIC = "C", nếu không sẽ crash (segfault)
import locale
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass

HAS_MPV = False
try:
    # Đảm bảo tìm thấy libmpv — hỗ trợ cả dev và PyInstaller frozen
    _search_dirs = []
    # 1. PyInstaller frozen: thư mục tạm (_MEIPASS) chứa binaries bundled
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        _search_dirs.append(sys._MEIPASS)
        # 2. Thư mục chứa exe/app (nơi build script copy binaries)
        _search_dirs.append(os.path.dirname(sys.executable))
    # 3. Thư mục chứa script (khi chạy dev)
    _search_dirs.append(os.path.dirname(os.path.abspath(__file__)))

    for _d in _search_dirs:
        if _d and _d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
        if os.name == "nt" and hasattr(os, 'add_dll_directory') and os.path.isdir(_d):
            os.add_dll_directory(_d)

    # macOS: thêm DYLD_LIBRARY_PATH nếu cần
    if sys.platform == "darwin":
        for _d in _search_dirs:
            if _d and os.path.isdir(_d):
                dyld_path = os.environ.get("DYLD_LIBRARY_PATH", "")
                if _d not in dyld_path:
                    os.environ["DYLD_LIBRARY_PATH"] = _d + os.pathsep + dyld_path

    import mpv
    HAS_MPV = True
except (ImportError, OSError):
    pass

# macOS: mpv không thể embed video vào widget PyQt5 (chỉ có audio, không có hình)
# → Tắt mpv trên macOS, dùng QMediaPlayer (AVFoundation) thay thế
import sys as _sys_check
if _sys_check.platform == "darwin":
    HAS_MPV = False

# Fallback: PyQt5 Multimedia (trên macOS dùng AVFoundation, hỗ trợ codec tốt)
HAS_MULTIMEDIA = False
if not HAS_MPV:
    if os.name == "nt":
        os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")
    try:
        from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
        from PyQt5.QtMultimediaWidgets import QVideoWidget
        HAS_MULTIMEDIA = True
    except ImportError:
        pass


def _subprocess_no_console_kwargs():
    """Tránh hiện console window khi chạy subprocess trên Windows."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _fmt_time(ms):
    """Format milliseconds → HH:MM:SS."""
    if ms < 0:
        ms = 0
    total_secs = ms // 1000
    h = total_secs // 3600
    m = (total_secs % 3600) // 60
    s = total_secs % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_secs(secs):
    """Format seconds → HH:MM:SS."""
    if secs < 0:
        secs = 0
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ============================================================================
# Range Slider — Thanh trượt 2 đầu cho trim
# ============================================================================

class RangeSlider(QWidget):
    """
    Custom widget hiển thị thanh trượt 2 đầu (trim start / trim end).
    Kéo thả trực tiếp để chọn vùng cắt.
    """

    rangeChanged = pyqtSignal(int, int)  # (start_ms, end_ms)
    handleDragged = pyqtSignal(int)        # ms — khi user kéo handle, seek video

    HANDLE_WIDTH = 14
    TRACK_HEIGHT = 8
    HANDLE_HEIGHT = 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min_val = 0
        self._max_val = 1000  # ms
        self._start = 0       # ms
        self._end = 1000      # ms
        self._playhead = 0    # ms (vị trí phát hiện tại)
        self._cut_markers = []  # list[int] ms — các vị trí đã cắt

        self._dragging = None  # "start", "end", hoặc None
        self.setMinimumHeight(44)
        self.setMaximumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_range(self, min_val, max_val):
        self._min_val = min_val
        self._max_val = max(max_val, min_val + 1)
        self._start = min_val
        self._end = max_val
        self.update()

    def set_start(self, val):
        self._start = max(self._min_val, min(val, self._end - 100))
        self.update()

    def set_end(self, val):
        self._end = min(self._max_val, max(val, self._start + 100))
        self.update()

    def set_playhead(self, val):
        self._playhead = val
        self.update()

    def start_val(self):
        return self._start

    def end_val(self):
        return self._end

    def add_cut_marker(self, ms):
        """Thêm marker cắt tại vị trí ms."""
        if ms not in self._cut_markers and self._min_val < ms < self._max_val:
            self._cut_markers.append(ms)
            self._cut_markers.sort()
            self.update()

    def clear_cut_markers(self):
        self._cut_markers.clear()
        self.update()

    def set_cut_markers(self, markers):
        """Đặt lại danh sách markers (thay thế hoàn toàn)."""
        self._cut_markers = sorted(markers)
        self.update()

    def get_cut_markers(self):
        return list(self._cut_markers)

    def _val_to_x(self, val):
        usable = self.width() - self.HANDLE_WIDTH * 2
        if self._max_val == self._min_val:
            return self.HANDLE_WIDTH
        ratio = (val - self._min_val) / (self._max_val - self._min_val)
        return int(self.HANDLE_WIDTH + ratio * usable)

    def _x_to_val(self, x):
        usable = self.width() - self.HANDLE_WIDTH * 2
        if usable <= 0:
            return self._min_val
        ratio = (x - self.HANDLE_WIDTH) / usable
        ratio = max(0.0, min(1.0, ratio))
        return int(self._min_val + ratio * (self._max_val - self._min_val))


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._handle_mouse_seek(event.x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._handle_mouse_seek(event.x())

    def _handle_mouse_seek(self, x):
        w = self.width()
        if w == 0 or self._duration_ms == 0:
            return
        ratio = max(0.0, min(1.0, x / w))
        pos_ms = int(ratio * self._duration_ms)
        self.seekRequested.emit(pos_ms)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        cy = h // 2

        # Track background
        track_y = cy - self.TRACK_HEIGHT // 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(60, 63, 70))
        p.drawRoundedRect(self.HANDLE_WIDTH, track_y,
                          w - self.HANDLE_WIDTH * 2, self.TRACK_HEIGHT,
                          4, 4)

        # Selected region (highlight)
        x_start = self._val_to_x(self._start)
        x_end = self._val_to_x(self._end)

        grad = QLinearGradient(x_start, track_y, x_end, track_y)
        grad.setColorAt(0, QColor(0, 180, 255, 180))
        grad.setColorAt(1, QColor(0, 220, 160, 180))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(x_start, track_y,
                          max(x_end - x_start, 2), self.TRACK_HEIGHT,
                          4, 4)

        # Playhead line
        x_play = self._val_to_x(self._playhead)
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        p.drawLine(x_play, cy - 14, x_play, cy + 14)

        # Handles
        for val, color in [(self._start, QColor(0, 180, 255)),
                           (self._end, QColor(0, 220, 160))]:
            hx = self._val_to_x(val)
            handle_rect = QRect(
                hx - self.HANDLE_WIDTH // 2,
                cy - self.HANDLE_HEIGHT // 2,
                self.HANDLE_WIDTH,
                self.HANDLE_HEIGHT,
            )
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(handle_rect, 4, 4)

            # Inner grip lines
            p.setPen(QPen(QColor(255, 255, 255, 160), 1))
            cx = handle_rect.center().x()
            for dy in [-4, 0, 4]:
                p.drawLine(cx - 2, cy + dy, cx + 2, cy + dy)

        # --- Cut markers (đường cắt đỏ) ---
        pen_marker = QPen(QColor(255, 80, 80, 220), 2)
        for mk in self._cut_markers:
            mx = self._val_to_x(mk)
            p.setPen(pen_marker)
            p.drawLine(mx, cy - 16, mx, cy + 16)
            # Tam giác nhỏ ở trên
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 80, 80, 200))
            tri = QPolygon([
                QPoint(mx - 4, cy - 16),
                QPoint(mx + 4, cy - 16),
                QPoint(mx, cy - 10),
            ])
            p.drawPolygon(tri)

        # Time labels
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        p.setPen(QColor(200, 200, 200))
        p.drawText(x_start - 20, h - 2, _fmt_time(self._start))
        p.drawText(x_end - 20, h - 2, _fmt_time(self._end))

        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x = event.x()
        x_start = self._val_to_x(self._start)
        x_end = self._val_to_x(self._end)

        # Check which handle is closer
        dist_start = abs(x - x_start)
        dist_end = abs(x - x_end)

        if dist_start < dist_end and dist_start < 20:
            self._dragging = "start"
        elif dist_end < 20:
            self._dragging = "end"
        elif dist_start < 20:
            self._dragging = "start"
        else:
            # Click trên track → di chuyển handle gần nhất
            if dist_start < dist_end:
                self._dragging = "start"
            else:
                self._dragging = "end"
            self._move_handle(x)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._move_handle(event.x())

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = None
            self.rangeChanged.emit(self._start, self._end)

    def _move_handle(self, x):
        val = self._x_to_val(x)
        if self._dragging == "start":
            self.set_start(val)
        elif self._dragging == "end":
            self.set_end(val)
        self.rangeChanged.emit(self._start, self._end)
        # Emit handle position cho video seek
        self.handleDragged.emit(val)


# ============================================================================
# Waveform Extract Worker — Trích xuất sóng âm thanh từ video
# ============================================================================

class WaveformExtractWorker(QThread):
    """Worker thread trích xuất audio waveform từ video bằng ffmpeg."""

    done = pyqtSignal(list)   # list[float] amplitudes (0.0 → 1.0)
    failed = pyqtSignal(str)

    SAMPLE_RATE = 8000        # 8kHz — đủ cho waveform display
    TARGET_POINTS = 2000      # Số điểm hiển thị

    def __init__(self, ffmpeg_path, video_path, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.video_path = video_path

    def run(self):
        try:
            cmd = [
                self.ffmpeg_path,
                "-i", self.video_path,
                "-ac", "1",               # mono
                "-ar", str(self.SAMPLE_RATE),
                "-f", "s16le",            # signed 16-bit little-endian PCM
                "-vn",                    # no video
                "pipe:1",
            ]
            kwargs = {"capture_output": True}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            proc = subprocess.run(cmd, **kwargs)
            if proc.returncode != 0:
                self.failed.emit("ffmpeg waveform extraction failed")
                return

            raw = proc.stdout
            if not raw:
                self.failed.emit("No audio data")
                return

            # Parse raw PCM s16le → absolute amplitudes
            n_samples = len(raw) // 2
            if n_samples == 0:
                self.failed.emit("Empty audio")
                return

            samples = struct.unpack(f"<{n_samples}h", raw)

            # Downsample to TARGET_POINTS by taking max absolute value per chunk
            chunk_size = max(1, n_samples // self.TARGET_POINTS)
            amplitudes = []
            for i in range(0, n_samples, chunk_size):
                chunk = samples[i:i + chunk_size]
                peak = max(abs(s) for s in chunk)
                amplitudes.append(peak / 32768.0)  # normalize to 0.0 → 1.0

            self.done.emit(amplitudes)
        except Exception as e:
            self.failed.emit(str(e))


# ============================================================================
# Waveform Widget — Hiển thị sóng âm thanh
# ============================================================================

class WaveformWidget(QWidget):
    seekRequested = pyqtSignal(int)

    """Widget hiển thị sóng âm thanh (audio waveform) trên timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._amplitudes = []      # list[float] 0.0 → 1.0
        self._playhead = 0         # 0.0 → 1.0 (tỷ lệ)
        self._duration_ms = 0
        self._cut_markers = []     # list[int] ms
        self._min_val = 0
        self._max_val = 1
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")

    def set_amplitudes(self, amps):
        self._amplitudes = amps
        self.update()

    def set_playhead_ms(self, ms):
        if self._duration_ms > 0:
            self._playhead = ms / self._duration_ms
        self.update()

    def set_duration(self, ms):
        self._duration_ms = ms
        self._max_val = ms

    def set_cut_markers(self, markers):
        self._cut_markers = list(markers)
        self.update()

    def clear(self):
        self._amplitudes = []
        self._cut_markers = []
        self.update()

    SILENCE_THRESHOLD = 0.02  # Ngưỡng xác định đoạn im lặng (0.0 → 1.0)


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._handle_mouse_seek(event.x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._handle_mouse_seek(event.x())

    def _handle_mouse_seek(self, x):
        w = self.width()
        if w == 0 or self._duration_ms == 0:
            return
        ratio = max(0.0, min(1.0, x / w))
        pos_ms = int(ratio * self._duration_ms)
        self.seekRequested.emit(pos_ms)

    def paintEvent(self, event):
        if not self._amplitudes:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        mid_y = h // 2
        n = len(self._amplitudes)

        if n == 0 or w == 0:
            p.end()
            return

        bar_w = max(1.0, w / n)

        # --- Vẽ vùng im lặng (nền đỏ mờ) để dễ nhận biết ---
        silence_start = None
        for i, amp in enumerate(self._amplitudes):
            is_silent = amp < self.SILENCE_THRESHOLD
            if is_silent and silence_start is None:
                silence_start = i
            elif not is_silent and silence_start is not None:
                # Vẽ vùng im lặng
                x1 = int(silence_start * w / n)
                x2 = int(i * w / n)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(180, 40, 40, 50))
                p.drawRect(x1, 0, x2 - x1, h)
                silence_start = None
        # Đoạn cuối nếu còn im lặng
        if silence_start is not None:
            x1 = int(silence_start * w / n)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(180, 40, 40, 50))
            p.drawRect(x1, 0, w - x1, h)

        # --- Vẽ đường baseline (trung tâm) ---
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawLine(0, mid_y, w, mid_y)

        # --- Vẽ sóng âm thanh với màu phân biệt ---
        for i, amp in enumerate(self._amplitudes):
            x = int(i * w / n)
            bar_h = max(1, int(amp * (mid_y - 2)))

            if amp < self.SILENCE_THRESHOLD:
                # Đoạn im lặng — chỉ vẽ đường mỏng màu đỏ/cam mờ
                color = QColor(255, 80, 50, 60)
            else:
                # Đoạn có âm thanh — gradient xanh lục → xanh dương theo cường độ
                intensity = min(255, int(amp * 300))
                color = QColor(0, 180 + intensity // 4, 100 + intensity // 2, 180)

            p.setPen(Qt.NoPen)
            p.setBrush(color)

            # Vẽ đối xứng trên/dưới
            p.drawRect(x, mid_y - bar_h, max(1, int(bar_w)), bar_h * 2)

        # Playhead
        if self._playhead > 0:
            px = int(self._playhead * w)
            p.setPen(QPen(QColor(255, 255, 255, 200), 2))
            p.drawLine(px, 0, px, h)

        # Cut markers
        if self._duration_ms > 0:
            pen_marker = QPen(QColor(255, 80, 80, 200), 2)
            for mk_ms in self._cut_markers:
                mx = int(mk_ms / self._duration_ms * w)
                p.setPen(pen_marker)
                p.drawLine(mx, 0, mx, h)

        p.end()


# ============================================================================
# Trim Export Worker — Thread cắt video
# ============================================================================

class TrimExportWorker(QThread):
    """Worker thread export các segment bằng ffmpeg."""

    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # (current, total)
    done = pyqtSignal(str)           # output directory
    failed = pyqtSignal(str)

    def __init__(self, ffmpeg_path, video_file, segments, out_dir, parent=None):
        """
        segments: list of (start_sec, end_sec, label)
        """
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.video_file = video_file
        self.segments = segments
        self.out_dir = out_dir
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            total = len(self.segments)
            stem = Path(self.video_file).stem
            ext = Path(self.video_file).suffix or ".mp4"

            for idx, (start_s, end_s, label) in enumerate(self.segments, 1):
                if self._stop:
                    self.log.emit("⛔ Export bị dừng bởi người dùng")
                    break

                duration = end_s - start_s
                out_name = f"{stem}_part{idx}_{_fmt_secs(start_s)}-{_fmt_secs(end_s)}{ext}"
                out_name = out_name.replace(":", "-")
                out_path = os.path.join(self.out_dir, out_name)

                self.log.emit(f"✂️ [{idx}/{total}] {label}: {_fmt_secs(start_s)} → {_fmt_secs(end_s)}")

                cmd = [
                    self.ffmpeg_path, "-y",
                    "-ss", str(start_s),
                    "-i", self.video_file,
                    "-t", str(duration),
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    out_path,
                ]

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **_subprocess_no_console_kwargs(),
                )

                if proc.returncode != 0:
                    self.log.emit(f"⚠ Lỗi ffmpeg part {idx}: {proc.stderr[:200]}")
                else:
                    self.log.emit(f"✅ Đã lưu: {out_name}")

                self.progress.emit(idx, total)

            self.done.emit(self.out_dir)
        except Exception as e:
            self.failed.emit(str(e))


# ============================================================================
# Video Trim Widget — Widget chính
# ============================================================================

class ExpandedVideoDialog(QDialog):
    """Cửa sổ lớn chứa toàn bộ VideoTrimWidget — xem trước + cắt như bình thường."""

    def __init__(self, video_path, ffmpeg_path=None, start_pos_ms=0, duration_ms=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎬 Xem trước & Cắt trực tiếp")
        self.setMinimumSize(800, 500)
        self.resize(1024, 700)
        self.setStyleSheet("""
            QDialog { background: #0d0d1a; }
        """)

        self._start_pos = start_pos_ms
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Thanh tiêu đề với nút fullscreen
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)
        title_label = QLabel("🎬 Xem trước & Cắt trực tiếp")
        title_label.setStyleSheet("color: #ccc; font-size: 14px; font-weight: bold;")
        title_bar.addWidget(title_label)
        title_bar.addStretch()

        self._btn_fullscreen = QPushButton("⬜ Full màn hình")
        self._btn_fullscreen.setFixedHeight(28)
        self._btn_fullscreen.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px;
                font-size: 12px;
                padding: 0 12px;
                color: #ccc;
            }
            QPushButton:hover { background: rgba(255,255,255,0.18); }
        """)
        self._btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        title_bar.addWidget(self._btn_fullscreen)
        layout.addLayout(title_bar)

        # Tạo 1 VideoTrimWidget mới bên trong dialog
        self.trim_widget = VideoTrimWidget(ffmpeg_path=ffmpeg_path, parent=self)
        layout.addWidget(self.trim_widget)

        # Load video sau khi dialog hiện
        self._video_path = video_path
        QTimer.singleShot(200, self._load_video)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._btn_fullscreen.setText("⬜ Full màn hình")
        else:
            self.showFullScreen()
            self._btn_fullscreen.setText("🔲 Thu nhỏ")

    def _load_video(self):
        if self._video_path:
            self.trim_widget.load_video(self._video_path)
            # Seek đến vị trí ban đầu sau khi video load xong
            if self._start_pos > 0:
                QTimer.singleShot(800, lambda: self._seek_start())

    def _seek_start(self):
        tw = self.trim_widget
        if tw._use_mpv and tw.mpv_player:
            try:
                tw.mpv_player.seek(self._start_pos / 1000.0, 'absolute')
            except Exception:
                pass
        elif tw.player:
            tw.player.setPosition(self._start_pos)

    def keyPressEvent(self, event):
        """Bắt phím Space để play/pause trong dialog."""
        if event.key() == Qt.Key_Space:
            self.trim_widget._toggle_play()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.trim_widget.cleanup()
        super().closeEvent(event)


class VideoTrimWidget(QWidget):
    """
    Widget để xem trước video và cắt trực tiếp bằng giao diện visual.
    """

    # Signal gửi log ra MainWindow
    log = pyqtSignal(str)

    def __init__(self, ffmpeg_path=None, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self._duration_ms = 0
        self._video_path = None
        self._export_worker = None
        self._waveform_worker = None

        self._build_ui()
        self._connect_signals()

    def set_ffmpeg_path(self, path):
        self.ffmpeg_path = path

    def _auto_detect_ffmpeg(self):
        """Tự động tìm ffmpeg."""
        import shutil
        _ffmpeg_name = "ffmpeg" if sys.platform == "darwin" else "ffmpeg.exe"
        # 1. Thư mục chứa script hiện tại
        app_dir = Path(__file__).parent
        for candidate in [
            app_dir / _ffmpeg_name,
            Path.cwd() / _ffmpeg_name,
        ]:
            if candidate.exists():
                self.ffmpeg_path = str(candidate)
                return
        # 2. Trong PATH
        found = shutil.which("ffmpeg")
        if found:
            self.ffmpeg_path = found



    def _on_zoom_changed(self, value):
        factor = value / 10.0
        # Determine base width securely. It is 800 roughly.
        new_width = int(800 * factor)
        self.timeline_container.setMinimumWidth(new_width)
        
        # Center the scrollbar on the current playhead
        if self._duration_ms > 0:
            ratio = self.seek_slider.value() / self._duration_ms
            h_bar = self.timeline_scroll.horizontalScrollBar()
            target_pos = int(ratio * h_bar.maximum())
            h_bar.setValue(target_pos)

    def eventFilter(self, source, event):
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.Wheel and source in (self.timeline_scroll.viewport(), self.timeline_scroll):
            delta = event.angleDelta().y()
            step = 5 if delta > 0 else -5
            current = self.zoom_slider.value()
            self.zoom_slider.setValue(max(10, current + step))
            return True
        return super().eventFilter(source, event)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # === File Picker Row ===
        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        file_row.addWidget(QLabel("🎬 Video:"))
        self.trim_video_edit = QLineEdit()
        self.trim_video_edit.setPlaceholderText("Chọn video để xem trước và cắt...")
        self.trim_video_edit.setReadOnly(True)
        file_row.addWidget(self.trim_video_edit, 1)
        self.btn_trim_pick = QPushButton("Chọn...")
        self.btn_trim_pick.setFixedWidth(80)
        self.btn_trim_pick.setFixedHeight(28)
        file_row.addWidget(self.btn_trim_pick)
        layout.addLayout(file_row)

        # === Remove Scroll Area to keep video fixed size ===
        scroll_layout = layout

        # === Video Player Area ===
        player_frame = QFrame()
        player_frame.setObjectName("videoPlayerFrame")
        player_frame.setStyleSheet("""
            #videoPlayerFrame {
                background: #1a1a2e;
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.08);
            }
        """)
        player_layout = QVBoxLayout(player_frame)
        player_layout.setContentsMargins(6, 6, 6, 6)
        player_layout.setSpacing(4)

        if HAS_MPV:
            # --- mpv Player (phát mọi format, tự đóng gói libmpv-2.dll) ---
            self.video_widget = QFrame()
            self.video_widget.setMinimumHeight(360)
            self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.video_widget.setStyleSheet("background: #000; border-radius: 6px;")
            player_layout.addWidget(self.video_widget)
            self.mpv_player = None  # Sẽ khởi tạo khi widget hiển thị
            self.player = None
            self._use_mpv = True

            # Timer poll vị trí phát (mpv không dùng Qt signal)
            self._mpv_timer = QTimer(self)
            self._mpv_timer.setInterval(200)
            self._mpv_timer.timeout.connect(self._mpv_poll_position)
        elif HAS_MULTIMEDIA:
            # --- QMediaPlayer fallback ---
            self._use_mpv = False
            self.mpv_player = None
            self.player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumHeight(360)
            self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.video_widget.setStyleSheet("background: #000; border-radius: 6px;")
            # KHÔNG gọi setVideoOutput ở đây, đợi đến khi load video
            # Tránh AVFoundation init sớm gây crash trên macOS
            self._media_output_connected = False
            player_layout.addWidget(self.video_widget)
        else:
            self._use_mpv = False
            self.mpv_player = None
            self.player = None
            self.video_widget = None
            fallback = QLabel("⚠ Không tìm thấy libmpv-2.dll.\n"
                              "Đặt file libmpv-2.dll cạnh app để xem trước video.\n\n"
                              "💡 Bạn vẫn có thể cắt video bình thường,\n"
                              "chỉ không xem trước được thôi.")
            fallback.setAlignment(Qt.AlignCenter)
            fallback.setStyleSheet("color: #ff9800; font-size: 13px; padding: 20px;")
            fallback.setMinimumHeight(100)
            fallback.setTextFormat(Qt.PlainText)
            player_layout.addWidget(fallback)

        # === Timeline Toolbar (CapCut Style) ===
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(36, 36)
        self.btn_play.setObjectName("trimPlayBtn")
        self.btn_play.setStyleSheet("""
            #trimPlayBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #00b4ff, stop:1 #00dc96);
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 16px;
                font-weight: bold;
            }
            #trimPlayBtn:hover { background: #00d4ff; }
        """)
        controls.addWidget(self.btn_play)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: #ccc; font-size: 12px; font-family: monospace;")
        controls.addWidget(self.time_label)

        controls.addSpacing(20)

        # --- Segment Buttons (Moved here to form a unified toolbar) ---
        # --- Unified Central Buttons ---
        self.btn_clear_segments = QPushButton("🧹 Xóa hết")
        self.btn_clear_segments.setFixedHeight(28)
        self.btn_clear_segments.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                color: #ccc;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                padding: 2px 12px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.15); }
        """)

        # Nút Tách (Ctrl+B)
        self.btn_cut = QPushButton("✂ Tách")
        self.btn_cut.setToolTip("Tách video tại Playhead (Ctrl+B)")
        self.btn_cut.setFixedHeight(28)
        self.btn_cut.setStyleSheet("""
            QPushButton {
                background: rgba(255, 80, 80, 0.15);
                border: 1px solid rgba(255, 80, 80, 0.3);
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 12px;
                color: #ff5050;
            }
            QPushButton:hover {
                background: rgba(255, 80, 80, 0.3);
            }
        """)
        
        controls.addWidget(self.btn_clear_segments)
        controls.addWidget(self.btn_cut)

        controls.addStretch(1)
        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("🔍 Zoom:"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(10, 400) # 1.0x to 50.0x
        self.zoom_slider.setValue(10)
        self.zoom_slider.setFixedWidth(180)
        self.zoom_slider.setToolTip("Phóng to / Thu nhỏ timeline")
        zoom_layout.addWidget(self.zoom_slider)
        controls.addLayout(zoom_layout)
        
        # We still need to create the widgets, just not add them to controls.
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setObjectName("seekSlider")
        self.seek_slider.hide()  # Unified timeline request
        self.seek_slider.setStyleSheet("""
            #seekSlider::groove:horizontal {
                height: 5px;
                background: #3c3f46;
                border-radius: 2px;
            }
            #seekSlider::handle:horizontal {
                background: #00b4ff;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            #seekSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00b4ff, stop:1 #00dc96);
                border-radius: 2px;
            }
        """)
        self.waveform_widget = WaveformWidget()
        self.range_slider = RangeSlider()

        

        # Volume
        self.btn_mute = QPushButton(" 🔊 ")
        self.btn_mute.setFixedHeight(28)
        self.btn_mute.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 6px;
                font-size: 14px;
                padding: 0 6px;
                color: #ccc;
            }
            QPushButton:hover { background: rgba(255,255,255,0.15); }
        """)
        controls.addWidget(self.btn_mute)

        

        player_layout.addLayout(controls)

        scroll_layout.addWidget(player_frame)

        # === COMPREHENSIVE ZOOMABLE TIMELINE ===
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.timeline_scroll.setFrameShape(QFrame.NoFrame)
        self.timeline_scroll.setStyleSheet("""
            QScrollArea { background: rgba(0, 0, 0, 0.2); border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
            QScrollBar:horizontal {
                background: rgba(255,255,255,0.03);
                height: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(255,255,255,0.15);
                border-radius: 5px;
                min-width: 40px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(255,255,255,0.25);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0; background: none;
            }
        """)

        self.timeline_container = QWidget()
        self.timeline_container.setStyleSheet("background: transparent;")
        time_col = QVBoxLayout(self.timeline_container)
        time_col.setContentsMargins(10, 10, 10, 10)
        time_col.setSpacing(2)
        
        # Add elements to timeline container
        time_col.addWidget(self.seek_slider)
        time_col.addWidget(self.waveform_widget)
        time_col.addWidget(self.range_slider)
        
        self.timeline_scroll.setWidget(self.timeline_container)
        self.timeline_scroll.setFixedHeight(150)
        self.timeline_scroll.viewport().installEventFilter(self)
        self.timeline_scroll.installEventFilter(self)
        
        scroll_layout.addWidget(self.timeline_scroll)

        # === Range Slider (Trim Selection) ===
        trim_section = QFrame()
        trim_section.setObjectName("trimSection")
        trim_section.setStyleSheet("""
            #trimSection {
                background: rgba(255,255,255,0.03);
                border-radius: 8px;
                padding: 8px;
            }
        """)
        trim_layout = QVBoxLayout(trim_section)
        trim_layout.setContentsMargins(12, 8, 12, 8)
        trim_layout.setSpacing(6)

        trim_header = QHBoxLayout()
        trim_header.addWidget(QLabel("🎚️ Vùng cắt:"))
        self.trim_info_label = QLabel("Kéo thả 2 đầu để chọn vùng cắt")
        self.trim_info_label.setStyleSheet("color: #888; font-size: 11px;")
        trim_header.addWidget(self.trim_info_label, 1)
        trim_layout.addLayout(trim_header)



        # Trim time display
        trim_times = QHBoxLayout()
        self.trim_start_label = QLabel("Bắt đầu: 00:00")
        self.trim_start_label.setStyleSheet("color: #00b4ff; font-size: 12px; font-family: monospace;")
        self.trim_end_label = QLabel("Kết thúc: 00:00")
        self.trim_end_label.setStyleSheet("color: #00dc96; font-size: 12px; font-family: monospace;")
        self.trim_duration_label = QLabel("Thời lượng: 00:00")
        self.trim_duration_label.setStyleSheet("color: #ddd; font-size: 12px; font-family: monospace;")
        trim_times.addWidget(self.trim_start_label)
        trim_times.addStretch(1)
        trim_times.addWidget(self.trim_duration_label)
        trim_times.addStretch(1)
        trim_times.addWidget(self.trim_end_label)
        trim_layout.addLayout(trim_times)

        scroll_layout.addWidget(trim_section)

        # === Segment List + Actions ===
        segment_section = QFrame()
        segment_section.setObjectName("segmentSection")
        segment_section.setStyleSheet("""
            #segmentSection {
                background: rgba(255,255,255,0.03);
                border-radius: 8px;
            }
        """)
        seg_layout = QVBoxLayout(segment_section)
        seg_layout.setContentsMargins(12, 8, 12, 8)
        seg_layout.setSpacing(6)

        seg_header = QHBoxLayout()
        seg_header.addWidget(QLabel("📋 Danh sách đoạn cắt:"))
        seg_header.addStretch(1)

        # --- Removed old List Actions (Moved to Top Toolbar) ---

        seg_layout.addLayout(seg_header)

        self.segment_list = QListWidget()
        self.segment_list.setMinimumHeight(80)
        self.segment_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.segment_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.segment_list.setStyleSheet("""
            QListWidget {
                background: rgba(0,0,0,0.2);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 6px;
                color: #ddd;
                font-family: monospace;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid rgba(255,255,255,0.05);
            }
            QListWidget::item:selected {
                background: rgba(0, 180, 255, 0.2);
            }
        """)
        seg_layout.addWidget(self.segment_list)

        scroll_layout.addWidget(segment_section)

        # === Export Row ===
        export_row = QHBoxLayout()
        export_row.setSpacing(10)

        export_row.addWidget(QLabel("📁 Thư mục lưu:"))
        self.export_dir_label = QLabel("(chưa chọn)")
        self.export_dir_label.setStyleSheet("color: #888; font-size: 12px;")
        export_row.addWidget(self.export_dir_label, 1)

        self.btn_pick_export_dir = QPushButton("Chọn...")
        self.btn_pick_export_dir.setFixedWidth(80)
        self.btn_pick_export_dir.setFixedHeight(30)
        export_row.addWidget(self.btn_pick_export_dir)

        self.btn_export = QPushButton("✂️ Export đoạn đã chọn")
        self.btn_export.setFixedHeight(36)
        self.btn_export.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00b4ff, stop:1 #00dc96);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00d4ff, stop:1 #00fca8);
            }
            QPushButton:disabled {
                background: #555;
                color: #999;
            }
        """)
        export_row.addWidget(self.btn_export)

        self.btn_export_all = QPushButton("📦 Export tất cả")
        self.btn_export_all.setFixedHeight(36)
        self.btn_export_all.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff8c00, stop:1 #ff5722);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffa040, stop:1 #ff7043);
            }
            QPushButton:disabled {
                background: #555;
                color: #999;
            }
        """)
        export_row.addWidget(self.btn_export_all)

        scroll_layout.addLayout(export_row)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,0.05);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00b4ff, stop:1 #00dc96);
                border-radius: 3px;
            }
        """)
        scroll_layout.addWidget(self.progress_bar)

        # Internal state
        self._segments = []  # list of (start_sec, end_sec)
        self._export_dir = ""
        self._is_muted = False

    def _connect_signals(self):
        self.btn_trim_pick.clicked.connect(self._pick_video)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_mute.clicked.connect(self._toggle_mute)
        if hasattr(self, 'btn_cut'):
            self.btn_cut.clicked.connect(self._cut_at_playhead)

        if self.player:  # QMediaPlayer fallback
            self.player.positionChanged.connect(self._on_position_changed)
            self.player.durationChanged.connect(self._on_duration_changed)
            self.player.stateChanged.connect(self._on_state_changed)
            self.player.error.connect(self._on_player_error)

        self.seek_slider.sliderMoved.connect(self._on_seek)
        self.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.waveform_widget.seekRequested.connect(self._on_seek)

        self.range_slider.rangeChanged.connect(self._on_range_changed)
        self.range_slider.handleDragged.connect(self._on_handle_dragged)

        
        

        if hasattr(self, 'zoom_slider'):
            self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        if hasattr(self, 'waveform_widget'):
            self.waveform_widget.seekRequested.connect(self._on_seek)
            
        # Ngăn các nút bấm cướp lấy phím Space
        for btn in self.findChildren(QPushButton):
            btn.setFocusPolicy(Qt.NoFocus)

        self.btn_clear_segments.clicked.connect(self._clear_segments)

        self.btn_pick_export_dir.clicked.connect(self._pick_export_dir)
        self.btn_export.clicked.connect(self._start_export)
        self.btn_export_all.clicked.connect(self._start_export_all)

        self._seeking = False
        self._mpv_is_playing = False  # Track mpv play state

        # --- Phím tắt Space: Play/Pause (WindowShortcut để hoạt động ở mọi nơi) ---
        self.shortcut_space = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.shortcut_space.setContext(Qt.WindowShortcut)
        self.shortcut_space.activated.connect(self._toggle_play)

        # --- Phím tắt Ctrl+B: cắt tại playhead ---
        sc_cut = QShortcut(QKeySequence("Ctrl+B"), self)
        sc_cut.activated.connect(self._cut_at_playhead)

    # ==================== Public API ====================

    def _pick_video(self):
        """Mở dialog chọn file video."""
        # Dùng top-level window làm parent cho dialog (tránh bị ẩn sau cửa sổ)
        parent_win = self.window() or self
        f, _ = QFileDialog.getOpenFileName(
            parent_win, "Chọn file Video", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.m4s);;All Files (*)",
        )
        if f:
            self.load_video(f)

    def load_video(self, path):
        """Load video vào player."""
        if not path or not os.path.exists(path):
            return

        # Auto-detect ffmpeg nếu chưa được set
        if not self.ffmpeg_path:
            self._auto_detect_ffmpeg()

        self._video_path = path
        self.trim_video_edit.setText(path)

        if self._use_mpv:
            # --- mpv: khởi tạo player và load file ---
            self._init_mpv_player()
            if self.mpv_player:
                self.mpv_player.command('loadfile', path)
                # Pause ngay để hiện frame đầu
                self.mpv_player.pause = True
                self._mpv_is_playing = False
                QTimer.singleShot(500, self._mpv_init_duration)
        elif self.player:
            # QMediaPlayer fallback
            # Kết nối video output lần đầu (deferred để tránh crash AVFoundation)
            if hasattr(self, '_media_output_connected') and not self._media_output_connected:
                self.player.setVideoOutput(self.video_widget)
                self._media_output_connected = True
            url = QUrl.fromLocalFile(str(path))
            self.player.setMedia(QMediaContent(url))
            self.player.pause()
            QTimer.singleShot(300, lambda: self.player.pause())

        # Dùng ffprobe lấy duration (fallback khi player không detect được)
        probe_dur = self._ffprobe_duration(path)
        if probe_dur and probe_dur > 0:
            self._ffprobe_duration_ms = int(probe_dur * 1000)
            QTimer.singleShot(600, self._apply_ffprobe_duration)

        # Tự động đặt export dir = folder chứa video
        if not self._export_dir:
            default_dir = str(Path(path).parent / "Trim_Parts")
            self._export_dir = default_dir
            self.export_dir_label.setText(default_dir)

        self.log.emit(f"🎬 Đã tải video: {Path(path).name}")

        # --- Trích xuất waveform ---
        self._extract_waveform(path)

    def _init_mpv_player(self):
        """Khởi tạo mpv player, gắn vào video_widget."""
        if self.mpv_player:
            return  # Đã khởi tạo rồi
        try:
            wid = int(self.video_widget.winId())

            # macOS: vo='gpu' embedded trong widget hay bị freeze
            # Dùng danh sách backend ưu tiên theo platform
            import sys as _sys
            if _sys.platform == "darwin":
                # macOS: thử không embed (mở cửa sổ mpv riêng) nếu embed bị lỗi
                try:
                    self.mpv_player = mpv.MPV(
                        wid=str(wid),
                        vo='libmpv',
                        hwdec='auto',
                        keep_open='yes',
                        osd_level=0,
                        input_default_bindings=False,
                        input_vo_keyboard=False,
                        log_handler=lambda *a: None,
                    )
                except Exception:
                    # Fallback: không embed, mpv sẽ mở cửa sổ riêng
                    self.mpv_player = mpv.MPV(
                        hwdec='auto',
                        keep_open='yes',
                        osd_level=0,
                        input_default_bindings=False,
                        input_vo_keyboard=False,
                        log_handler=lambda *a: None,
                    )
            else:
                self.mpv_player = mpv.MPV(
                    wid=str(wid),
                    vo='gpu',
                    hwdec='auto',
                    keep_open='yes',
                    osd_level=0,
                    input_default_bindings=False,
                    input_vo_keyboard=False,
                    log_handler=lambda *a: None,
                )
        except Exception as e:
            self.log.emit(f"⚠ Không thể khởi tạo mpv: {e}")
            self.mpv_player = None

    def _mpv_init_duration(self):
        """Lấy duration từ mpv sau khi load file."""
        if not self.mpv_player:
            return
        try:
            dur_s = self.mpv_player.duration
            if dur_s and dur_s > 0:
                dur = int(dur_s * 1000)
                self._duration_ms = dur
                self.seek_slider.setMaximum(dur)
                self.range_slider.set_range(0, dur)
                self.waveform_widget.set_duration(dur)
                self.time_label.setText(f"00:00 / {_fmt_time(dur)}")
                self._update_trim_labels()
                self.log.emit(f"📐 Duration (mpv): {_fmt_time(dur)}")
        except Exception:
            pass

    def _apply_ffprobe_duration(self):
        """Áp dụng duration từ ffprobe nếu QMediaPlayer chưa detect được."""
        if self._duration_ms <= 0 and hasattr(self, '_ffprobe_duration_ms'):
            dur = self._ffprobe_duration_ms
            self._duration_ms = dur
            self.seek_slider.setMaximum(dur)
            self.range_slider.set_range(0, dur)
            self.waveform_widget.set_duration(dur)
            self.time_label.setText(f"00:00 / {_fmt_time(dur)}")
            self._update_trim_labels()
            self.log.emit(f"📐 Duration (ffprobe): {_fmt_time(dur)}")

    def _extract_waveform(self, path):
        """Bắt đầu trích xuất waveform từ video (trong background thread)."""
        if not self.ffmpeg_path:
            return
        # Dừng worker cũ nếu còn
        if self._waveform_worker and self._waveform_worker.isRunning():
            self._waveform_worker.terminate()
            self._waveform_worker.wait(500)

        self.waveform_widget.clear()
        self._waveform_worker = WaveformExtractWorker(self.ffmpeg_path, path)
        self._waveform_worker.done.connect(self._on_waveform_done)
        self._waveform_worker.failed.connect(self._on_waveform_failed)
        self._waveform_worker.start()
        self.log.emit("🌊 Đang trích xuất sóng âm thanh...")

    def _on_waveform_done(self, amplitudes):
        """Waveform đã trích xuất xong."""
        self.waveform_widget.set_duration(self._duration_ms)
        self.waveform_widget.set_amplitudes(amplitudes)
        self.log.emit(f"✅ Sóng âm thanh: {len(amplitudes)} điểm")

    def _on_waveform_failed(self, err):
        """Không trích xuất được waveform."""
        self.log.emit(f"⚠ Không thể trích xuất sóng âm thanh: {err}")

    def _ffprobe_duration(self, path):
        """Lấy duration của video bằng ffprobe/ffmpeg."""
        ffmpeg = self.ffmpeg_path
        if not ffmpeg:
            return None
        # Thử ffprobe cùng thư mục với ffmpeg
        ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
        probe_cmd = ffprobe if os.path.exists(ffprobe) else ffmpeg

        try:
            if probe_cmd == ffmpeg:
                # Dùng ffmpeg -i để lấy duration
                cmd = [ffmpeg, "-i", path]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    **_subprocess_no_console_kwargs(),
                )
                # ffmpeg -i sẽ exit lỗi nhưng stderr chứa info
                import re
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", proc.stderr)
                if m:
                    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    return h * 3600 + mi * 60 + s + ms / 100.0
            else:
                cmd = [
                    probe_cmd, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    **_subprocess_no_console_kwargs(),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return float(proc.stdout.strip())
        except Exception:
            pass
        return None

    # ==================== Player Controls ====================

    def _toggle_play(self):
        if self._use_mpv and self.mpv_player:
            if self._mpv_is_playing:
                self.mpv_player.pause = True
                self._mpv_timer.stop()
                self._mpv_is_playing = False
                self.btn_play.setText("▶")
            else:
                self.mpv_player.pause = False
                self._mpv_timer.start()
                self._mpv_is_playing = True
                self.btn_play.setText("⏸")
        elif self.player:
            if self.player.state() == QMediaPlayer.PlayingState:
                self.player.pause()
            else:
                self.player.play()

    def _toggle_mute(self):
        self._is_muted = not self._is_muted
        if self._use_mpv and self.mpv_player:
            self.mpv_player.mute = self._is_muted
        elif self.player:
            self.player.setMuted(self._is_muted)
        self.btn_mute.setText("🔇" if self._is_muted else "🔊")

    # --- mpv position polling ---
    def _mpv_poll_position(self):
        """Timer callback: poll mpv position và cập nhật UI."""
        if not self.mpv_player:
            return
        try:
            pos_s = self.mpv_player.time_pos  # seconds (float)
            dur_s = self.mpv_player.duration
        except Exception:
            return
        if pos_s is None or pos_s < 0:
            pos_s = 0
        pos = int(pos_s * 1000)
        if dur_s and dur_s > 0:
            dur = int(dur_s * 1000)
            if self._duration_ms != dur:
                self._duration_ms = dur
                self.seek_slider.setMaximum(dur)
                self.range_slider.set_range(0, dur)
                self.waveform_widget.set_duration(dur)
        if not self._seeking:
            self.seek_slider.setValue(pos)
        self.range_slider.set_playhead(pos)
        self.waveform_widget.set_playhead_ms(pos)
        self.time_label.setText(f"{_fmt_time(pos)} / {_fmt_time(self._duration_ms)}")
        # Kiểm tra nếu hết video (eof-reached)
        try:
            eof = self.mpv_player.eof_reached
            if eof:
                self._mpv_timer.stop()
                self._mpv_is_playing = False
                self.btn_play.setText("▶")
        except Exception:
            pass

    # --- QMediaPlayer callbacks (fallback) ---
    def _on_position_changed(self, position):
        if not self._seeking:
            self.seek_slider.setValue(position)
        self.range_slider.set_playhead(position)
        self.waveform_widget.set_playhead_ms(position)
        self.time_label.setText(
            f"{_fmt_time(position)} / {_fmt_time(self._duration_ms)}"
        )

    def _on_duration_changed(self, duration):
        self._duration_ms = duration
        self.seek_slider.setMaximum(duration)
        self.range_slider.set_range(0, duration)
        self.waveform_widget.set_duration(duration)
        self.time_label.setText(f"00:00 / {_fmt_time(duration)}")
        self._update_trim_labels()

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self.btn_play.setText("⏸")
        else:
            self.btn_play.setText("▶")

    def _on_player_error(self):
        if self.player:
            err = self.player.errorString()
            self.log.emit(f"⚠ Lỗi phát video: {err}")

    # --- Seek ---
    def _on_seek(self, position):
        if self._use_mpv and self.mpv_player:
            try:
                self.mpv_player.seek(position / 1000.0, 'absolute')
            except Exception:
                pass
        elif self.player:
            self.player.setPosition(position)

    def _on_seek_pressed(self):
        self._seeking = True

    def _on_seek_released(self):
        self._seeking = False
        val = self.seek_slider.value()
        if self._use_mpv and self.mpv_player:
            try:
                self.mpv_player.seek(val / 1000.0, 'absolute')
            except Exception:
                pass
        elif self.player:
            self.player.setPosition(val)

    # ==================== Range Slider ====================

    def _on_range_changed(self, start_ms, end_ms):
        self._update_trim_labels()

    def _on_handle_dragged(self, ms):
        """Khi user kéo handle trên range slider, video seek theo (không phát video)."""
        if self._use_mpv and self.mpv_player:
            try:
                self.mpv_player.seek(ms / 1000.0, 'absolute')
            except Exception:
                pass
        elif self.player:
            self.player.setPosition(ms)

    def _cut_at_playhead(self):
        """Ctrl+B: đặt marker cắt tại vị trí playhead hiện tại."""
        if self._duration_ms <= 0:
            return
        # Lấy vị trí hiện tại
        if self._use_mpv and self.mpv_player:
            try:
                pos_s = self.mpv_player.time_pos
                pos = int(pos_s * 1000) if pos_s else 0
            except Exception:
                pos = self.seek_slider.value()
        elif self.player:
            pos = self.player.position()
        else:
            pos = self.seek_slider.value()
        if pos is None or pos <= 0:
            return

        self.range_slider.add_cut_marker(pos)

        # Auto tạo segment từ các markers
        self._auto_segments_from_markers()
        self.log.emit(f"✂ Cắt tại {_fmt_time(pos)}  (Ctrl+B)")

    def _auto_segments_from_markers(self):
        """Từ danh sách cut markers, tự động tạo segment list."""
        markers = self.range_slider.get_cut_markers()
        if not markers:
            return

        # Tạo các segment từ 0 → marker1, marker1 → marker2, ... → end
        points = [0] + markers + [self._duration_ms]
        self._segments.clear()  # Sync với self._segments
        self.segment_list.clear()

        for i in range(len(points) - 1):
            s_ms = points[i]
            e_ms = points[i + 1]
            if e_ms - s_ms < 500:  # Bỏ qua đoạn < 0.5s
                continue
            s_s = s_ms / 1000.0
            e_s = e_ms / 1000.0
            self._segments.append((s_s, e_s))  # Sync vào internal list

        self._refresh_segment_list()
        # Sync waveform markers
        self.waveform_widget.set_cut_markers(markers)
        self.log.emit(f"📄 Tạo {len(self._segments)} đoạn từ {len(markers)} điểm cắt")

    def _update_trim_labels(self):
        start_ms = self.range_slider.start_val()
        end_ms = self.range_slider.end_val()
        dur_ms = end_ms - start_ms

        self.trim_start_label.setText(f"Bắt đầu: {_fmt_time(start_ms)}")
        self.trim_end_label.setText(f"Kết thúc: {_fmt_time(end_ms)}")
        self.trim_duration_label.setText(f"Thời lượng: {_fmt_time(dur_ms)}")
        self.trim_info_label.setText(
            f"Đã chọn {_fmt_time(dur_ms)} "
            f"({_fmt_time(start_ms)} → {_fmt_time(end_ms)})"
        )

    # ==================== Segment Management ====================

    def _add_segment(self):
        if self._duration_ms <= 0:
            QMessageBox.information(self, "Chưa có video", "Vui lòng chọn video trước.")
            return

        start_s = self.range_slider.start_val() / 1000.0
        end_s = self.range_slider.end_val() / 1000.0

        if end_s - start_s < 0.5:
            QMessageBox.warning(self, "Đoạn quá ngắn", "Đoạn cắt phải dài hơn 0.5 giây.")
            return

        self._segments.append((start_s, end_s))
        self._refresh_segment_list()
        self.log.emit(f"➕ Đã thêm segment: {_fmt_secs(start_s)} → {_fmt_secs(end_s)}")

    def _add_all(self):
        if self._duration_ms <= 0:
            QMessageBox.information(self, "Chưa có video", "Vui lòng chọn video trước.")
            return

        self._segments.clear()
        total_s = self._duration_ms / 1000.0
        self._segments.append((0, total_s))
        self._refresh_segment_list()
        self.log.emit(f"📋 Đã chọn toàn bộ video: 00:00 → {_fmt_secs(total_s)}")

    def _remove_segment(self):
        selected = self.segment_list.selectedItems()
        if not selected:
            return
        # Xóa từ cuối để không lệch index
        indices = sorted([self.segment_list.row(item) for item in selected], reverse=True)
        for idx in indices:
            if 0 <= idx < len(self._segments):
                self._segments.pop(idx)
        self._refresh_segment_list()
        # Sync lại cut markers trên timeline từ segments còn lại
        self._sync_markers_from_segments()

    def _sync_markers_from_segments(self):
        """Tính lại cut markers trên timeline từ danh sách segments còn lại."""
        if not self._segments:
            self.range_slider.clear_cut_markers()
            return
        # Lấy tất cả boundary points từ segments (trừ 0 và duration)
        boundary_set = set()
        for s_s, e_s in self._segments:
            boundary_set.add(int(s_s * 1000))
            boundary_set.add(int(e_s * 1000))
        # Loại bỏ điểm đầu (0) và điểm cuối (duration)
        boundary_set.discard(0)
        boundary_set.discard(self._duration_ms)
        self.range_slider.set_cut_markers(list(boundary_set))
        self.waveform_widget.set_cut_markers(list(boundary_set))

    def _clear_segments(self):
        self._segments.clear()
        self.range_slider.clear_cut_markers()  # Xóa cả markers trên timeline
        self.waveform_widget.set_cut_markers([])  # Xóa markers trên waveform
        self._refresh_segment_list()

    def _refresh_segment_list(self):
        self.segment_list.clear()
        for idx, (start_s, end_s) in enumerate(self._segments, 1):
            dur = end_s - start_s
            text = (
                f"  Part {idx}:  {_fmt_secs(start_s)} → {_fmt_secs(end_s)}"
                f"  ({_fmt_secs(dur)})"
            )
            item = QListWidgetItem(text)
            self.segment_list.addItem(item)

    # ==================== Export ====================

    def _pick_export_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục lưu export", self._export_dir or ""
        )
        if d:
            self._export_dir = d
            self.export_dir_label.setText(d)

    def _start_export(self):
        """Export chỉ các đoạn được chọn trong danh sách."""
        selected = self.segment_list.selectedItems()
        if not selected:
            QMessageBox.information(
                self, "Chưa chọn đoạn",
                "Vui lòng chọn đoạn cần export trong danh sách.\n"
                "(Giữ Ctrl để chọn nhiều đoạn)"
            )
            return

        # Lấy segment data từ các item được chọn
        chosen = []
        for item in selected:
            idx = self.segment_list.row(item)
            if 0 <= idx < len(self._segments):
                s, e = self._segments[idx]
                chosen.append((s, e, f"Part {idx + 1}"))

        if not chosen:
            return

        self._do_export(chosen)

    def _start_export_all(self):
        """Export tất cả đoạn."""
        if not self._segments:
            QMessageBox.information(
                self, "Chưa có đoạn", "Vui lòng thêm đoạn cắt trước khi export."
            )
            return

        segments_with_labels = [
            (s, e, f"Part {i+1}")
            for i, (s, e) in enumerate(self._segments)
        ]
        self._do_export(segments_with_labels)

    def _do_export(self, segments_with_labels):
        """Thực hiện export với danh sách segments cho trước."""
        if not self._video_path:
            QMessageBox.information(self, "Chưa có video", "Vui lòng chọn video trước.")
            return

        if not self.ffmpeg_path:
            QMessageBox.critical(
                self, "Thiếu FFmpeg", "Không tìm thấy FFmpeg để cắt video."
            )
            return

        if not self._export_dir:
            self._pick_export_dir()
            if not self._export_dir:
                return

        self.btn_export.setEnabled(False)
        self.btn_export_all.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(segments_with_labels))

        self._export_worker = TrimExportWorker(
            self.ffmpeg_path, self._video_path,
            segments_with_labels, self._export_dir,
        )
        self._export_worker.log.connect(self.log.emit)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_progress(self, current, total):
        self.progress_bar.setValue(current)

    def _on_export_done(self, out_dir):
        self.btn_export.setEnabled(True)
        self.btn_export_all.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.log.emit(f"✅ Export hoàn tất! Đã lưu tại: {out_dir}")
        QMessageBox.information(
            self, "Hoàn tất",
            f"Đã export {len(self._segments)} đoạn thành công!\n"
            f"Lưu tại: {out_dir}"
        )

    def _on_export_failed(self, err):
        self.btn_export.setEnabled(True)
        self.btn_export_all.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.log.emit(f"❌ Export lỗi: {err}")
        QMessageBox.warning(self, "Lỗi", f"Export thất bại:\n{err}")

    def stop_export(self):
        """Dừng export worker nếu đang chạy."""
        if self._export_worker and self._export_worker.isRunning():
            self._export_worker.stop()

    def _expand_video(self):
        """Mở cửa sổ lớn chứa toàn bộ giao diện cắt video."""
        if not self._video_path:
            QMessageBox.information(self, "Chưa có video", "Chọn video trước khi mở rộng.")
            return

        # Lấy vị trí hiện tại
        pos_ms = 0
        if self._use_mpv and self.mpv_player:
            try:
                pos_s = self.mpv_player.time_pos
                pos_ms = int(pos_s * 1000) if pos_s else 0
            except Exception:
                pass
        elif self.player:
            pos_ms = self.player.position()

        # Pause player chính
        if self._use_mpv and self.mpv_player:
            self.mpv_player.pause = True
            self._mpv_timer.stop()
            self._mpv_is_playing = False
            self.btn_play.setText("▶")
        elif self.player:
            self.player.pause()

        # Mở dialog chứa toàn bộ trim widget
        dlg = ExpandedVideoDialog(
            video_path=self._video_path,
            ffmpeg_path=self.ffmpeg_path,
            start_pos_ms=pos_ms,
            duration_ms=self._duration_ms,
            parent=self,
        )
        dlg.exec_()

    def cleanup(self):
        """Dọn dẹp khi widget bị xóa."""
        if hasattr(self, 'mpv_player') and self.mpv_player:
            try:
                self.mpv_player.terminate()
            except Exception:
                pass
            self.mpv_player = None
        if self.player:
            try:
                self.player.stop()
                # QUAN TRỌNG: disconnect video output trước khi destroy widget
                # Tránh crash AVFoundation khi QVideoWidget bị hủy
                self.player.setVideoOutput(None)
            except Exception:
                pass
            self.player = None
        self.stop_export()
