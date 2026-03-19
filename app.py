# Fix locale cho macOS: libmpv yêu cầu LC_NUMERIC = "C", nếu không sẽ crash (segfault)
import locale
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass
import os
os.environ["LC_NUMERIC"] = "C"

import json
import sys
import subprocess
import tempfile
import urllib.request
import shutil
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal, QSettings, QTimer
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QTextEdit,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QComboBox,
    QMessageBox,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QProgressBar,
    QHeaderView,
    QInputDialog,
    QRadioButton,
    QButtonGroup,
    QSizePolicy,
    QTabWidget,
    QPlainTextEdit,
    QSpinBox,
    QListWidget,
    QListWidgetItem,
    QDateEdit,
    QDialog,
    QScrollArea,
)
from PyQt5.QtCore import QDate

from bilibili_api import (
    BilibiliQRLogin,
    BilibiliDownloader,
    BilibiliCookieRefresh,
    load_bilibili_cookies,
    save_bilibili_cookies,
    cookies_to_netscape_file,
    QUALITY_MAP,
)
from bilibili_workers import QRPollWorker, BilibiliDownloadWorker
from video_trim_widget import VideoTrimWidget
from douyin_downloader import (
    is_douyin_url,
    DouyinScanWorker,
    DouyinDownloadWorker,
)

BROWSERS = ["none", "chrome", "edge", "firefox", "brave", "opera", "chromium", "safari"]

def _unblock_file(file_path):
    """
    Unblock file trên Windows để tránh lỗi Application Control policy (WinError 4551).
    Xóa zone identifier để Windows không chặn file được tải từ internet.
    """
    if os.name != "nt":
        return

    try:
        file_path = Path(file_path)
        if not file_path.exists():
            return

        resolved_path = file_path.resolve()
        path_str = str(resolved_path)

        # Phương pháp 1: Xóa zone identifier trực tiếp (nhanh nhất và hiệu quả nhất)
        # Zone identifier là alternate data stream của NTFS
        # Không thể dùng os.path.exists() với alternate streams, phải thử xóa trực tiếp
        zone_file = path_str + ":Zone.Identifier"
        try:
            # Thử xóa bằng cmd (cách duy nhất để xóa alternate data stream)
            subprocess.run(
                ["cmd", "/c", f'del /f /a "{zone_file}"'],
                capture_output=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass

        # Phương pháp 2: Sử dụng PowerShell Unblock-File (cần quyền)
        try:
            # Escape path cho PowerShell
            ps_path = path_str.replace('"', '`"')
            ps_cmd = f'[System.IO.File]::SetAttributes("{ps_path}", [System.IO.FileAttributes]::Normal); Unblock-File -Path "{ps_path}" -ErrorAction SilentlyContinue'
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps_cmd,
                ],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass

        # Phương pháp 3: Sử dụng win32api nếu có (hiệu quả nhất nhưng cần pywin32)
        try:
            import win32api
            import win32con

            # Xóa READONLY và SYSTEM attributes
            win32api.SetFileAttributes(path_str, win32con.FILE_ATTRIBUTE_NORMAL)
        except ImportError:
            # Không có pywin32, bỏ qua
            pass
        except Exception:
            pass

    except Exception:
        # Nếu không unblock được thì bỏ qua, không làm crash app
        pass

def _ensure_exe_unblocked(exe_path, is_exe):
    """
    Đảm bảo file .exe được unblock trước khi chạy subprocess.
    """
    if is_exe and exe_path:
        _unblock_file(exe_path)

def _subprocess_no_console_kwargs():
    """
    Trên Windows, khi chạy bản .exe (GUI) mà gọi thêm các chương trình console
    như yt-dlp.exe, nếu không set cờ CREATE_NO_WINDOW thì Windows sẽ bật thêm
    một cửa sổ console mỗi lần gọi subprocess.
    Hàm này trả về kwargs phù hợp để truyền vào subprocess.run/Popen.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}

try:
    import browser_cookie3  # pip install browser-cookie3
except Exception:
    browser_cookie3 = None

class VideoListViewDialog(QDialog):
    """Cửa sổ popup để hiển thị danh sách video trong cửa sổ lớn, rộng rãi hơn."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Danh sách video - Cửa sổ lớn")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 800)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Thanh công cụ
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Danh sách video:"))
        toolbar.addStretch(1)
        self.btn_select_all = QPushButton("Chọn tất cả")
        self.btn_clear_all = QPushButton("Bỏ chọn tất cả")
        self.btn_download_thumbs = QPushButton("Tải thumbnail")
        toolbar.addWidget(self.btn_select_all)
        toolbar.addWidget(self.btn_clear_all)
        toolbar.addWidget(self.btn_download_thumbs)
        layout.addLayout(toolbar)

        # Thanh tìm kiếm cho cửa sổ lớn
        search_bar = QHBoxLayout()
        lbl_search = QLabel("Tìm kiếm:")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Lọc theo tiêu đề hoặc URL trong cửa sổ lớn..."
        )
        search_bar.addWidget(lbl_search)
        search_bar.addWidget(self.search_edit)
        layout.addLayout(search_bar)

        # Bảng danh sách video
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Chọn",
                "Thumbnail",
                "Tiêu đề",
                "Độ dài",
                "Ngày đăng",
                "Dung lượng",
                "Lượt xem",
                "Lượt thích",
                "URL",
                "Trạng thái",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(144, 81))

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(84)

        layout.addWidget(self.table)

        # Nút đóng
        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

        # Kết nối sự kiện
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_clear_all.clicked.connect(self.clear_all)
        self.btn_download_thumbs.clicked.connect(self._download_thumbnails_from_popup)

        # Kết nối signal itemChanged để đồng bộ với bảng chính
        self.table.itemChanged.connect(self._on_popup_item_changed)

        # Lưu trữ items để đồng bộ với bảng chính
        self.items = []

        # Debounce tìm kiếm cho cửa sổ lớn
        self._search_query = ""
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_search_filter)
        self.search_edit.textChanged.connect(self._on_search_text_changed)

    def populate_table(self, items, parent_table):
        """Điền dữ liệu vào bảng từ danh sách items và đồng bộ với bảng chính."""
        self.items = items
        self.parent_table = parent_table
        self.table.setRowCount(0)

        for i, item in enumerate(items):
            self.table.insertRow(i)

            # Checkbox - đồng bộ với bảng chính
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            # Lấy trạng thái từ bảng chính
            if i < parent_table.rowCount():
                parent_chk = parent_table.item(i, 0)
                if parent_chk:
                    chk.setCheckState(parent_chk.checkState())
            else:
                chk.setCheckState(Qt.Checked)
            self.table.setItem(i, 0, chk)

            # Không cần kết nối ở đây, sẽ dùng itemChanged của table

            # Thumbnail
            thumb_item = QTableWidgetItem()
            if self.parent_window and hasattr(self.parent_window, "chk_fast_scan"):
                if not self.parent_window.chk_fast_scan.isChecked():
                    icon = (
                        self.parent_window._thumbnail_icon(item)
                        if hasattr(self.parent_window, "_thumbnail_icon")
                        else None
                    )
                    if icon:
                        thumb_item.setIcon(icon)
                        thumb_item.setText(" ")
                    else:
                        thumb_item.setText("(none)")
                else:
                    thumb_item.setText("—")
            else:
                thumb_item.setText("—")
            self.table.setItem(i, 1, thumb_item)

            # Tiêu đề
            title_item = QTableWidgetItem(item.get("title", ""))
            title_item.setToolTip(item.get("title", ""))
            self.table.setItem(i, 2, title_item)

            # Các cột khác - sử dụng các hàm format từ parent
            if self.parent_window:
                self.table.setItem(
                    i,
                    3,
                    QTableWidgetItem(
                        self.parent_window._fmt_duration(item.get("duration"))
                    ),
                )
                self.table.setItem(
                    i,
                    4,
                    QTableWidgetItem(
                        self.parent_window._fmt_upload_date(item.get("upload_date"))
                    ),
                )
                self.table.setItem(
                    i,
                    5,
                    QTableWidgetItem(
                        self.parent_window._fmt_size(item.get("filesize"))
                    ),
                )
                self.table.setItem(
                    i,
                    6,
                    QTableWidgetItem(
                        self.parent_window._fmt_int(item.get("view_count"))
                    ),
                )
                self.table.setItem(
                    i,
                    7,
                    QTableWidgetItem(
                        self.parent_window._fmt_int(item.get("like_count"))
                    ),
                )
            else:
                self.table.setItem(
                    i, 3, QTableWidgetItem(str(item.get("duration", "-")))
                )
                self.table.setItem(
                    i, 4, QTableWidgetItem(str(item.get("upload_date", "-")))
                )
                self.table.setItem(
                    i, 5, QTableWidgetItem(str(item.get("filesize", "-")))
                )
                self.table.setItem(
                    i, 6, QTableWidgetItem(str(item.get("view_count", "-")))
                )
                self.table.setItem(
                    i, 7, QTableWidgetItem(str(item.get("like_count", "-")))
                )

            # URL
            url_item = QTableWidgetItem(item.get("url", ""))
            url_item.setToolTip(item.get("url", ""))
            self.table.setItem(i, 8, url_item)

            # Trạng thái
            archive = (
                self.parent_window._read_archive()
                if self.parent_window and hasattr(self.parent_window, "_read_archive")
                else set()
            )
            key = (
                self.parent_window._guess_key(item)
                if self.parent_window and hasattr(self.parent_window, "_guess_key")
                else None
            )
            status = "Đã tải" if key and key in archive else "Mới"
            self.table.setItem(i, 9, QTableWidgetItem(status))

    def _on_popup_item_changed(self, item):
        """Xử lý khi item trong cửa sổ popup thay đổi - đồng bộ với bảng chính."""
        # Chỉ xử lý checkbox ở cột 0
        if item.column() == 0:
            row = item.row()
            state = item.checkState()
            self._sync_checkbox(row, state)

    def _on_search_text_changed(self, text: str):
        """Lưu query và khởi động lại timer debounce cho popup."""
        self._search_query = text or ""
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer.start()

    def _apply_search_filter(self):
        """
        Lọc các dòng trong bảng popup theo từ khóa trên tiêu đề (cột 2) và URL (cột 8).
        Không thay đổi self.items, chỉ ẩn/hiện hàng.
        """
        q = (self._search_query or "").strip().lower()
        if not q:
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return

        for r in range(self.table.rowCount()):
            title_item = self.table.item(r, 2)
            url_item = self.table.item(r, 8)
            title = (title_item.text() if title_item else "").lower()
            url = (url_item.text() if url_item else "").lower()
            match = (q in title) or (q in url)

            # Nếu dòng bị ẩn, bỏ chọn checkbox trong popup để đồng bộ logic với bảng chính
            if not match:
                chk_item = self.table.item(r, 0)
                if chk_item and chk_item.checkState() == Qt.Checked:
                    chk_item.setCheckState(Qt.Unchecked)
            self.table.setRowHidden(r, not match)

    def _download_thumbnails_from_popup(self):
        """
        Gọi hành động tải thumbnail từ cửa sổ chính.
        Checkbox ở popup đã được đồng bộ với bảng chính, nên chỉ cần
        ủy quyền cho parent_window xử lý.
        """
        if self.parent_window and hasattr(
            self.parent_window, "download_thumbnails_selected"
        ):
            self.parent_window.download_thumbnails_selected()

    def _sync_checkbox(self, row, state):
        """Đồng bộ checkbox với bảng chính."""
        if self.parent_table and row < self.parent_table.rowCount():
            parent_chk = self.parent_table.item(row, 0)
            if parent_chk:
                # Tạm thời ngắt kết nối để tránh vòng lặp
                try:
                    self.parent_table.itemChanged.disconnect()
                except:
                    pass
                parent_chk.setCheckState(state)
                # Kết nối lại
                if self.parent_window:
                    self.parent_table.itemChanged.connect(
                        self.parent_window._on_table_item_changed
                    )

    def select_all(self):
        """Chọn tất cả video."""
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, 0)
            if chk:
                chk.setCheckState(Qt.Checked)

    def clear_all(self):
        """Bỏ chọn tất cả video."""
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, 0)
            if chk:
                chk.setCheckState(Qt.Unchecked)

class ExpandWorker(QThread):
    log = pyqtSignal(str)
    result = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        source_urls,
        browser_cookie,
        cookies_file,
        ytdlp_main,
        ytdlp_is_exe=False,
        fetch_extra_meta=False,
        filter_date_enabled=False,
        filter_date_start=None,
        filter_date_end=None,
        parent=None,
    ):
        super().__init__(parent)
        self.source_urls = source_urls
        self.browser_cookie = browser_cookie
        self.cookies_file = cookies_file
        self.ytdlp_main = ytdlp_main
        self.ytdlp_is_exe = ytdlp_is_exe
        # Khi True: sau khi quét xong sẽ gọi yt-dlp từng video để bổ sung thumbnail/duration/size
        # -> rất chậm với playlist/profile lớn. Mặc định tắt để ưu tiên tốc độ.
        self.fetch_extra_meta = fetch_extra_meta
        # Lọc theo khoảng ngày
        self.filter_date_enabled = filter_date_enabled
        self.filter_date_start = filter_date_start  # QDate hoặc None
        self.filter_date_end = filter_date_end  # QDate hoặc None

    def _pick_thumb(self, j):
        thumb = j.get("thumbnail") or ""
        if thumb:
            return thumb
        thumbs = j.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs:
            last = thumbs[-1]
            if isinstance(last, dict):
                return last.get("url") or ""
        return ""

    def _pick_size(self, j):
        size = j.get("filesize") or j.get("filesize_approx")
        if size:
            return int(size)
        req = j.get("requested_formats")
        if isinstance(req, list):
            total = 0
            ok = False
            for f in req:
                if isinstance(f, dict):
                    s = f.get("filesize") or f.get("filesize_approx")
                    if s:
                        total += int(s)
                        ok = True
            if ok:
                return total
        return None

    def _fetch_detail_meta(self, video_url):
        """
        Gọi yt-dlp để lấy metadata chi tiết cho 1 video.
        Ưu tiên dùng cookie (nếu có). Nếu lệnh có cookie bị lỗi, sẽ thử lại
        một lần KHÔNG dùng cookie để tránh trường hợp cấu hình cookie sai làm
        hỏng luôn bước lấy metadata.
        """

        def _build_cmd(use_cookies: bool = True):
            cmd = [
                str(self.ytdlp_main) if self.ytdlp_is_exe else sys.executable,
            ]
            if not self.ytdlp_is_exe:
                cmd.append(str(self.ytdlp_main))
            cmd += [
                "--dump-single-json",
                "--skip-download",
                "--no-playlist",
                video_url,
            ]
            if use_cookies:
                if self.cookies_file:
                    cmd[3:3] = ["--cookies", self.cookies_file]
                elif self.browser_cookie != "none":
                    cmd[3:3] = ["--cookies-from-browser", self.browser_cookie]
            return cmd

        # Đảm bảo file exe được unblock trước khi chạy
        _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)

        # 1. Thử với cookie (nếu có)
        for use_cookies in (True, False):
            cmd = _build_cmd(use_cookies=use_cookies)
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_subprocess_no_console_kwargs(),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                break
            # Nếu lần đầu (có cookie) thất bại thì thử lại không cookie.
            # Nếu lần thứ hai cũng thất bại thì sẽ trả về {} ở dưới.
            proc = None

        if proc is None or not proc.stdout.strip():
            return {}

        try:
            j = json.loads(proc.stdout)
        except Exception:
            return {}

        return {
            # Bổ sung thêm nhiều trường để có thể hiển thị thông tin video đơn lẻ
            "title": j.get("title"),
            "thumbnail": self._pick_thumb(j),
            "duration": j.get("duration"),
            "filesize": self._pick_size(j),
            "upload_date": j.get("upload_date"),
            "view_count": j.get("view_count"),
            "like_count": j.get("like_count"),
        }

    def run(self):
        try:
            out = []
            for src in self.source_urls:
                # Chuẩn hoá một số URL đặc biệt trước khi quét
                raw_src = src
                if "space.bilibili.com" in src and "/video" not in src:
                    # Trang space của Bilibili cần chuyển sang tab /video để yt-dlp bung được list
                    base = src.split("?", 1)[0].rstrip("/")
                    src = base + "/video"
                    self.log.emit(f"🔎 Quét: {raw_src}  -> dùng tab video: {src}")
                else:
                    self.log.emit(f"🔎 Quét: {src}")
                added = 0

                # Một số nguồn như Bilibili trả JSON dạng khác, xử lý riêng để bung được list.
                if "bilibili.com" in src:
                    # Đảm bảo file exe được unblock trước khi chạy
                    _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)

                    cmd = [
                        str(self.ytdlp_main) if self.ytdlp_is_exe else sys.executable,
                    ]
                    if not self.ytdlp_is_exe:
                        cmd.append(str(self.ytdlp_main))
                    cmd += [
                        "-J",  # --dump-single-json
                        "--ignore-errors",
                        src,
                    ]
                    if self.cookies_file:
                        cmd[3:3] = ["--cookies", self.cookies_file]
                    elif self.browser_cookie != "none":
                        cmd[3:3] = ["--cookies-from-browser", self.browser_cookie]

                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        **_subprocess_no_console_kwargs(),
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        try:
                            root = json.loads(proc.stdout)
                        except Exception:
                            root = {}
                        entries = root.get("entries")
                        if not entries:
                            # Nếu không có entries, có thể là single video -> root chính là info
                            if root.get("id") and root.get("title"):
                                entries = [root]
                            else:
                                entries = []
                        for j in entries:
                            if not isinstance(j, dict):
                                continue
                            u = j.get("webpage_url") or j.get("url")
                            if not u:
                                continue
                            out.append(
                                {
                                    "title": j.get("title") or "(không có tiêu đề)",
                                    "url": u,
                                    "id": str(j.get("id") or ""),
                                    "extractor": (
                                        j.get("extractor_key") or j.get("ie_key") or ""
                                    ).lower(),
                                    "thumbnail": self._pick_thumb(j),
                                    "duration": j.get("duration"),
                                    "filesize": self._pick_size(j),
                                    "view_count": j.get("view_count"),
                                    "like_count": j.get("like_count"),
                                    "upload_date": j.get("upload_date"),
                                }
                            )
                            added += 1
                else:
                    # Đảm bảo file exe được unblock trước khi chạy
                    _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)

                    # Thử quét như playlist trước (với --flat-playlist)
                    cmd = [
                        str(self.ytdlp_main) if self.ytdlp_is_exe else sys.executable
                    ]
                    if not self.ytdlp_is_exe:
                        cmd.append(str(self.ytdlp_main))
                    cmd += ["--flat-playlist", "--dump-json", "--ignore-errors", src]
                    if self.cookies_file:
                        cmd[3:3] = ["--cookies", self.cookies_file]
                    elif self.browser_cookie != "none":
                        cmd[3:3] = ["--cookies-from-browser", self.browser_cookie]

                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        **_subprocess_no_console_kwargs(),
                    )
                    lines = proc.stdout.splitlines()

                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            j = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        u = j.get("webpage_url") or j.get("url")
                        if not u:
                            continue
                        out.append(
                            {
                                "title": j.get("title") or "(không có tiêu đề)",
                                "url": u,
                                "id": str(j.get("id") or ""),
                                "extractor": (
                                    j.get("extractor_key") or j.get("ie_key") or ""
                                ).lower(),
                                "thumbnail": self._pick_thumb(j),
                                "duration": j.get("duration"),
                                "filesize": self._pick_size(j),
                                "view_count": j.get("view_count"),
                                "like_count": j.get("like_count"),
                                "upload_date": j.get("upload_date"),
                            }
                        )
                        added += 1

                    # Nếu --flat-playlist không trả về gì, thử quét như video đơn lẻ
                    if added == 0:
                        # Đảm bảo file exe được unblock trước khi chạy
                        _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)

                        # Thử với --dump-single-json (không có --flat-playlist) để xem có phải video đơn lẻ không
                        cmd_single = [
                            (
                                str(self.ytdlp_main)
                                if self.ytdlp_is_exe
                                else sys.executable
                            )
                        ]
                        if not self.ytdlp_is_exe:
                            cmd_single.append(str(self.ytdlp_main))
                        cmd_single += [
                            "--dump-single-json",
                            "--no-playlist",
                            "--ignore-errors",
                            src,
                        ]
                        if self.cookies_file:
                            cmd_single[3:3] = ["--cookies", self.cookies_file]
                        elif self.browser_cookie != "none":
                            cmd_single[3:3] = [
                                "--cookies-from-browser",
                                self.browser_cookie,
                            ]

                        proc_single = subprocess.run(
                            cmd_single,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            **_subprocess_no_console_kwargs(),
                        )
                        if proc_single.returncode == 0 and proc_single.stdout.strip():
                            try:
                                j_single = json.loads(proc_single.stdout)
                                # Kiểm tra xem có phải video đơn lẻ không
                                if j_single.get("id") and j_single.get("title"):
                                    u_single = (
                                        j_single.get("webpage_url")
                                        or j_single.get("url")
                                        or src
                                    )
                                    out.append(
                                        {
                                            "title": j_single.get("title")
                                            or "(không có tiêu đề)",
                                            "url": u_single,
                                            "id": str(j_single.get("id") or ""),
                                            "extractor": (
                                                j_single.get("extractor_key")
                                                or j_single.get("ie_key")
                                                or ""
                                            ).lower(),
                                            "thumbnail": self._pick_thumb(j_single),
                                            "duration": j_single.get("duration"),
                                            "filesize": self._pick_size(j_single),
                                            "view_count": j_single.get("view_count"),
                                            "like_count": j_single.get("like_count"),
                                            "upload_date": j_single.get("upload_date"),
                                        }
                                    )
                                    added += 1
                            except json.JSONDecodeError:
                                pass

                if added == 0:
                    # Trường hợp không bung được playlist/profile: coi đây là video đơn lẻ
                    # và cố gắng lấy đầy đủ metadata cho URL này để hiển thị.
                    meta = self._fetch_detail_meta(src)
                    if meta:
                        out.append(
                            {
                                "title": meta.get("title") or "(không có tiêu đề)",
                                "url": src,
                                "id": "",
                                "extractor": "",
                                "thumbnail": meta.get("thumbnail"),
                                "duration": meta.get("duration"),
                                "filesize": meta.get("filesize"),
                                "view_count": meta.get("view_count"),
                                "like_count": meta.get("like_count"),
                                "upload_date": meta.get("upload_date"),
                            }
                        )
                        self.log.emit(
                            "  ℹ Không bung được playlist/profile, nhưng đã nhận diện video đơn lẻ và lấy thông tin chi tiết."
                        )
                    else:
                        # Fallback cuối cùng: vẫn thêm URL nhưng không có metadata
                        out.append(
                            {
                                "title": "(single URL)",
                                "url": src,
                                "id": "",
                                "extractor": "",
                            }
                        )
                        self.log.emit(
                            "  ℹ Không bung được playlist/profile, giữ URL gốc (không lấy được metadata chi tiết)."
                        )
                else:
                    self.log.emit(f"  ✅ {added} video")

            seen = set()
            uniq = []
            for item in out:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    uniq.append(item)

            # Một số nguồn (đặc biệt khi --flat-playlist) thiếu thumbnail/duration/size.
            # Fallback này rất chậm nên chỉ bật khi người dùng yêu cầu.
            # Nếu đang bật lọc theo ngày, cần bổ sung upload_date cho các video thiếu
            need_upload_date = self.filter_date_enabled
            if self.fetch_extra_meta or need_upload_date:
                missing = []
                if self.fetch_extra_meta:
                    missing = [
                        x
                        for x in uniq
                        if not (x.get("thumbnail") or "").strip()
                        or x.get("duration") is None
                        or x.get("filesize") is None
                        or not (x.get("upload_date") or "").strip()
                    ]
                elif need_upload_date:
                    # Chỉ bổ sung upload_date nếu đang lọc theo ngày
                    missing = [
                        x for x in uniq if not (x.get("upload_date") or "").strip()
                    ]

                if missing:
                    reason = (
                        "chế độ chi tiết" if self.fetch_extra_meta else "lọc theo ngày"
                    )
                    self.log.emit(
                        f"🧩 Bổ sung metadata cho {len(missing)} video ({reason})..."
                    )
                    for idx, it in enumerate(missing, 1):
                        meta = self._fetch_detail_meta(it["url"])
                        if self.fetch_extra_meta:
                            if (
                                meta.get("thumbnail")
                                and not (it.get("thumbnail") or "").strip()
                            ):
                                it["thumbnail"] = meta.get("thumbnail")
                            if (
                                it.get("duration") is None
                                and meta.get("duration") is not None
                            ):
                                it["duration"] = meta.get("duration")
                            if (
                                it.get("filesize") is None
                                and meta.get("filesize") is not None
                            ):
                                it["filesize"] = meta.get("filesize")
                        if (
                            not (it.get("upload_date") or "").strip()
                            and (meta.get("upload_date") or "").strip()
                        ):
                            it["upload_date"] = meta.get("upload_date")
                        if idx % 10 == 0:
                            self.log.emit(f"  ...đã xử lý {idx}/{len(missing)}")

            # Lọc theo khoảng ngày nếu được bật (sau khi đã bổ sung metadata)
            if (
                self.filter_date_enabled
                and self.filter_date_start
                and self.filter_date_end
            ):

                def _parse_upload_date(s):
                    """Trả về QDate hoặc None nếu không parse được."""
                    if not s:
                        return None
                    s = str(s).strip()
                    if not s:
                        return None

                    # Dạng YYYYMMDD (ví dụ: 20241015)
                    if len(s) == 8 and s.isdigit():
                        try:
                            year = int(s[0:4])
                            month = int(s[4:6])
                            day = int(s[6:8])
                            date = QDate(year, month, day)
                            if date.isValid():
                                return date
                        except Exception:
                            pass

                    # Dạng YYYY-MM-DD (ví dụ: 2024-10-15)
                    if "-" in s:
                        try:
                            parts = s.split("-")
                            if len(parts) == 3:
                                year = int(parts[0])
                                month = int(parts[1])
                                day = int(parts[2])
                                date = QDate(year, month, day)
                                if date.isValid():
                                    return date
                        except Exception:
                            pass

                    # Dạng YYYY/MM/DD (ví dụ: 2024/10/15)
                    if "/" in s:
                        try:
                            parts = s.split("/")
                            if len(parts) == 3:
                                year = int(parts[0])
                                month = int(parts[1])
                                day = int(parts[2])
                                date = QDate(year, month, day)
                                if date.isValid():
                                    return date
                        except Exception:
                            pass

                    return None

                filtered = []
                items_without_date = 0
                items_with_invalid_date = 0
                debug_samples = []  # Lưu một vài mẫu để debug

                for item in uniq:
                    upload_date_raw = item.get("upload_date")
                    item_date = _parse_upload_date(upload_date_raw)

                    if not upload_date_raw:
                        items_without_date += 1
                        if len(debug_samples) < 3:
                            debug_samples.append(
                                f"Không có upload_date: {item.get('title', '')[:50]}"
                            )
                    elif not item_date:
                        items_with_invalid_date += 1
                        if len(debug_samples) < 3:
                            debug_samples.append(
                                f"upload_date không parse được: '{upload_date_raw}' (title: {item.get('title', '')[:50]})"
                            )
                    else:
                        # Kiểm tra xem ngày có nằm trong khoảng không (bao gồm cả ngày bắt đầu và kết thúc)
                        if self.filter_date_start <= item_date <= self.filter_date_end:
                            filtered.append(item)
                        elif len(debug_samples) < 3:
                            debug_samples.append(
                                f"Ngày ngoài khoảng: {item_date.toString('dd/MM/yyyy')} (upload_date: '{upload_date_raw}')"
                            )

                original_count = len(uniq)
                uniq = filtered
                start_str = self.filter_date_start.toString("dd/MM/yyyy")
                end_str = self.filter_date_end.toString("dd/MM/yyyy")

                # Log chi tiết để debug
                log_msg = f"📅 Đã lọc: {original_count} -> {len(uniq)} video (từ {start_str} đến {end_str})"
                if items_without_date > 0 or items_with_invalid_date > 0:
                    log_msg += f"\n   (Không có ngày: {items_without_date}, Ngày không hợp lệ: {items_with_invalid_date})"
                if debug_samples:
                    for sample in debug_samples[:2]:  # Chỉ hiển thị 2 mẫu đầu
                        log_msg += f"\n   - {sample}"

                self.log.emit(log_msg)

            self.result.emit(uniq)
        except Exception as e:
            self.failed.emit(str(e))

class DownloadWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal()
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # current, total

    def __init__(
        self,
        items,
        out_dir,
        mode,
        video_quality,
        browser_cookie,
        cookies_file,
        overwrite_mode,
        include_thumbnail,
        ytdlp_main,
        ytdlp_is_exe,
        archive_file,
        force_codec=False,
        codec="h264",
        parent=None,
    ):
        super().__init__(parent)
        self.items = items
        self.out_dir = out_dir
        self.mode = mode
        self.video_quality = video_quality
        self.browser_cookie = browser_cookie
        self.cookies_file = cookies_file
        self.overwrite_mode = overwrite_mode
        self.include_thumbnail = include_thumbnail
        self.ytdlp_main = ytdlp_main
        self.ytdlp_is_exe = ytdlp_is_exe
        self.archive_file = archive_file
        self.force_codec = force_codec
        self.codec = codec
        self._stop = False
        # Thống kê
        self.total = len(items)
        self.success_count = 0
        self.skip_count = 0
        self.error_count = 0

    def stop(self):
        self._stop = True

    def _base_cmd(self):
        cmd = [str(self.ytdlp_main) if self.ytdlp_is_exe else sys.executable]
        if not self.ytdlp_is_exe:
            cmd.append(str(self.ytdlp_main))
        cmd += [
            "--newline",
            "-P",
            self.out_dir,
            "--download-archive",
            self.archive_file,
            "-o",
            "%(playlist_title,channel,uploader,extractor)s/%(title).200B [%(id)s].%(ext)s",
        ]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        elif self.browser_cookie != "none":
            cmd += ["--cookies-from-browser", self.browser_cookie]

        if self.mode == "video":
            # Chọn chuỗi format theo tuỳ chọn chất lượng
            q = getattr(self, "video_quality", "best") or "best"

            # Map chất lượng (video_quality) sang tham số chiều cao (height)
            q_map = {
                "max4320": 4320,
                "max2160": 2160,
                "max1440": 1440,
                "max1080": 1080,
                "max720": 720,
                "max480": 480,
                "max360": 360,
            }

            # Ưu tiên audio m4a (aac) để tương thích tốt với các trình phát video mặc định trên Windows.
            # Tránh mặc định tải Opus đôi khi không phát được tiếng trên Windows Movies & TV.

            # Bộ lọc codec (dùng khi force_codec=True)
            codec_filter_map = {
                "h264": "[vcodec^=avc1]",
                "av1": "[vcodec^=av01]",
                "h265": "[vcodec~=(?:hvc1|hev1|hevc)]",
                "vp9": "[vcodec^=vp09]",
            }
            c = codec_filter_map.get(self.codec, "") if self.force_codec else ""

            if q in q_map:
                h = q_map[q]
                if c:
                    fmt = (
                        f"bestvideo{c}[height<={h}]+bestaudio[ext=m4a]"
                        f"/bestvideo{c}[height<={h}]+bestaudio"
                        f"/bestvideo[height<={h}]+bestaudio[ext=m4a]"
                        f"/bestvideo[height<={h}]+bestaudio"
                        f"/best[height<={h}]/best"
                    )
                else:
                    fmt = f"bestvideo[height<={h}]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
            else:  # "best"
                if c:
                    fmt = (
                        f"bestvideo{c}+bestaudio[ext=m4a]"
                        f"/bestvideo{c}+bestaudio"
                        f"/bestvideo+bestaudio[ext=m4a]"
                        f"/bestvideo+bestaudio/best"
                    )
                else:
                    fmt = "bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

            cmd += ["-f", fmt, "--merge-output-format", "mp4"]
        else:
            cmd += ["-x", "--audio-format", "mp3"]

        # Tăng khả năng chịu lỗi mạng (quan trọng khi tải từ CDN như Bilibili)
        cmd += [
            "--concurrent-fragments",
            "10",
            "--retries",
            "infinite",
            "--fragment-retries",
            "infinite",
            "--retry-sleep",
            "exp=1:30",
            "--socket-timeout",
            "60",
            # Buộc tải theo từng chunk nhỏ (10MB) để mỗi chunk được retry độc lập
            # Cực kỳ quan trọng với Bilibili CDN vì URL hết hạn nhanh
            "--http-chunk-size",
            "10485760",
        ]

        if self.overwrite_mode == "skip":
            cmd += ["--no-overwrites"]
        else:
            cmd += ["--force-overwrites"]

        if self.include_thumbnail:
            cmd += ["--write-thumbnail", "--convert-thumbnails", "jpg"]

        return cmd

    def run(self):
        try:
            total = len(self.items)
            for i, item in enumerate(self.items, 1):
                if self._stop:
                    self.log.emit("⏹ Đã dừng.")
                    break

                self.progress.emit(i - 1, total)
                title = item.get("title", "")
                url = item.get("url", "")
                self.log.emit(f"\n=== [{i}/{total}] {title}")

                # Đảm bảo file exe được unblock trước khi chạy
                _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)

                cmd = self._base_cmd() + [url]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **_subprocess_no_console_kwargs(),
                )

                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        s = line.rstrip()
                        if "has already been recorded in the archive" in s:
                            self.log.emit("⏭ Đã tải trước đó -> BỎ QUA")
                            self.skip_count += 1
                        else:
                            self.log.emit(s)
                    if self._stop:
                        proc.terminate()
                        break

                rc = proc.wait()
                if rc != 0 and not self._stop:
                    # Thử fallback bằng native Bilibili download engine
                    if "bilibili.com" in url:
                        self.log.emit("⚠ yt-dlp lỗi, đang thử download engine dự phòng Bilibili...")
                        try:
                            fallback_ok = self._bilibili_native_download(url)
                            if fallback_ok:
                                self.success_count += 1
                                continue
                        except Exception as fb_err:
                            self.log.emit(f"⚠ Fallback cũng lỗi: {fb_err}")
                    self.error_count += 1
                    self.log.emit(f"⚠ Lỗi tải (exit={rc})")
                elif rc == 0:
                    # Chỉ tính là thành công nếu không bị stop giữa chừng và không nằm trong danh sách skip
                    # (skip đã được cộng riêng ở trên khi gặp dòng archive)
                    self.success_count += 1

            self.progress.emit(total, total)
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))

    def _bilibili_native_download(self, url):
        """
        Fallback: tải Bilibili video bằng native download engine
        khi yt-dlp lỗi. Trả về True nếu thành công.
        """
        from bilibili_api import BilibiliDownloader, load_bilibili_cookies
        from pathlib import Path

        # Load cookies đã lưu từ QR login
        cookies = None
        profiles_path = Path(self.out_dir).parent / "cookie_profiles.json"
        # Thử nhiều đường dẫn có thể
        for candidate in [
            profiles_path,
            Path("cookie_profiles.json"),
            Path(os.path.dirname(os.path.abspath(__file__))) / "cookie_profiles.json",
        ]:
            cookies = load_bilibili_cookies(candidate)
            if cookies:
                break

        if cookies:
            sessdata = cookies.get("SESSDATA", "")
            self.log.emit(f"🔑 Đã load cookie Bilibili (SESSDATA: {sessdata[:8]}...)")
        else:
            self.log.emit("⚠️ Không có cookie Bilibili — chất lượng có thể bị giới hạn 720P")

        # Map quality — luôn yêu cầu cao nhất, Bilibili sẽ trả best available
        q = getattr(self, "video_quality", "best") or "best"
        quality_map = {
            "best": 127, "max4320": 127, "max2160": 120,
            "max1440": 116, "max1080": 112, "max720": 64,
            "max480": 32, "max360": 16,
        }
        quality_id = quality_map.get(q, 127)  # Mặc định yêu cầu cao nhất
        self.log.emit(f"🎯 Yêu cầu chất lượng: {quality_id} (setting: {q})")

        # Tìm ffmpeg
        _ffmpeg_name = "ffmpeg" if sys.platform == "darwin" else "ffmpeg.exe"
        ffmpeg = "ffmpeg"
        for p in [Path(self.out_dir) / _ffmpeg_name, Path(_ffmpeg_name)]:
            if p.exists():
                ffmpeg = str(p)
                break

        # Progress callback — log tiến độ tải
        last_pct = [0]
        def _progress(downloaded, total, phase=""):
            if total > 0:
                pct = int(downloaded * 100 / total)
                # Chỉ log mỗi 10%
                if pct >= last_pct[0] + 10 or pct >= 100:
                    last_pct[0] = pct
                    mb_down = downloaded / 1024 / 1024
                    mb_total = total / 1024 / 1024
                    phase_label = f" ({phase})" if phase else ""
                    self.log.emit(
                        f"📥 Tải{phase_label}: {pct}% ({mb_down:.1f}/{mb_total:.1f} MB)"
                    )

        downloader = BilibiliDownloader(ffmpeg_path=ffmpeg)
        results = downloader.download_video(
            url_or_bvid=url,
            output_dir=self.out_dir,
            quality=quality_id,
            cookies=cookies if cookies else None,
            progress_callback=_progress,
            log_callback=lambda msg: self.log.emit(msg),
        )

        if results:
            for fp in results:
                self.log.emit(f"✅ Fallback thành công: {fp}")
            return True
        return False

class ThumbnailDownloadWorker(QThread):
    """Worker tải riêng thumbnail cho các video đã chọn (không tải video)."""

    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, items, out_dir, parent=None):
        super().__init__(parent)
        self.items = items
        self.out_dir = out_dir
        # Thống kê
        self.total = len(items)
        self.success_count = 0
        self.error_count = 0

    def _safe_filename(self, title, vid, index):
        """Tạo tên file an toàn từ tiêu đề + id + index."""
        base = title or "thumbnail"
        base = base.strip()
        if not base:
            base = "thumbnail"
        # Thêm id để giảm trùng tên nếu có
        if vid:
            base = f"{base} [{vid}]"
        else:
            base = f"{base} [{index}]"
        # Loại bỏ ký tự không hợp lệ trên Windows
        invalid = '<>:"/\\|?*'
        for ch in invalid:
            base = base.replace(ch, "_")
        # Giới hạn độ dài để tránh lỗi đường dẫn quá dài
        if len(base) > 180:
            base = base[:180]
        return base

    def run(self):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            total = len(self.items)
            for i, item in enumerate(self.items, 1):
                self.progress.emit(i - 1, total)
                if not isinstance(item, dict):
                    continue
                thumb_url = (item.get("thumbnail") or "").strip()
                if not thumb_url:
                    self.log.emit(
                        f"⚠ Bỏ qua: video '{item.get('title', '')}' không có thumbnail URL."
                    )
                    continue

                title = item.get("title", "")
                vid = str(item.get("id") or "").strip()
                fname_base = self._safe_filename(title, vid, i)

                # Lấy phần mở rộng từ URL nếu có, fallback .jpg
                parsed = urlparse(thumb_url)
                ext = os.path.splitext(parsed.path)[1]
                if not ext or len(ext) > 5:
                    ext = ".jpg"
                filename = fname_base + ext
                out_path = os.path.join(self.out_dir, filename)

                try:
                    self.log.emit(f"🖼 Đang tải thumbnail: {title}")
                    with urllib.request.urlopen(thumb_url, timeout=15) as resp, open(
                        out_path, "wb"
                    ) as f:
                        f.write(resp.read())
                    self.success_count += 1
                except Exception as e:
                    self.log.emit(f"⚠ Lỗi tải thumbnail '{title}': {e}")
                    self.error_count += 1

            self.progress.emit(total, total)
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))

class MergeWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, ffmpeg_path, video_file, audio_file, out_file, parent=None):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.video_file = video_file
        self.audio_file = audio_file
        self.out_file = out_file

    def run(self):
        try:
            if not self.ffmpeg_path:
                self.failed.emit("Không tìm thấy FFmpeg.")
                return

            # Đảm bảo file exe được unblock trước khi chạy
            if self.ffmpeg_path and self.ffmpeg_path.endswith(".exe"):
                _unblock_file(self.ffmpeg_path)

            # -y để luôn ghi đè file output nếu trùng tên
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i",
                self.video_file,
                "-i",
                self.audio_file,
                "-c",
                "copy",
                self.out_file,
            ]

            self.log.emit(f"🎬 Đang gộp vào: {os.path.basename(self.out_file)}...")

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_subprocess_no_console_kwargs(),
            )

            if proc.returncode == 0:
                self.done.emit(self.out_file)
            else:
                self.failed.emit(f"Lỗi FFmpeg: {proc.stderr}")
        except Exception as e:
            self.failed.emit(str(e))

class SplitWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self, ffmpeg_path, video_file, segments, out_dir, overlap_sec=0, parent=None
    ):
        """
        segments: list of (start_sec, end_sec)
        out_dir: root output folder
        overlap_sec: seconds to cut back from start
        """
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.video_file = video_file
        self.segments = segments
        self.out_dir = out_dir
        self.overlap_sec = overlap_sec
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            if not self.ffmpeg_path:
                self.failed.emit("Không tìm thấy FFmpeg.")
                return

            base_name = os.path.splitext(os.path.basename(self.video_file))[0]

            for i, (start, end) in enumerate(self.segments):
                if not self._is_running:
                    break

                part_idx = i + 1
                part_folder = os.path.join(self.out_dir, f"p{part_idx}")
                os.makedirs(part_folder, exist_ok=True)

                out_file = os.path.join(
                    part_folder, f"{base_name}_part_{part_idx:03d}.mp4"
                )

                # Áp dụng overlap: lùi start lại nhưng không nhỏ hơn 0
                actual_start = max(0, start - self.overlap_sec)

                # Đảm bảo file exe được unblock trước khi chạy
                if self.ffmpeg_path and self.ffmpeg_path.endswith(".exe"):
                    _unblock_file(self.ffmpeg_path)

                # Lệnh cắt lossless: đặt -ss trước -i để nhanh, nhưng -to phải tính lại nếu -ss trước -i?
                # Với -c copy, dùng -ss sau -i đôi khi chính xác hơn nhưng chậm hơn.
                # Tuy nhiên, yêu cầu "giống losslesscut" + nhanh -> -ss trước -i.
                # Lưu ý: Với -ss trước -i, -to là timestamp tuyệt đối trong file gốc (nếu dùng ffmpeg mới).

                cmd = [
                    self.ffmpeg_path,
                    "-y",
                    "-ss",
                    str(actual_start),
                    "-to",
                    str(end),
                    "-i",
                    self.video_file,
                    "-c",
                    "copy",
                    "-avoid_negative_ts",
                    "make_non_negative",  # Giúp fix lỗi ts khi dùng -ss trước -i
                    out_file,
                ]

                self.log.emit(
                    f"✂️ Đang cắt phần {part_idx} (Folder p{part_idx}): {actual_start}s -> {end}s..."
                )

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **_subprocess_no_console_kwargs(),
                )

                if proc.returncode != 0:
                    self.log.emit(f"⚠️ Cảnh báo lỗi phần {part_idx}: {proc.stderr}")

            if self._is_running:
                self.done.emit(self.out_dir)
            else:
                self.log.emit("⏹ Đã dừng cắt giữa chừng.")

        except Exception as e:
            self.failed.emit(str(e))

class FastConcatWorker(QThread):
    log = pyqtSignal(str)
    failed = pyqtSignal(str)
    done = pyqtSignal(str)

    def __init__(self, video_files, out_file, ffmpeg_path):
        super().__init__()
        self.video_files = video_files
        self.out_file = out_file
        self.ffmpeg_path = ffmpeg_path
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            if not self.ffmpeg_path:
                self.failed.emit("Không tìm thấy FFmpeg.")
                return

            if not self.video_files:
                self.failed.emit("Chưa chọn file video nào.")
                return

            out_dir = os.path.dirname(os.path.abspath(self.out_file))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

            # Đảm bảo file exe được unblock trước khi chạy
            if self.ffmpeg_path and self.ffmpeg_path.endswith(".exe"):
                _unblock_file(self.ffmpeg_path)

            list_file = os.path.join(out_dir, "concat_list.txt")
            with open(list_file, "w", encoding="utf-8") as f:
                for vf in self.video_files:
                    safe_path = (
                        os.path.abspath(vf).replace(os.sep, "/").replace("'", r"'\''")
                    )
                    f.write(f"file '{safe_path}'\n")

            cmd = [
                self.ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c",
                "copy",
                "-fflags",
                "+genpts",
                "-movflags",
                "+faststart",
                self.out_file,
            ]

            self.log.emit(f"⚡ Bắt đầu ghép {len(self.video_files)} video...")

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_subprocess_no_console_kwargs(),
            )

            try:
                os.remove(list_file)
            except:
                pass

            if proc.returncode != 0:
                self.failed.emit(f"Lỗi ghép video: {proc.stderr}")
                return

            if self._is_running:
                self.done.emit(self.out_file)
            else:
                self.log.emit("⏹ Quá trình ghép bị dừng.")

        except Exception as e:
            self.failed.emit(str(e))

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Tool")
        # Giới hạn kích thước cửa sổ không vượt quá màn hình người dùng
        from PyQt5.QtWidgets import QDesktopWidget
        screen_geo = QDesktopWidget().availableGeometry()
        target_w = min(1600, int(screen_geo.width() * 0.92))
        target_h = min(900, int(screen_geo.height() * 0.92))
        self.resize(target_w, target_h)

        # Khi đóng gói bằng PyInstaller (.exe), dùng thư mục chứa .exe để lưu dữ liệu lâu dài.
        # KHÔNG dùng _MEIPASS (temp dir thay đổi mỗi lần chạy) vì sẽ mất settings/cookie sau mỗi restart.
        if getattr(sys, "frozen", False):
            self.project_root = Path(sys.executable).resolve().parent
        else:
            self.project_root = Path(__file__).resolve().parent
        self.ytdlp_is_exe = False

        # Tên file yt-dlp tuỳ theo hệ điều hành
        _ytdlp_exe_name = "yt-dlp" if sys.platform == "darwin" else "yt-dlp.exe"

        if getattr(sys, "frozen", False):
            # Khi chạy bản đóng gói bởi PyInstaller
            app_dir = Path(sys.executable).resolve().parent
            # Thư mục tạm chứa file giải nén của PyInstaller
            bundle_dir = Path(getattr(sys, "_MEIPASS", app_dir))

            candidates = [
                bundle_dir / _ytdlp_exe_name,
                app_dir / _ytdlp_exe_name,
                bundle_dir / "yt-dlp" / _ytdlp_exe_name,
            ]

            chosen = next((p for p in candidates if p.exists()), None)
            if chosen:
                if os.name == "nt":
                    _unblock_file(chosen)  # Unblock để tránh lỗi Application Control policy
                self.ytdlp_main = chosen
                self.ytdlp_is_exe = True
            else:
                # Nếu không tìm thấy exe, fallback về script (nếu có nhúng bộ src)
                self.ytdlp_main = bundle_dir / "yt-dlp" / "yt_dlp" / "__main__.py"
                self.ytdlp_is_exe = False
        else:
            # macOS: ưu tiên yt-dlp từ hệ thống (Homebrew) vì binary tải về
            # thường bị Gatekeeper block → timeout khi chạy
            if sys.platform == "darwin":
                import shutil as _shutil_ytdlp
                system_ytdlp = _shutil_ytdlp.which("yt-dlp")
                if system_ytdlp:
                    self.ytdlp_main = Path(system_ytdlp)
                    self.ytdlp_is_exe = True
                else:
                    # Fallback binary cục bộ
                    exe_path = self.project_root / _ytdlp_exe_name
                    if exe_path.exists():
                        self.ytdlp_main = exe_path
                        self.ytdlp_is_exe = True
                    else:
                        self.ytdlp_main = (
                            self.project_root / "yt-dlp" / "yt_dlp" / "__main__.py"
                        )
            else:
                # Windows: ưu tiên exe cục bộ
                exe_path = self.project_root / _ytdlp_exe_name
                if exe_path.exists():
                    if os.name == "nt":
                        _unblock_file(exe_path)
                    self.ytdlp_main = exe_path
                    self.ytdlp_is_exe = True
                else:
                    self.ytdlp_main = (
                        self.project_root / "yt-dlp" / "yt_dlp" / "__main__.py"
                    )

        # Đường dẫn logo (dùng cho sidebar + icon cửa sổ)
        self.logo_path = self._detect_logo_path()
        if self.logo_path is not None:
            try:
                self.setWindowIcon(QIcon(str(self.logo_path)))
            except Exception:
                pass

        self.items = []
        self.expander = None
        self.worker = None
        self.bilibili_worker = None
        self.merge_worker = None
        self.split_worker = None

        self.thumb_cache_dir = self.project_root / ".thumb_cache"
        self.thumb_cache_dir.mkdir(exist_ok=True)
        self.runtime_cookie_file = None

        self.cookie_profiles_path = self.project_root / "cookie_profiles.json"
        self.cookie_profiles = {}

        # QSettings để lưu đường dẫn đã chọn
        self.settings = QSettings("VideoDownloader", "App")
        self._build_ui()
        self._restore_settings()
        self._load_cookie_profiles()
        self._refresh_cookie_profiles_combo()
        self.on_browser_changed()
        self._detect_ffmpeg()
        self._apply_style()

        # Khôi phục trạng thái đăng nhập Bilibili (nếu có cookie cũ)
        QTimer.singleShot(500, self._restore_bilibili_login_state)

    def _detect_ffmpeg(self):
        """Kiểm tra xem ffmpeg có tồn tại không và log kết quả."""
        self.ffmpeg_path = None

        # Tên file ffmpeg tuỳ theo hệ điều hành
        _ffmpeg_name = "ffmpeg" if sys.platform == "darwin" else "ffmpeg.exe"

        # 0. Nếu đang chạy bản đóng gói, ưu tiên tìm ngay trong bundle
        if getattr(sys, "frozen", False):
            bundle_dir = Path(getattr(sys, "_MEIPASS", ""))
            bundle_ffmpeg = bundle_dir / _ffmpeg_name
            if bundle_ffmpeg.exists():
                if os.name == "nt":
                    _unblock_file(bundle_ffmpeg)
                self.ffmpeg_path = str(bundle_ffmpeg)
                self.append_log(
                    f"✅ FFmpeg tìm thấy trong gói ứng dụng: {self.ffmpeg_path}"
                )
                return

        # 1. Thử tìm trong folder chứa file app hoặc folder dự án
        app_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else self.project_root
        )
        local_ffmpeg = app_dir / _ffmpeg_name
        if local_ffmpeg.exists():
            if os.name == "nt":
                _unblock_file(local_ffmpeg)
            self.ffmpeg_path = str(local_ffmpeg)
            self.append_log(
                f"✅ FFmpeg tìm thấy tại thư mục chương trình: {self.ffmpeg_path}"
            )
            return

        # 2. Thử tìm bằng shutil.which (chuẩn nhất cho PATH)
        found_in_path = shutil.which("ffmpeg")
        if found_in_path:
            self.ffmpeg_path = found_in_path
            self.append_log(
                f"✅ FFmpeg tìm thấy trong hệ thống (PATH): {found_in_path}"
            )
            return

        # 3. Thử chạy lệnh trực tiếp (fallback)
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                **_subprocess_no_console_kwargs(),
            )
            self.ffmpeg_path = "ffmpeg"
            self.append_log("✅ FFmpeg tìm thấy qua lệnh trực tiếp")
            return
        except Exception:
            pass

        # Nếu thất bại toàn bộ
        self.append_log("❌ KHÔNG tìm thấy FFmpeg trong dự án hoặc PATH.")
        # log PATH để debug
        env_path = os.environ.get("PATH", "")
        self.append_log(f"ℹ️ PATH hiện tại: {env_path[:100]}...")
        if sys.platform == "darwin":
            self.append_log(
                "💡 Mẹo: Hãy copy file ffmpeg vào chính folder này hoặc cài qua: brew install ffmpeg"
            )
        else:
            self.append_log(
                "💡 Mẹo: Anh hãy copy file ffmpeg.exe vào chính folder này để tool nhận diện nhanh nhất."
            )

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        middle = QFrame()
        m = QVBoxLayout(middle)
        m.setContentsMargins(16, 16, 16, 16)
        m.setSpacing(10)

        gb_config = QGroupBox("Cấu hình nguồn tải")
        cfg = QVBoxLayout(gb_config)
        gb_config.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        cfg.addWidget(
            QLabel("Nguồn URL (kênh YouTube, playlist, profile TikTok, hoặc URL video):")
        )
        self.source_input = QTextEdit()
        self.source_input.setAcceptRichText(False)  # Chỉ nhận plain text, tránh paste màu đen
        self.source_input.setPlaceholderText(
            "https://www.youtube.com/@channel/videos\nhttps://www.youtube.com/playlist?list=...\nhttps://www.tiktok.com/@username\nhttps://v.douyin.com/..."
        )
        self.source_input.setFixedHeight(80)
        cfg.addWidget(self.source_input)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Thư mục lưu:"))
        saved_download_dir = self.settings.value("download_out_dir", "")
        default_dir = saved_download_dir if saved_download_dir else str(Path.home() / "Downloads" / "VideoBulk")
        self.out_dir = QLineEdit(default_dir)
        row1.addWidget(self.out_dir)
        self.btn_browse = QPushButton("📁 Chọn thư mục")
        row1.addWidget(self.btn_browse)
        cfg.addLayout(row1)

        row2 = QGridLayout()
        row2.setHorizontalSpacing(10)
        row2.setVerticalSpacing(8)

        row2.addWidget(QLabel("Chế độ:"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Video mp4", "video")
        self.mode_combo.addItem("Audio mp3", "audio")
        row2.addWidget(self.mode_combo, 0, 1)

        row2.addWidget(QLabel("Chất lượng video:"), 1, 0)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems([
            "Tự động (tốt nhất có thể)", "1080p (Full HD)", "2K (1440p) - Cần Premium", 
            "4K (2160p) - Cần Premium", "8K (4320p) - Cần Premium", "720p (HD)", "480p", "360p"
        ])
        # Just manually map them back via iterating or setting data later, but the exact order was:
        # best, max1080, max1440, max2160, max4320, max720, max480, max360.
        # It's better to keep the original addItems syntax
        self.quality_combo.addItem("Tự động (tốt nhất có thể)", "best")
        self.quality_combo.addItem("1080p (Full HD)", "max1080")
        self.quality_combo.addItem("2K (1440p) - Cần Premium", "max1440")
        self.quality_combo.addItem("4K (2160p) - Cần Premium", "max2160")
        self.quality_combo.addItem("8K (4320p) - Cần Premium", "max4320")
        self.quality_combo.addItem("720p (HD)", "max720")
        self.quality_combo.addItem("480p", "max480")
        self.quality_combo.addItem("360p", "max360")
        row2.addWidget(self.quality_combo, 1, 1)

        row2.addWidget(QLabel("Cookie (trình duyệt):"), 0, 2)
        self.browser_combo = QComboBox()
        self.browser_combo.addItems(BROWSERS)
        row2.addWidget(self.browser_combo, 0, 3)

        row2.addWidget(QLabel("Profile browser:"), 1, 2)
        self.browser_profile_combo = QComboBox()
        self.browser_profile_combo.addItem("auto", "auto")
        row2.addWidget(self.browser_profile_combo, 1, 3)

        row2.addWidget(QLabel("Nếu đã tải:"), 0, 4)
        self.overwrite_combo = QComboBox()
        self.overwrite_combo.addItem("Bỏ qua", "skip")
        self.overwrite_combo.addItem("Ghi đè", "overwrite")
        row2.addWidget(self.overwrite_combo, 0, 5)

        row2.addWidget(QLabel("Download engine:"), 1, 4)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("yt-dlp (mặc định)", "ytdlp")
        self.engine_combo.addItem("Bilibili Native API", "bilibili_native")
        row2.addWidget(self.engine_combo, 1, 5)

        self.btn_test_cookie = QPushButton("🍪 Test")
        self.btn_test_cookie.setObjectName("testCookieBtn")
        self.btn_test_cookie.setFixedHeight(28)
        row2.addWidget(self.btn_test_cookie, 3, 4)

        self.chk_force_codec = QCheckBox("Bắt buộc cùng chuẩn codec khi tải list")
        self.chk_force_codec.setChecked(True)
        row2.addWidget(self.chk_force_codec, 3, 0, 1, 2)
        row2.addWidget(QLabel("Codec:"), 3, 2)
        self.codec_combo = QComboBox()
        self.codec_combo.addItem("H264 (AVC) - Phổ biến nhất", "h264")
        self.codec_combo.addItem("AV1 - Nén tốt, cần phần cứng mới", "av1")
        self.codec_combo.addItem("H265 (HEVC) - Nén tốt hơn H264", "h265")
        self.codec_combo.addItem("VP9", "vp9")
        row2.addWidget(self.codec_combo, 3, 3)
        self.chk_force_codec.toggled.connect(self.codec_combo.setEnabled)

        row2.setColumnStretch(1, 2)
        row2.setColumnStretch(3, 2)
        row2.setColumnStretch(5, 1)
        cfg.addLayout(row2)

        self.chk_show_cookie_advanced = QCheckBox("Hiện phần Cookie nâng cao (profile + JSON thủ công)")
        self.chk_show_cookie_advanced.setChecked(False)
        cfg.addWidget(self.chk_show_cookie_advanced)

        self.cookie_adv_wrap = QWidget()
        cookie_adv_layout = QVBoxLayout(self.cookie_adv_wrap)
        cookie_adv_layout.setContentsMargins(0, 0, 0, 0)
        cookie_adv_layout.setSpacing(6)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Cookie profile:"))
        self.cookie_profile_combo = QComboBox()
        profile_row.addWidget(self.cookie_profile_combo, 1)
        self.btn_profile_save = QPushButton("Lưu profile")
        self.btn_profile_delete = QPushButton("Xoá profile")
        profile_row.addWidget(self.btn_profile_save)
        profile_row.addWidget(self.btn_profile_delete)
        cookie_adv_layout.addLayout(profile_row)

        self.cookie_json_input = QTextEdit()
        self.cookie_json_input.setPlaceholderText("Tuỳ chọn: dán JSON cookie export (ví dụ Bilibili) để dùng --cookies tự động\nĐịnh dạng: {'url':'...','cookies':[...]}")
        self.cookie_json_input.setFixedHeight(50)
        cookie_adv_layout.addWidget(self.cookie_json_input)
        cfg.addWidget(self.cookie_adv_wrap)

        self.chk_thumbnail = QCheckBox("Tải thumbnail cùng video (.jpg)")
        self.chk_thumbnail.setChecked(True)
        cfg.addWidget(self.chk_thumbnail)

        date_filter_wrapper = QVBoxLayout()
        self.chk_filter_date = QCheckBox("Lọc theo khoảng ngày:")
        date_filter_wrapper.addWidget(self.chk_filter_date)

        date_filter_layout = QHBoxLayout()
        date_filter_layout.addWidget(QLabel("Từ ngày:"))
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addMonths(-1))
        self.date_start.setDisplayFormat("dd/MM/yyyy")
        self.date_start.setEnabled(False)
        date_filter_layout.addWidget(self.date_start)

        date_filter_layout.addWidget(QLabel("Đến ngày:"))
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate())
        self.date_end.setDisplayFormat("dd/MM/yyyy")
        self.date_end.setEnabled(False)
        date_filter_layout.addWidget(self.date_end)

        self.btn_today = QPushButton("Hôm nay")
        self.btn_today.setEnabled(False)
        self.btn_today.clicked.connect(lambda: self.date_end.setDate(QDate.currentDate()))
        date_filter_layout.addWidget(self.btn_today)
        date_filter_layout.addStretch(1)
        date_filter_wrapper.addLayout(date_filter_layout)
        cfg.addLayout(date_filter_wrapper)

        self.chk_filter_date.toggled.connect(
            lambda checked: [
                self.date_start.setEnabled(checked),
                self.date_end.setEnabled(checked),
                self.btn_today.setEnabled(checked),
            ]
        )

        self.chk_fast_scan = QCheckBox("Chế độ quét nhanh (bỏ qua bước bổ sung metadata khi thiếu)")
        self.chk_fast_scan.setChecked(True)
        cfg.addWidget(self.chk_fast_scan)

        m.addWidget(gb_config, 0)

        # -- HÀNH ĐỘNG CENTER (Thay thế Sidebar) --
        gb_actions = QGroupBox("Danh sách video tìm được")
        tv = QVBoxLayout(gb_actions)
        tv.setContentsMargins(12, 12, 12, 12)
        tv.setSpacing(10)

        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        
        def _get_btn_style(bg_color, hover_color):
            return f"QPushButton {{ background: {bg_color}; color: white; padding: 6px 12px; border-radius: 6px; font-weight: bold; border: none; }} QPushButton:hover {{ background: {hover_color}; }}"

        self.btn_scan = QPushButton("🔍 Quét danh sách")
        self.btn_scan.setStyleSheet(_get_btn_style("#2563eb", "#3b82f6")) # Blue
        
        self.btn_select_all = QPushButton("Tất cả")
        self.btn_select_all.setStyleSheet(_get_btn_style("#475569", "#64748b")) # Slate
        
        self.btn_clear = QPushButton("Bỏ chọn")
        self.btn_clear.setStyleSheet(_get_btn_style("#475569", "#64748b")) # Slate
        
        self.btn_select_top_views = QPushButton("👁 Top View")
        self.btn_select_top_views.setStyleSheet(_get_btn_style("#8b5cf6", "#a78bfa")) # Purple
        
        self.btn_select_top_likes = QPushButton("❤ Top Like")
        self.btn_select_top_likes.setStyleSheet(_get_btn_style("#8b5cf6", "#a78bfa")) # Purple
        
        self.btn_download_thumbs = QPushButton("🖼 Tải thumbnail")
        self.btn_download_thumbs.setStyleSheet(_get_btn_style("#d97706", "#f59e0b")) # Amber
        
        self.btn_download = QPushButton("⬇ Tải video")
        self.btn_download.setStyleSheet(_get_btn_style("#16a34a", "#22c55e")) # Green
        
        self.btn_stop = QPushButton("⏹ Dừng")
        self.btn_stop.setStyleSheet(_get_btn_style("#e11d48", "#f43f5e")) # Red
        self.btn_stop.setEnabled(False)

        for b in [
            self.btn_scan, self.btn_select_all, self.btn_clear, 
            self.btn_select_top_views, self.btn_select_top_likes, 
            self.btn_download_thumbs, self.btn_download, self.btn_stop
        ]:
            act_row.addWidget(b)
        
        act_row.addStretch(1)
        self.footer_status = QLabel("● Trạng thái: Sẵn sàng")
        self.footer_status.setStyleSheet("font-weight: bold; color: #a8b2d1;")
        act_row.addWidget(self.footer_status)
        tv.addLayout(act_row)

        sort_bar = QHBoxLayout()
        sort_bar.addWidget(QLabel("Sắp xếp:"))
        self.sort_group = QButtonGroup(self)
        self.sort_default = QRadioButton("Mặc định")
        self.sort_views = QRadioButton("Nhiều lượt xem")
        self.sort_likes = QRadioButton("Nhiều lượt thích")
        self.sort_default.setChecked(True)
        self.sort_group.addButton(self.sort_default, 0)
        self.sort_group.addButton(self.sort_views, 1)
        self.sort_group.addButton(self.sort_likes, 2)
        sort_bar.addWidget(self.sort_default)
        sort_bar.addWidget(self.sort_views)
        sort_bar.addWidget(self.sort_likes)
        
        sort_bar.addSpacing(16)
        sort_bar.addWidget(QLabel("Thời gian:"))
        self.btn_select_oldest = QPushButton("Video cũ nhất")
        self.btn_select_by_month = QPushButton("Chọn theo tháng/năm")
        sort_bar.addWidget(self.btn_select_oldest)
        sort_bar.addWidget(self.btn_select_by_month)
        
        sort_bar.addSpacing(16)
        sort_bar.addWidget(QLabel("Tìm kiếm:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Nhập tên phim, URL...")
        self.search_edit.setMaximumWidth(200)
        sort_bar.addWidget(self.search_edit)
        
        sort_bar.addStretch(1)
        self.btn_open_large_view = QPushButton("🔍 Mở cửa sổ lớn")
        sort_bar.addWidget(self.btn_open_large_view)
        tv.addLayout(sort_bar)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_search_filter)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Chọn", "Thumbnail", "Tiêu đề", "Độ dài", "Ngày đăng",
            "Dung lượng", "Lượt xem", "Lượt thích", "URL", "Trạng thái"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(144, 81))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for i in range(3, 10):
            if i != 8: header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(84)
        tv.addWidget(self.table)
        gb_actions.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.search_edit.textChanged.connect(self._on_search_text_changed)

        m.addWidget(gb_actions, 1)

        from PyQt5.QtWidgets import QStackedWidget; self.tabs = QStackedWidget()

        try:
            from reup_tool_widget import ReupToolWidget
            self.tab_reup = ReupToolWidget(self)
            if hasattr(self.tab_reup, "sidebar"): self.tab_reup.sidebar.hide()
            self.tabs.addWidget(self.tab_reup)
        except Exception as e:
            self.tab_reup = QWidget()
            err_layout = QVBoxLayout(self.tab_reup)
            err_lbl = QLabel(f"Không thể tải Reup Tool: {e}")
            err_layout.addWidget(err_lbl)
            self.tabs.addWidget(self.tab_reup)

        self.tab_download = QWidget()
        tab_h_download = QHBoxLayout(self.tab_download)
        tab_h_download.setContentsMargins(0, 0, 0, 0)
        tab_h_download.addWidget(middle, 1)
        self.tabs.addWidget(self.tab_download)


        # --- Tab 2: Công cụ Merger ---
        self.tab_merger = QWidget()
        tab_v_merger = QVBoxLayout(self.tab_merger)
        tab_v_merger.setContentsMargins(10, 10, 10, 10)

        # Công cụ Merger (Phase 02)
        self.gb_merger = QGroupBox("Gộp Video & Audio (Bilibili)")
        mg = QVBoxLayout(self.gb_merger)

        row_v = QHBoxLayout()
        row_v.addWidget(QLabel("File Video:"))
        self.merge_video_edit = QLineEdit()
        self.merge_video_edit.setPlaceholderText("Chọn file video (mp4, m4s...)")
        self.btn_merge_pick_video = QPushButton("Chọn...")
        self.btn_merge_pick_video.setFixedWidth(80)
        row_v.addWidget(self.merge_video_edit)
        row_v.addWidget(self.btn_merge_pick_video)
        mg.addLayout(row_v)

        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("File Audio:"))
        self.merge_audio_edit = QLineEdit()
        self.merge_audio_edit.setPlaceholderText("Chọn file audio (mp3, m4s, m4a...)")
        self.btn_merge_pick_audio = QPushButton("Chọn...")
        self.btn_merge_pick_audio.setFixedWidth(80)
        row_a.addWidget(self.merge_audio_edit)
        row_a.addWidget(self.btn_merge_pick_audio)
        mg.addLayout(row_a)

        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("File kết quả:"))
        self.merge_out_edit = QLineEdit()
        self.merge_out_edit.setPlaceholderText("Lưu thành (vd: merged.mp4)")
        self.btn_merge_pick_out = QPushButton("Chọn...")
        self.btn_merge_pick_out.setFixedWidth(80)
        row_out.addWidget(self.merge_out_edit)
        row_out.addWidget(self.btn_merge_pick_out)
        mg.addLayout(row_out)

        row_btn = QHBoxLayout()
        self.btn_run_merge = QPushButton("🚀 Bắt đầu gộp")
        self.btn_run_merge.setFixedHeight(34)
        row_btn.addStretch(1)
        row_btn.addWidget(self.btn_run_merge)
        row_btn.addStretch(1)
        mg.addLayout(row_btn)

        tab_v_merger.addWidget(self.gb_merger)
        tab_v_merger.addStretch(1)
        self.tabs.addWidget(self.tab_merger)

        # --- Tab 3: ✂️ Cắt trực tiếp (Tab riêng cho VideoTrimWidget) ---
        self.tab_trim = QWidget()
        tab_v_trim = QVBoxLayout(self.tab_trim)
        tab_v_trim.setContentsMargins(10, 10, 10, 10)
        tab_v_trim.setSpacing(6)
        self.video_trim_widget = VideoTrimWidget()
        self.video_trim_widget.log.connect(self.append_log)
        tab_v_trim.addWidget(self.video_trim_widget)
        self.tabs.addWidget(self.tab_trim)

        # --- Tab 4: Chia & Ghép Video ---
        self.tab_split_concat = QWidget()
        # Wrapper scroll area để cuộn khi nội dung nhiều
        _sc_split_inner = QWidget()
        tab_v_split_concat = QVBoxLayout(_sc_split_inner)
        tab_v_split_concat.setContentsMargins(10, 10, 10, 10)

        # Công cụ Splitter (Phase 03 + 05 Upgrade)
        self.gb_splitter = QGroupBox("Chia nhỏ video dài")
        sg = QVBoxLayout(self.gb_splitter)

        row_s_v = QHBoxLayout()
        row_s_v.addWidget(QLabel("Video gốc:"))
        self.split_video_edit = QLineEdit()
        self.split_video_edit.setPlaceholderText("Chọn video cần cắt (mp4, mkv...)")
        self.btn_split_pick_video = QPushButton("Chọn...")
        self.btn_split_pick_video.setFixedWidth(80)
        row_s_v.addWidget(self.split_video_edit)
        row_s_v.addWidget(self.btn_split_pick_video)

        # Nút load video vào player xem trước (chuyển sang tab Cắt trực tiếp)
        self.btn_split_preview = QPushButton("👁 Xem")
        self.btn_split_preview.setFixedWidth(60)
        self.btn_split_preview.setToolTip("Mở video trong tab Cắt trực tiếp")
        row_s_v.addWidget(self.btn_split_preview)

        sg.addLayout(row_s_v)

        row_s_out = QHBoxLayout()
        row_s_out.addWidget(QLabel("Thư mục lưu:"))
        self.split_out_edit = QLineEdit()
        self.split_out_edit.setPlaceholderText("Chọn folder lưu các p1, p2...")
        self.btn_split_pick_out = QPushButton("Chọn...")
        self.btn_split_pick_out.setFixedWidth(80)
        row_s_out.addWidget(self.split_out_edit)
        row_s_out.addWidget(self.btn_split_pick_out)
        sg.addLayout(row_s_out)

        row_s_cfg = QHBoxLayout()
        row_s_cfg.addWidget(QLabel("Cắt lùi (Overlap):"))
        self.split_overlap_spin = QSpinBox()
        self.split_overlap_spin.setRange(0, 60)
        self.split_overlap_spin.setValue(10)
        self.split_overlap_spin.setSuffix(" giây")
        row_s_cfg.addWidget(self.split_overlap_spin)
        row_s_cfg.addStretch(1)
        sg.addLayout(row_s_cfg)

        mode_box = QGroupBox("Chế độ cắt")
        mb = QHBoxLayout(mode_box)
        self.split_mode_auto = QRadioButton("Cắt tự động (Đều nhau)")
        self.split_mode_manual = QRadioButton("Cắt thủ công (Timeline)")
        self.split_mode_auto.setChecked(True)
        mb.addWidget(self.split_mode_auto)
        mb.addWidget(self.split_mode_manual)
        sg.addWidget(mode_box)

        # Container cho Auto mode
        self.split_pane_auto = QWidget()
        pa = QHBoxLayout(self.split_pane_auto)
        pa.setContentsMargins(0, 0, 0, 0)

        # Bên trái: Cắt theo thời lượng (mỗi phần X phút)
        row_auto_time = QHBoxLayout()
        row_auto_time.addWidget(QLabel("Cắt mỗi phần:"))
        self.split_auto_time = QSpinBox()
        self.split_auto_time.setRange(1, 600)
        self.split_auto_time.setValue(10)
        self.split_auto_time.setSuffix(" phút")
        row_auto_time.addWidget(self.split_auto_time)
        pa.addLayout(row_auto_time)

        # Khoảng cách giữa 2 phần
        pa.addStretch(1)

        # Bên phải: Cắt đều theo số lượng đoạn
        row_auto_parts = QHBoxLayout()
        self.split_use_parts = QCheckBox("Hoặc cắt đều thành:")
        self.split_use_parts.setToolTip(
            "Bật lên để chia video thành đúng số lượng đoạn bằng nhau, bỏ qua phần 'Cắt mỗi phần'."
        )
        row_auto_parts.addWidget(self.split_use_parts)

        self.split_auto_parts = QSpinBox()
        self.split_auto_parts.setRange(2, 1000)
        self.split_auto_parts.setValue(3)
        self.split_auto_parts.setSuffix(" đoạn")
        row_auto_parts.addWidget(self.split_auto_parts)
        pa.addLayout(row_auto_parts)

        pa.addStretch(1)
        sg.addWidget(self.split_pane_auto)

        # Container cho Manual mode
        self.split_pane_manual = QWidget()
        pm = QVBoxLayout(self.split_pane_manual)
        pm.setContentsMargins(0, 0, 0, 0)
        pm.addWidget(QLabel("Timeline (vd: 00:00-30:00, mỗi khoảng 1 dòng):"))
        self.split_ranges_edit = QPlainTextEdit()
        self.split_ranges_edit.setPlaceholderText("00:00 - 10:00\n10:00 - 25:30")
        self.split_ranges_edit.setFixedHeight(80)
        pm.addWidget(self.split_ranges_edit)
        sg.addWidget(self.split_pane_manual)
        self.split_pane_manual.setVisible(False)

        row_s_btn = QHBoxLayout()
        self.btn_run_split = QPushButton("✂️ Bắt đầu cắt")
        self.btn_run_split.setFixedHeight(34)
        row_s_btn.addStretch(1)
        row_s_btn.addWidget(self.btn_run_split)
        row_s_btn.addStretch(1)
        sg.addLayout(row_s_btn)

        tab_v_split_concat.addWidget(self.gb_splitter)

        # Ghép Video Nhanh
        self.gb_fast_concat = QGroupBox("Ghép Video Nhanh")
        fcg = QVBoxLayout(self.gb_fast_concat)

        fcg.addWidget(QLabel("Các video:"))

        row_fc_v = QHBoxLayout()
        self.fc_list = QListWidget()
        row_fc_v.addWidget(self.fc_list)

        fc_btn_layout = QVBoxLayout()
        self.btn_fc_pick_video = QPushButton("Chọn File...")
        self.btn_fc_up = QPushButton("Lên trên")
        self.btn_fc_down = QPushButton("Xuống dưới")
        self.btn_fc_remove = QPushButton("Xóa")

        fc_btn_layout.addWidget(self.btn_fc_pick_video)
        fc_btn_layout.addWidget(self.btn_fc_up)
        fc_btn_layout.addWidget(self.btn_fc_down)
        fc_btn_layout.addWidget(self.btn_fc_remove)
        fc_btn_layout.addStretch(1)
        row_fc_v.addLayout(fc_btn_layout)

        fcg.addLayout(row_fc_v)

        row_fc_out = QHBoxLayout()
        row_fc_out.addWidget(QLabel("Lưu thành:"))
        self.fc_out_edit = QLineEdit()
        self.fc_out_edit.setPlaceholderText("VD: D:/Downloads/fast_merged.mp4")
        self.btn_fc_pick_out = QPushButton("Chọn...")
        self.btn_fc_pick_out.setFixedWidth(80)
        row_fc_out.addWidget(self.fc_out_edit)
        row_fc_out.addWidget(self.btn_fc_pick_out)
        fcg.addLayout(row_fc_out)

        row_fc_mode = QHBoxLayout()
        self.btn_fc_check_compat = QPushButton("🔍 Kiểm tra tương thích")
        self.btn_fc_check_compat.setToolTip(
            "Kiểm tra FPS, độ phân giải và codec của tất cả video trong danh sách.\n"
            "Phát hiện video không đồng nhất có thể gây lỗi khi ghép nhanh."
        )
        row_fc_mode.addWidget(self.btn_fc_check_compat)
        row_fc_mode.addStretch(1)
        fcg.addLayout(row_fc_mode)

        row_fc_btn = QHBoxLayout()
        self.btn_run_fast_concat = QPushButton("⚡ Bắt đầu ghép nhanh")
        self.btn_run_fast_concat.setFixedHeight(34)
        row_fc_btn.addStretch(1)
        row_fc_btn.addWidget(self.btn_run_fast_concat)
        row_fc_btn.addStretch(1)
        fcg.addLayout(row_fc_btn)

        tab_v_split_concat.addWidget(self.gb_fast_concat)
        tab_v_split_concat.addStretch(1)

        # Wrap nội dung trong QScrollArea
        _sc_split = QScrollArea()
        _sc_split.setWidgetResizable(True)
        _sc_split.setWidget(_sc_split_inner)
        _sc_split.setFrameShape(QFrame.NoFrame)
        _sc_split_layout = QVBoxLayout(self.tab_split_concat)
        _sc_split_layout.setContentsMargins(0, 0, 0, 0)
        _sc_split_layout.addWidget(_sc_split)

        self.tabs.addWidget(self.tab_split_concat)

        # --- Tab 5: Cài đặt (Settings) ---
        self.tab_settings = QWidget()
        settings_layout = QVBoxLayout(self.tab_settings)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.setSpacing(14)

        # === Section 1: Đăng nhập Bilibili QR (Firefox) ===
        gb_qr = QGroupBox("🔐 Đăng nhập Bilibili bằng QR (Firefox)")
        qr_layout = QVBoxLayout(gb_qr)
        qr_layout.setSpacing(10)

        qr_desc = QLabel(
            "Quét QR code để đăng nhập Bilibili qua trình duyệt Firefox ẩn. "
            "Cookie sẽ được lưu tự động — không cần nhập thủ công."
        )
        qr_desc.setWordWrap(True)
        qr_desc.setObjectName("muted")
        qr_layout.addWidget(qr_desc)

        # QR Code display area
        qr_display = QHBoxLayout()
        qr_display.addStretch(1)

        qr_center = QVBoxLayout()
        qr_center.setAlignment(Qt.AlignCenter)

        self.qr_container = QFrame()
        self.qr_container.setObjectName("qrContainer")
        self.qr_container.setFixedSize(320, 320)
        qr_inner = QVBoxLayout(self.qr_container)
        qr_inner.setContentsMargins(0, 0, 0, 0)
        qr_inner.setAlignment(Qt.AlignCenter)
        self.qr_image_label = QLabel("QR Code")
        self.qr_image_label.setObjectName("qrImageLabel")
        self.qr_image_label.setFixedSize(280, 280)
        self.qr_image_label.setAlignment(Qt.AlignCenter)
        self.qr_image_label.setText("📷")
        self.qr_image_label.setToolTip("Nhấn vào QR để phóng to")
        self.qr_image_label.setCursor(Qt.PointingHandCursor)
        self.qr_image_label.mousePressEvent = self._qr_image_clicked
        self.qr_image_label.setStyleSheet(
            "font-size: 48px; background: rgba(255,255,255,0.95); border-radius: 12px;"
        )
        qr_inner.addWidget(self.qr_image_label)
        qr_center.addWidget(self.qr_container)

        # Status & Timer
        self.qr_status_label = QLabel("Nhấn 'Tạo mã QR' để bắt đầu")
        self.qr_status_label.setObjectName("qrStatusLabel")
        self.qr_status_label.setAlignment(Qt.AlignCenter)
        qr_center.addWidget(self.qr_status_label)

        timer_row = QHBoxLayout()
        timer_row.setAlignment(Qt.AlignCenter)
        timer_row.addWidget(QLabel("Hết hạn sau:"))
        self.qr_timer_label = QLabel("--:--")
        self.qr_timer_label.setObjectName("qrTimerLabel")
        timer_row.addWidget(self.qr_timer_label)
        qr_center.addLayout(timer_row)

        scan_status = QHBoxLayout()
        scan_status.setAlignment(Qt.AlignCenter)
        self.qr_scan_status = QLabel("")
        self.qr_scan_status.setObjectName("qrStatusLabel")
        scan_status.addWidget(self.qr_scan_status)
        qr_center.addLayout(scan_status)

        qr_display.addLayout(qr_center)
        qr_display.addStretch(1)
        qr_layout.addLayout(qr_display)

        # Action buttons
        qr_btn_row = QHBoxLayout()
        qr_btn_row.setAlignment(Qt.AlignCenter)
        self.btn_qr_refresh = QPushButton("🔄 Tạo mã QR mới")
        self.btn_qr_refresh.setObjectName("qrRefreshBtn")
        self.btn_view_cookies = QPushButton("📋 Xem cookie đã lưu")
        self.btn_view_cookies.setObjectName("viewCookieBtn")
        qr_btn_row.addWidget(self.btn_qr_refresh)
        qr_btn_row.addWidget(self.btn_view_cookies)
        qr_layout.addLayout(qr_btn_row)

        # Info banner
        info_banner = QFrame()
        info_banner.setObjectName("infoBanner")
        info_inner = QHBoxLayout(info_banner)
        info_inner.setContentsMargins(12, 8, 12, 8)
        info_icon = QLabel("ℹ️")
        info_icon.setFixedWidth(24)
        info_inner.addWidget(info_icon)
        info_text = QLabel(
            "App sử dụng Firefox để xử lý đăng nhập Bilibili. "
            "Cookie được lưu an toàn trong thư mục .runtime/"
        )
        info_text.setWordWrap(True)
        info_text.setObjectName("muted")
        info_inner.addWidget(info_text, 1)
        qr_layout.addWidget(info_banner)

        settings_layout.addWidget(gb_qr)

        # === Section 2: Cập nhật yt-dlp & FFmpeg ===
        gb_update = QGroupBox("🔄 Cập nhật yt-dlp & FFmpeg")
        update_layout = QVBoxLayout(gb_update)
        update_layout.setSpacing(12)

        # 2 version cards in a row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        # Card 1: yt-dlp
        card_ytdlp = QFrame()
        card_ytdlp.setObjectName("versionCard")
        c1 = QVBoxLayout(card_ytdlp)
        c1_header = QHBoxLayout()
        c1_icon = QLabel("⬇")
        c1_icon.setFixedWidth(24)
        c1_header.addWidget(c1_icon)
        c1_name = QLabel("yt-dlp")
        c1_name.setObjectName("versionName")
        c1_header.addWidget(c1_name)
        c1_header.addStretch(1)
        c1.addLayout(c1_header)

        c1_ver = QHBoxLayout()
        c1_ver.addWidget(QLabel("Hiện tại:"))
        self.ytdlp_version_badge = QLabel("chưa kiểm tra")
        self.ytdlp_version_badge.setObjectName("versionBadge")
        c1_ver.addWidget(self.ytdlp_version_badge)
        c1_ver.addStretch(1)
        c1.addLayout(c1_ver)

        c1_btns = QHBoxLayout()
        self.btn_check_ytdlp = QPushButton("🔍 Kiểm tra")
        self.btn_check_ytdlp.setFixedHeight(30)
        self.btn_update_ytdlp = QPushButton("⬆ Cập nhật")
        self.btn_update_ytdlp.setFixedHeight(30)
        self.btn_update_ytdlp.setObjectName("updateBtn")
        c1_btns.addWidget(self.btn_check_ytdlp)
        c1_btns.addWidget(self.btn_update_ytdlp)
        c1.addLayout(c1_btns)
        cards_row.addWidget(card_ytdlp)

        # Card 2: FFmpeg
        card_ffmpeg = QFrame()
        card_ffmpeg.setObjectName("versionCard")
        c2 = QVBoxLayout(card_ffmpeg)
        c2_header = QHBoxLayout()
        c2_icon = QLabel("🎬")
        c2_icon.setFixedWidth(24)
        c2_header.addWidget(c2_icon)
        c2_name = QLabel("FFmpeg")
        c2_name.setObjectName("versionName")
        c2_header.addWidget(c2_name)
        c2_header.addStretch(1)
        c2.addLayout(c2_header)

        c2_ver = QHBoxLayout()
        c2_ver.addWidget(QLabel("Hiện tại:"))
        self.ffmpeg_version_badge = QLabel("chưa kiểm tra")
        self.ffmpeg_version_badge.setObjectName("versionBadge")
        c2_ver.addWidget(self.ffmpeg_version_badge)
        c2_ver.addStretch(1)
        c2.addLayout(c2_ver)

        c2_btns = QHBoxLayout()
        self.btn_check_ffmpeg = QPushButton("🔍 Kiểm tra")
        self.btn_check_ffmpeg.setFixedHeight(30)
        self.btn_update_ffmpeg = QPushButton("⬆ Cập nhật")
        self.btn_update_ffmpeg.setFixedHeight(30)
        self.btn_update_ffmpeg.setObjectName("updateBtn")
        c2_btns.addWidget(self.btn_check_ffmpeg)
        c2_btns.addWidget(self.btn_update_ffmpeg)
        c2.addLayout(c2_btns)
        cards_row.addWidget(card_ffmpeg)

        update_layout.addLayout(cards_row)

        # Check all button
        check_all_row = QHBoxLayout()
        check_all_row.addStretch(1)
        self.btn_check_all_updates = QPushButton("🔄 Kiểm tra tất cả")
        self.btn_check_all_updates.setObjectName("scanBtn")
        self.btn_check_all_updates.setFixedHeight(36)
        check_all_row.addWidget(self.btn_check_all_updates)
        check_all_row.addStretch(1)
        update_layout.addLayout(check_all_row)

        settings_layout.addWidget(gb_update)
        settings_layout.addStretch(1)

        self.tabs.addWidget(self.tab_settings)

        # Tabs đã được quản lý ở root layout

        # Panel phải: tiến trình + log layout dọc, chiếm khoảng 1/4 chiều rộng
        right = QFrame()
        right.setObjectName("rightPanel")
        # Không đặt min/max width cố định — để stretch factor tự phân bổ
        right.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        r = QVBoxLayout(right)
        r.setContentsMargins(12, 12, 12, 12)
        r.setSpacing(12)

        # --- Progress section (flat, matching Figma) ---
        progress_header = QLabel("📊 Tiến trình")
        progress_header.setObjectName("sectionHeader")
        r.addWidget(progress_header)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        r.addWidget(self.progress)

        self.progress_text = QLabel("0% - Sẵn sàng")
        self.progress_text.setObjectName("muted")
        self.progress_text.setAlignment(Qt.AlignCenter)
        r.addWidget(self.progress_text)

        r.addSpacing(8)

        # --- Log section ---
        log_header = QLabel("📝 Nhật ký hoạt động")
        log_header.setObjectName("sectionHeader")
        r.addWidget(log_header)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        r.addWidget(self.log_box, 1)

        log_btns = QHBoxLayout()
        self.btn_clear_log = QPushButton("🗑 Clear")
        self.btn_clear_log.setObjectName("logBtn")
        self.btn_save_log = QPushButton("💾 Save")
        self.btn_save_log.setObjectName("logBtn")
        self.btn_open_log_popup = QPushButton("↗ Extend")
        self.btn_open_log_popup.setObjectName("logBtn")
        self.btn_open_log_popup.setToolTip(
            "Mở nhật ký hoạt động trong cửa sổ riêng, rộng rãi hơn để dễ xem."
        )
        log_btns.addWidget(self.btn_clear_log)
        log_btns.addWidget(self.btn_save_log)
        log_btns.addWidget(self.btn_open_log_popup)
        log_btns.addStretch(1)
        r.addLayout(log_btns)

        # Root layout chia tabs và right panel

        self.global_sidebar = self._build_new_sidebar()
        root.insertWidget(0, self.global_sidebar)
        root.addWidget(self.tabs, 5)
        root.addWidget(right, 2)

        # signals
        self.btn_browse.clicked.connect(self.choose_dir)
        self.btn_scan.clicked.connect(self.scan_list)
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_clear.clicked.connect(self.clear_selection)
        self.btn_download.clicked.connect(self.download_selected)
        self.btn_download_thumbs.clicked.connect(self.download_thumbnails_selected)
        self.btn_stop.clicked.connect(self.stop_all)
        self.btn_clear_log.clicked.connect(self.log_box.clear)
        self.btn_save_log.clicked.connect(self.save_log)
        self.btn_open_log_popup.clicked.connect(self.open_log_popup)
        self.btn_profile_save.clicked.connect(self.save_cookie_profile)
        self.btn_profile_delete.clicked.connect(self.delete_cookie_profile)
        self.cookie_profile_combo.currentIndexChanged.connect(
            self.on_cookie_profile_changed
        )
        self.btn_test_cookie.clicked.connect(self.test_auto_cookie)
        self.browser_combo.currentIndexChanged.connect(self.on_browser_changed)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        self.chk_show_cookie_advanced.toggled.connect(self.cookie_adv_wrap.setVisible)
        self.sort_group.buttonClicked.connect(self.on_sort_changed)
        self.btn_select_top_views.clicked.connect(self.select_top_views)
        self.btn_select_top_likes.clicked.connect(self.select_top_likes)
        self.btn_select_oldest.clicked.connect(self.select_oldest_video)
        self.btn_select_by_month.clicked.connect(self.select_videos_by_month)
        self.btn_open_large_view.clicked.connect(self.open_large_view)

        # Merge signals
        self.btn_merge_pick_video.clicked.connect(self.pick_merge_video)
        self.btn_merge_pick_audio.clicked.connect(self.pick_merge_audio)
        self.btn_merge_pick_out.clicked.connect(self.pick_merge_out)
        self.btn_run_merge.clicked.connect(self.run_merge)

        # Split signals
        self.btn_split_pick_video.clicked.connect(self.pick_split_video)
        self.btn_split_pick_out.clicked.connect(self.pick_split_out)
        self.btn_run_split.clicked.connect(self.run_split)
        self.split_mode_auto.toggled.connect(self._toggle_split_panes)
        self.split_mode_manual.toggled.connect(self._toggle_split_panes)
        self.btn_split_preview.clicked.connect(self._load_video_preview)

        # Fast Concat signals
        self.btn_fc_pick_video.clicked.connect(self.pick_fc_video)
        self.btn_fc_up.clicked.connect(self.fc_move_up)
        self.btn_fc_down.clicked.connect(self.fc_move_down)
        self.btn_fc_remove.clicked.connect(self.fc_remove_item)
        self.btn_fc_pick_out.clicked.connect(self.pick_fc_out)
        self.btn_fc_check_compat.clicked.connect(self.check_fc_compat)
        self.btn_run_fast_concat.clicked.connect(self.run_fast_concat)

        # Settings tab signals
        self.btn_qr_refresh.clicked.connect(self._on_qr_refresh)
        self.btn_view_cookies.clicked.connect(self._on_view_cookies)
        self.btn_check_ytdlp.clicked.connect(self._check_ytdlp_version)
        self.btn_check_ffmpeg.clicked.connect(self._check_ffmpeg_version)
        self.btn_update_ytdlp.clicked.connect(self._update_ytdlp)
        self.btn_update_ffmpeg.clicked.connect(self._update_ffmpeg)
        self.btn_check_all_updates.clicked.connect(self._check_all_updates)

        self.cookie_adv_wrap.setVisible(False)


    def _switch_nav(self, widget, reup_page=None):
        self.tabs.setCurrentWidget(widget)
        if reup_page and hasattr(self.tab_reup, "switch_page"):
            self.tab_reup.switch_page(reup_page)

    def _build_new_sidebar(self):
        from PyQt5.QtWidgets import QFrame, QVBoxLayout, QPushButton, QLabel, QSpacerItem, QSizePolicy
        from PyQt5.QtCore import Qt
        
        sidebar = QFrame()
        sidebar.setObjectName("newSidebar")
        sidebar.setFixedWidth(240)
        
        # Applying rough CSS matching ui.pen
        sidebar.setStyleSheet("""
            QFrame#newSidebar {
                background-color: #1e1e2e; /* $bgSecondary */
                border-right: 1px solid #2d2d3f;
            }
            QPushButton.navBtn {
                text-align: left;
                padding: 12px 16px;
                background-color: transparent;
                color: #F1F5F9;
                border-radius: 8px;
                font-family: 'Inter', sans-serif;
                font-size: 14px;
                font-weight: 600;
                border: none;
            }
            QPushButton.navBtn:hover {
                background-color: #2d2d3f;
            }
            QPushButton.navBtn:checked {
                background-color: rgba(139, 92, 246, 0.2);
                color: #8b5cf6;
            }
            QLabel#logoText {
                color: #F1F5F9;
                font-size: 20px;
                font-weight: bold;
                font-family: 'Inter', sans-serif;
                margin: 8px;
            }
        """)
        
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 32, 20, 24)
        layout.setSpacing(8)
        
        logo_layout = QHBoxLayout()
        logo_layout.setContentsMargins(0, 0, 0, 0)
        
        logo_icon = QLabel()
        from PyQt5.QtGui import QPixmap
        import os
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            logo_icon.setPixmap(pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_layout.addWidget(logo_icon)
        
        logo = QLabel("Video Tool")
        logo.setObjectName("logoText")
        logo_layout.addWidget(logo)
        logo_layout.addStretch()
        
        layout.addLayout(logo_layout)
        layout.addSpacing(24)
        
        # Buttons
        routes = [
            ("So sánh phụ đề", "languages", lambda: self._switch_nav(self.tab_reup, "compare")),
            ("Chia phụ đề", "scissors", lambda: self._switch_nav(self.tab_reup, "split")),
            ("Tải video", "download", lambda: self._switch_nav(self.tab_download)),
            ("Cắt trực tiếp", "crop", lambda: self._switch_nav(self.tab_trim)),
            ("Ghép & Tách Video", "split-square-vertical", lambda: self._switch_nav(self.tab_split_concat)),
            ("Gộp Video", "combine", lambda: self._switch_nav(self.tab_merger)),
            ("Cài đặt chung", "settings", lambda: self._switch_nav(self.tab_settings)),
        ]
        
        self.nav_buttons = []
        for text_label, icon, slot in routes:
            btn = QPushButton(text_label)
            btn.setProperty("class", "navBtn")
            btn.setCheckable(True)
            # Use a lambda that captures the slot, and sets checked state
            def make_slot(s, b):
                def handler():
                    for ob in self.nav_buttons:
                        if ob != b: ob.setChecked(False)
                    b.setChecked(True)
                    s()
                return handler
            
            btn.clicked.connect(make_slot(slot, btn))
            layout.addWidget(btn)
            self.nav_buttons.append(btn)
            
        layout.addStretch(1)
        
        # Set default
        if self.nav_buttons:
            self.nav_buttons[0].setChecked(True)
            
        return sidebar
    def on_mode_changed(self):
        """Bật/tắt combobox chất lượng khi chọn Video/Audio."""
        is_video = self.mode_combo.currentData() == "video"
        self.quality_combo.setEnabled(is_video)

    def _restore_settings(self):
        """Khôi phục cài đặt đã lưu từ QSettings vào các widget UI."""
        # --- Tab Tải video ---
        browser = self.settings.value("browser", "none")
        idx = self.browser_combo.findData(browser)
        if idx >= 0:
            self.browser_combo.setCurrentIndex(idx)

        quality = self.settings.value("video_quality", "best")
        idx = self.quality_combo.findData(quality)
        if idx >= 0:
            self.quality_combo.setCurrentIndex(idx)

        overwrite = self.settings.value("overwrite_mode", "skip")
        idx = self.overwrite_combo.findData(overwrite)
        if idx >= 0:
            self.overwrite_combo.setCurrentIndex(idx)

        thumbnail = self.settings.value("include_thumbnail", True, type=bool)
        self.chk_thumbnail.setChecked(thumbnail)

        force_codec = self.settings.value("force_codec", True, type=bool)
        self.chk_force_codec.setChecked(force_codec)
        self.codec_combo.setEnabled(force_codec)

        codec = self.settings.value("codec", "h264")
        idx = self.codec_combo.findData(codec)
        if idx >= 0:
            self.codec_combo.setCurrentIndex(idx)

        engine = self.settings.value("download_engine", "ytdlp")
        idx = self.engine_combo.findData(engine)
        if idx >= 0:
            self.engine_combo.setCurrentIndex(idx)

        # Kết nối tín hiệu lưu cài đặt
        self.browser_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue("browser", self.browser_combo.currentData())
        )
        self.quality_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue(
                "video_quality", self.quality_combo.currentData()
            )
        )
        self.overwrite_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue(
                "overwrite_mode", self.overwrite_combo.currentData()
            )
        )
        self.chk_thumbnail.toggled.connect(
            lambda v: self.settings.setValue("include_thumbnail", v)
        )
        self.chk_force_codec.toggled.connect(
            lambda v: self.settings.setValue("force_codec", v)
        )
        self.codec_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue("codec", self.codec_combo.currentData())
        )
        self.engine_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue(
                "download_engine", self.engine_combo.currentData()
            )
        )

    def _apply_style(self):
        self.setStyleSheet("""
        /* ═══════════════════════════════════════════════════ */
        /*  FIGMA DESIGN v3 - Figma-Matched Dark Theme       */
        /* ═══════════════════════════════════════════════════ */

        /* === Base === */
        QWidget {
            background: #1a1a2e;
            color: #F1F5F9;
            font-family: ".AppleSystemUIFont", "Helvetica Neue", "Segoe UI", "Inter", sans-serif;
            font-size: 14px;
        }

        /* === Sidebar === */
        QFrame#sidebar {
            background: #1a1a2e;
            border-right: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 0px;
        }
        QLabel#brand {
            font-size: 19px;
            font-weight: 800;
            color: #FFFFFF;
            letter-spacing: 0.5px;
        }
        QLabel#statusChip {
            background: #188038;
            color: #e6f4ea;
            border-radius: 14px;
            font-weight: 700;
            padding: 4px 10px;
        }
        QLabel#muted {
            color: #9ca3af;
            font-size: 12px;
        }
        QLabel#footerStatus {
            color: #4ADE80;
            font-weight: 700;
            font-size: 13px;
        }

        /* === GroupBox (Cards) === */
        QGroupBox {
            background: rgba(22, 33, 62, 0.5);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            margin-top: 12px;
            font-weight: 700;
            font-size: 14px;
            padding-top: 18px;
            color: #CBD5E1;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 16px;
            padding: 0 8px;
            color: #F1F5F9;
        }

        /* === Input Fields === */
        QTextEdit, QLineEdit, QPlainTextEdit {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 8px 12px;
            color: #F1F5F9;
            selection-background-color: #3B82F6;
        }
        QTextEdit:focus, QLineEdit:focus, QPlainTextEdit:focus {
            border: 1px solid #3B82F6;
        }

        /* === ComboBox === */
        QComboBox {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 6px 28px 6px 10px;
            color: #F1F5F9;
            min-height: 20px;
        }
        QComboBox:focus { border: 1px solid #3B82F6; }
        QComboBox::drop-down {
            border: none;
            background: transparent;
            width: 24px;
            border-top-right-radius: 6px;
            border-bottom-right-radius: 6px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0; height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #94A3B8;
            margin-right: 6px;
        }
        QComboBox QAbstractItemView {
            background: #1a1a2e;
            border: 1px solid rgba(255, 255, 255, 0.15);
            selection-background-color: #1E3A5F;
            color: #F1F5F9;
            outline: none;
        }

        /* === Table === */
        QTableWidget {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 6px;
            padding: 0px;
            gridline-color: rgba(255, 255, 255, 0.05);
            alternate-background-color: rgba(0, 0, 0, 0.1);
            selection-background-color: rgba(255, 255, 255, 0.05);
        }
        QHeaderView::section {
            background: #1a1a2e;
            color: #CBD5E1;
            border: none;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding: 8px 6px;
            font-weight: 600;
            font-size: 12px;
        }

        /* === Default Buttons === */
        QPushButton {
            background: #374151;
            color: #F1F5F9;
            border: none;
            border-radius: 8px;
            padding: 9px 14px;
            font-weight: 600;
            font-size: 13px;
        }
        QPushButton:hover { background: #4b5563; }
        QPushButton:pressed { background: #374151; }
        QPushButton:disabled { background: #1f2937; color: #475569; }

        /* --- Scan Button (Blue Solid) --- */
        QPushButton#scanBtn {
            background: #2563eb;
            color: white;
            font-size: 14px;
            padding: 10px 14px;
        }
        QPushButton#scanBtn:hover {
            background: #3b82f6;
        }
        QPushButton#scanBtn:pressed {
            background: #1d4ed8;
        }

        /* --- Download Button (Rose-Pink Gradient - Figma matched) --- */
        QPushButton#downloadBtn {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #e94560, stop:1 #ff6b9d);
            color: white;
            font-size: 14px;
            font-weight: 700;
            padding: 10px 14px;
        }
        QPushButton#downloadBtn:hover {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #d13952, stop:1 #e95a8a);
        }
        QPushButton#downloadBtn:pressed {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #be2f46, stop:1 #d4497b);
        }

        /* --- Thumbnail Button --- */
        QPushButton#thumbBtn {
            background: #374151;
            color: #CBD5E1;
        }
        QPushButton#thumbBtn:hover {
            background: #4b5563;
            color: white;
        }

        /* --- Outline Buttons (Select/Clear/Top) --- */
        QPushButton#outlineBtn {
            background: transparent;
            color: #CBD5E1;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        QPushButton#outlineBtn:hover {
            background: rgba(255, 255, 255, 0.05);
            color: white;
            border-color: rgba(255, 255, 255, 0.4);
        }

        /* --- Danger Button (Stop) --- */
        QPushButton#dangerBtn {
            background: #dc2626;
            color: white;
        }
        QPushButton#dangerBtn:hover {
            background: #ef4444;
        }
        QPushButton#dangerBtn:disabled {
            background: rgba(220, 38, 38, 0.4);
            color: rgba(255, 255, 255, 0.4);
        }

        /* --- Test Cookie Button (Teal) --- */
        QPushButton#testCookieBtn {
            background: #0d9488;
            color: white;
            font-size: 13px;
            padding: 10px 14px;
        }
        QPushButton#testCookieBtn:hover {
            background: #14b8a6;
        }

        /* --- Gemini Buttons --- */
        /* --- Log Panel Buttons --- */
        QPushButton#logBtn {
            background: #374151;
            color: #94A3B8;
            padding: 6px 12px;
            font-size: 12px;
        }
        QPushButton#logBtn:hover {
            background: #4b5563;
            color: white;
        }

        /* === Browse / Choose Buttons (in rows) === */
        QPushButton[text="📁 Chọn thư mục"], QPushButton[text="Chọn..."],
        QPushButton[text="Chọn binary..."], QPushButton[text="Chọn File..."],
        QPushButton[text="Chọn..."] {
            background: #374151;
        }
        QPushButton[text="📁 Chọn thư mục"]:hover, QPushButton[text="Chọn..."]:hover,
        QPushButton[text="Chọn binary..."]:hover, QPushButton[text="Chọn File..."]:hover {
            background: #4b5563;
        }

        /* === Progress Bar === */
        QProgressBar {
            border: none;
            border-radius: 4px;
            text-align: center;
            background: rgba(0, 0, 0, 0.5);
            min-height: 10px;
            max-height: 10px;
            color: transparent;
        }
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #3B82F6, stop:0.5 #8B5CF6, stop:1 #EC4899);
            border-radius: 4px;
        }

        /* === Tab Widget (Pill-style tabs matching Figma) === */
        QTabWidget::pane {
            border: none;
            background: #1a1a2e;
        }
        QTabBar {
            background: rgba(26, 26, 46, 0.8);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }
        QTabBar::tab {
            background: transparent;
            color: #9ca3af;
            border: none;
            border-radius: 20px;
            padding: 8px 16px;
            min-width: 100px;
            font-weight: 500;
            font-size: 13px;
            margin: 4px 2px;
        }
        QTabBar::tab:selected {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #2563eb, stop:1 #7c3aed);
            color: white;
            font-weight: 600;
        }
        QTabBar::tab:!selected:hover {
            background: rgba(255, 255, 255, 0.05);
            color: white;
        }

        /* === Checkbox & RadioButton === */
        QCheckBox {
            spacing: 8px;
            color: #CBD5E1;
        }
        QCheckBox::indicator {
            width: 16px; height: 16px;
            border-radius: 4px;
            border: 1.5px solid rgba(255, 255, 255, 0.2);
            background: transparent;
        }
        QCheckBox::indicator:checked {
            background: #3B82F6;
            border-color: #3B82F6;
            image: none;
        }
        QCheckBox::indicator:hover {
            border-color: #3B82F6;
        }
        QRadioButton {
            spacing: 8px;
            color: #CBD5E1;
        }
        QRadioButton::indicator {
            width: 16px; height: 16px;
            border-radius: 8px;
            border: 1.5px solid rgba(255, 255, 255, 0.2);
            background: transparent;
        }
        QRadioButton::indicator:checked {
            background: #3B82F6;
            border-color: #3B82F6;
        }
        QRadioButton::indicator:hover {
            border-color: #3B82F6;
        }

        /* === SpinBox === */
        QSpinBox {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 6px 10px;
            color: #F1F5F9;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background: rgba(255, 255, 255, 0.05);
            border: none;
            width: 20px;
        }
        QSpinBox::up-arrow { border-bottom: 5px solid #94A3B8; border-left: 4px solid transparent; border-right: 4px solid transparent; width:0; height:0; }
        QSpinBox::down-arrow { border-top: 5px solid #94A3B8; border-left: 4px solid transparent; border-right: 4px solid transparent; width:0; height:0; }

        /* === DateEdit === */
        QDateEdit {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 6px 10px;
            color: #F1F5F9;
        }

        /* === Scrollbar === */
        QScrollBar:vertical {
            background: rgba(0, 0, 0, 0.2);
            width: 8px;
            border-radius: 4px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.2);
            min-height: 30px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.3); }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

        QScrollBar:horizontal {
            background: rgba(0, 0, 0, 0.2);
            height: 8px;
            border-radius: 4px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: rgba(255, 255, 255, 0.2);
            min-width: 30px;
            border-radius: 4px;
        }
        QScrollBar::handle:horizontal:hover { background: rgba(255, 255, 255, 0.3); }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

        /* === ListWidget === */
        QListWidget {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 4px;
            color: #F1F5F9;
        }
        QListWidget::item {
            padding: 6px 10px;
            border-radius: 6px;
        }
        QListWidget::item:selected {
            background: rgba(37, 99, 235, 0.2);
        }
        QListWidget::item:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        /* === ToolTip === */
        QToolTip {
            background: #1a1a2e;
            color: #F1F5F9;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 12px;
        }

        /* === Dialog === */
        QDialog {
            background: #1a1a2e;
        }

        /* === MessageBox === */
        QMessageBox {
            background: #1a1a2e;
        }

        /* ═══════════════════════════════════════════════════ */
        /*  SETTINGS TAB - Figma Styles                       */
        /* ═══════════════════════════════════════════════════ */

        /* --- QR Section --- */
        QFrame#qrContainer {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #3B82F6, stop:0.5 #8B5CF6, stop:1 #EC4899);
            border-radius: 16px;
            padding: 20px;
        }
        QLabel#qrImageLabel {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 12px;
            padding: 16px;
        }
        QLabel#qrStatusLabel {
            color: #94A3B8;
            font-size: 13px;
        }
        QLabel#qrTimerLabel {
            color: #fbbf24;
            font-size: 16px;
            font-weight: 700;
        }

        /* --- Version Cards --- */
        QFrame#versionCard {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 16px;
        }
        QLabel#versionName {
            font-size: 15px;
            font-weight: 700;
            color: #F1F5F9;
        }
        QLabel#versionBadge {
            background: rgba(22, 163, 74, 0.2);
            color: #4ADE80;
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-radius: 10px;
            padding: 2px 10px;
            font-size: 12px;
            font-weight: 700;
        }
        QLabel#versionBadgeWarning {
            background: rgba(234, 179, 8, 0.2);
            color: #FDE68A;
            border: 1px solid rgba(234, 179, 8, 0.3);
            border-radius: 10px;
            padding: 2px 10px;
            font-size: 12px;
            font-weight: 700;
        }

        /* --- Info Banner --- */
        QFrame#infoBanner {
            background: rgba(37, 99, 235, 0.1);
            border: 1px solid rgba(59, 130, 246, 0.3);
            border-radius: 10px;
            padding: 12px 16px;
        }

        /* --- Settings Buttons --- */
        QPushButton#qrRefreshBtn {
            background: #2563eb;
            color: white;
            font-size: 14px;
            padding: 10px 20px;
        }
        QPushButton#qrRefreshBtn:hover {
            background: #3b82f6;
        }
        QPushButton#viewCookieBtn {
            background: #374151;
            color: #CBD5E1;
            font-size: 14px;
            padding: 10px 20px;
        }
        QPushButton#viewCookieBtn:hover {
            background: #4b5563;
            color: white;
        }

        /* === Right Panel === */
        QFrame#rightPanel {
            background: #1a1a2e;
            border-left: 1px solid rgba(255, 255, 255, 0.1);
        }
        QLabel#sectionHeader {
            color: #F1F5F9;
            font-size: 14px;
            font-weight: 700;
            padding-bottom: 4px;
        }
        """
        )

    def now(self):
        return datetime.now().strftime("%H:%M:%S")

    def append_log(self, msg):
        text = f"[{self.now()}] {msg}"
        self.log_box.append(text)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

        # Nếu đang mở cửa sổ log lớn thì cập nhật song song
        if hasattr(self, "_log_popup_box") and self._log_popup_box is not None:
            self._log_popup_box.append(text)
            self._log_popup_box.verticalScrollBar().setValue(
                self._log_popup_box.verticalScrollBar().maximum()
            )

    def set_busy(self, busy):
        self.btn_scan.setEnabled(not busy)
        self.btn_download.setEnabled(not busy)
        self.btn_select_all.setEnabled(not busy)
        self.btn_clear.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        st = "Đang chạy" if busy else "Sẵn sàng"
        # status_chip là optional (có thể đã bị bỏ khỏi UI), nên cần kiểm tra trước khi set
        if hasattr(self, "status_chip"):
            self.status_chip.setText(st)
        self.footer_status.setText(f"● Trạng thái: {st}")

    def choose_dir(self):
        # Lấy đường dẫn hiện tại hoặc đường dẫn đã lưu
        current_dir = self.out_dir.text().strip()
        if not current_dir or not os.path.exists(current_dir):
            # Nếu đường dẫn hiện tại không hợp lệ, lấy từ QSettings
            current_dir = self.settings.value("download_out_dir", "")
            if not current_dir:
                current_dir = str(Path.home() / "Downloads")

        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu", current_dir)
        if d:
            self.out_dir.setText(d)
            # Lưu đường dẫn để nhớ cho lần sau
            self.settings.setValue("download_out_dir", d)

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu log", "download_log.txt", "Text (*.txt)"
        )
        if path:
            Path(path).write_text(self.log_box.toPlainText(), encoding="utf-8")
            self.append_log(f"💾 Đã lưu log: {path}")

    def open_log_popup(self):
        """Mở cửa sổ popup hiển thị log ở kích thước lớn hơn."""
        if not hasattr(self, "_log_popup") or self._log_popup is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Nhật ký hoạt động - Cửa sổ lớn")
            dlg.resize(900, 600)

            layout = QVBoxLayout(dlg)
            self._log_popup_box = QTextEdit()
            self._log_popup_box.setReadOnly(True)
            layout.addWidget(self._log_popup_box)

            btn_row = QHBoxLayout()
            btn_close = QPushButton("Đóng")
            btn_close.clicked.connect(dlg.close)
            btn_row.addStretch(1)
            btn_row.addWidget(btn_close)
            layout.addLayout(btn_row)

            self._log_popup = dlg

        # Đồng bộ nội dung hiện tại trước khi hiển thị
        if hasattr(self, "_log_popup_box") and self._log_popup_box is not None:
            self._log_popup_box.setPlainText(self.log_box.toPlainText())
            self._log_popup_box.verticalScrollBar().setValue(
                self._log_popup_box.verticalScrollBar().maximum()
            )

        self._log_popup.show()
        self._log_popup.raise_()
        self._log_popup.activateWindow()

    # Đã bỏ tính năng chạy lại bằng Admin theo yêu cầu người dùng.

    def _source_urls(self):
        import re
        url_pattern = re.compile(r'https?://[^\s<>"\'，。！？、）\)]+')
        results = []
        for line in self.source_input.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            # Nếu dòng chỉ chứa URL thuần (không có text thừa)
            if line.startswith("http://") or line.startswith("https://"):
                # Vẫn có thể có text thừa sau URL (ví dụ: "https://... 复制此链接")
                urls = url_pattern.findall(line)
                if urls:
                    results.extend(urls)
                else:
                    results.append(line)
            else:
                # Dòng chứa text lẫn URL (share text từ Douyin, TikTok, etc.)
                urls = url_pattern.findall(line)
                if urls:
                    results.extend(urls)
                # Nếu không tìm thấy URL, bỏ qua dòng này
        return results

    def _detect_browser_profiles(self, browser_name):
        roots = {
            "chrome": Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/User Data",
            "edge": Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft/Edge/User Data",
            "brave": Path(os.environ.get("LOCALAPPDATA", ""))
            / "BraveSoftware/Brave-Browser/User Data",
            "chromium": Path(os.environ.get("LOCALAPPDATA", "")) / "Chromium/User Data",
            "opera": Path(os.environ.get("APPDATA", ""))
            / "Opera Software/Opera Stable",
        }
        root = roots.get(browser_name)
        if not root or not root.exists():
            return ["auto"]

        names = ["auto"]
        if browser_name == "opera":
            # Opera chủ yếu 1 profile chính
            return names + ["Opera Stable"]

        for p in root.iterdir():
            if not p.is_dir():
                continue
            n = p.name
            if n == "Default" or n.startswith("Profile "):
                names.append(n)
        # unique giữ thứ tự
        out = []
        seen = set()
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def on_browser_changed(self):
        b = self.browser_combo.currentText()
        profiles = self._detect_browser_profiles(b)
        self.browser_profile_combo.blockSignals(True)
        self.browser_profile_combo.clear()
        for p in profiles:
            self.browser_profile_combo.addItem(p, p)
        self.browser_profile_combo.blockSignals(False)

    def test_auto_cookie(self):
        """Test cookie: thử browser_cookie3 trước, nếu thất bại thì dùng --cookies-from-browser yt-dlp."""
        urls = self._source_urls()
        if not urls:
            QMessageBox.information(
                self, "Thiếu URL", "Hãy nhập URL để app biết domain cần test cookie."
            )
            return
        profile = self.browser_profile_combo.currentData() or "auto"
        browser = self.browser_combo.currentText()
        if browser == "none":
            QMessageBox.information(
                self,
                "Chưa chọn trình duyệt",
                "Hãy chọn trình duyệt trong ô 'Cookie (trình duyệt)' trước khi test.",
            )
            return
        self.append_log(f"\n🧪 === BẮT ĐẦU TEST COOKIE ({browser}) ===")
        f = self._build_cookie_file_from_browser(browser, urls, profile)
        if f:
            self.append_log(f"✅ Bước 1 OK: Cookie file đã tạo tại {f}")
            self._test_ytdlp_cookie(f, urls[0])
        else:
            self.append_log(
                f"⚠ browser_cookie3 không thể đọc cookie từ {browser} "
                f"(Chrome 127+ App-Bound Encryption hoặc lỗi khác).\n"
                f"  → Thử --cookies-from-browser {browser} trực tiếp qua yt-dlp..."
            )
            self._test_ytdlp_browser_cookie(browser, urls[0])

    def _test_ytdlp_browser_cookie(self, browser: str, url: str):
        """Test --cookies-from-browser trực tiếp qua yt-dlp (bypass browser_cookie3).
        Thử theo thứ tự: browser → browser+keyring → thông báo hướng dẫn thủ công."""
        if not url:
            return

        _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)

        def _run_variant(cookie_browser_arg: str) -> tuple[bool, str, str]:
            """Chạy yt-dlp với --cookies-from-browser <arg>. Trả về (ok, stdout, stderr)."""
            cmd = (
                [str(self.ytdlp_main)]
                if self.ytdlp_is_exe
                else [sys.executable, str(self.ytdlp_main)]
            )
            cmd += [
                "--cookies-from-browser",
                cookie_browser_arg,
                "--dump-single-json",
                "--skip-download",
                "--no-playlist",
                url,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=45,
                    **_subprocess_no_console_kwargs(),
                )
                return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()
            except subprocess.TimeoutExpired:
                return False, "", "timeout"
            except Exception as e:
                return False, "", str(e)

        # Các biến thể thử theo thứ tự
        variants = [browser]
        # Chromium-based browsers: thử thêm +keyring
        if browser in ("chrome", "edge", "brave", "chromium", "opera"):
            variants.append(f"{browser}+keyring")

        ok = False
        last_stderr = ""
        used_variant = ""

        for variant in variants:
            self.append_log(
                f"🧪 Thử --cookies-from-browser {variant} "
                f"({url[:60]}{'...' if len(url) > 60 else ''})"
            )
            ok, stdout, last_stderr = _run_variant(variant)
            is_dpapi = (
                "dpapi" in last_stderr.lower()
                or "failed to decrypt" in last_stderr.lower()
                or "app-bound" in last_stderr.lower()
            )

            if ok and stdout.startswith("{"):
                used_variant = variant
                break
            elif is_dpapi:
                self.append_log(
                    f"  ❌ DPAPI error với '{variant}' — Chrome đang mở và mã hoá session key.\n"
                    f"     Thử variant tiếp theo..."
                )
            else:
                self.append_log(f"  ❌ Thất bại với '{variant}' (exit≠0)")

        if ok and stdout.startswith("{"):
            try:
                info = json.loads(stdout)
                title = info.get("title", "(không có tên)")
                fmts = info.get("formats") or []
                max_h = max(
                    (f.get("height") or 0 for f in fmts if isinstance(f, dict)),
                    default=0,
                )
                self.append_log(
                    f"✅ --cookies-from-browser {used_variant} OK! "
                    f"Title: '{title}' | Chất lượng cao nhất: {max_h}p"
                )
                QMessageBox.information(
                    self,
                    "Test cookie thành công ✅",
                    f"--cookies-from-browser {used_variant} hoạt động tốt!\n\n"
                    f"Video: {title}\n"
                    f"Chất lượng cao nhất có được: {max_h}p\n\n"
                    "(yt-dlp sẽ tự lấy cookie khi tải — không cần làm thêm gì)",
                )
            except Exception:
                self.append_log(f"✅ --cookies-from-browser {used_variant} OK.")
            return

        # Tất cả variants đều thất bại — phân tích lý do và hướng dẫn
        is_dpapi_final = (
            "dpapi" in last_stderr.lower()
            or "failed to decrypt" in last_stderr.lower()
            or "app-bound" in last_stderr.lower()
        )
        if is_dpapi_final:
            self.append_log(
                "❌ DPAPI / App-Bound Encryption — Chrome đang chạy và giữ khoá mã hoá.\n"
                "   Giải pháp:\n"
                "   1️⃣  Đóng hoàn toàn Chrome rồi bấm Test lại\n"
                "   2️⃣  Dùng Firefox (không bị vấn đề này) — chọn 'firefox' ở ô Cookie\n"
                "   3️⃣  Export cookie thủ công bằng extension 'Get cookies.txt LOCALLY'\n"
                "       rồi dán vào ô JSON Cookie (mục Cookie nâng cao)"
            )
            QMessageBox.warning(
                self,
                "Chrome App-Bound Encryption",
                "Chrome đang chạy và giữ khoá mã hoá — yt-dlp không thể đọc cookie.\n\n"
                "Các lựa chọn:\n\n"
                "1) Đóng hoàn toàn Chrome → thử lại\n\n"
                "2) Dùng Firefox thay thế:\n"
                "   • Đăng nhập Bilibili trên Firefox\n"
                "   • Chọn 'firefox' ở ô Cookie (trình duyệt)\n\n"
                "3) Export cookie thủ công:\n"
                "   • Cài extension 'Get cookies.txt LOCALLY' trên Chrome\n"
                "   • Export → dán vào ô JSON Cookie (mục Cookie nâng cao)",
            )
        else:
            lines_err = last_stderr.splitlines()
            warns = [
                l
                for l in lines_err
                if any(
                    k in l.lower()
                    for k in ("warning", "error", "premium", "cookie", "login")
                )
            ]
            warn_text = "\n".join(warns[:10]) if warns else last_stderr[:400]
            self.append_log(f"❌ Tất cả variants thất bại.\n{warn_text}")
            QMessageBox.warning(
                self,
                "Test cookie thất bại",
                f"Không lấy được cookie từ {browser}.\n\n"
                f"{warn_text[:400]}\n\n"
                "Thử: Đăng nhập lại trình duyệt, hoặc dùng JSON cookie thủ công.",
            )

    def _test_ytdlp_cookie(self, cookies_file: str, url: str):
        """Bước 2: Xác minh cookie hoạt động với yt-dlp (--skip-download)."""
        if not url:
            return
        self.append_log(f"🧪 Bước 2: Kiểm tra cookie với yt-dlp ({url[:60]}...)")

        cmd = (
            [str(self.ytdlp_main)]
            if self.ytdlp_is_exe
            else [sys.executable, str(self.ytdlp_main)]
        )
        cmd += [
            "--cookies",
            cookies_file,
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            url,
        ]

        _ensure_exe_unblocked(self.ytdlp_main, self.ytdlp_is_exe)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                **_subprocess_no_console_kwargs(),
            )
            output = proc.stdout.strip()
            stderr = proc.stderr.strip()
            if proc.returncode == 0 and output.startswith("{"):
                try:
                    info = json.loads(output)
                    title = info.get("title", "(không có tên)")
                    fmts = info.get("formats") or []
                    max_h = max(
                        (f.get("height") or 0 for f in fmts if isinstance(f, dict)),
                        default=0,
                    )
                    self.append_log(
                        f"✅ Bước 2 OK: yt-dlp đọc được metadata."
                        f" Tiêu đề: '{title}'"
                        f" | Chất lượng cao nhất: {max_h}p"
                    )
                    QMessageBox.information(
                        self,
                        "Test cookie thành công",
                        f"Cookie hoạt động tốt! ✅\n\n"
                        f"Video: {title}\n"
                        f"Chất lượng cao nhất có được: {max_h}p",
                    )
                except Exception:
                    self.append_log("✅ Bước 2 OK: yt-dlp trả về JSON hợp lệ.")
            else:
                # Highlight những cảnh báo quan trọng
                lines_out = (stderr or "").splitlines()
                warns = [
                    l
                    for l in lines_out
                    if any(
                        k in l.lower()
                        for k in ("warning", "error", "premium", "cookie", "login")
                    )
                ]
                warn_text = "\n".join(warns[:10]) if warns else stderr[:400]
                self.append_log(
                    f"⚠ Bước 2: yt-dlp trả về exit={proc.returncode}.\n{warn_text}"
                )
                QMessageBox.warning(
                    self,
                    "Cookie có thể không đủ quyền",
                    f"yt-dlp chạy nhưng có vấn đề (exit={proc.returncode}).\n\n"
                    f"{warn_text[:500]}\n\n"
                    "Gợi ý: Đăng nhập lại vào trình duyệt rồi thử lại.",
                )
        except subprocess.TimeoutExpired:
            self.append_log("⚠ Bước 2: yt-dlp timeout (>30s) khi test cookie.")
        except Exception as e:
            self.append_log(f"⚠ Bước 2 lỗi: {e}")

    def _guess_cookie_domain(self, urls):
        for u in urls:
            try:
                host = (urlparse(u).hostname or "").lower()
            except Exception:
                host = ""
            if not host:
                continue
            parts = host.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return host
        return ""

    def _build_cookie_file_from_browser(self, browser_name, urls, profile_name="auto"):
        if browser_name == "none":
            return None
        if browser_cookie3 is None:
            self.append_log(
                "⚠ Thiếu thư viện browser-cookie3, không thể lấy cookie tự động."
            )
            return None

        domain = self._guess_cookie_domain(urls)
        if not domain:
            self.append_log("⚠ Không xác định được domain để lọc cookie.")
            return None

        fn_map = {
            "chrome": getattr(browser_cookie3, "chrome", None),
            "edge": getattr(browser_cookie3, "edge", None),
            "firefox": getattr(browser_cookie3, "firefox", None),
            "brave": getattr(browser_cookie3, "brave", None),
            "opera": getattr(browser_cookie3, "opera", None),
            "chromium": getattr(browser_cookie3, "chromium", None),
        }
        getter = fn_map.get(browser_name)
        if getter is None:
            return None

        def _build_kwargs(profile):
            # KHÔNG truyền domain_name vào đây để lấy TOÀN BỘ cookie,
            # sau đó lọc thủ công. Việc này tránh lỗi lọc sai domain của thư viện.
            kw = {}
            if profile and profile != "auto":
                if browser_name in ("chrome", "edge", "brave", "chromium"):
                    root_map = {
                        "chrome": Path(os.environ.get("LOCALAPPDATA", ""))
                        / "Google/Chrome/User Data",
                        "edge": Path(os.environ.get("LOCALAPPDATA", ""))
                        / "Microsoft/Edge/User Data",
                        "brave": Path(os.environ.get("LOCALAPPDATA", ""))
                        / "BraveSoftware/Brave-Browser/User Data",
                        "chromium": Path(os.environ.get("LOCALAPPDATA", ""))
                        / "Chromium/User Data",
                    }
                    root = root_map.get(browser_name)
                    if root:
                        cookie_file = root / profile / "Network/Cookies"
                        if not cookie_file.exists():
                            cookie_file = root / profile / "Cookies"
                        key_file = root / "Local State"
                        if cookie_file.exists():
                            kw["cookie_file"] = str(cookie_file)
                        if key_file.exists():
                            kw["key_file"] = str(key_file)
                elif browser_name == "opera":
                    root = (
                        Path(os.environ.get("APPDATA", ""))
                        / "Opera Software/Opera Stable"
                    )
                    cookie_file = root / "Network/Cookies"
                    if cookie_file.exists():
                        kw["cookie_file"] = str(cookie_file)
                    if (root / "Local State").exists():
                        kw["key_file"] = str(root / "Local State")
            return kw

        attempts = (
            [profile_name]
            if profile_name and profile_name != "auto"
            else ["auto"]
            + [p for p in self._detect_browser_profiles(browser_name) if p != "auto"]
        )
        jar = None
        last_err = ""
        success_profile = ""

        self.append_log(
            f"🔍 Bắt đầu tìm cookie {browser_name} cho domain '{domain}'..."
        )

        for p in attempts:
            try:
                kw = _build_kwargs(p)
                self.append_log(f"  👉 Thử profile: {p}...")
                jar = getter(**kw)

                # Kiểm tra xem jar có cookie nào hợp lệ cho domain không
                if jar:
                    # Đếm sơ bộ
                    count_match = 0
                    for c in jar:
                        if domain in (c.domain or ""):
                            count_match += 1

                    if count_match > 0:
                        self.append_log(
                            f"  ✅ Profile '{p}' có {count_match} cookie khớp."
                        )
                        success_profile = p
                        last_err = ""
                        break
                    else:
                        self.append_log(
                            f"  ⚠ Profile '{p}' đọc được cookie nhưng KHÔNG CÓ domain {domain}"
                        )
                else:
                    last_err = f"profile {p} trả về rỗng"
            except Exception as e:
                msg = str(e)
                err_lower = msg.lower()

                # Check lỗi do trình duyệt đang mở -> file db bị lock
                if (
                    "unable to read database" in err_lower
                    or "locked" in err_lower
                    or "busy" in err_lower
                    or "database disk image is malformed" in err_lower
                ):
                    self.append_log(
                        f"  ❌ File cookie bị khoá (có thể {browser_name} đang mở). "
                        f"Thử sao chép DB để đọc..."
                    )
                    # Thử copy DB sang tmp rồi đọc lại (không cần đóng trình duyệt)
                    copied = self._copy_browser_cookie_db(browser_name, p)
                    if copied:
                        try:
                            kw_copy = {"cookie_file": str(copied["cookie"])}
                            if copied["key"]:
                                kw_copy["key_file"] = str(copied["key"])
                            jar_copy = getter(**kw_copy)
                            if jar_copy:
                                count_c = sum(
                                    1 for c in jar_copy if domain in (c.domain or "")
                                )
                                if count_c > 0:
                                    self.append_log(
                                        f"  ✅ Đọc từ bản sao thành công! "
                                        f"{count_c} cookie khớp (profile '{p}')."
                                    )
                                    jar = jar_copy
                                    success_profile = p
                                    last_err = ""
                                    break
                                else:
                                    self.append_log(
                                        f"  ⚠ Bản sao không có cookie cho domain {domain}"
                                    )
                        except Exception as e_copy:
                            self.append_log(f"  ❌ Vẫn lỗi với bản sao: {e_copy}")
                        finally:
                            try:
                                copied["cookie"].unlink(missing_ok=True)
                            except Exception:
                                pass
                elif (
                    "unable to get key" in err_lower
                    or "key for cookie" in err_lower
                    or "app-bound" in err_lower
                    or "cannot decrypt" in err_lower
                ):
                    self.append_log(
                        f"  ❌ Không giải mã được encryption key (profile '{p}').\n"
                        f"     Chrome 127+ dùng App-Bound Encryption mới →"
                        f" browser_cookie3 không hỗ trợ.\n"
                        f"     yt-dlp sẽ tự xử lý bằng"
                        f" --cookies-from-browser {browser_name}."
                    )
                    last_err = "key_decryption_error"
                elif "requires admin" in err_lower or "requiresadminerror" in msg:
                    self.append_log(f"  ❌ Cần quyền Admin để giải mã cookie của {p}.")
                else:
                    self.append_log(f"  ❌ Lỗi profile {p}: {msg}")
                last_err = msg

        if not success_profile or jar is None:
            self.append_log(
                f"⚠ Thất bại: Không tìm thấy cookie {domain} trong bất kỳ profile nào."
            )
            return None

        rows = ["# Netscape HTTP Cookie File"]
        count = 0
        for c in jar:
            try:
                dom = c.domain or ""
                if not dom:
                    continue
                # Lọc domain thủ công
                if domain not in dom:
                    continue

                include_sub = "TRUE" if dom.startswith(".") else "FALSE"
                path = c.path or "/"
                secure = "TRUE" if c.secure else "FALSE"
                exp = int(c.expires or 0)
                name = c.name or ""
                value = c.value or ""
                if not name:
                    continue
                rows.append(
                    "\t".join([dom, include_sub, path, secure, str(exp), name, value])
                )
                count += 1
            except Exception:
                continue

        if count == 0:
            self.append_log(f"⚠ Đã quét nhưng không lọc được cookie nào cho {domain}")
            return None

        tmp_dir = self.project_root / ".runtime"
        tmp_dir.mkdir(exist_ok=True)
        out = tmp_dir / f"cookies_auto_{browser_name}.txt"
        out.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.append_log(
            f"🍪 Đã xuất {count} cookie từ {browser_name} (profile '{success_profile}')"
        )
        return str(out)

    def _copy_browser_cookie_db(
        self, browser_name: str, profile_name: str
    ) -> dict | None:
        """
        Sao chép file SQLite cookie của trình duyệt sang thư mục tạm để đọc
        ngay cả khi trình duyệt đang mở (file bị lock).
        Trả về dict {'cookie': Path, 'key': Path|None} hoặc None nếu thất bại.
        """
        root_map = {
            "chrome": Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/User Data",
            "edge": Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft/Edge/User Data",
            "brave": Path(os.environ.get("LOCALAPPDATA", ""))
            / "BraveSoftware/Brave-Browser/User Data",
            "chromium": Path(os.environ.get("LOCALAPPDATA", "")) / "Chromium/User Data",
            "opera": Path(os.environ.get("APPDATA", ""))
            / "Opera Software/Opera Stable",
        }
        root = root_map.get(browser_name)
        if not root or not root.exists():
            return None

        key_file = root / "Local State"

        # Xác định danh sách profile cần thử
        if profile_name and profile_name not in ("auto", ""):
            profile_dirs = [root / profile_name]
        else:
            profile_dirs = [root / "Default"]
            for d in sorted(root.glob("Profile *")):
                if d.is_dir():
                    profile_dirs.append(d)

        tmp_dir = self.project_root / ".runtime"
        tmp_dir.mkdir(exist_ok=True)
        ts = int(datetime.now().timestamp())

        for profile_dir in profile_dirs:
            for cookie_rel in ["Network/Cookies", "Cookies"]:
                cookie_file = profile_dir / cookie_rel
                if not cookie_file.exists():
                    continue
                try:
                    tmp_cookie = tmp_dir / f"cookies_copy_{browser_name}_{ts}.db"
                    shutil.copy2(str(cookie_file), str(tmp_cookie))
                    # Cũng copy WAL/SHM nếu có (để đảm bảo nhất quán)
                    for ext in ["-wal", "-shm"]:
                        wal = Path(str(cookie_file) + ext)
                        if wal.exists():
                            try:
                                shutil.copy2(str(wal), str(tmp_cookie) + ext)
                            except Exception:
                                pass
                    self.append_log(
                        f"  📋 Đã sao chép: {cookie_file.parent.name}/{cookie_file.name}"
                    )
                    return {
                        "cookie": tmp_cookie,
                        "key": key_file if key_file.exists() else None,
                    }
                except Exception as e:
                    self.append_log(f"  ⚠ Không copy được {cookie_file.name}: {e}")
        return None

    def _load_cookie_profiles(self):
        if not self.cookie_profiles_path.exists():
            self.cookie_profiles = {}
            return
        try:
            data = json.loads(self.cookie_profiles_path.read_text(encoding="utf-8"))
            self.cookie_profiles = data if isinstance(data, dict) else {}
        except Exception:
            self.cookie_profiles = {}

    def _save_cookie_profiles(self):
        self.cookie_profiles_path.write_text(
            json.dumps(self.cookie_profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _refresh_cookie_profiles_combo(self):
        self.cookie_profile_combo.blockSignals(True)
        self.cookie_profile_combo.clear()
        self.cookie_profile_combo.addItem("(Không dùng profile)", "")
        for name in sorted(self.cookie_profiles.keys()):
            self.cookie_profile_combo.addItem(name, name)
        self.cookie_profile_combo.blockSignals(False)

    def on_cookie_profile_changed(self):
        key = self.cookie_profile_combo.currentData()
        if not key:
            return
        raw = self.cookie_profiles.get(key, "")
        if raw:
            self.cookie_json_input.setPlainText(raw)
            self.append_log(f"🍪 Đã nạp cookie profile: {key}")

    def save_cookie_profile(self):
        raw = self.cookie_json_input.toPlainText().strip()
        if not raw:
            QMessageBox.information(self, "Thiếu dữ liệu", "Bạn chưa dán JSON cookie.")
            return
        try:
            data = json.loads(raw)
        except Exception:
            QMessageBox.warning(self, "Cookie JSON lỗi", "Cookie JSON không hợp lệ.")
            return

        default_name = "profile"
        if isinstance(data, dict):
            url = str(data.get("url") or "").strip()
            if url:
                default_name = (
                    url.replace("https://", "").replace("http://", "").split("/")[0]
                )

        profile_name, ok = QInputDialog.getText(
            self, "Đặt tên cookie profile", "Tên profile:", text=default_name
        )
        profile_name = (profile_name or "").strip()
        if not ok or not profile_name:
            return

        self.cookie_profiles[profile_name] = raw
        self._save_cookie_profiles()
        self._refresh_cookie_profiles_combo()
        idx = self.cookie_profile_combo.findData(profile_name)
        if idx >= 0:
            self.cookie_profile_combo.setCurrentIndex(idx)
        self.append_log(f"✅ Đã lưu cookie profile: {profile_name}")

    def delete_cookie_profile(self):
        key = self.cookie_profile_combo.currentData()
        if not key:
            QMessageBox.information(self, "Thông báo", "Bạn chưa chọn profile để xoá.")
            return
        if key in self.cookie_profiles:
            del self.cookie_profiles[key]
            self._save_cookie_profiles()
            self._refresh_cookie_profiles_combo()
            self.cookie_profile_combo.setCurrentIndex(0)
            self.append_log(f"🗑 Đã xoá cookie profile: {key}")

    def _build_cookie_file_from_json(self):
        raw = self.cookie_json_input.toPlainText().strip()
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except Exception:
            # Không show dialog — caller sẽ log cảnh báo và fallback sang browser cookie
            return None

        cookies = data.get("cookies") if isinstance(data, dict) else None
        if not isinstance(cookies, list) or not cookies:
            # Không show dialog — caller sẽ log cảnh báo và fallback sang browser cookie
            return None

        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            if not isinstance(c, dict):
                continue
            domain = c.get("domain") or ""
            if not domain:
                continue
            host_only = bool(c.get("hostOnly", False))
            include_sub = "FALSE" if host_only else "TRUE"
            path = c.get("path") or "/"
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            exp = c.get("expirationDate")
            if exp is None:
                exp = 0
            try:
                exp = int(float(exp))
            except Exception:
                exp = 0
            name = c.get("name") or ""
            value = c.get("value") or ""
            if not name:
                continue
            lines.append(
                "\t".join([domain, include_sub, path, secure, str(exp), name, value])
            )

        if len(lines) <= 1:
            QMessageBox.warning(
                self, "Cookie JSON lỗi", "Không parse được cookie hợp lệ."
            )
            return None

        tmp_dir = self.project_root / ".runtime"
        tmp_dir.mkdir(exist_ok=True)
        out = tmp_dir / "cookies_runtime.txt"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.runtime_cookie_file = str(out)
        return self.runtime_cookie_file

    def _archive_file(self):
        out_dir = self.out_dir.text().strip()
        os.makedirs(out_dir, exist_ok=True)
        return str(Path(out_dir) / "downloaded.archive")

    def _read_archive(self):
        af = Path(self._archive_file())
        if not af.exists():
            return set()
        return {
            x.strip()
            for x in af.read_text(encoding="utf-8", errors="ignore").splitlines()
            if x.strip()
        }

    def _guess_key(self, item):
        ex, vid = item.get("extractor", "").strip(), item.get("id", "").strip()
        return f"{ex} {vid}" if ex and vid else None

    def _thumb_cache_path(self, item):
        vid = (item.get("id") or "").strip()
        ex = (item.get("extractor") or "").strip()
        if vid and ex:
            name = f"{ex}_{vid}.jpg"
        elif vid:
            name = f"{vid}.jpg"
        else:
            name = f"u_{abs(hash(item.get('url', '')))}.jpg"
        return self.thumb_cache_dir / name

    def _thumbnail_icon(self, item):
        thumb_url = (item.get("thumbnail") or "").strip()
        if not thumb_url:
            return None

        cache_file = self._thumb_cache_path(item)
        if not cache_file.exists():
            try:
                req = urllib.request.Request(
                    thumb_url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.tiktok.com/",
                    },
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = resp.read()
                cache_file.write_bytes(data)
            except Exception:
                return None

        pix = QPixmap(str(cache_file))
        if pix.isNull():
            return None

        pix = pix.scaled(144, 81, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QIcon(pix)

    def _fmt_duration(self, sec):
        if sec is None:
            return "-"
        try:
            sec = int(sec)
        except Exception:
            return "-"
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _detect_logo_path(self) -> Path | None:
        """Tìm logo.png ở thư mục project hoặc cạnh file .exe khi đóng gói."""
        candidates = []
        # Khi chạy từ mã nguồn
        candidates.append(self.project_root / "logo.png")
        # Khi chạy dạng .exe (PyInstaller)
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / "logo.png")

        for p in candidates:
            try:
                if p.is_file():
                    return p
            except Exception:
                continue
        return None

    def _load_logo_pixmap(self, max_height: int = 80):
        """Load logo.png thành QPixmap đã scale, hoặc None nếu không có."""
        if not getattr(self, "logo_path", None):
            return None
        try:
            pix = QPixmap(str(self.logo_path))
            if pix.isNull():
                return None
            return pix.scaledToHeight(max_height, Qt.SmoothTransformation)
        except Exception:
            return None

    def _fmt_size(self, n):
        if n is None:
            return "-"
        try:
            n = float(n)
        except Exception:
            return "-"
        units = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while n >= 1024 and i < len(units) - 1:
            n /= 1024
            i += 1
        return f"{n:.1f} {units[i]}"

    def _fmt_int(self, n):
        if n is None:
            return "-"
        try:
            n = int(n)
        except Exception:
            return "-"
        return f"{n:,}".replace(",", ".")

    def _fmt_upload_date(self, s):
        """Nhận upload_date dạng 'YYYYMMDD' và trả về 'YYYY-MM-DD'."""
        if not s:
            return "-"
        s = str(s)
        if len(s) == 8 and s.isdigit():
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        return s

    def _parse_upload_date(self, s):
        """Trả về tuple (year, month, day) hoặc None nếu không parse được."""
        if not s:
            return None
        s = str(s)
        if len(s) == 8 and s.isdigit():
            try:
                return int(s[0:4]), int(s[4:6]), int(s[6:8])
            except Exception:
                return None
        # Thử dạng YYYY-MM-DD
        try:
            parts = s.split("-")
            if len(parts) == 3:
                return int(parts[0]), int(parts[1]), int(parts[2])
        except Exception:
            return None
        return None

    def _populate_table(self):
        ar = self._read_archive()

        # Ngắt kết nối tạm thời để tránh trigger khi đang populate
        try:
            self.table.itemChanged.disconnect()
        except:
            pass

        self.table.setRowCount(0)
        for i, item in enumerate(self.items):
            self.table.insertRow(i)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(i, 0, chk)

            thumb_item = QTableWidgetItem()
            # Để danh sách hiển thị nhanh hơn, có thể bỏ qua load thumbnail nếu bật chế độ quét nhanh
            if (
                not getattr(self, "chk_fast_scan", None)
                or not self.chk_fast_scan.isChecked()
            ):
                icon = self._thumbnail_icon(item)
                if icon:
                    thumb_item.setIcon(icon)
                    thumb_item.setText(" ")
                else:
                    thumb_item.setText("(none)")
            else:
                thumb_item.setText("—")
            self.table.setItem(i, 1, thumb_item)

            title_item = QTableWidgetItem(item.get("title", ""))
            title_item.setToolTip(item.get("title", ""))
            self.table.setItem(i, 2, title_item)

            self.table.setItem(
                i, 3, QTableWidgetItem(self._fmt_duration(item.get("duration")))
            )
            self.table.setItem(
                i, 4, QTableWidgetItem(self._fmt_upload_date(item.get("upload_date")))
            )
            self.table.setItem(
                i, 5, QTableWidgetItem(self._fmt_size(item.get("filesize")))
            )

            # Lượt xem / Lượt thích
            self.table.setItem(
                i, 6, QTableWidgetItem(self._fmt_int(item.get("view_count")))
            )
            self.table.setItem(
                i, 7, QTableWidgetItem(self._fmt_int(item.get("like_count")))
            )

            url_val = item.get("url", "")
            url_item = QTableWidgetItem(url_val)
            url_item.setToolTip(url_val)
            self.table.setItem(i, 8, url_item)

            st = "Mới"
            k = self._guess_key(item)
            if k and k in ar:
                st = "Đã tải"
            self.table.setItem(i, 9, QTableWidgetItem(st))

        # Kết nối signal itemChanged để đồng bộ với cửa sổ popup
        self.table.itemChanged.connect(self._on_table_item_changed)

    def _on_table_item_changed(self, item):
        """Xử lý khi item trong bảng thay đổi - đồng bộ với cửa sổ popup nếu đang mở."""
        # Chỉ xử lý checkbox ở cột 0
        if item.column() == 0:
            row = item.row()
            state = item.checkState()
            self._sync_to_popup(row, state)

    def _sync_to_popup(self, row, state):
        """Đồng bộ thay đổi từ bảng chính sang cửa sổ popup nếu đang mở."""
        if (
            hasattr(self, "_large_view_dialog")
            and self._large_view_dialog
            and self._large_view_dialog.isVisible()
        ):
            if row < self._large_view_dialog.table.rowCount():
                popup_chk = self._large_view_dialog.table.item(row, 0)
                if popup_chk:
                    # Tạm thời ngắt kết nối để tránh vòng lặp
                    try:
                        self._large_view_dialog.table.itemChanged.disconnect()
                    except:
                        pass
                    popup_chk.setCheckState(state)
                    # Kết nối lại
                    self._large_view_dialog.table.itemChanged.connect(
                        self._large_view_dialog._on_popup_item_changed
                    )

    def _on_search_text_changed(self, text: str):
        """Lưu query và khởi động lại timer debounce."""
        self._search_query = text
        # Mỗi lần gõ lại reset timer, chỉ lọc khi user dừng gõ ~300ms
        if hasattr(self, "_search_timer") and self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer.start()

    def _apply_search_filter(self):
        """
        Ẩn/hiện các dòng trong bảng theo từ khóa tìm kiếm.

        - Tìm trên cột Tiêu đề (2) và URL (8)
        - Không đụng tới self.items để tránh ảnh hưởng logic chọn/sort hiện có
        """
        query = getattr(self, "_search_query", "") or ""
        query = query.strip().lower()

        # Nếu không có gì để tìm thì hiện toàn bộ
        if not query:
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return

        for r in range(self.table.rowCount()):
            title_item = self.table.item(r, 2)
            url_item = self.table.item(r, 8)
            title = (title_item.text() if title_item else "").lower()
            url = (url_item.text() if url_item else "").lower()
            match = (query in title) or (query in url)

            # Nếu không khớp, ẩn dòng và bỏ chọn checkbox để tránh bị tải nhầm
            if not match:
                chk_item = self.table.item(r, 0)
                if chk_item and chk_item.checkState() == Qt.Checked:
                    chk_item.setCheckState(Qt.Unchecked)
            self.table.setRowHidden(r, not match)

    def scan_list(self):
        urls = self._source_urls()
        if not urls:
            QMessageBox.information(self, "Thiếu URL", "Bạn chưa nhập URL.")
            return

        # --- Douyin: phát hiện và chuyển sang Selenium downloader ---
        douyin_urls = [u for u in urls if is_douyin_url(u)]
        other_urls = [u for u in urls if not is_douyin_url(u)]

        if douyin_urls:
            self.set_busy(True)
            self.progress.setValue(0)
            self.progress_text.setText("0% - Đang quét Douyin...")
            cookies_json = self.cookie_json_input.toPlainText().strip()
            self.append_log(f"🔍 Phát hiện {len(douyin_urls)} link Douyin → dùng Selenium")
            self._douyin_scanner = DouyinScanWorker(douyin_urls, cookies_json)
            self._douyin_scanner.log.connect(self.append_log)
            self._douyin_scanner.result.connect(self._on_douyin_scan_done)
            self._douyin_scanner.failed.connect(self.on_scan_failed)
            self._douyin_scanner.start()
            # Nếu có URL non-Douyin, log cảnh báo
            if other_urls:
                self.append_log(
                    f"ℹ️ {len(other_urls)} URL không phải Douyin sẽ được bỏ qua "
                    f"trong lần quét này. Hãy quét riêng chúng."
                )
            return

        # --- Normal flow (yt-dlp) ---
        if not self.ytdlp_main.exists():
            QMessageBox.warning(
                self, "Thiếu yt-dlp", "Không tìm thấy yt-dlp trong dự án."
            )
            return

        cookies_file = self._build_cookie_file_from_json()
        if self.cookie_json_input.toPlainText().strip() and not cookies_file:
            # JSON lỗi → cảnh báo nhưng vẫn fallback sang browser cookie
            self.append_log(
                "⚠ JSON cookie trong ô 'Cookie nâng cao' không hợp lệ — "
                "bỏ qua, thử browser cookie."
            )
        if not cookies_file:
            cookies_file = self._build_cookie_file_from_browser(
                self.browser_combo.currentText(),
                urls,
                self.browser_profile_combo.currentData() or "auto",
            )
        self.append_log("Bắt đầu quét danh sách...")
        if cookies_file:
            if self.cookie_json_input.toPlainText().strip():
                self.append_log("🍪 Đang dùng cookie JSON runtime")
            else:
                self.append_log("🍪 Đang dùng cookie lấy tự động từ trình duyệt")
        elif self.browser_combo.currentText() not in ("none", ""):
            b = self.browser_combo.currentText()
            self.append_log(
                f"🍪 browser_cookie3 không đọc được → yt-dlp sẽ dùng"
                f" --cookies-from-browser {b} trực tiếp"
            )

        # fetch_extra_meta = False nếu đang bật chế độ quét nhanh
        fetch_extra_meta = (
            not self.chk_fast_scan.isChecked()
            if hasattr(self, "chk_fast_scan")
            else False
        )

        # Lấy thông tin lọc khoảng ngày
        filter_date_enabled = False
        filter_date_start = None
        filter_date_end = None
        if hasattr(self, "chk_filter_date") and self.chk_filter_date.isChecked():
            filter_date_enabled = True
            filter_date_start = self.date_start.date()
            filter_date_end = self.date_end.date()
            # Kiểm tra ngày bắt đầu <= ngày kết thúc
            if filter_date_start > filter_date_end:
                QMessageBox.warning(
                    self, "Lỗi", "Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc."
                )
                self.set_busy(False)
                return

        self.expander = ExpandWorker(
            urls,
            self.browser_combo.currentText(),
            cookies_file,
            self.ytdlp_main,
            ytdlp_is_exe=self.ytdlp_is_exe,
            fetch_extra_meta=fetch_extra_meta,
            filter_date_enabled=filter_date_enabled,
            filter_date_start=filter_date_start,
            filter_date_end=filter_date_end,
        )
        self.expander.log.connect(self.append_log)
        self.expander.result.connect(self.on_scan_done)
        self.expander.failed.connect(self.on_scan_failed)
        self.expander.start()

    def on_scan_done(self, items):
        # Lưu danh sách gốc và áp dụng sắp xếp hiện tại
        self.items = list(items)
        self._original_items = list(
            items
        )  # Lưu bản gốc để khôi phục khi chọn "Thứ tự mặc định"
        self.on_sort_changed()
        self.progress.setValue(100)
        self.progress_text.setText(f"100% - Quét xong ({len(items)} video)")
        self.append_log(f"✅ Quét xong: {len(items)} video")

        # Cập nhật cửa sổ popup nếu đang mở
        if (
            hasattr(self, "_large_view_dialog")
            and self._large_view_dialog
            and self._large_view_dialog.isVisible()
        ):
            self._large_view_dialog.populate_table(self.items, self.table)

        self.set_busy(False)

    def on_sort_changed(self, button=None):
        if not hasattr(self, "items") or not isinstance(self.items, list):
            return

        # Lấy danh sách gốc từ lần quét cuối (nếu có)
        if not hasattr(self, "_original_items"):
            self._original_items = list(self.items)

        # Xác định chế độ sắp xếp từ radio button được chọn
        if self.sort_views.isChecked():
            self.items.sort(key=lambda x: (x.get("view_count") or 0), reverse=True)
        elif self.sort_likes.isChecked():
            self.items.sort(key=lambda x: (x.get("like_count") or 0), reverse=True)
        else:  # Thứ tự mặc định
            # Khôi phục thứ tự gốc
            if hasattr(self, "_original_items"):
                self.items = list(self._original_items)

        self._populate_table()

    def select_top_views(self):
        """Chọn N video có nhiều lượt xem nhất"""
        if not self.items:
            QMessageBox.information(
                self, "Thông báo", "Chưa có danh sách video. Hãy quét danh sách trước."
            )
            return

        n, ok = QInputDialog.getInt(
            self,
            "Chọn top video",
            "Nhập số lượng video muốn chọn (nhiều lượt xem nhất):",
            value=10,
            min=1,
            max=len(self.items),
        )
        if not ok:
            return

        # Sắp xếp theo lượt xem giảm dần
        sorted_items = sorted(
            self.items, key=lambda x: (x.get("view_count") or 0), reverse=True
        )
        top_n = sorted_items[:n]

        # Tìm các video này trong bảng và chọn
        self.clear_selection()
        top_urls = {item.get("url") for item in top_n}

        for r in range(self.table.rowCount()):
            item = self.items[r]
            if item.get("url") in top_urls:
                chk = self.table.item(r, 0)
                if chk:
                    chk.setCheckState(Qt.Checked)

        self.append_log(f"✅ Đã chọn {n} video có nhiều lượt xem nhất")

    def select_top_likes(self):
        """Chọn N video có nhiều lượt thích nhất"""
        if not self.items:
            QMessageBox.information(
                self, "Thông báo", "Chưa có danh sách video. Hãy quét danh sách trước."
            )
            return

        n, ok = QInputDialog.getInt(
            self,
            "Chọn top video",
            "Nhập số lượng video muốn chọn (nhiều lượt thích nhất):",
            value=10,
            min=1,
            max=len(self.items),
        )
        if not ok:
            return

        # Sắp xếp theo lượt thích giảm dần
        sorted_items = sorted(
            self.items, key=lambda x: (x.get("like_count") or 0), reverse=True
        )
        top_n = sorted_items[:n]

        # Tìm các video này trong bảng và chọn
        self.clear_selection()
        top_urls = {item.get("url") for item in top_n}

        for r in range(self.table.rowCount()):
            item = self.items[r]
            if item.get("url") in top_urls:
                chk = self.table.item(r, 0)
                if chk:
                    chk.setCheckState(Qt.Checked)

        self.append_log(f"✅ Đã chọn {n} video có nhiều lượt thích nhất")

    def select_oldest_video(self):
        """Chọn video cũ nhất (dựa trên upload_date)."""
        if not self.items:
            QMessageBox.information(
                self, "Thông báo", "Chưa có danh sách video. Hãy quét danh sách trước."
            )
            return

        # Tìm ngày nhỏ nhất trong các video có upload_date hợp lệ
        dated = []
        for idx, it in enumerate(self.items):
            d = self._parse_upload_date(it.get("upload_date"))
            if d:
                dated.append((d, idx))
        if not dated:
            QMessageBox.information(
                self,
                "Thông báo",
                "Danh sách hiện tại không có thông tin ngày đăng để lọc.",
            )
            return

        min_date = min(d for d, _ in dated)
        # Có thể có nhiều video cùng ngày cũ nhất
        target_indices = [idx for d, idx in dated if d == min_date]

        self.clear_selection()
        for r in target_indices:
            chk = self.table.item(r, 0)
            if chk:
                chk.setCheckState(Qt.Checked)

        y, m, day = min_date
        self.append_log(
            f"✅ Đã chọn {len(target_indices)} video cũ nhất (ngày {day:02d}-{m:02d}-{y})"
        )

    def select_videos_by_month(self):
        """Chọn video theo tháng/năm (ví dụ 1/2022)."""
        if not self.items:
            QMessageBox.information(
                self, "Thông báo", "Chưa có danh sách video. Hãy quét danh sách trước."
            )
            return

        text, ok = QInputDialog.getText(
            self,
            "Chọn theo tháng/năm",
            "Nhập tháng/năm (ví dụ: 1/2022 hoặc 01-2022):",
        )
        if not ok:
            return

        text = (text or "").strip()
        if not text:
            return

        # Parse tháng/năm
        m = y = None
        try:
            if "/" in text:
                p1, p2 = text.split("/", 1)
            elif "-" in text:
                p1, p2 = text.split("-", 1)
            else:
                raise ValueError
            m = int(p1)
            y = int(p2)
        except Exception:
            QMessageBox.warning(
                self,
                "Lỗi định dạng",
                "Vui lòng nhập đúng định dạng, ví dụ: 1/2022 hoặc 01-2022.",
            )
            return

        if not (1 <= m <= 12):
            QMessageBox.warning(self, "Lỗi định dạng", "Tháng phải trong khoảng 1-12.")
            return

        matched_rows = []
        for r, it in enumerate(self.items):
            d = self._parse_upload_date(it.get("upload_date"))
            if not d:
                continue
            yy, mm, _ = d
            if yy == y and mm == m:
                matched_rows.append(r)

        if not matched_rows:
            QMessageBox.information(
                self, "Không tìm thấy", f"Không có video nào trong tháng {m:02d}/{y}."
            )
            return

        self.clear_selection()
        for r in matched_rows:
            chk = self.table.item(r, 0)
            if chk:
                chk.setCheckState(Qt.Checked)

        self.append_log(f"✅ Đã chọn {len(matched_rows)} video trong tháng {m:02d}/{y}")

    def open_large_view(self):
        """Mở cửa sổ popup để hiển thị danh sách video trong cửa sổ lớn."""
        if not hasattr(self, "items") or not self.items:
            QMessageBox.information(
                self, "Thông báo", "Chưa có danh sách video. Hãy quét danh sách trước."
            )
            return

        # Tạo hoặc cập nhật cửa sổ popup
        if not hasattr(self, "_large_view_dialog") or self._large_view_dialog is None:
            self._large_view_dialog = VideoListViewDialog(self)

        # Điền dữ liệu vào cửa sổ popup
        self._large_view_dialog.populate_table(self.items, self.table)

        # Hiển thị cửa sổ
        self._large_view_dialog.show()
        self._large_view_dialog.raise_()
        self._large_view_dialog.activateWindow()

    def on_scan_failed(self, err):
        self.append_log(f"❌ Lỗi quét: {err}")
        self.set_busy(False)

    def _on_douyin_scan_done(self, items):
        """Xử lý kết quả quét Douyin — merge vào items rồi hiện bảng."""
        self.items = list(items)
        self._original_items = list(items)
        self.on_sort_changed()
        self.progress.setValue(100)
        self.progress_text.setText(f"100% - Quét xong ({len(items)} video Douyin)")
        self.append_log(f"✅ Quét xong: {len(items)} video Douyin")
        self.set_busy(False)

    def _start_next_douyin_download(self, out_dir):
        """Tải video Douyin tiếp theo trong queue."""
        if not self._douyin_download_queue:
            self.append_log(
                f"🎉 Hoàn tất tải {self._douyin_download_done}/{self._douyin_download_total} "
                f"video Douyin"
            )
            self.progress.setValue(100)
            self.progress_text.setText("100% - Tải xong!")
            self.set_busy(False)
            return

        item = self._douyin_download_queue.pop(0)
        idx = self._douyin_download_done + 1
        total = self._douyin_download_total
        self.append_log(f"📥 [{idx}/{total}] {item.get('title', 'N/A')}")

        self._douyin_dl_worker = DouyinDownloadWorker(item, out_dir)
        self._douyin_dl_worker.log.connect(self.append_log)
        self._douyin_dl_worker.progress.connect(
            lambda p: self.progress.setValue(
                int((self._douyin_download_done / self._douyin_download_total) * 100
                    + p / self._douyin_download_total)
            )
        )
        self._douyin_dl_worker.finished.connect(
            lambda ok, msg: self._on_douyin_download_finished(ok, msg, out_dir)
        )
        self._douyin_dl_worker.start()

    def _on_douyin_download_finished(self, success, message, out_dir):
        """Callback khi một video Douyin tải xong."""
        self._douyin_download_done += 1
        if not success:
            self.append_log(f"⚠ Lỗi: {message}")
        pct = int(self._douyin_download_done / self._douyin_download_total * 100)
        self.progress.setValue(pct)
        self.progress_text.setText(
            f"{pct}% - Đã tải {self._douyin_download_done}/{self._douyin_download_total}"
        )
        # Tải video tiếp theo
        self._start_next_douyin_download(out_dir)

    def select_all(self):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.Checked)

    def clear_selection(self):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.Unchecked)

    def selected_items(self):
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.checkState() == Qt.Checked:
                out.append(self.items[r])
        return out

    def download_selected(self):
        selected = self.selected_items()
        if not selected:
            QMessageBox.information(self, "Chưa chọn", "Bạn chưa chọn video nào.")
            return

        out_dir = self.out_dir.text().strip()
        if not out_dir:
            QMessageBox.information(self, "Thiếu thư mục", "Bạn chưa chọn thư mục lưu.")
            return
        os.makedirs(out_dir, exist_ok=True)

        cookies_file = self._build_cookie_file_from_json()
        if self.cookie_json_input.toPlainText().strip() and not cookies_file:
            # JSON lỗi → cảnh báo nhưng vẫn fallback sang browser cookie
            self.append_log(
                "⚠ JSON cookie trong ô 'Cookie nâng cao' không hợp lệ — "
                "bỏ qua, thử browser cookie."
            )
        if not cookies_file:
            cookie_urls = [x.get("url", "") for x in selected if isinstance(x, dict)]
            cookies_file = self._build_cookie_file_from_browser(
                self.browser_combo.currentText(),
                cookie_urls,
                self.browser_profile_combo.currentData() or "auto",
            )

        self.set_busy(True)
        self.progress.setValue(0)
        self.progress_text.setText("0% - Bắt đầu tải")
        self.append_log(f"🚀 Bắt đầu tải {len(selected)} video")

        engine = self.engine_combo.currentData()

        # === Douyin Custom Engine ===
        douyin_items = [it for it in selected if it.get('extractor') == 'Douyin']
        if douyin_items:
            self.append_log(f"🔧 Engine: Douyin Selenium (tải {len(douyin_items)} video)")
            self._douyin_download_queue = list(douyin_items)
            self._douyin_download_total = len(douyin_items)
            self._douyin_download_done = 0
            self._start_next_douyin_download(out_dir)
            return

        # === Bilibili Native API Engine ===
        if engine == "bilibili_native":
            # Kiểm tra URL có phải Bilibili không
            non_bili_urls = [
                it.get("url", "") for it in selected
                if "bilibili.com" not in (it.get("url", "") or "")
            ]
            if non_bili_urls:
                QMessageBox.warning(
                    self,
                    "URL không phải Bilibili",
                    f"Có {len(non_bili_urls)} URL không phải bilibili.com.\n"
                    f"Bilibili Native API chỉ hỗ trợ video từ bilibili.com.\n\n"
                    f"Vui lòng chọn engine 'yt-dlp' hoặc chỉ chọn video Bilibili.",
                )
                self.set_busy(False)
                return

            # Load cookies từ QR login
            bili_cookies = load_bilibili_cookies(self.cookie_profiles_path)
            if not bili_cookies:
                QMessageBox.warning(
                    self,
                    "Chưa đăng nhập Bilibili",
                    "Bạn chưa đăng nhập Bilibili bằng QR code.\n\n"
                    "Vui lòng vào tab ⚙️ Cài đặt → 🔐 Đăng nhập Bilibili bằng QR\n"
                    "để đăng nhập trước khi dùng Bilibili Native API.",
                )
                self.set_busy(False)
                return

            video_quality = (
                self.quality_combo.currentData()
                if self.mode_combo.currentData() == "video"
                else "max1080"
            )

            self.append_log("🔧 Engine: Bilibili Native API")
            self.append_log(f"🍪 Đang dùng cookie từ QR login")

            urls = [it.get("url", "") for it in selected]
            self.bilibili_worker = BilibiliDownloadWorker(
                urls=urls,
                out_dir=out_dir,
                quality=video_quality,
                cookies=bili_cookies,
                ffmpeg_path=self.ffmpeg_path or "ffmpeg",
            )
            self.bilibili_worker.log.connect(self.append_log)
            self.bilibili_worker.progress.connect(self.on_progress)
            self.bilibili_worker.done.connect(self.on_done)
            self.bilibili_worker.failed.connect(self.on_failed)
            self.bilibili_worker.start()
            return

        # === yt-dlp Engine (mặc định) ===
        self.append_log("🔧 Engine: yt-dlp")
        self.append_log("📁 Tự tạo thư mục theo playlist/channel khi tải hàng loạt")
        self.append_log(
            f"🖼 Thumbnail: {'Bật' if self.chk_thumbnail.isChecked() else 'Tắt'}"
        )
        if cookies_file:
            if self.cookie_json_input.toPlainText().strip():
                self.append_log("🍪 Đang dùng cookie JSON runtime")
            else:
                self.append_log("🍪 Đang dùng cookie lấy tự động từ trình duyệt")
        elif self.browser_combo.currentText() not in ("none", ""):
            b = self.browser_combo.currentText()
            self.append_log(
                f"🍪 browser_cookie3 không đọc được → yt-dlp sẽ dùng"
                f" --cookies-from-browser {b} trực tiếp"
            )

        video_quality = (
            self.quality_combo.currentData()
            if self.mode_combo.currentData() == "video"
            else None
        )

        self.worker = DownloadWorker(
            selected,
            out_dir,
            self.mode_combo.currentData(),
            video_quality,
            self.browser_combo.currentText(),
            cookies_file,
            self.overwrite_combo.currentData(),
            self.chk_thumbnail.isChecked(),
            self.ytdlp_main,
            self.ytdlp_is_exe,
            self._archive_file(),
            force_codec=(
                self.chk_force_codec.isChecked()
                if self.mode_combo.currentData() == "video"
                else False
            ),
            codec=self.codec_combo.currentData(),
        )
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def download_thumbnails_selected(self):
        """Tải riêng thumbnail cho các video đang được chọn trong bảng."""
        selected = self.selected_items()
        if not selected:
            QMessageBox.information(self, "Chưa chọn", "Bạn chưa chọn video nào.")
            return

        out_dir = self.out_dir.text().strip()
        if not out_dir:
            QMessageBox.information(self, "Thiếu thư mục", "Bạn chưa chọn thư mục lưu.")
            return

        # Lưu thumbnail vào thư mục con "thumbnails" trong thư mục tải
        thumb_dir = os.path.join(out_dir, "thumbnails")
        os.makedirs(thumb_dir, exist_ok=True)

        self.set_busy(True)
        self.progress.setValue(0)
        self.progress_text.setText("0% - Bắt đầu tải thumbnail")
        self.append_log(f"🖼 Bắt đầu tải {len(selected)} thumbnail vào: {thumb_dir}")

        self.thumb_worker = ThumbnailDownloadWorker(selected, thumb_dir)
        self.thumb_worker.log.connect(self.append_log)
        self.thumb_worker.progress.connect(self.on_thumb_progress)
        self.thumb_worker.done.connect(self.on_thumb_done)
        self.thumb_worker.failed.connect(self.on_thumb_failed)
        self.thumb_worker.start()

    def on_thumb_progress(self, current, total):
        if total <= 0:
            self.progress.setValue(0)
            return
        pct = int(current * 100 / total)
        self.progress.setValue(pct)
        self.progress_text.setText(f"{pct}% - {current}/{total} thumbnail")

    def on_thumb_done(self):
        self.progress.setValue(100)
        self.progress_text.setText("100% - Hoàn tất tải thumbnail")
        msg = "✅ Hoàn tất tải thumbnail."
        # Thêm thống kê chi tiết nếu worker có thuộc tính thống kê
        if hasattr(self, "thumb_worker") and self.thumb_worker is not None:
            total = getattr(self.thumb_worker, "total", 0)
            ok = getattr(self.thumb_worker, "success_count", 0)
            err = getattr(self.thumb_worker, "error_count", 0)
            msg += f" 📊 Tổng: {total}, thành công: {ok}, lỗi: {err}."
        self.append_log(msg)
        self.set_busy(False)

    def on_thumb_failed(self, msg):
        self.append_log(f"❌ Lỗi tải thumbnail: {msg}")
        self.set_busy(False)

    def on_progress(self, current, total):
        if total <= 0:
            self.progress.setValue(0)
            return
        pct = int(current * 100 / total)
        self.progress.setValue(pct)
        self.progress_text.setText(f"{pct}% - {current}/{total} video")

    def on_done(self):
        self.progress.setValue(100)
        self.progress_text.setText("100% - Hoàn tất")
        msg = "✅ Hoàn tất tải."
        if hasattr(self, "worker") and self.worker is not None:
            total = getattr(self.worker, "total", 0)
            ok = getattr(self.worker, "success_count", 0)
            skip = getattr(self.worker, "skip_count", 0)
            err = getattr(self.worker, "error_count", 0)
            msg += (
                f" 📊 Tổng: {total}, tải mới: {ok}, bỏ qua (đã có): {skip}, lỗi: {err}."
            )
        self.append_log(msg)
        self._populate_table()
        self.set_busy(False)

    def on_failed(self, err):
        self.append_log(f"❌ Lỗi: {err}")
        self.set_busy(False)

    def stop_all(self):
        if self.expander and self.expander.isRunning():
            self.expander.terminate()
            self.append_log("⏹ Dừng quét.")
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.append_log("⏹ Yêu cầu dừng tải (yt-dlp).")
        if self.bilibili_worker and self.bilibili_worker.isRunning():
            self.bilibili_worker.stop()
            self.append_log("⏹ Yêu cầu dừng tải (Bilibili Native).")
        if self.merge_worker and self.merge_worker.isRunning():
            self.merge_worker.terminate()
            self.append_log("⏹ Dừng gộp video.")
        if self.split_worker and self.split_worker.isRunning():
            self.split_worker.terminate()
            self.append_log("⏹ Dừng cắt video.")
        self.set_busy(False)

    def pick_merge_video(self):
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file Video",
            "",
            "Video Files (*.mp4 *.m4s *.mkv *.avi);;All Files (*)",
        )
        if f:
            self.merge_video_edit.setText(f)
            # Tự động đặt thư mục lưu dựa trên file video gốc nếu chưa chọn
            if not self.merge_out_edit.text():
                p = Path(f)
                suggested = p.parent / f"{p.stem}_merged.mp4"
                self.merge_out_edit.setText(str(suggested))

    def pick_merge_audio(self):
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file Audio",
            "",
            "Audio Files (*.mp3 *.m4s *.m4a *.wav);;All Files (*)",
        )
        if f:
            self.merge_audio_edit.setText(f)

    def pick_merge_out(self):
        # Lấy đường dẫn đã lưu hoặc dùng thư mục chứa file video gốc
        default_path = self.merge_out_edit.text()
        if not default_path:
            # Nếu chưa có đường dẫn, lấy từ file video gốc
            video_path = self.merge_video_edit.text().strip()
            if video_path and os.path.exists(video_path):
                default_path = str(Path(video_path).parent / "merged_video.mp4")
            else:
                # Hoặc lấy đường dẫn đã lưu từ QSettings
                saved_path = self.settings.value("merge_out_dir", "")
                if saved_path:
                    default_path = str(Path(saved_path).parent / "merged_video.mp4")
                else:
                    default_path = "merged_video.mp4"

        f, _ = QFileDialog.getSaveFileName(
            self, "Lưu file gộp", default_path, "Video (*.mp4)"
        )
        if f:
            self.merge_out_edit.setText(f)
            # Lưu đường dẫn thư mục để nhớ cho lần sau
            self.settings.setValue("merge_out_dir", f)

    def run_merge(self):
        v = self.merge_video_edit.text().strip()
        a = self.merge_audio_edit.text().strip()
        out = self.merge_out_edit.text().strip()

        if not v or not a:
            QMessageBox.warning(
                self, "Thiếu file", "Vui lòng chọn cả file Video và Audio nguồn."
            )
            return

        if not out:
            QMessageBox.warning(
                self, "Thiếu file lưu", "Vui lòng chọn đường dẫn lưu file kết quả."
            )
            return

        if not self.ffmpeg_path:
            QMessageBox.critical(
                self, "Thiếu FFmpeg", "Không tìm thấy FFmpeg để thực hiện gộp."
            )
            return

        self.set_busy(True)
        self.append_log(f"🎬 Bắt đầu gộp video...")

        self.merge_worker = MergeWorker(self.ffmpeg_path, v, a, out)
        self.merge_worker.log.connect(self.append_log)
        self.merge_worker.done.connect(self.on_merge_done)
        self.merge_worker.failed.connect(self.on_merge_failed)
        self.merge_worker.start()

    def on_merge_done(self, out_path):
        self.append_log(f"✅ Gộp thành công: {out_path}")
        # Log tóm tắt riêng cho chức năng gộp
        self.append_log("📊 Tóm tắt gộp: 1 video đầu ra, lỗi: 0")
        self.set_busy(False)
        QMessageBox.information(self, "Thành công", f"Đã gộp xong video:\n{out_path}")

    def on_merge_failed(self, err):
        self.append_log(f"❌ Lỗi gộp: {err}")
        self.set_busy(False)
        QMessageBox.warning(self, "Lỗi", f"Quá trình gộp thất bại:\n{err}")

    def _toggle_split_panes(self):
        is_auto = self.split_mode_auto.isChecked()
        self.split_pane_auto.setVisible(is_auto)
        self.split_pane_manual.setVisible(not is_auto)

    def _get_video_duration(self, file_path):
        """Lấy tổng thời gian video (giây) bằng ffmpeg."""
        if not self.ffmpeg_path:
            return 0
        try:
            # Đã unblock ở bước chọn file, ở đây chỉ chạy.
            # Dùng encoding='utf-8' và errors='replace' để tránh lỗi ký tự đặc biệt trong tên file hoặc output.
            proc = subprocess.run(
                [self.ffmpeg_path, "-i", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_subprocess_no_console_kwargs(),
            )
            output = proc.stderr

            # Regex linh hoạt hơn: 'Duration: HH:MM:SS[.mm]'
            # Một số bản ffmpeg có thể có khoảng trắng khác nhau hoặc dùng dấu chấm/phảy cho ms.
            import re

            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)(\.\d+)?", output)
            if m:
                h = int(m.group(1))
                m_val = int(m.group(2))
                s = int(m.group(3))
                duration = h * 3600 + m_val * 60 + s
                # self.append_log(f"ℹ️ Đã lấy thời lượng: {duration}s ({m.group(1)}:{m.group(2)}:{m.group(3)})")
                return duration
            else:
                self.append_log(
                    f"⚠️ Không tìm thấy thông tin 'Duration' trong output của FFmpeg."
                )
                # Log một đoạn output để debug nếu cần
                if output:
                    self.append_log(f"DEBUG FFmpeg (100 char): {output[:100]}...")
        except Exception as e:
            self.append_log(f"❌ Lỗi khi gọi FFmpeg lấy thời lượng: {str(e)}")
        return 0

    def pick_split_video(self):
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file Video cần cắt",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.m4s);;All Files (*)",
        )
        if f:
            self.split_video_edit.setText(f)
            # Tự động đặt thư mục lưu dựa trên file video gốc nếu chưa chọn
            if not self.split_out_edit.text():
                self.split_out_edit.setText(str(Path(f).parent / "Split_Parts"))

    def _load_video_preview(self):
        """Load video đã chọn vào player xem trước (chuyển sang tab Cắt trực tiếp)."""
        video_path = self.split_video_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.information(
                self, "Chưa chọn video",
                "Vui lòng chọn file video trước khi xem trước."
            )
            return

        # Truyền ffmpeg_path cho widget
        self.video_trim_widget.set_ffmpeg_path(self.ffmpeg_path)

        # Chuyển sang tab Cắt trực tiếp
        if hasattr(self, 'tab_trim'):
            self.tabs.setCurrentWidget(self.tab_trim)

        # Load video vào player
        self.video_trim_widget.load_video(video_path)

    def pick_split_out(self):
        # Lấy đường dẫn đã lưu hoặc dùng thư mục chứa file video gốc
        default_dir = self.split_out_edit.text()
        if not default_dir:
            # Nếu chưa có đường dẫn, lấy từ file video gốc
            video_path = self.split_video_edit.text().strip()
            if video_path and os.path.exists(video_path):
                default_dir = str(Path(video_path).parent / "Split_Parts")
            else:
                # Hoặc lấy đường dẫn đã lưu từ QSettings
                default_dir = self.settings.value("split_out_dir", "")
                if not default_dir:
                    default_dir = str(Path.home() / "Downloads")

        d = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục lưu kết quả", default_dir
        )
        if d:
            self.split_out_edit.setText(d)
            # Lưu đường dẫn để nhớ cho lần sau
            self.settings.setValue("split_out_dir", d)

    def _parse_timestamp(self, ts):
        """Chuyển 00:00:00 hoặc 00:00 hoặc số giây sang int giây."""
        ts = ts.strip().replace(",", ".")
        if not ts:
            return 0
        parts = ts.split(":")
        try:
            if len(parts) == 3:
                return int(
                    float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                )
            elif len(parts) == 2:
                return int(float(parts[0]) * 60 + float(parts[1]))
            else:
                return int(float(parts[0]))
        except:
            return 0

    def run_split(self):
        v = self.split_video_edit.text().strip()
        out_root = self.split_out_edit.text().strip()

        if not v or not os.path.exists(v):
            QMessageBox.warning(
                self, "Thiếu file", "Vui lòng chọn file Video gốc hợp lệ."
            )
            return

        # Tự động đặt thư mục lưu nếu chưa chọn
        if not out_root:
            # Lấy từ file video gốc
            out_root = str(Path(v).parent / "Split_Parts")
            self.split_out_edit.setText(out_root)

        segments = []
        if self.split_mode_manual.isChecked():
            # Parse ranges thủ công
            lines = self.split_ranges_edit.toPlainText().strip().split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Normalize dashes to simple hyphen (-)
                line = line.replace("—", "-").replace("–", "-").replace("->", "-")
                pts = line.split("-")

                if len(pts) == 2:
                    s = self._parse_timestamp(pts[0])
                    e = self._parse_timestamp(pts[1])
                    if e > s:
                        segments.append((s, e))
        else:
            # Cắt tự động đều nhau
            duration = self._get_video_duration(v)
            if duration <= 0:
                QMessageBox.warning(
                    self, "Lỗi meta", "Không lấy được thời lượng video để cắt tự động."
                )
                return

            # Nếu người dùng bật chế độ cắt theo SỐ LƯỢNG đoạn
            if (
                getattr(self, "split_use_parts", None)
                and self.split_use_parts.isChecked()
            ):
                parts = self.split_auto_parts.value()
                if parts < 2:
                    QMessageBox.warning(
                        self,
                        "Thiếu thông tin",
                        "Số lượng đoạn phải từ 2 trở lên khi cắt đều theo số lượng.",
                    )
                    return
                if duration < parts:
                    QMessageBox.warning(
                        self,
                        "Không hợp lệ",
                        "Video quá ngắn so với số lượng đoạn muốn cắt.",
                    )
                    return

                base_step = duration // parts
                if base_step <= 0:
                    QMessageBox.warning(
                        self,
                        "Không hợp lệ",
                        "Không thể tính được độ dài mỗi đoạn. Vui lòng giảm số lượng đoạn.",
                    )
                    return

                curr = 0
                for i in range(parts):
                    if i == parts - 1:
                        nxt = duration
                    else:
                        nxt = curr + base_step
                    segments.append((curr, nxt))
                    curr = nxt

                # Ghi nhớ số phần để log tóm tắt khi xong
                self._last_split_parts = parts
            else:
                # Cắt theo thời lượng cố định (mỗi phần X phút)
                step = self.split_auto_time.value() * 60
                curr = 0
                while curr < duration:
                    nxt = min(curr + step, duration)
                    segments.append((curr, nxt))
                    if nxt >= duration:
                        break
                    curr += step

                # Ghi nhớ số phần để log tóm tắt khi xong
                self._last_split_parts = len(segments)

        if not segments:
            QMessageBox.warning(
                self,
                "Thiếu thông tin",
                "Vui lòng nhập timeline hoặc chọn chế độ cắt hợp lệ.",
            )
            return

        if not self.ffmpeg_path:
            QMessageBox.critical(
                self, "Thiếu FFmpeg", "Không tìm thấy FFmpeg để thực hiện cắt."
            )
            return

        os.makedirs(out_root, exist_ok=True)
        overlap = self.split_overlap_spin.value()

        self.set_busy(True)
        self.append_log(
            f"✂️ Chế độ: {'Tự động' if self.split_mode_auto.isChecked() else 'Thủ công'}"
        )
        self.append_log(f"✂️ Bắt đầu chia nhỏ video ({len(segments)} phần)...")

        self.split_worker = SplitWorker(
            self.ffmpeg_path, v, segments, out_root, overlap
        )
        self.split_worker.log.connect(self.append_log)
        self.split_worker.done.connect(self.on_split_done)
        self.split_worker.failed.connect(self.on_split_failed)
        self.split_worker.start()

    def on_split_done(self, out_dir):
        self.append_log(f"✅ Chia nhỏ video hoàn tất tại: {out_dir}")
        # Tóm tắt số phần đã cắt (nếu còn nhớ từ lần run_split gần nhất)
        parts = getattr(self, "_last_split_parts", None)
        if isinstance(parts, int) and parts > 0:
            self.append_log(f"📊 Tóm tắt cắt: Đã cắt thành {parts} phần.")
        self.set_busy(False)
        QMessageBox.information(
            self,
            "Thành công",
            f"Đã chia nhỏ xong video.\nCác file lưu theo folder p1, p2... tại:\n{out_dir}",
        )

    def on_split_failed(self, err):
        self.append_log(f"❌ Lỗi cắt: {err}")
        self.set_busy(False)
        QMessageBox.warning(self, "Lỗi", f"Quá trình cắt video thất bại:\n{err}")

    # ================= Fast Concat =================
    def pick_fc_video(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn các file video",
            "",
            "Video Files (*.mp4 *.mkv *.mov *.avi *.m4s)",
        )
        if files:
            for f in files:
                self.fc_list.addItem(f)
            # Tự động đặt thư mục lưu dựa trên file video đầu tiên nếu chưa chọn
            if not self.fc_out_edit.text() and files:
                video_path = files[0]
                suggested = Path(video_path).parent / "fast_merged.mp4"
                self.fc_out_edit.setText(str(suggested))

    def fc_move_up(self):
        row = self.fc_list.currentRow()
        if row > 0:
            item = self.fc_list.takeItem(row)
            self.fc_list.insertItem(row - 1, item)
            self.fc_list.setCurrentRow(row - 1)

    def fc_move_down(self):
        row = self.fc_list.currentRow()
        if row >= 0 and row < self.fc_list.count() - 1:
            item = self.fc_list.takeItem(row)
            self.fc_list.insertItem(row + 1, item)
            self.fc_list.setCurrentRow(row + 1)

    def fc_remove_item(self):
        row = self.fc_list.currentRow()
        if row >= 0:
            self.fc_list.takeItem(row)

    def pick_fc_out(self):
        # Lấy đường dẫn đã lưu hoặc dùng thư mục chứa file video đầu tiên
        default_path = self.fc_out_edit.text()
        if not default_path:
            # Nếu chưa có đường dẫn, lấy từ file video đầu tiên trong danh sách
            if self.fc_list.count() > 0:
                first_video = self.fc_list.item(0).text().strip()
                if first_video and os.path.exists(first_video):
                    default_path = str(Path(first_video).parent / "fast_merged.mp4")
                else:
                    # Hoặc lấy đường dẫn đã lưu từ QSettings
                    saved_path = self.settings.value("fc_out_dir", "")
                    if saved_path:
                        default_path = str(Path(saved_path).parent / "fast_merged.mp4")
                    else:
                        default_path = "fast_merged.mp4"
            else:
                # Hoặc lấy đường dẫn đã lưu từ QSettings
                saved_path = self.settings.value("fc_out_dir", "")
                if saved_path:
                    default_path = str(Path(saved_path).parent / "fast_merged.mp4")
                else:
                    default_path = "fast_merged.mp4"

        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu file gộp", default_path, "Video Files (*.mp4)"
        )
        if path:
            self.fc_out_edit.setText(path)
            # Lưu đường dẫn để nhớ cho lần sau
            self.settings.setValue("fc_out_dir", path)

    def run_fast_concat(self):
        v_lines = [
            self.fc_list.item(i).text().strip()
            for i in range(self.fc_list.count())
            if self.fc_list.item(i).text().strip()
        ]
        out_root = self.fc_out_edit.text().strip()

        if not v_lines:
            QMessageBox.warning(
                self, "Thiếu file", "Vui lòng chọn ít nhất 1 video để ghép."
            )
            return

        for v in v_lines:
            if not os.path.exists(v):
                QMessageBox.warning(self, "Lỗi file", f"File không tồn tại:\n{v}")
                return

        # Tự động đặt đường dẫn lưu nếu chưa chọn
        if not out_root:
            # Lấy từ file video đầu tiên
            first_video = v_lines[0]
            out_root = str(Path(first_video).parent / "fast_merged.mp4")
            self.fc_out_edit.setText(out_root)

        if not self.ffmpeg_path:
            QMessageBox.critical(self, "Thiếu FFmpeg", "Không tìm thấy FFmpeg.")
            return

        self.set_busy(True)
        self.append_log(f"⚡ Đang ghép {len(v_lines)} video...")

        self.fc_worker = FastConcatWorker(v_lines, out_root, self.ffmpeg_path)
        self.fc_worker.log.connect(self.append_log)
        self.fc_worker.done.connect(self.on_fc_done)
        self.fc_worker.failed.connect(self.on_fc_failed)
        self.fc_worker.start()

    def _probe_video(self, filepath):
        """Dùng ffprobe để lấy thông tin video: fps, width, height, codec."""
        try:
            cmd = [
                str(self.ffmpeg_path)
                .replace("ffmpeg", "ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "v:0",
                str(filepath),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_subprocess_no_console_kwargs(),
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if not streams:
                return None
            s = streams[0]
            fps_str = s.get("r_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                fps = round(int(num) / int(den), 3)
            except Exception:
                fps = 0.0
            return {
                "fps": fps,
                "width": int(s.get("width", 0)),
                "height": int(s.get("height", 0)),
                "codec": s.get("codec_name", "unknown"),
            }
        except Exception:
            return None

    def check_fc_compat(self):
        """Kiểm tra tính tương thích (FPS, độ phân giải, codec) của các video trong danh sách ghép."""
        if self.fc_list.count() == 0:
            QMessageBox.information(
                self, "Không có video", "Danh sách ghép nhanh đang trống."
            )
            return

        # Xác định đường dẫn ffprobe
        ffprobe_path = (
            str(self.ffmpeg_path)
            .replace("ffmpeg", "ffprobe")
        )

        infos = []
        for i in range(self.fc_list.count()):
            path = self.fc_list.item(i).text()
            info = self._probe_video(path)
            infos.append((Path(path).name, info))

        lines = []
        all_fps = set()
        all_res = set()
        all_codecs = set()

        for name, info in infos:
            if info:
                all_fps.add(info["fps"])
                all_res.add((info["width"], info["height"]))
                all_codecs.add(info["codec"])
                lines.append(
                    f"  ✔ {name}\n"
                    f"     {info['width']}×{info['height']}  |  {info['fps']} fps  |  {info['codec']}"
                )
            else:
                lines.append(f"  ⚠ {name}\n     (Không đọc được thông tin)")

        issues = []
        if len(all_fps) > 1:
            issues.append(
                f"⚠ FPS khác nhau: {', '.join(str(f) for f in sorted(all_fps))}"
            )
        if len(all_res) > 1:
            issues.append(
                f"⚠ Độ phân giải khác nhau: {', '.join(f'{w}×{h}' for w,h in sorted(all_res))}"
            )
        if len(all_codecs) > 1:
            issues.append(f"⚠ Codec khác nhau: {', '.join(sorted(all_codecs))}")

        summary = (
            "\n".join(issues)
            if issues
            else "✅ Tất cả video tương thích (-c copy có thể dùng được)"
        )

        msg = "=== Kiểm tra tương thích video ===\n\n"
        msg += "\n".join(lines)
        msg += "\n\n--- Kết quả ---\n" + summary

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Kiểm tra tương thích ghép nhanh")
        dlg.setText(summary)
        dlg.setDetailedText(msg)
        dlg.setStandardButtons(QMessageBox.Ok)
        dlg.exec_()

    def on_fc_done(self, out_file):
        self.append_log(f"✅ Ghép video hoàn tất: {out_file}")
        # Tóm tắt số video đã ghép
        total = self.fc_list.count()
        if total > 0:
            self.append_log(f"📊 Tóm tắt ghép nhanh: Đã ghép {total} video vào 1 file.")
        self.set_busy(False)
        QMessageBox.information(
            self, "Thành công", f"Đã ghép video thành công:\n{out_file}"
        )

    def on_fc_failed(self, err):
        self.append_log(f"❌ Lỗi ghép: {err}")
        self.set_busy(False)
        QMessageBox.warning(self, "Lỗi", f"Ghép video gặp lỗi:\n{err}")

    def closeEvent(self, event):
        # Cleanup video trim widget trước khi đóng
        # Tránh crash AVFoundation khi QVideoWidget bị hủy
        if hasattr(self, 'trim_widget') and self.trim_widget:
            try:
                self.trim_widget.cleanup()
            except Exception:
                pass
        return super().closeEvent(event)
    # ================= Settings Tab Handlers =================

    def _qr_image_clicked(self, event):
        """Mở popup phóng to QR code để dễ quét."""
        if not hasattr(self, '_qr_pixmap_bytes') or not self._qr_pixmap_bytes:
            return
        from PyQt5.QtGui import QPixmap
        dlg = QDialog(self)
        dlg.setWindowTitle("Quét mã QR bằng app Bilibili")
        dlg.setFixedSize(500, 540)
        dlg.setStyleSheet("background: white;")
        lay = QVBoxLayout(dlg)
        lay.setAlignment(Qt.AlignCenter)

        title = QLabel("Mở app Bilibili → Quét mã (扫一扫)")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #333; font-size: 14px; font-weight: bold; padding: 8px;")
        lay.addWidget(title)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap()
        pixmap.loadFromData(self._qr_pixmap_bytes)
        img_label.setPixmap(
            pixmap.scaled(400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        lay.addWidget(img_label)

        hint = QLabel("Dùng scanner trong app Bilibili, không dùng camera điện thoại")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px; padding: 4px;")
        lay.addWidget(hint)

        dlg.exec_()

    def _on_qr_refresh(self):
        """Tạo mã QR mới cho đăng nhập Bilibili bằng API."""
        import urllib.request
        import io

        self.qr_status_label.setText("⏳ Đang tạo mã QR...")
        self.qr_scan_status.setText("")
        self.qr_timer_label.setText("--:--")
        self.append_log("🔐 QR Login: Đang gọi Bilibili API tạo mã QR...")

        # Dừng timer poll cũ nếu có
        if hasattr(self, '_qr_poll_timer') and self._qr_poll_timer is not None:
            self._qr_poll_timer.stop()
        if hasattr(self, '_qr_countdown_timer') and self._qr_countdown_timer is not None:
            self._qr_countdown_timer.stop()

        try:
            # Gọi API tạo QR code
            api_url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
            req = urllib.request.Request(api_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com/"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("code") != 0:
                raise RuntimeError(f"Bilibili API lỗi: {data.get('message', 'unknown')}")

            qr_url = data["data"]["url"]
            self._bili_qrcode_key = data["data"]["qrcode_key"]
            self.append_log(f"✅ Đã nhận QR code key: {self._bili_qrcode_key[:8]}...")

            # Tạo QR code image bằng thư viện qrcode
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=10, border=2)
            qr.add_data(qr_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")

            # Chuyển PIL Image → QPixmap
            buffer = io.BytesIO()
            qr_img.save(buffer, format="PNG")
            buffer.seek(0)
            from PyQt5.QtGui import QPixmap
            qr_bytes = buffer.read()
            self._qr_pixmap_bytes = qr_bytes  # Lưu để dùng cho click-to-enlarge
            pixmap = QPixmap()
            pixmap.loadFromData(qr_bytes)
            self.qr_image_label.setPixmap(
                pixmap.scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.qr_image_label.setStyleSheet(
                "background: white; border-radius: 8px; padding: 4px;"
            )

            self.qr_status_label.setText("📱 Mở app Bilibili → Quét mã QR")
            self.qr_scan_status.setText("⏱ Đang chờ quét...")
            self.append_log("📱 Đã hiện QR code — mở app Bilibili để quét!")

            # Bắt đầu đếm ngược 3 phút (180 giây)
            self._qr_remaining_secs = 180
            from PyQt5.QtCore import QTimer
            self._qr_countdown_timer = QTimer(self)
            self._qr_countdown_timer.timeout.connect(self._qr_countdown_tick)
            self._qr_countdown_timer.start(1000)

            # Poll trạng thái quét mỗi 3 giây
            self._qr_poll_timer = QTimer(self)
            self._qr_poll_timer.timeout.connect(self._qr_poll_status)
            self._qr_poll_timer.start(3000)

        except Exception as e:
            self.qr_status_label.setText("❌ Lỗi tạo QR code")
            self.qr_scan_status.setText("")
            self.append_log(f"❌ QR Login lỗi: {e}")
            QMessageBox.warning(self, "Lỗi QR Login", f"Không thể tạo mã QR:\n{e}")

    def _qr_countdown_tick(self):
        """Đếm ngược thời gian hết hạn QR."""
        self._qr_remaining_secs -= 1
        mins = self._qr_remaining_secs // 60
        secs = self._qr_remaining_secs % 60
        self.qr_timer_label.setText(f"{mins:02d}:{secs:02d}")

        if self._qr_remaining_secs <= 0:
            self._stop_qr_polling()
            self.qr_status_label.setText("⏰ Mã QR đã hết hạn")
            self.qr_scan_status.setText("Nhấn 'Tạo mã QR mới' để thử lại")
            self.append_log("⏰ QR code đã hết hạn.")

    def _qr_poll_status(self):
        """Poll API kiểm tra trạng thái quét QR."""
        import urllib.request

        if not hasattr(self, '_bili_qrcode_key'):
            self._stop_qr_polling()
            return

        try:
            poll_url = (
                f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
                f"?qrcode_key={self._bili_qrcode_key}"
            )
            req = urllib.request.Request(poll_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com/"
            })
            resp = urllib.request.urlopen(req, timeout=10)
            # Lấy Set-Cookie headers
            set_cookies = resp.headers.get_all("Set-Cookie") or []
            body = json.loads(resp.read().decode("utf-8"))

            code = body.get("data", {}).get("code", -1)

            if code == 86101:
                # Chưa quét
                self.qr_scan_status.setText("⏱ Đang chờ quét...")
            elif code == 86090:
                # Đã quét, chưa xác nhận
                self.qr_scan_status.setText("✅ Đã quét! Xác nhận trên điện thoại...")
                self.qr_status_label.setText("📱 Nhấn xác nhận trên app Bilibili")
            elif code == 0:
                # Đăng nhập thành công!
                self._stop_qr_polling()
                self.qr_status_label.setText("✅ Đăng nhập Bilibili thành công!")
                self.qr_scan_status.setText("🍪 Đã lưu cookie tự động")
                self.append_log("✅ Bilibili QR Login thành công!")

                # Trích xuất cookie từ response URL và Set-Cookie headers
                refresh_token = body.get("data", {}).get("refresh_token", "")
                url_with_cookies = body.get("data", {}).get("url", "")
                self._save_bilibili_cookies(url_with_cookies, set_cookies, refresh_token)

                # Lấy buvid3/buvid4 (cần cho WBI authentication)
                try:
                    login_helper = BilibiliQRLogin()
                    buvid3, buvid4 = login_helper.fetch_buvid()
                    if buvid3 or buvid4:
                        self._append_bilibili_buvid(buvid3, buvid4)
                        self.append_log(f"🔑 Đã lấy buvid3/buvid4 cho WBI auth")
                except Exception as e:
                    self.append_log(f"⚠ Không lấy được buvid: {e}")

                # Lấy thông tin user
                try:
                    bili_cookies = load_bilibili_cookies(self.cookie_profiles_path)
                    if bili_cookies:
                        user_info = login_helper.fetch_user_info(bili_cookies)
                        uname = user_info.get("uname", "")
                        if uname:
                            self.qr_status_label.setText(
                                f"✅ Đã đăng nhập: {uname}"
                            )
                            self.append_log(f"👤 Đã đăng nhập với tài khoản: {uname}")
                except Exception:
                    pass

            elif code == 86038:
                # QR code hết hạn
                self._stop_qr_polling()
                self.qr_status_label.setText("⏰ Mã QR đã hết hạn")
                self.qr_scan_status.setText("Nhấn 'Tạo mã QR mới' để thử lại")
                self.append_log("⏰ QR code hết hạn.")
            else:
                self.append_log(f"⚠ QR poll code không xác định: {code}")

        except Exception as e:
            self.append_log(f"⚠ QR poll lỗi: {e}")

    def _stop_qr_polling(self):
        """Dừng tất cả timer liên quan QR."""
        if hasattr(self, '_qr_poll_timer') and self._qr_poll_timer is not None:
            self._qr_poll_timer.stop()
            self._qr_poll_timer = None
        if hasattr(self, '_qr_countdown_timer') and self._qr_countdown_timer is not None:
            self._qr_countdown_timer.stop()
            self._qr_countdown_timer = None

    def _restore_bilibili_login_state(self):
        """Khôi phục trạng thái đăng nhập Bilibili từ cookie đã lưu khi khởi động app."""
        try:
            bili_cookies = load_bilibili_cookies(self.cookie_profiles_path)
            if not bili_cookies or not bili_cookies.get("SESSDATA"):
                return  # Chưa đăng nhập

            # Kiểm tra cookie còn hợp lệ bằng cách gọi API
            login_helper = BilibiliQRLogin()
            user_info = login_helper.fetch_user_info(bili_cookies)
            uname = user_info.get("uname", "")
            is_login = user_info.get("isLogin", False)

            if is_login and uname:
                self.qr_status_label.setText(f"✅ Đã đăng nhập: {uname}")
                self.qr_scan_status.setText("🍪 Cookie từ phiên trước")
                self.append_log(f"🔐 Bilibili: Đã khôi phục phiên đăng nhập — {uname}")
            elif bili_cookies.get("SESSDATA"):
                # Cookie tồn tại nhưng có thể đã hết hạn
                self.qr_status_label.setText("⚠ Cookie Bilibili có thể đã hết hạn")
                self.qr_scan_status.setText("Nhấn 'Tạo mã QR mới' để đăng nhập lại")
                self.append_log("⚠ Bilibili: Cookie cũ không còn hợp lệ, cần đăng nhập lại")

        except Exception as e:
            # Không block UI nếu lỗi mạng/API
            self.append_log(f"ℹ Bilibili: Không kiểm tra được cookie cũ ({e})")

    def _append_bilibili_buvid(self, buvid3, buvid4):
        """Thêm buvid3/buvid4 vào cookie profile Bilibili đã lưu."""
        from datetime import datetime, timedelta
        profile_name = "www.bilibili.com"
        raw = self.cookie_profiles.get(profile_name, "")
        if not raw:
            return

        try:
            data = json.loads(raw)
            cookies_list = data.get("cookies", [])

            # Xóa buvid cũ nếu có
            cookies_list = [c for c in cookies_list if c.get("name") not in ("buvid3", "buvid4")]

            # Thêm buvid mới
            for name, value in [("buvid3", buvid3), ("buvid4", buvid4)]:
                if value:
                    cookies_list.append({
                        "domain": ".bilibili.com",
                        "hostOnly": False,
                        "path": "/",
                        "secure": True,
                        "name": name,
                        "value": value,
                        "expirationDate": int((datetime.now() + timedelta(days=180)).timestamp())
                    })

            data["cookies"] = cookies_list
            self.cookie_profiles[profile_name] = json.dumps(data, ensure_ascii=False)
            self._save_cookie_profiles()

        except Exception as e:
            self.append_log(f"⚠ Lỗi lưu buvid: {e}")

    def _save_bilibili_cookies(self, url_with_cookies, set_cookie_headers, refresh_token):
        """Lưu cookie Bilibili vào cookie_profiles.json."""
        from urllib.parse import urlparse, parse_qs
        from datetime import datetime, timedelta

        cookies_list = []

        # 1. Parse cookie params từ URL (thường chứa DedeUserID, bili_jct, SESSDATA, etc.)
        if url_with_cookies:
            parsed = urlparse(url_with_cookies)
            params = parse_qs(parsed.query)
            for key, values in params.items():
                if key in ("url",):  # skip non-cookie params
                    continue
                cookies_list.append({
                    "domain": ".bilibili.com",
                    "hostOnly": False,
                    "path": "/",
                    "secure": True,
                    "name": key,
                    "value": values[0] if values else "",
                    "expirationDate": int((datetime.now() + timedelta(days=180)).timestamp())
                })

        # 2. Parse từ Set-Cookie headers
        for header in set_cookie_headers:
            try:
                # Format: "name=value; Path=/; Domain=.bilibili.com; ..."
                parts = header.split(";")
                name_value = parts[0].strip()
                if "=" not in name_value:
                    continue
                name, value = name_value.split("=", 1)
                name = name.strip()
                value = value.strip()

                # Parse domain, path, etc.
                domain = ".bilibili.com"
                path = "/"
                secure = False
                for p in parts[1:]:
                    p = p.strip().lower()
                    if p.startswith("domain="):
                        domain = p.split("=", 1)[1].strip()
                    elif p.startswith("path="):
                        path = p.split("=", 1)[1].strip()
                    elif p == "secure":
                        secure = True

                cookies_list.append({
                    "domain": domain,
                    "hostOnly": False,
                    "path": path,
                    "secure": secure,
                    "name": name,
                    "value": value,
                    "expirationDate": int((datetime.now() + timedelta(days=180)).timestamp())
                })
            except Exception:
                continue

        if refresh_token:
            cookies_list.append({
                "domain": ".bilibili.com",
                "hostOnly": False,
                "path": "/",
                "secure": True,
                "name": "refresh_token",
                "value": refresh_token,
                "expirationDate": int((datetime.now() + timedelta(days=180)).timestamp())
            })

        if not cookies_list:
            self.append_log("⚠ Không trích xuất được cookie từ response.")
            return

        # Lưu vào cookie_profiles
        profile_data = json.dumps({
            "url": "https://www.bilibili.com/",
            "cookies": cookies_list
        }, ensure_ascii=False)

        profile_name = "www.bilibili.com"
        self.cookie_profiles[profile_name] = profile_data
        self._save_cookie_profiles()
        self._refresh_cookie_profiles_combo()

        # Chọn profile vừa lưu
        idx = self.cookie_profile_combo.findData(profile_name)
        if idx >= 0:
            self.cookie_profile_combo.setCurrentIndex(idx)

        # Cũng ghi vào text area cookie_json_input
        self.cookie_json_input.setPlainText(profile_data)

        self.append_log(f"🍪 Đã lưu {len(cookies_list)} cookie cho Bilibili")
        self.append_log(f"🍪 Cookie profile: {profile_name}")

    def _on_view_cookies(self):
        """Hiện dialog danh sách cookies đã lưu."""
        try:
            profiles_path = self.project_root / "cookie_profiles.json"
            if profiles_path.exists():
                with open(profiles_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                domains = list(data.keys())
                if domains:
                    msg = "Cookie đã lưu cho:\n\n" + "\n".join(
                        f"  • {d}" for d in domains
                    )
                else:
                    msg = "Chưa có cookie nào được lưu."
            else:
                msg = "Chưa có file cookie_profiles.json."
            QMessageBox.information(self, "Cookie đã lưu", msg)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Không thể đọc cookie: {e}")

    def _fetch_latest_github_tag(self, repo):
        """Lấy tag name release mới nhất từ GitHub API. Trả về (tag, error)."""
        import urllib.request
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VideoDownloader/2.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("tag_name", ""), None
        except Exception as e:
            return None, str(e)

    def _check_ytdlp_version(self):
        """Kiểm tra version yt-dlp hiện tại và so sánh với bản mới nhất trên GitHub."""
        current = None
        try:
            result = subprocess.run(
                [str(self.ytdlp_main), "--version"],
                capture_output=True, text=True, timeout=10,
                **_subprocess_no_console_kwargs()
            )
            current = result.stdout.strip()
        except FileNotFoundError:
            self.ytdlp_version_badge.setText("không tìm thấy")
            self.ytdlp_version_badge.setObjectName("versionBadgeWarning")
            self.append_log("❌ yt-dlp: không tìm thấy binary")
            self._apply_style()
            return
        except Exception as e:
            self.ytdlp_version_badge.setText("lỗi")
            self.ytdlp_version_badge.setObjectName("versionBadgeWarning")
            self.append_log(f"❌ yt-dlp version check lỗi: {e}")
            self._apply_style()
            return

        if not current:
            self.ytdlp_version_badge.setText("không rõ")
            self.ytdlp_version_badge.setObjectName("versionBadgeWarning")
            self._apply_style()
            return

        # So sánh với version mới nhất trên GitHub
        latest, err = self._fetch_latest_github_tag("yt-dlp/yt-dlp")
        if latest:
            latest_clean = latest.strip()
            if current == latest_clean:
                self.ytdlp_version_badge.setText(f"{current} ✅ mới nhất")
                self.ytdlp_version_badge.setObjectName("versionBadge")
                self.append_log(f"✅ yt-dlp {current} — đã là phiên bản mới nhất")
            else:
                self.ytdlp_version_badge.setText(f"{current} → {latest_clean}")
                self.ytdlp_version_badge.setObjectName("versionBadgeWarning")
                self.append_log(f"⬆ yt-dlp {current} → có bản mới: {latest_clean}")
        else:
            self.ytdlp_version_badge.setText(current)
            self.ytdlp_version_badge.setObjectName("versionBadge")
            self.append_log(f"✅ yt-dlp version: {current} (không kiểm tra được bản mới: {err})")
        self._apply_style()

    def _check_ffmpeg_version(self):
        """Kiểm tra version FFmpeg hiện tại và so sánh với bản mới nhất trên GitHub."""
        current = None
        try:
            result = subprocess.run(
                [str(self.ffmpeg_path), "-version"],
                capture_output=True, text=True, timeout=10,
                **_subprocess_no_console_kwargs()
            )
            output = result.stdout.strip()
            if output:
                first_line = output.split("\n")[0]
                parts = first_line.split()
                current = parts[2] if len(parts) >= 3 else first_line[:40]
        except FileNotFoundError:
            self.ffmpeg_version_badge.setText("không tìm thấy")
            self.ffmpeg_version_badge.setObjectName("versionBadgeWarning")
            self.append_log("❌ FFmpeg: không tìm thấy binary")
            self._apply_style()
            return
        except Exception as e:
            self.ffmpeg_version_badge.setText("lỗi")
            self.ffmpeg_version_badge.setObjectName("versionBadgeWarning")
            self.append_log(f"❌ FFmpeg version check lỗi: {e}")
            self._apply_style()
            return

        if not current:
            self.ffmpeg_version_badge.setText("không rõ")
            self.ffmpeg_version_badge.setObjectName("versionBadgeWarning")
            self._apply_style()
            return

        # So sánh với version mới nhất trên GitHub (BtbN/FFmpeg-Builds)
        latest, err = self._fetch_latest_github_tag("BtbN/FFmpeg-Builds")
        if latest:
            # Kiểm tra xem current version có chứa thông tin từ latest release không
            # FFmpeg version thường là dạng "N-xxxxx-gYYYYYY" hoặc "7.1-essentials_build"
            # GitHub tag thường là dạng "autobuild-2026-03-13-12-50" hoặc "latest"
            self.ffmpeg_version_badge.setText(f"{current}")
            self.ffmpeg_version_badge.setObjectName("versionBadge")
            self.append_log(f"✅ FFmpeg version: {current} (GitHub release: {latest})")
        else:
            self.ffmpeg_version_badge.setText(current)
            self.ffmpeg_version_badge.setObjectName("versionBadge")
            self.append_log(f"✅ FFmpeg version: {current}")
        self._apply_style()

    def _update_ytdlp(self):
        """Tải và cập nhật yt-dlp tự động từ GitHub (chạy trong background thread)."""
        self.append_log("⬆ Đang tải yt-dlp mới nhất từ GitHub...")
        self.btn_update_ytdlp.setEnabled(False)
        self.btn_update_ytdlp.setText("Đang tải...")

        from PyQt5.QtCore import QThread, pyqtSignal

        class YtdlpUpdateWorker(QThread):
            log = pyqtSignal(str)
            finished = pyqtSignal(bool, str)  # success, message

            def __init__(self, project_root, parent=None):
                super().__init__(parent)
                self.project_root = project_root

            def run(self):
                import urllib.request, tempfile, shutil
                try:
                    if sys.platform == "darwin":
                        download_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
                        _ytdlp_name = "yt-dlp"
                    else:
                        download_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
                        _ytdlp_name = "yt-dlp.exe"
                    self.log.emit(f"📥 Tải từ: {download_url}")

                    if getattr(sys, "frozen", False):
                        dest_dir = Path(sys.executable).resolve().parent
                    else:
                        dest_dir = self.project_root
                    dest_ytdlp = dest_dir / _ytdlp_name

                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_file = Path(tmp_dir) / _ytdlp_name
                        self.log.emit("📥 Đang tải... (có thể mất 1-2 phút)")
                        urllib.request.urlretrieve(download_url, str(tmp_file))
                        self.log.emit(f"✅ Tải xong ({tmp_file.stat().st_size // (1024*1024)} MB)")

                        backup = dest_dir / f"{_ytdlp_name}.bak"
                        if dest_ytdlp.exists():
                            try:
                                if backup.exists():
                                    backup.unlink()
                                dest_ytdlp.rename(backup)
                                self.log.emit(f"📋 Đã backup {_ytdlp_name} cũ")
                            except Exception as e:
                                self.log.emit(f"⚠ Không backup được: {e}")

                        shutil.copy2(str(tmp_file), str(dest_ytdlp))
                        if os.name == "nt":
                            _unblock_file(dest_ytdlp)
                        else:
                            os.chmod(str(dest_ytdlp), 0o755)

                    backup = dest_dir / f"{_ytdlp_name}.bak"
                    if backup.exists():
                        try:
                            backup.unlink()
                        except Exception:
                            pass

                    self.finished.emit(True, str(dest_ytdlp))
                except Exception as e:
                    # Khôi phục backup nếu có
                    _ytdlp_name = "yt-dlp" if sys.platform == "darwin" else "yt-dlp.exe"
                    if getattr(sys, "frozen", False):
                        restore_dir = Path(sys.executable).resolve().parent
                    else:
                        restore_dir = self.project_root
                    backup = restore_dir / f"{_ytdlp_name}.bak"
                    dest = restore_dir / _ytdlp_name
                    if backup.exists() and not dest.exists():
                        try:
                            backup.rename(dest)
                        except Exception:
                            pass
                    self.finished.emit(False, str(e))

        def on_ytdlp_update_done(success, msg):
            self.btn_update_ytdlp.setEnabled(True)
            self.btn_update_ytdlp.setText("⬆ Cập nhật")
            if success:
                self.ytdlp_main = Path(msg)
                self.ytdlp_is_exe = True
                self.append_log(f"✅ Đã cập nhật yt-dlp: {msg}")
                self._check_ytdlp_version()
                QMessageBox.information(self, "Cập nhật yt-dlp", f"Cập nhật yt-dlp thành công!\nFile: {msg}")
            else:
                self.append_log(f"❌ Lỗi cập nhật yt-dlp: {msg}")
                QMessageBox.warning(self, "Lỗi cập nhật yt-dlp", f"Lỗi:\n{msg}")

        self._ytdlp_update_worker = YtdlpUpdateWorker(self.project_root, self)
        self._ytdlp_update_worker.log.connect(self.append_log)
        self._ytdlp_update_worker.finished.connect(on_ytdlp_update_done)
        self._ytdlp_update_worker.start()

    def _update_ffmpeg(self):
        """Tải và cập nhật FFmpeg tự động (chạy trong background thread)."""
        self.append_log("⬆ Đang tải FFmpeg mới nhất...")
        self.btn_update_ffmpeg.setEnabled(False)
        self.btn_update_ffmpeg.setText("Đang tải...")

        from PyQt5.QtCore import QThread, pyqtSignal
        import platform

        class FfmpegUpdateWorker(QThread):
            log = pyqtSignal(str)
            finished = pyqtSignal(bool, str)  # success, dest_path_or_error

            def __init__(self, project_root, parent=None):
                super().__init__(parent)
                self.project_root = project_root

            def run(self):
                import urllib.request, zipfile, tempfile, shutil
                _ffmpeg_name = "ffmpeg" if sys.platform == "darwin" else "ffmpeg.exe"
                try:
                    if sys.platform == "darwin":
                        # macOS: dùng evermeet.cx hoặc fallback homebrew
                        zip_url = "https://evermeet.cx/ffmpeg/getrelease/zip"
                    else:
                        zip_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
                    self.log.emit(f"📥 Tải từ: {zip_url}")

                    if getattr(sys, "frozen", False):
                        dest_dir = Path(sys.executable).resolve().parent
                    else:
                        dest_dir = self.project_root
                    dest_ffmpeg = dest_dir / _ffmpeg_name

                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_zip = Path(tmp_dir) / "ffmpeg.zip"
                        self.log.emit("📥 Đang tải... (có thể mất 1-2 phút)")

                        # Tải với timeout 60s
                        try:
                            urllib.request.urlretrieve(zip_url, str(tmp_zip))
                        except Exception as dl_err:
                            if sys.platform == "darwin":
                                # Fallback: dùng brew update ffmpeg
                                self.log.emit(f"⚠ Không tải được từ evermeet.cx: {dl_err}")
                                self.log.emit("🔄 Đang thử cập nhật qua Homebrew...")
                                import subprocess
                                result = subprocess.run(
                                    ["brew", "upgrade", "ffmpeg"],
                                    capture_output=True, text=True, timeout=300
                                )
                                if result.returncode == 0:
                                    # Tìm đường dẫn ffmpeg mới
                                    which_result = subprocess.run(
                                        ["which", "ffmpeg"],
                                        capture_output=True, text=True
                                    )
                                    ffmpeg_loc = which_result.stdout.strip()
                                    if ffmpeg_loc:
                                        self.finished.emit(True, ffmpeg_loc)
                                    else:
                                        self.finished.emit(True, "ffmpeg")
                                else:
                                    self.finished.emit(False, f"Homebrew: {result.stderr[:200]}")
                                return
                            else:
                                raise dl_err

                        self.log.emit(f"✅ Tải xong ({tmp_zip.stat().st_size // (1024*1024)} MB)")

                        self.log.emit("📦 Đang giải nén...")
                        with zipfile.ZipFile(str(tmp_zip), "r") as zf:
                            ffmpeg_in_zip = None
                            if sys.platform == "darwin":
                                for name in zf.namelist():
                                    basename = name.rsplit("/", 1)[-1] if "/" in name else name
                                    if basename == "ffmpeg":
                                        ffmpeg_in_zip = name
                                        break
                            else:
                                for name in zf.namelist():
                                    if name.endswith("/bin/ffmpeg.exe") or name.endswith("\\bin\\ffmpeg.exe"):
                                        ffmpeg_in_zip = name
                                        break
                                if not ffmpeg_in_zip:
                                    for name in zf.namelist():
                                        if name.lower().endswith("ffmpeg.exe"):
                                            ffmpeg_in_zip = name
                                            break

                            if not ffmpeg_in_zip:
                                raise RuntimeError(f"Không tìm thấy {_ffmpeg_name} trong file zip")

                            self.log.emit(f"📦 Tìm thấy: {ffmpeg_in_zip}")

                            if dest_ffmpeg.exists():
                                backup = dest_dir / f"{_ffmpeg_name}.bak"
                                try:
                                    if backup.exists():
                                        backup.unlink()
                                    dest_ffmpeg.rename(backup)
                                    self.log.emit(f"📋 Đã backup {_ffmpeg_name} cũ")
                                except Exception as e:
                                    self.log.emit(f"⚠ Không backup được: {e}")

                            extracted = Path(tmp_dir) / f"extracted_{_ffmpeg_name}"
                            with zf.open(ffmpeg_in_zip) as src, open(str(extracted), "wb") as dst:
                                shutil.copyfileobj(src, dst)

                            shutil.copy2(str(extracted), str(dest_ffmpeg))
                            if os.name == "nt":
                                _unblock_file(dest_ffmpeg)
                            else:
                                os.chmod(str(dest_ffmpeg), 0o755)

                    backup = dest_dir / f"{_ffmpeg_name}.bak"
                    if backup.exists():
                        try:
                            backup.unlink()
                        except Exception:
                            pass

                    self.finished.emit(True, str(dest_ffmpeg))

                except Exception as e:
                    if getattr(sys, "frozen", False):
                        restore_dir = Path(sys.executable).resolve().parent
                    else:
                        restore_dir = self.project_root
                    backup = restore_dir / f"{_ffmpeg_name}.bak"
                    dest = restore_dir / _ffmpeg_name
                    if backup.exists() and not dest.exists():
                        try:
                            backup.rename(dest)
                        except Exception:
                            pass
                    self.finished.emit(False, str(e))

        def on_ffmpeg_update_done(success, msg):
            self.btn_update_ffmpeg.setEnabled(True)
            self.btn_update_ffmpeg.setText("⬆ Cập nhật")
            if success:
                self.ffmpeg_path = msg
                self.append_log(f"✅ Đã cập nhật FFmpeg: {msg}")
                self._check_ffmpeg_version()
                QMessageBox.information(self, "Cập nhật FFmpeg", f"Cập nhật FFmpeg thành công!\nFile: {msg}")
            else:
                self.append_log(f"❌ Lỗi cập nhật FFmpeg: {msg}")
                QMessageBox.warning(self, "Lỗi cập nhật FFmpeg", f"Lỗi:\n{msg}")

        self._ffmpeg_update_worker = FfmpegUpdateWorker(self.project_root, self)
        self._ffmpeg_update_worker.log.connect(self.append_log)
        self._ffmpeg_update_worker.finished.connect(on_ffmpeg_update_done)
        self._ffmpeg_update_worker.start()

    def _check_all_updates(self):
        """Kiểm tra tất cả version cùng lúc."""
        self.append_log("🔄 Đang kiểm tra phiên bản...")
        self._check_ytdlp_version()
        self._check_ffmpeg_version()
        self.append_log("✅ Kiểm tra phiên bản hoàn tất.")

    # ================= Vocal Isolation (Demucs) =================

def main():
    # Hỗ trợ DPI scaling cho màn hình có scaling > 100%
    import os
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication(sys.argv)
    w = MainWindow()

    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
