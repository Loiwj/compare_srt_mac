#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ứng dụng so sánh và chỉnh sửa file SRT
Giao diện Qt5 với sidebar và main content area
"""

import sys
from pathlib import Path
from typing import Optional
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTextEdit,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QMessageBox,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QGroupBox,
    QRadioButton,
    QCheckBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QPlainTextEdit,
    QDoubleSpinBox,
    QStackedWidget,
    QGridLayout,
    QFrame,
    QScrollArea,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon, QDesktopServices, QPixmap
import subprocess
import os

from srt_parser import (
    parse_srt_file,
    compare_srt_files,
    create_thaisub_file,
    save_srt_file,
    SubtitleEntry,
    fix_srt_entry,
)

try:
    import capcut_srt_gui as capcut_srt
except ImportError:
    capcut_srt = None


class TranslateVerifyWorker(QThread):
    """Thread để gọi API dịch thuật kiểm tra nghĩa (Google Translate)"""
    finished = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, original_text: str, translated_text: str, target_lang: str = "vi", translate_file1: bool = False, parent=None):
        super().__init__(parent)
        self.original_text = original_text
        self.translated_text = translated_text
        self.target_lang = target_lang
        self.translate_file1 = translate_file1

    def run(self):
        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source='auto', target=self.target_lang)
            # Dịch File 1 nếu user chọn option "Dịch cả File 1"
            if self.translate_file1:
                res_orig = translator.translate(self.original_text) if self.original_text.strip() else ""
            else:
                res_orig = ""  # Không dịch File 1
            res_trans = translator.translate(self.translated_text) if self.translated_text.strip() else ""
            self.finished.emit(res_orig, res_trans)
        except Exception as e:
            self.failed.emit(f"Lỗi dịch thuật: {str(e)}")


class CompareThread(QThread):
    """Thread để so sánh file SRT không block UI"""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, file1: Path, file2: Path, tolerance_ms: int):
        super().__init__()
        self.file1 = file1
        self.file2 = file2
        self.tolerance_ms = tolerance_ms

    def run(self):
        try:
            self.progress.emit(10)
            result = compare_srt_files(self.file1, self.file2, self.tolerance_ms)
            self.progress.emit(100)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class CheckOverlapThread(QThread):
    """Thread để kiểm tra overlap audio không block UI"""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)
    log_message = pyqtSignal(str)

    def __init__(self, project_dir: str, video_speed: float, audio_speed: float):
        super().__init__()
        self.project_dir = project_dir
        self.video_speed = video_speed
        self.audio_speed = audio_speed

    def run(self):
        try:
            if capcut_srt is None:
                self.error.emit("capcut_srt_gui module không khả dụng")
                return

            self.progress.emit(5)
            self.log_message.emit("Đang kiểm tra overlap audio...")

            overlaps = capcut_srt.check_audio_overlap(
                self.project_dir,
                self.video_speed,
                self.audio_speed,
                progress_callback=self.progress.emit,
            )

            self.progress.emit(100)
            self.finished.emit(overlaps)
        except Exception as e:
            self.error.emit(str(e))


class ReupToolWidget(QWidget):
    """Ứng dụng chính so sánh file SRT"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file1_path: Optional[Path] = None
        self.file2_path: Optional[Path] = None
        self.file1_entries = []
        self.file2_entries = []
        self.compare_result = None
        self.is_dark_mode = True  # Mặc định là dark mode
        self.last_thaisub_file = None  # Lưu file thaisub vừa tạo
        # Biến dùng cho chức năng chia phụ đề
        self.split_file_path: Optional[Path] = None
        self.split_entries = []
        self.split_chunks = []
        # Biến cho CapCut SRT
        self.capcut_project_dir = None
        self.translate_entries = []
        self.translate_chunks = []  # List of (start_idx, end_idx)
        self.translated_results = {}  # chunk_index -> translated_entries
        self.translate_worker = None
        self.manual_translate_worker = None
        self.translate_retry_counts = {}  # chunk_index -> số lần retry tự động
        self.auto_retry_pending_chunks = set()
        self.auto_retry_round = 0
        self.auto_retry_max_round = 0
        self._auto_retry_running = False
        self.compare_thread = None
        self.check_overlap_thread = None

        self.init_ui()
        self.apply_dark_theme()
        self.log_message("Reup Tool khởi động")

    def init_ui(self):
        """Khởi tạo giao diện"""
        # Layout chính cho widget
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self.sidebar = self.create_sidebar()
        self.sidebar.setMinimumWidth(140)
        self.sidebar.setMaximumWidth(220)
        main_layout.addWidget(self.sidebar)

        # Main content area
        content_area = self.create_content_area()
        main_layout.addWidget(content_area, 1)

    def create_sidebar(self) -> QWidget:
        """Tạo sidebar navigation"""
        sidebar = QWidget()
        sidebar.setStyleSheet(
            """
            QWidget {
                background-color: #1e1e1e;
            }
            QLabel {
                color: #ffffff;
                padding: 10px;
            }
            QPushButton {
                background-color: transparent;
                color: #ffffff;
                text-align: left;
                padding: 12px 15px;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #2d2d2d;
            }
            QPushButton:pressed {
                background-color: #3d3d3d;
            }
        """
        )

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # Logo (nếu tìm thấy file logo.png)
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignCenter)
        logo_pix = self._load_logo_pixmap(max_height=80)
        if logo_pix is not None:
            logo_label.setPixmap(logo_pix)
            logo_label.setMinimumHeight(90)
            layout.addWidget(logo_label)

        # Title
        title_label = QLabel("SRT Compare Tool")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        dev_label = QLabel("Phát triển bởi Dương Quốc Lợi")
        dev_label.setAlignment(Qt.AlignCenter)
        dev_label.setStyleSheet("color: #888888; font-size: 13px;")
        layout.addWidget(dev_label)

        layout.addSpacing(20)

        # Menu items
        self.menu_items = {}

        menu_data = [
            ("So sánh SRT", "compare"),
            ("Chia phụ đề", "split"),
        ]

        for text, key in menu_data:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self.switch_page(k))
            self.menu_items[key] = btn
            layout.addWidget(btn)

        # Đặt trang mặc định
        self.menu_items["compare"].setChecked(True)
        self.active_menu_key = "compare"
        self.update_active_menu_style()

        layout.addStretch()

        # Device status
        self.status_label = QLabel("Trạng thái: Sẵn sàng")
        self.status_label.setStyleSheet("color: #ffc107; padding: 10px;")
        layout.addWidget(self.status_label)

        return sidebar

    def create_content_area(self) -> QWidget:
        """Tạo main content area"""
        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: #1a1a2e;")

        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stack widget để chuyển đổi giữa các trang
        self.stacked_widget = QStackedWidget()
        layout.addWidget(self.stacked_widget)

        # Tạo các trang
        self.compare_page = self.create_compare_page()
        self.split_page = self.create_split_page()
        self.translate_page = self.create_translate_page()

        self.stacked_widget.addWidget(self.compare_page)
        self.stacked_widget.addWidget(self.split_page)
        self.stacked_widget.addWidget(self.translate_page)

        # Hiển thị trang so sánh mặc định
        self.stacked_widget.setCurrentIndex(0)

        return content_widget

    def _load_logo_pixmap(self, max_height: int = 80):
        """Tìm và load logo.png, trả về QPixmap đã scale hoặc None nếu không có."""
        candidates = []
        # Thư mục chứa file .py hiện tại
        candidates.append(Path(__file__).resolve().parent / "logo.png")
        # Khi chạy dạng .exe (PyInstaller)
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / "logo.png")

        for p in candidates:
            try:
                if p.is_file():
                    pix = QPixmap(str(p))
                    if not pix.isNull():
                        return pix.scaledToHeight(max_height, Qt.SmoothTransformation)
            except Exception:
                continue
        return None

    def create_compare_page(self) -> QWidget:
        """Tạo trang so sánh SRT — Figma 3-column card layout"""
        page = QWidget()
        page.setStyleSheet("background-color: #1a1a2e; color: #F1F5F9;")

        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ── Three-column main area ──
        columns_layout = QHBoxLayout()
        columns_layout.setSpacing(8)

        card_panel_style = """
            QFrame {
                background: rgba(22, 33, 62, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }
        """

        # --- LEFT: File gốc ---
        left_panel = QFrame()
        left_panel.setStyleSheet(card_panel_style)
        left_vbox = QVBoxLayout(left_panel)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(0)

        left_header_w = QWidget()
        left_header_w.setStyleSheet("background: rgba(0,0,0,0.3); border-top-left-radius: 10px; border-top-right-radius: 10px; border-bottom: 1px solid rgba(255,255,255,0.1);")
        lh_layout = QVBoxLayout(left_header_w)
        lh_layout.setContentsMargins(12, 12, 12, 12)
        lh_actions = QHBoxLayout()
        
        open_file1_btn = QPushButton("📄 Mở file gốc")
        open_file1_btn.setToolTip("Mở file SRT gốc")
        open_file1_btn.setStyleSheet("QPushButton { background: #2563eb; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 600; border: none; } QPushButton:hover { background: #3b82f6; }")
        open_file1_btn.clicked.connect(lambda: self.select_file(1))
        lh_actions.addWidget(open_file1_btn)

        _editor_name = "TextEdit" if sys.platform == "darwin" else "Notepad"
        notepad1_btn = QPushButton(f"📝 {_editor_name} 1")
        notepad1_btn.setStyleSheet("QPushButton { background: #6d28d9; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 600; border: none; } QPushButton:hover { background: #7c3aed; }")
        notepad1_btn.clicked.connect(self.open_file1_in_notepad)
        lh_actions.addWidget(notepad1_btn)
        lh_actions.addStretch()

        self.file1_label = QLabel("Chưa chọn file gốc")
        self.file1_label.setStyleSheet("color: #9ca3af; font-size: 12px; border: none; background: transparent;")
        
        lh_layout.addLayout(lh_actions)
        lh_layout.addWidget(self.file1_label)
        left_vbox.addWidget(left_header_w)

        self.left_scroll = QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.left_entries_widget = QWidget()
        self.left_entries_layout = QVBoxLayout(self.left_entries_widget)
        self.left_entries_layout.setContentsMargins(8, 8, 8, 8)
        self.left_entries_layout.setSpacing(6)
        self.left_entries_layout.addStretch()
        self.left_scroll.setWidget(self.left_entries_widget)
        left_vbox.addWidget(self.left_scroll, 1)

        columns_layout.addWidget(left_panel, 2)

        # --- CENTER: So sánh ---
        center_panel = QFrame()
        center_panel.setStyleSheet(card_panel_style)
        center_vbox = QVBoxLayout(center_panel)
        center_vbox.setContentsMargins(0, 0, 0, 0)
        center_vbox.setSpacing(0)

        center_header_w = QWidget()
        center_header_w.setStyleSheet("background: rgba(0,0,0,0.3); border-top-left-radius: 10px; border-top-right-radius: 10px; border-bottom: 1px solid rgba(255,255,255,0.1);")
        ch_layout = QVBoxLayout(center_header_w)
        ch_layout.setContentsMargins(12, 12, 12, 12)
        
        compare_btn = QPushButton("⚡ So sánh")
        compare_btn.setStyleSheet("QPushButton { background: #8b5cf6; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 700; border: none; } QPushButton:hover { background: #7c3aed; }")
        compare_btn.clicked.connect(self.start_compare)
        
        ch_layout.addWidget(compare_btn)
        
        # Spacer
        center_spacer_label = QLabel("")
        center_spacer_label.setStyleSheet("font-size: 12px; border: none; background: transparent;")
        ch_layout.addWidget(center_spacer_label)

        center_vbox.addWidget(center_header_w)

        self.center_scroll = QScrollArea()
        self.center_scroll.setWidgetResizable(True)
        self.center_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.center_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.center_entries_widget = QWidget()
        self.center_entries_layout = QVBoxLayout(self.center_entries_widget)
        self.center_entries_layout.setContentsMargins(8, 8, 8, 8)
        self.center_entries_layout.setSpacing(6)
        self.center_entries_layout.addStretch()
        self.center_scroll.setWidget(self.center_entries_widget)
        center_vbox.addWidget(self.center_scroll, 1)

        columns_layout.addWidget(center_panel, 1)

        # --- RIGHT: File dịch ---
        right_panel = QFrame()
        right_panel.setStyleSheet(card_panel_style)
        right_vbox = QVBoxLayout(right_panel)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(0)

        right_header_w = QWidget()
        right_header_w.setStyleSheet("background: rgba(0,0,0,0.3); border-top-left-radius: 10px; border-top-right-radius: 10px; border-bottom: 1px solid rgba(255,255,255,0.1);")
        rh_layout = QVBoxLayout(right_header_w)
        rh_layout.setContentsMargins(12, 12, 12, 12)
        rh_actions = QHBoxLayout()
        
        open_file2_btn = QPushButton("📄 Mở file dịch")
        open_file2_btn.setToolTip("Mở file SRT dịch")
        open_file2_btn.setStyleSheet("QPushButton { background: #2563eb; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 600; border: none; } QPushButton:hover { background: #3b82f6; }")
        open_file2_btn.clicked.connect(lambda: self.select_file(2))
        rh_actions.addWidget(open_file2_btn)

        _editor_name2 = "TextEdit" if sys.platform == "darwin" else "Notepad"
        notepad2_btn = QPushButton(f"📝 {_editor_name2} 2")
        notepad2_btn.setStyleSheet("QPushButton { background: #6d28d9; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 600; border: none; } QPushButton:hover { background: #7c3aed; }")
        notepad2_btn.clicked.connect(self.open_file2_in_notepad)
        rh_actions.addWidget(notepad2_btn)

        create_thaisub_btn = QPushButton("📝 Tạo Thai Sub")
        create_thaisub_btn.setToolTip("Tạo file Thai Sub trống")
        create_thaisub_btn.setStyleSheet("QPushButton { background: #0d9488; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 600; border: none; } QPushButton:hover { background: #14b8a6; }")
        create_thaisub_btn.clicked.connect(self.create_thaisub)
        rh_actions.addWidget(create_thaisub_btn)

        rh_actions.addStretch()

        self.file2_label = QLabel("Chưa chọn file dịch")
        self.file2_label.setStyleSheet("color: #9ca3af; font-size: 12px; border: none; background: transparent;")

        rh_layout.addLayout(rh_actions)
        rh_layout.addWidget(self.file2_label)
        right_vbox.addWidget(right_header_w)

        self.right_scroll = QScrollArea()
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.right_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.right_entries_widget = QWidget()
        self.right_entries_layout = QVBoxLayout(self.right_entries_widget)
        self.right_entries_layout.setContentsMargins(8, 8, 8, 8)
        self.right_entries_layout.setSpacing(6)
        self.right_entries_layout.addStretch()
        self.right_scroll.setWidget(self.right_entries_widget)
        right_vbox.addWidget(self.right_scroll, 1)

        columns_layout.addWidget(right_panel, 2)
        
        main_layout.addLayout(columns_layout, 1)

        # ── Bottom: Stats bar + auto-fix ──
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet("""
            QFrame {
                background: rgba(22, 33, 62, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }
        """)
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(16, 8, 16, 8)

        self.stats_label = QLabel("Chọn 2 file SRT để bắt đầu so sánh")
        self.stats_label.setStyleSheet("color: #9ca3af; font-size: 13px; border: none;")
        bottom_layout.addWidget(self.stats_label)

        bottom_layout.addStretch()

        # Option: dịch cả File 1
        self.chk_translate_file1 = QCheckBox("Dịch cả File 1")
        self.chk_translate_file1.setChecked(False)
        self.chk_translate_file1.setToolTip("Khi bấm 🔍 dịch kiểm tra, có dịch cả File 1 sang tiếng Việt không?")
        self.chk_translate_file1.setStyleSheet("color: #22d3ee; font-size: 12px; border: none;")
        bottom_layout.addWidget(self.chk_translate_file1)

        # Auto-fix section
        self.auto_fix_radio1 = QRadioButton("File 1 → File 2")
        self.auto_fix_radio1.setChecked(True)
        self.auto_fix_radio1.setStyleSheet("color: #CBD5E1; font-size: 12px; border: none;")
        self.auto_fix_radio2 = QRadioButton("File 2 → File 1")
        self.auto_fix_radio2.setStyleSheet("color: #CBD5E1; font-size: 12px; border: none;")
        bottom_layout.addWidget(self.auto_fix_radio1)
        bottom_layout.addWidget(self.auto_fix_radio2)

        auto_fix_btn = QPushButton("🔧 Tự động sửa lỗi")
        auto_fix_btn.setStyleSheet("""
            QPushButton { background: #16a34a; color: white; padding: 6px 14px;
                          border-radius: 6px; font-weight: 600; border: none; font-size: 12px; }
            QPushButton:hover { background: #22c55e; }
        """)
        auto_fix_btn.clicked.connect(self.auto_fix_errors)
        bottom_layout.addWidget(auto_fix_btn)

        main_layout.addWidget(bottom_bar)

        # ── Hidden results_table for backward compat ──
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(
            ["STT", "Loại", "File 1", "File 2", "Lệch (ms)"]
        )
        self.results_table.hide()

        return page

    def _make_entry_card(self, index, start_time, end_time, content_text, highlight_color="rgba(0,0,0,0.3)", error_types=None):
        """Tạo card widget cho 1 subtitle entry. error_types: set chứa 'start'/'end' nếu bị lệch."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {highlight_color};
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                padding: 0;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)

        # Header: index + timestamps (tô đỏ phần bị lệch)
        start_color = "#ef4444" if error_types and "start" in error_types else "#22d3ee"
        end_color = "#ef4444" if error_types and "end" in error_types else "#22d3ee"
        header = QLabel(
            f'<span style="color:#eab308;font-weight:700;">{index}</span>  '
            f'<span style="color:{start_color};font-family:Menlo,Consolas,monospace;font-weight:600;">{start_time}</span>'
            f'<span style="color:#6b7280;"> → </span>'
            f'<span style="color:{end_color};font-family:Menlo,Consolas,monospace;font-weight:600;">{end_time}</span>'
        )
        header.setStyleSheet("border: none; font-size: 12px;")
        card_layout.addWidget(header)

        # Text content
        text_color = "#ef4444" if error_types else "#F1F5F9"
        text_label = QLabel(content_text)
        text_label.setWordWrap(True)
        text_label.setStyleSheet(f"color: {text_color}; font-size: 13px; border: none;")
        card_layout.addWidget(text_label)

        return card

    def _make_status_indicator(self, index, status, color, e1=None, e2=None):
        """Tạo comparison status indicator cho center column"""
        indicator = QFrame()
        indicator.setStyleSheet("border: none; background: transparent;")
        v = QVBoxLayout(indicator)
        v.setContentsMargins(0, 4, 0, 4)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(2)

        idx_label = QLabel(str(index))
        idx_label.setAlignment(Qt.AlignCenter)
        idx_label.setStyleSheet("color: #6b7280; font-size: 11px; border: none;")
        v.addWidget(idx_label)

        status_label = QLabel(status)
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600; border: none;")
        v.addWidget(status_label)

        # Nút dịch kiểm tra cho các dòng diff (có cả e1 và e2)
        if e1 is not None and e2 is not None:
            verify_btn = QPushButton("🔍")
            verify_btn.setToolTip("Dịch kiểm tra: dịch cả 2 bên sang tiếng Việt để so sánh")
            verify_btn.setFixedSize(28, 28)
            verify_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(139, 92, 246, 0.3);
                    color: white;
                    border: 1px solid rgba(139, 92, 246, 0.5);
                    border-radius: 14px;
                    font-size: 14px;
                    padding: 0;
                }
                QPushButton:hover {
                    background: rgba(139, 92, 246, 0.6);
                    border-color: #8b5cf6;
                }
            """)
            # Capture e1, e2 by value
            verify_btn.clicked.connect(lambda _, a=e1, b=e2: self._verify_translation(a, b))
            v.addWidget(verify_btn, 0, Qt.AlignCenter)

        line = QFrame()
        line.setFixedSize(1, 12)
        line.setStyleSheet("background: #4b5563; border: none;")
        v.addWidget(line, 0, Qt.AlignCenter)

        return indicator

    def _populate_compare_cards(self):
        """Populate 3-column card views after comparison (lazy loading)"""
        # Guard check: layouts may not exist yet
        if not hasattr(self, 'left_entries_layout'):
            return

        # Clear existing cards
        for layout in [self.left_entries_layout, self.center_entries_layout, self.right_entries_layout]:
            while layout.count() > 0:
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        entries1 = getattr(self, 'file1_entries', []) or []
        entries2 = getattr(self, 'file2_entries', []) or []
        result = getattr(self, 'compare_result', None)

        if not entries1 and not entries2:
            return

        # Build error lookup: index -> set of error types
        error_lookup = {}
        if result and result.get("errors"):
            for err in result["errors"]:
                idx = err["index"]
                if idx not in error_lookup:
                    error_lookup[idx] = []
                error_lookup[idx].append(err)

        # Index lookup for entries2
        entries2_by_idx = {e.index: e for e in entries2} if entries2 else {}

        # ── Pre-compute all rows for lazy loading ──
        # Mỗi row là dict chứa đủ thông tin để render 3 cột
        self._compare_rows = []
        total = 0
        same_count = 0
        diff_count = 0
        missing_count = 0

        max_e2_idx = max((e.index for e in entries2), default=0) if entries2 else 0

        for e1 in entries1:
            total += 1
            e2 = entries2_by_idx.get(e1.index)
            content_text = "\n".join(e1.content) if e1.content else ""

            if e2 is None:
                if e1.index <= max_e2_idx:
                    missing_count += 1
                    self._compare_rows.append({
                        "type": "missing",
                        "e1": e1, "e2": None,
                        "left_text": content_text,
                        "status": "△ Thiếu", "status_color": "#ef4444",
                    })
                else:
                    self._compare_rows.append({
                        "type": "pending",
                        "e1": e1, "e2": None,
                        "left_text": content_text,
                        "status": "⌛ Chờ dịch", "status_color": "#9ca3af",
                    })
            elif e1.index in error_lookup:
                diff_count += 1
                right_content = "\n".join(e2.content) if e2.content else ""
                # Xác định loại lệch: start, end hoặc cả hai
                err_types = set()
                for err in error_lookup[e1.index]:
                    err_types.add(err["type"])  # "start" hoặc "end"
                self._compare_rows.append({
                    "type": "diff",
                    "e1": e1, "e2": e2,
                    "left_text": content_text,
                    "right_text": right_content,
                    "right_color": "rgba(234, 179, 8, 0.1)",
                    "status": "⚠ Khác", "status_color": "#eab308",
                    "error_types": err_types,  # {"start"}, {"end"}, hoặc {"start", "end"}
                })
            else:
                same_count += 1
                right_content = "\n".join(e2.content) if e2.content else ""
                self._compare_rows.append({
                    "type": "same",
                    "e1": e1, "e2": e2,
                    "left_text": content_text,
                    "right_text": right_content,
                    "right_color": "rgba(0,0,0,0.3)",
                    "status": "✓ Giống", "status_color": "#22c55e",
                })

        # Extra entries in file2 not in file1
        entries1_indices = {e.index for e in entries1} if entries1 else set()
        for e2 in entries2:
            if e2.index not in entries1_indices:
                total += 1
                diff_count += 1
                extra_content = "\n".join(e2.content) if e2.content else ""
                self._compare_rows.append({
                    "type": "extra",
                    "e1": None, "e2": e2,
                    "right_text": extra_content,
                    "right_color": "rgba(96, 165, 250, 0.1)",
                })

        # ── Lưu tất cả rows (trước khi filter) ──
        self._compare_all_rows = list(self._compare_rows)

        # Áp dụng filter theo dropdown hiện tại
        self._compare_rows = self._get_filtered_rows(self._compare_all_rows)

        # ── Lazy loading state ──
        self._compare_rendered_count = 0
        self._compare_batch_size = 100  # Số entry render mỗi lần
        self._compare_is_loading = False
        self._scroll_syncing = False  # Chống loop đồng bộ scroll

        # Render batch đầu tiên
        self._render_compare_batch()

        # Kết nối scroll event:
        # - left_scroll: đồng bộ scroll + load more
        # - center_scroll, right_scroll: cũng load more khi gần cuối
        for scroll, handler in [
            (self.left_scroll, self._on_left_scroll),
            (self.center_scroll, self._on_any_scroll_load_more),
            (self.right_scroll, self._on_any_scroll_load_more),
        ]:
            try:
                scroll.verticalScrollBar().valueChanged.disconnect(handler)
            except (TypeError, RuntimeError):
                pass
            scroll.verticalScrollBar().valueChanged.connect(handler)

        # Update stats (hiện ngay toàn bộ thống kê)
        loaded_text = f"  (hiện {min(self._compare_batch_size, len(self._compare_rows))}/{len(self._compare_rows)})" if len(self._compare_rows) > self._compare_batch_size else ""
        self.stats_label.setText(
            f'Tổng: <b style="color:white">{total}</b> câu   '
            f'Giống: <b style="color:#22c55e">{same_count}</b>   '
            f'Khác: <b style="color:#eab308">{diff_count}</b>   '
            f'Thiếu: <b style="color:#ef4444">{missing_count}</b>'
            f'{loaded_text}'
        )

    def _render_compare_batch(self):
        """Render thêm 1 batch entries vào 3 columns"""
        if not hasattr(self, '_compare_rows') or self._compare_is_loading:
            return

        rows = self._compare_rows
        start = self._compare_rendered_count
        end = min(start + self._compare_batch_size, len(rows))

        if start >= len(rows):
            return  # Đã render hết

        self._compare_is_loading = True

        # Xóa stretch cũ ở cuối (nếu có)
        for layout in [self.left_entries_layout, self.center_entries_layout, self.right_entries_layout]:
            count = layout.count()
            if count > 0:
                item = layout.itemAt(count - 1)
                if item and not item.widget():
                    layout.takeAt(count - 1)

        for i in range(start, end):
            row = rows[i]
            e1 = row.get("e1")
            e2 = row.get("e2")

            if row["type"] == "extra":
                # Chỉ có bên phải (extra entry trong file2)
                right_card = self._make_editable_entry_card(
                    e2.index, e2.start_time, e2.end_time,
                    row["right_text"], e2, row["right_color"]
                )
                self.right_entries_layout.addWidget(right_card)
            else:
                # Lấy error_types cho diff rows
                err_types = row.get("error_types") if row["type"] == "diff" else None

                # Left card
                left_card = self._make_entry_card(
                    e1.index, e1.start_time, e1.end_time,
                    row["left_text"], "rgba(0,0,0,0.3)", error_types=err_types
                )
                self.left_entries_layout.addWidget(left_card)

                # Center status (truyền e1, e2 cho diff rows để có nút dịch kiểm tra)
                if row["type"] == "diff":
                    status_widget = self._make_status_indicator(
                        e1.index, row["status"], row["status_color"], e1=e1, e2=e2
                    )
                else:
                    status_widget = self._make_status_indicator(
                        e1.index, row["status"], row["status_color"]
                    )
                self.center_entries_layout.addWidget(status_widget)

                # Right card (nếu có)
                if row["type"] in ("diff", "same"):
                    right_card = self._make_editable_entry_card(
                        e2.index, e2.start_time, e2.end_time,
                        row["right_text"], e2, row["right_color"],
                        error_types=err_types
                    )
                    self.right_entries_layout.addWidget(right_card)

        # Add stretches at end
        self.left_entries_layout.addStretch()
        self.center_entries_layout.addStretch()
        self.right_entries_layout.addStretch()

        self._compare_rendered_count = end
        self._compare_is_loading = False

        # Cập nhật stats label nếu chưa render hết
        if end < len(rows):
            current_stats = self.stats_label.text()
            # Cập nhật phần "(hiện X/Y)"
            import re
            current_stats = re.sub(r'\(hiện \d+/\d+\)', f'(hiện {end}/{len(rows)})', current_stats)
            if '(hiện' not in current_stats:
                current_stats += f'  (hiện {end}/{len(rows)})'
            self.stats_label.setText(current_stats)
        else:
            # Đã render hết — xóa text "(hiện X/Y)"
            import re
            current_stats = self.stats_label.text()
            current_stats = re.sub(r'\s*\(hiện \d+/\d+\)', '', current_stats)
            self.stats_label.setText(current_stats)

    def _on_left_scroll(self, value):
        """Master scroll handler: đồng bộ scroll + load thêm khi scroll File gốc"""
        # ── Đồng bộ scroll cho center + right ──
        if not self._scroll_syncing:
            self._scroll_syncing = True
            left_bar = self.left_scroll.verticalScrollBar()
            max_val = left_bar.maximum()
            if max_val > 0:
                ratio = value / max_val
                # Sync center scroll
                center_bar = self.center_scroll.verticalScrollBar()
                center_bar.setValue(int(ratio * center_bar.maximum()))
                # Sync right scroll
                right_bar = self.right_scroll.verticalScrollBar()
                right_bar.setValue(int(ratio * right_bar.maximum()))
            self._scroll_syncing = False

        # ── Load more khi scroll gần cuối ──
        if not hasattr(self, '_compare_rows'):
            return
        if self._compare_rendered_count >= len(self._compare_rows):
            return  # Đã render hết
        if self._compare_is_loading:
            return

        scrollbar = self.left_scroll.verticalScrollBar()
        max_val = scrollbar.maximum()
        if max_val <= 0:
            return

        # Load thêm khi scroll đạt 80% chiều dài
        threshold = int(max_val * 0.8)
        if value >= threshold:
            self._compare_batch_size = 50  # Batch nhỏ hơn cho lần load tiếp
            self._render_compare_batch()

    def _on_any_scroll_load_more(self, value):
        """Load thêm khi scroll gần cuối ở center hoặc right panel"""
        if not hasattr(self, '_compare_rows'):
            return
        if self._compare_rendered_count >= len(self._compare_rows):
            return
        if self._compare_is_loading:
            return

        # Lấy scrollbar của sender
        sender_bar = self.sender()
        if not sender_bar:
            return
        max_val = sender_bar.maximum()
        if max_val <= 0:
            return

        threshold = int(max_val * 0.8)
        if value >= threshold:
            self._compare_batch_size = 50
            self._render_compare_batch()

    def _get_filtered_rows(self, all_rows):
        """Mặc định chỉ hiện các phụ đề bị lệch (khác nhau, thiếu, dư). Ẩn giống nhau và chưa dịch."""
        return [r for r in all_rows if r["type"] not in ("same", "pending")]

    def _on_filter_changed(self, index):
        """Xử lý khi user đổi filter dropdown"""
        if not hasattr(self, '_compare_all_rows') or not self._compare_all_rows:
            return

        # Re-filter
        self._compare_rows = self._get_filtered_rows(self._compare_all_rows)

        # Clear existing cards
        for layout in [self.left_entries_layout, self.center_entries_layout, self.right_entries_layout]:
            while layout.count() > 0:
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        # Reset lazy loading state
        self._compare_rendered_count = 0
        self._compare_batch_size = 100

        # Render lại batch đầu tiên
        self._render_compare_batch()

        # Cập nhật stats label
        filter_name = "Tất cả"
        count = len(self._compare_rows)
        self.stats_label.setText(
            f'Lọc: <b style="color:#8b5cf6">{filter_name}</b>  —  '
            f'Hiện: <b style="color:white">{count}</b> mục'
        )

    def _make_editable_entry_card(self, index, start_time, end_time, content_text, entry_obj, highlight_color="rgba(0,0,0,0.3)", error_types=None):
        """Tạo card có thể sửa trực tiếp cho file dịch (Feature 3). error_types: set chứa 'start'/'end'."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {highlight_color};
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                padding: 0;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)

        # Header: index + timestamps (tô đỏ phần bị lệch)
        start_color = "#ef4444" if error_types and "start" in error_types else "#22d3ee"
        end_color = "#ef4444" if error_types and "end" in error_types else "#22d3ee"
        header = QLabel(
            f'<span style="color:#eab308;font-weight:700;">{index}</span>  '
            f'<span style="color:{start_color};font-family:Menlo,Consolas,monospace;font-weight:600;">{start_time}</span>'
            f'<span style="color:#6b7280;"> → </span>'
            f'<span style="color:{end_color};font-family:Menlo,Consolas,monospace;font-weight:600;">{end_time}</span>'
        )
        header.setStyleSheet("border: none; font-size: 12px;")
        card_layout.addWidget(header)

        # Text content (QLabel - hiển thị bình thường)
        text_label = QLabel(content_text)
        text_label.setWordWrap(True)
        text_label.setStyleSheet("color: #F1F5F9; font-size: 13px; border: none;")
        card_layout.addWidget(text_label)

        # Edit area (QTextEdit - ẩn cho đến khi double-click)
        edit_widget = QTextEdit()
        edit_widget.setPlainText(content_text)
        edit_widget.setStyleSheet("""
            QTextEdit {
                background: rgba(0,0,0,0.4);
                color: #F1F5F9;
                border: 1px solid #8b5cf6;
                border-radius: 4px;
                font-size: 13px;
                padding: 4px;
            }
        """)
        edit_widget.setMaximumHeight(80)
        edit_widget.hide()
        card_layout.addWidget(edit_widget)

        # Buttons row (ẩn cho đến khi double-click)
        btn_row = QWidget()
        btn_row.setStyleSheet("border: none;")
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 2, 0, 0)
        btn_layout.setSpacing(6)

        save_btn = QPushButton("Lưu")
        save_btn.setStyleSheet("""
            QPushButton { background: #16a34a; color: white; padding: 4px 12px;
                          border-radius: 4px; font-weight: 600; font-size: 12px; border: none; }
            QPushButton:hover { background: #22c55e; }
        """)

        cancel_btn = QPushButton("Hủy")
        cancel_btn.setStyleSheet("""
            QPushButton { background: #4b5563; color: white; padding: 4px 12px;
                          border-radius: 4px; font-weight: 600; font-size: 12px; border: none; }
            QPushButton:hover { background: #6b7280; }
        """)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch()
        btn_row.hide()
        card_layout.addWidget(btn_row)

        # Double-click handler: chuyển sang edit mode
        original_text = content_text

        def enter_edit_mode(event):
            text_label.hide()
            edit_widget.setPlainText(text_label.text())
            edit_widget.show()
            btn_row.show()
            edit_widget.setFocus()

        text_label.mouseDoubleClickEvent = enter_edit_mode

        def save_edit():
            new_text = edit_widget.toPlainText().strip()
            # Cập nhật entry_obj
            entry_obj.content = new_text.split("\n") if new_text else []
            # Cập nhật label
            text_label.setText(new_text)
            # Lưu file
            if self.file2_path and self.file2_entries:
                try:
                    save_srt_file(self.file2_entries, self.file2_path)
                    self.log_message(f"✅ Đã lưu sửa đổi entry #{index}")
                except Exception as e:
                    self.log_message(f"❌ Lỗi lưu file: {e}")
            # Quay lại view mode
            edit_widget.hide()
            btn_row.hide()
            text_label.show()

        def cancel_edit():
            edit_widget.hide()
            btn_row.hide()
            text_label.show()

        save_btn.clicked.connect(save_edit)
        cancel_btn.clicked.connect(cancel_edit)

        return card

    # ===== Filter: Chỉ hiện khác biệt =====

    def _on_diff_filter_toggled(self, checked):
        """Khi toggle filter 'Chỉ hiện khác biệt' → re-render compare cards"""
        if not hasattr(self, '_compare_all_rows'):
            return

        # Lọc lại rows từ all_rows
        if checked:
            self._compare_rows = [r for r in self._compare_all_rows if r["type"] != "same"]
        else:
            self._compare_rows = list(self._compare_all_rows)

        # Clear existing cards
        for layout in [self.left_entries_layout, self.center_entries_layout, self.right_entries_layout]:
            while layout.count() > 0:
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        # Reset lazy loading
        self._compare_rendered_count = 0
        self._compare_batch_size = 100
        self._compare_is_loading = False

        # Re-render
        self._render_compare_batch()

        # Cập nhật stats label hiển thị số filtered
        filtered_info = f"  (lọc: {len(self._compare_rows)}/{len(self._compare_all_rows)})" if checked else ""
        loaded_text = ""
        if len(self._compare_rows) > self._compare_batch_size:
            loaded_text = f"  (hiện {min(self._compare_batch_size, len(self._compare_rows))}/{len(self._compare_rows)})"

        # Rebuild stats from all_rows (tổng thực tế)
        same_count = sum(1 for r in self._compare_all_rows if r["type"] == "same")
        diff_count = sum(1 for r in self._compare_all_rows if r["type"] in ("diff", "extra"))
        missing_count = sum(1 for r in self._compare_all_rows if r["type"] == "missing")
        total = len(self._compare_all_rows)

        self.stats_label.setText(
            f'Tổng: <b style="color:white">{total}</b> câu   '
            f'Giống: <b style="color:#22c55e">{same_count}</b>   '
            f'Khác: <b style="color:#eab308">{diff_count}</b>   '
            f'Thiếu: <b style="color:#ef4444">{missing_count}</b>'
            f'{filtered_info}{loaded_text}'
        )

    def create_split_page(self) -> QWidget:


        """Tạo trang chia nhỏ phụ đề SRT để dễ copy"""
        page = QWidget()
        page.setStyleSheet("background-color: #1a1a2e; color: #F1F5F9;")

        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Cột trái: chọn file & cấu hình chia
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setSpacing(15)

        split_group = QGroupBox("Chia nhỏ phụ đề SRT")
        split_group.setStyleSheet(
            """
            QGroupBox {
                color: #ffffff;
                border: 2px solid #d32f2f;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-size: 16px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background-color: #d32f2f;
            }
        """
        )
        split_layout = QVBoxLayout(split_group)

        # Chọn file SRT để chia
        file_layout = QHBoxLayout()
        self.split_file_label = QLabel("Chưa chọn file")
        self.split_file_label.setStyleSheet(
            "color: #888888; font-size: 15px; font-weight: 500;"
        )
        split_file_btn = QPushButton("📁 Chọn file SRT")
        split_file_btn.clicked.connect(self.split_select_file)
        file_layout.addWidget(self.split_file_label, 1)
        file_layout.addWidget(split_file_btn)
        split_layout.addLayout(file_layout)

        # Nhập số phụ đề mỗi nhóm
        size_layout = QHBoxLayout()
        size_label = QLabel("Số phụ đề mỗi nhóm:")
        size_label.setStyleSheet("font-size: 15px; font-weight: 500;")
        size_layout.addWidget(size_label)
        self.split_size_spin = QSpinBox()
        self.split_size_spin.setRange(1, 100000)
        self.split_size_spin.setValue(500)
        self.split_size_spin.setStyleSheet("font-size: 15px;")
        size_layout.addWidget(self.split_size_spin)
        size_layout.addStretch()
        split_layout.addLayout(size_layout)

        # Nút thực hiện chia
        split_btn = QPushButton("✂️ Chia phụ đề")
        split_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #d32f2f;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b71c1c;
            }
        """
        )
        split_btn.clicked.connect(self.split_subtitles)
        split_layout.addWidget(split_btn)

        # Gợi ý sử dụng
        split_info = QLabel(
            "💡 Ví dụ: có 3000 phụ đề, chia mỗi nhóm 400–500 để dễ copy từng phần."
        )
        split_info.setStyleSheet("color: #ffc107; font-size: 15px;")
        split_info.setWordWrap(True)
        split_layout.addWidget(split_info)

        left_layout.addWidget(split_group)
        left_layout.addStretch()

        # Cột phải: danh sách nhóm + nội dung để copy
        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setSpacing(15)

        # Danh sách nhóm
        chunks_group = QGroupBox("Các nhóm phụ đề")
        chunks_group.setStyleSheet(split_group.styleSheet())
        chunks_layout = QVBoxLayout(chunks_group)

        self.split_chunks_list = QListWidget()
        self.split_chunks_list.setStyleSheet(
            """
            QListWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444;
                font-size: 16px;
            }
            QListWidget::item {
                padding: 6px 8px;
            }
            QListWidget::item:selected {
                background-color: #d32f2f;
                color: #ffffff;
            }
            QListWidget::item:hover {
                background-color: #333333;
            }
        """
        )
        self.split_chunks_list.currentRowChanged.connect(self.update_split_preview)
        chunks_layout.addWidget(self.split_chunks_list)

        right_layout.addWidget(chunks_group)

        # Nội dung nhóm để copy
        preview_group = QGroupBox("Nội dung nhóm (bôi đen rồi Ctrl+C để copy)")
        preview_group.setStyleSheet(split_group.styleSheet())
        preview_layout = QVBoxLayout(preview_group)

        self.split_preview = QPlainTextEdit()
        self.split_preview.setReadOnly(True)
        self.split_preview.setStyleSheet(
            """
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444;
                font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                font-size: 18px;
            }
        """
        )
        preview_layout.addWidget(self.split_preview)

        # Nút copy nhanh nội dung nhóm hiện tại
        copy_btn_layout = QHBoxLayout()
        copy_btn_layout.addStretch()
        self.split_copy_btn = QPushButton("📋 Copy nhóm hiện tại")
        self.split_copy_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 8px 16px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """
        )
        self.split_copy_btn.clicked.connect(self.split_copy_current_chunk)
        copy_btn_layout.addWidget(self.split_copy_btn)
        preview_layout.addLayout(copy_btn_layout)

        right_layout.addWidget(preview_group)

        # Ghép 2 cột
        main_layout.addWidget(left_col, 1)
        main_layout.addWidget(right_col, 2)

        return page

    def create_status_page(self) -> QWidget:
        """Tạo trang trạng thái"""
        page = QWidget()
        page.setStyleSheet("background-color: #1a1a2e; color: #F1F5F9;")

        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        status_label = QLabel("Trạng thái hệ thống")
        status_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(status_label)

        self.system_status = QTextEdit()
        self.system_status.setReadOnly(True)
        self.system_status.setStyleSheet(
            """
            QTextEdit {
                background-color: #1e1e1e;
                color: #00ff00;
                border: 1px solid #444;
                font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                font-size: 15px;
            }
        """
        )
        layout.addWidget(self.system_status)

        self.update_system_status()

        return page

    def apply_dark_theme(self):
        """Áp dụng dark theme — Figma palette v3"""
        self.is_dark_mode = True
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #1a1a2e;
            }
            QWidget {
                color: #F1F5F9;
            }
            QPushButton {
                background-color: #374151;
                color: #F1F5F9;
                padding: 8px 14px;
                border-radius: 8px;
                border: none;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #4b5563;
            }
            QPushButton:pressed {
                background-color: #374151;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: rgba(0, 0, 0, 0.3);
                color: #F1F5F9;
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 6px 10px;
                border-radius: 6px;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border-color: #3B82F6;
            }
            QMessageBox {
                background-color: #1a1a2e;
                color: #F1F5F9;
            }
            QMessageBox QLabel {
                color: #F1F5F9;
                font-size: 14px;
            }
            QMessageBox QPushButton {
                background: #2563eb;
                color: #ffffff;
                padding: 8px 20px;
                border-radius: 6px;
                border: none;
                font-weight: bold;
            }
            QMessageBox QPushButton:hover {
                background: #3b82f6;
            }
            QGroupBox {
                background-color: rgba(22, 33, 62, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                margin-top: 12px;
                padding: 16px 12px 12px 12px;
                font-weight: bold;
                color: #F1F5F9;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: #F1F5F9;
            }
            QRadioButton, QCheckBox {
                color: #CBD5E1;
                spacing: 6px;
            }
            QRadioButton::indicator, QCheckBox::indicator {
                width: 16px; height: 16px;
            }
            QScrollBar:vertical {
                background: rgba(0, 0, 0, 0.2); width: 8px; border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2); min-height: 30px; border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """
        )
        self.update_widgets_theme()

    def apply_light_theme(self):
        """Áp dụng light theme"""
        self.is_dark_mode = False
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #fafafa;
            }
            QWidget {
                color: #000000;
                font-size: 15px;
                font-weight: 500;
            }
            QPushButton {
                background-color: #1976D2;
                color: #ffffff;
                padding: 12px 18px;
                border-radius: 5px;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1565C0;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QLineEdit, QSpinBox {
                background-color: #ffffff;
                color: #000000;
                border: 2px solid #1976D2;
                padding: 10px;
                border-radius: 4px;
                font-size: 15px;
                font-weight: 500;
            }
            QLabel {
                font-size: 15px;
                font-weight: 500;
                color: #000000;
            }
            QMessageBox {
                background-color: #ffffff;
                color: #000000;
            }
            QMessageBox QLabel {
                color: #000000;
                font-size: 15px;
            }
            QMessageBox QPushButton {
                background-color: #1976D2;
                color: #ffffff;
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 15px;
                font-weight: bold;
            }
            QMessageBox QPushButton:hover {
                background-color: #1565C0;
            }
        """
        )
        self.update_widgets_theme()

    def switch_to_dark(self):
        """Chuyển sang Dark Mode"""
        if not self.is_dark_mode:
            self.apply_dark_theme()
            if hasattr(self, "dark_mode_btn") and hasattr(self, "light_mode_btn"):
                self.dark_mode_btn.setChecked(True)
                self.light_mode_btn.setChecked(False)
            self.log_message("Đã chuyển sang Dark Mode")

    def switch_to_light(self):
        """Chuyển sang Light Mode"""
        if self.is_dark_mode:
            self.apply_light_theme()
            if hasattr(self, "dark_mode_btn") and hasattr(self, "light_mode_btn"):
                self.dark_mode_btn.setChecked(False)
                self.light_mode_btn.setChecked(True)
            self.log_message("Đã chuyển sang Light Mode")

    def update_widgets_theme(self):
        """Cập nhật theme cho tất cả các widget"""
        if self.is_dark_mode:
            # Dark theme colors
            bg_main = "#1a1a2e"
            bg_sidebar = "#1e1e2e"
            bg_widget = "#1a1a2e"
            bg_group = "rgba(0, 0, 0, 0.3)"
            text_color = "#F1F5F9"
            text_secondary = "#94A3B8"
            text_warning = "#F59E0B"
            text_success = "#10B981"
            border_color = "rgba(255, 255, 255, 0.1)"
            log_bg = "rgba(0, 0, 0, 0.3)"
            log_text = "#4ADE80"
            table_bg = "rgba(0, 0, 0, 0.3)"
            table_header = "#1e1e2e"
        else:
            # Light theme colors - cải thiện contrast và dễ nhìn hơn
            bg_main = "#ffffff"
            bg_sidebar = "#ffffff"
            bg_widget = "#ffffff"
            bg_group = "#f5f5f5"
            text_color = "#000000"
            text_secondary = "#333333"
            text_warning = "#E65100"
            text_success = "#1B5E20"
            border_color = "#1976D2"
            log_bg = "#ffffff"
            log_text = "#1B5E20"
            table_bg = "#ffffff"
            table_header = "#BBDEFB"

        # Update sidebar
        if hasattr(self, "sidebar") and self.sidebar:
            hover_bg = bg_group if self.is_dark_mode else "#E3F2FD"
            pressed_bg = bg_widget if self.is_dark_mode else "#BBDEFB"
            self.sidebar.setStyleSheet(
                f"""
                QWidget {{
                    background-color: {bg_sidebar};
                }}
                QLabel {{
                    color: {text_color};
                    padding: 10px;
                    font-size: 15px;
                    font-weight: 500;
                }}
                QPushButton {{
                    background-color: transparent;
                    color: {text_color};
                    text-align: left;
                    padding: 14px 18px;
                    border: none;
                    border-radius: 5px;
                    font-size: 15px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {hover_bg};
                }}
                QPushButton:pressed {{
                    background-color: {pressed_bg};
                }}
            """
            )

        # Update content area
        content_area = self.findChild(QWidget)
        if content_area:
            content_area.setStyleSheet(f"background-color: {bg_main};")

        # Update pages
        for i in range(self.stacked_widget.count()):
            page = self.stacked_widget.widget(i)
            if page:
                page.setStyleSheet(f"background-color: {bg_main}; color: {text_color};")

        # Update log text
        if hasattr(self, "log_text"):
            log_border = border_color if self.is_dark_mode else "#1976D2"
            self.log_text.setStyleSheet(
                f"""
                QTextEdit {{
                    background-color: {log_bg};
                    color: {log_text};
                    border: 2px solid {log_border};
                    font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                    font-size: 17px;
                }}
            """
            )

        # Update results table
        if hasattr(self, "results_table"):
            table_grid = border_color if self.is_dark_mode else "#90CAF9"
            self.results_table.setStyleSheet(
                f"""
                QTableWidget {{
                    background-color: {table_bg};
                    color: {text_color};
                    gridline-color: {table_grid};
                    font-size: 15px;
                    font-weight: 500;
                }}
                QHeaderView::section {{
                    background-color: {table_header};
                    color: {text_color};
                    padding: 10px;
                    font-size: 15px;
                    font-weight: bold;
                }}
            """
            )

        # Update edit text
        if hasattr(self, "edit_text"):
            edit_border = border_color if self.is_dark_mode else "#1976D2"
            self.edit_text.setStyleSheet(
                f"""
                QTextEdit {{
                    background-color: {bg_widget};
                    color: {text_color};
                    border: 2px solid {edit_border};
                    font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                    font-size: 15px;
                    font-weight: 500;
                }}
            """
            )

        # Update system status
        if hasattr(self, "system_status"):
            status_border = border_color if self.is_dark_mode else "#1976D2"
            self.system_status.setStyleSheet(
                f"""
                QTextEdit {{
                    background-color: {bg_widget};
                    color: {text_success};
                    border: 2px solid {status_border};
                    font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                    font-size: 15px;
                    font-weight: 500;
                }}
            """
            )

        # Update labels
        if hasattr(self, "file1_label"):
            if self.file1_path:
                self.file1_label.setStyleSheet(
                    f"color: {text_success}; font-size: 15px; font-weight: bold;"
                )
            else:
                self.file1_label.setStyleSheet(
                    f"color: {text_secondary}; font-size: 15px; font-weight: 500;"
                )

        if hasattr(self, "file2_label"):
            if self.file2_path:
                self.file2_label.setStyleSheet(
                    f"color: {text_success}; font-size: 15px; font-weight: bold;"
                )
            else:
                self.file2_label.setStyleSheet(
                    f"color: {text_secondary}; font-size: 15px; font-weight: 500;"
                )

        if hasattr(self, "capcut_project_label"):
            if self.capcut_project_dir:
                self.capcut_project_label.setStyleSheet(
                    f"color: {text_success}; font-size: 15px; font-weight: bold;"
                )
            else:
                self.capcut_project_label.setStyleSheet(
                    f"color: {text_secondary}; font-size: 15px; font-weight: 500;"
                )

        if hasattr(self, "edit_file_label"):
            if hasattr(self, "edit_file_path") and self.edit_file_path:
                self.edit_file_label.setStyleSheet(
                    f"color: {text_success}; font-size: 15px; font-weight: bold;"
                )
            else:
                self.edit_file_label.setStyleSheet(
                    f"color: {text_secondary}; font-size: 15px; font-weight: 500;"
                )

        if hasattr(self, "status_label"):
            self.status_label.setStyleSheet(
                f"color: {text_warning}; padding: 10px; font-size: 15px; font-weight: bold;"
            )

        # Update radio buttons
        if hasattr(self, "auto_fix_radio1"):
            radio_color = text_color
            self.auto_fix_radio1.setStyleSheet(
                f"color: {radio_color}; font-size: 15px; font-weight: 500;"
            )
            self.auto_fix_radio2.setStyleSheet(
                f"color: {radio_color}; font-size: 15px; font-weight: 500;"
            )

        # Update group boxes
        for group in self.findChildren(QGroupBox):
            if (
                group.title() != "Nhật ký hoạt động"
            ):  # Skip log group, handled separately
                group_border = border_color if self.is_dark_mode else "#1976D2"
                group.setStyleSheet(
                    f"""
                    QGroupBox {{
                        color: {text_color};
                        border: 2px solid {group_border};
                        border-radius: 5px;
                        margin-top: 10px;
                        padding-top: 10px;
                        font-size: 15px;
                        font-weight: 500;
                    }}
                    QGroupBox::title {{
                        subcontrol-origin: margin;
                        left: 10px;
                        padding: 0 5px;
                        font-size: 16px;
                        font-weight: bold;
                        color: {text_color};
                    }}
                """
                )

        # Update active menu style
        if hasattr(self, "active_menu_key"):
            self.update_active_menu_style()

        # Update log group box (special red border)
        log_group = None
        for group in self.findChildren(QGroupBox):
            if group.title() == "Nhật ký hoạt động":
                log_group = group
                break

        if log_group:
            if self.is_dark_mode:
                log_group.setStyleSheet(
                    """
                    QGroupBox {
                        color: #ffffff;
                        border: 2px solid #d32f2f;
                        border-radius: 5px;
                        margin-top: 10px;
                        padding-top: 10px;
                    }
                    QGroupBox::title {
                        subcontrol-origin: margin;
                        left: 10px;
                        padding: 0 5px;
                        background-color: #d32f2f;
                    }
                """
                )
            else:
                log_group.setStyleSheet(
                    """
                    QGroupBox {
                        color: #000000;
                        border: 2px solid #d32f2f;
                        border-radius: 5px;
                        margin-top: 10px;
                        padding-top: 10px;
                    }
                    QGroupBox::title {
                        subcontrol-origin: margin;
                        left: 10px;
                        padding: 0 5px;
                        background-color: #d32f2f;
                        color: #ffffff;
                    }
                """
                )

    def update_active_menu_style(self):
        """Cập nhật style cho menu item đang active"""
        # Reset tất cả menu buttons
        for btn in self.menu_items.values():
            btn.setStyleSheet("")
            btn.setChecked(False)

        # Highlight active button
        if hasattr(self, "active_menu_key") and self.active_menu_key in self.menu_items:
            self.menu_items[self.active_menu_key].setChecked(True)
            self.menu_items[self.active_menu_key].setStyleSheet(
                """
                QPushButton {
                    background-color: #d32f2f;
                    color: #ffffff;
                }
            """
            )

    def switch_page(self, page_key: str):
        """Chuyển đổi giữa các trang"""
        page_map = {"compare": 0, "split": 1, "translate": 2}

        # Set active menu
        self.active_menu_key = page_key
        self.update_active_menu_style()

        # Switch page
        if page_key in page_map:
            self.stacked_widget.setCurrentIndex(page_map[page_key])
            self.log_message(f"Chuyển sang trang: {page_key}")

    def select_file(self, file_num: int):
        """Chọn file SRT"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Chọn File SRT {file_num}", "", "SRT Files (*.srt);;All Files (*)"
        )

        if file_path:
            path = Path(file_path)
            if file_num == 1:
                self.file1_path = path
                self.file1_label.setText(path.name)
                self.file1_label.setStyleSheet("color: #4caf50;")
                # Đồng bộ cho tab Chia phụ đề: dùng luôn File 1 làm nguồn chia
                self.split_file_path = path
                if hasattr(self, "split_file_label"):
                    self.split_file_label.setText(path.name)
                    self.split_file_label.setStyleSheet(
                        "color: #4caf50; font-size: 15px; font-weight: bold;"
                    )

                # Đồng bộ cho tab Dịch bằng Gemini: mặc định dùng File 1 làm nguồn dịch
                try:
                    self.translate_entries = parse_srt_file(path)
                    if hasattr(self, "translate_file_label"):
                        self.translate_file_label.setText(path.name)
                        self.translate_file_label.setStyleSheet(
                            "color: #4caf50; font-weight: bold;"
                        )

                    # Nếu tab Chia phụ đề đã có nhóm thì ưu tiên giữ cách chia đó
                    if self.split_entries and self.split_chunks:
                        self._sync_split_to_translate()
                    elif not self.translate_chunks:
                        # Chỉ reset khi chưa có nhóm dịch nào (tránh xóa nhầm công việc đang dịch)
                        if hasattr(self, "translate_chunks_list"):
                            self.translate_chunks_list.clear()
                        self.translate_chunks = []
                        self.translated_results = {}
                        if hasattr(self, "translate_result_preview"):
                            self.translate_result_preview.clear()
                        if hasattr(self, "translate_original_preview"):
                            self.translate_original_preview.clear()
                        if hasattr(self, "translate_progress"):
                            self.translate_progress.setMaximum(100)
                            self.translate_progress.setValue(0)
                        if hasattr(self, "btn_start_translate"):
                            self.btn_start_translate.setEnabled(False)
                        if hasattr(self, "btn_save_to_file2"):
                            self.btn_save_to_file2.setEnabled(False)
                        if hasattr(self, "btn_compare_timeline"):
                            self.btn_compare_timeline.setEnabled(False)

                    self.log_message(
                        f"[Dịch] Đã đồng bộ nguồn dịch từ File 1: {path.name} ({len(self.translate_entries)} phụ đề)"
                    )
                except Exception as e:
                    self.log_message(
                        f"[Dịch] ⚠️ Không thể đồng bộ File 1 sang tab Dịch: {e}"
                    )

                self.log_message(f"Đã chọn File 1: {path.name}")
            else:
                self.file2_path = path
                self.file2_label.setText(path.name)
                self.file2_label.setStyleSheet("color: #4caf50;")
                self.log_message(f"Đã chọn File 2: {path.name}")

    def start_compare(self):
        """Bắt đầu so sánh file"""
        if not self.file1_path or not self.file2_path:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn đủ 2 file SRT!")
            return

        if not self.file1_path.exists() or not self.file2_path.exists():
            QMessageBox.warning(self, "Lỗi", "Một trong các file không tồn tại!")
            return

        self.log_message("Bắt đầu so sánh...")
        self.status_label.setText("Trạng thái: Đang so sánh...")

        tolerance = 0

        # Chạy so sánh trong thread riêng
        self.compare_thread = CompareThread(self.file1_path, self.file2_path, tolerance)
        self.compare_thread.finished.connect(self.on_compare_finished)
        self.compare_thread.error.connect(self.on_compare_error)
        self.compare_thread.start()

    def on_compare_finished(self, result: dict):
        """Xử lý khi so sánh hoàn thành"""
        self.compare_result = result
        self.status_label.setText("Trạng thái: Hoàn thành")

        # Lưu entries để dùng cho auto fix
        try:
            if self.file1_path:
                self.file1_entries = parse_srt_file(self.file1_path)
            if self.file2_path:
                self.file2_entries = parse_srt_file(self.file2_path)
        except:
            pass

        total = result.get("total_compared", 0)
        errors_count = len(result.get("errors", []))
        matched = result.get("matched", 0)

        # Log thêm dòng tổng quan kiểu "Đã so sánh X/Y, đúng A, sai B"
        if total > 0:
            self.log_message(
                f"📊 Đã so sánh {total}/{max(result.get('file1_entries', 0), result.get('file2_entries', 0))} phụ đề "
                f"→ ĐÚNG {matched}, LỆCH {errors_count}"
            )

        # Hiển thị kết quả trong bảng
        self.results_table.setRowCount(0)

        if result["errors"]:
            self.log_message(
                f"Phát hiện {errors_count} lỗi lệch thời gian (tính theo từng mốc start/end)"
            )
            for error in result["errors"]:
                row = self.results_table.rowCount()
                self.results_table.insertRow(row)

                error_type = "BẮT ĐẦU" if error["type"] == "start" else "KẾT THÚC"
                self.results_table.setItem(
                    row, 0, QTableWidgetItem(str(error["index"]))
                )
                self.results_table.setItem(row, 1, QTableWidgetItem(error_type))
                self.results_table.setItem(
                    row, 2, QTableWidgetItem(error["file1_time"])
                )
                self.results_table.setItem(
                    row, 3, QTableWidgetItem(error["file2_time"])
                )
                self.results_table.setItem(
                    row, 4, QTableWidgetItem(str(error["diff_ms"]))
                )

                # Tô màu dòng lỗi
                for col in range(5):
                    item = self.results_table.item(row, col)
                    if item:
                        item.setBackground(QColor("#3d1e1e"))
        else:
            self.log_message("✅ Tất cả thời gian đều khớp!")
            QMessageBox.information(
                self, "Thành công", "Tất cả thời gian đều khớp nhau!"
            )

        # Populate card views
        self._populate_compare_cards()

        # Tổng kết
        summary = f"Tổng kết: {result['matched']}/{result['total_compared']} khớp, {len(result['errors'])} lệch"
        self.log_message(summary)

        if result["file1_extra"]:
            self.log_message(f"⚠️ File 1 có thêm {len(result['file1_extra'])} entries")
        if result["file2_extra"]:
            self.log_message(f"⚠️ File 2 có thêm {len(result['file2_extra'])} entries")

    def on_compare_error(self, error_msg: str):
        """Xử lý lỗi khi so sánh"""
        self.status_label.setText("Trạng thái: Lỗi")
        self.log_message(f"❌ Lỗi: {error_msg}")
        QMessageBox.critical(self, "Lỗi", f"Lỗi khi so sánh:\n{error_msg}")

    def create_thaisub(self):
        """Tạo file thaisub"""
        if not self.file1_path:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn File 1 trước!")
            return

        try:
            # Tạo tên file thaisub
            base_name = self.file1_path.stem
            done_file = self.file1_path.parent / f"{base_name} done.srt"

            if done_file.exists():
                source_file = done_file
                base_name = source_file.stem.replace(" done", "")
            else:
                source_file = self.file1_path

            output_file = source_file.parent / f"{base_name}_thaisub.srt"

            # Kiểm tra file đã tồn tại
            if output_file.exists():
                reply = QMessageBox.question(
                    self,
                    "File đã tồn tại",
                    f"File {output_file.name} đã tồn tại.\nBạn có muốn ghi đè không?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    self.log_message("Đã hủy tạo file thaisub")
                    return

            # Tạo file thaisub hoàn toàn rỗng (không có timeline, không có số thứ tự)
            with open(output_file, "w", encoding="utf-8") as f:
                pass  # File rỗng

            self.last_thaisub_file = output_file  # Lưu file thaisub vừa tạo

            # Tự động chọn làm File 2
            self.file2_path = output_file
            self.file2_label.setText(output_file.name)
            self.file2_label.setStyleSheet("color: #4caf50;")

            # Khởi tạo entries rỗng
            self.file2_entries = []

            self.log_message(f"✅ Đã tạo file thaisub rỗng: {output_file.name}")
            self.log_message(f"✅ Đã tự động chọn làm File 2")
            QMessageBox.information(
                self,
                "Thành công",
                f"Đã tạo file rỗng:\n{output_file}\n\nĐã tự động chọn làm File 2!",
            )
        except Exception as e:
            self.log_message(f"❌ Lỗi tạo file: {str(e)}")
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi tạo file:\n{str(e)}")

    def open_file1_in_notepad(self):
        """Mở File 1 bằng TextEdit (macOS) hoặc Notepad (Windows)"""
        if not self.file1_path or not self.file1_path.exists():
            QMessageBox.warning(
                self, "Cảnh báo", "Chưa có File 1 để mở!\nVui lòng chọn File 1 trước."
            )
            return

        try:
            file_path_str = str(self.file1_path.absolute())
            if sys.platform == "darwin":
                # macOS: mở bằng TextEdit
                subprocess.Popen(["open", "-a", "TextEdit", file_path_str])
                self.log_message(f"📝 Đã mở File 1 bằng TextEdit: {self.file1_path.name}")
            else:
                # Windows: mở bằng Notepad
                subprocess.Popen(["notepad.exe", file_path_str])
                self.log_message(f"📝 Đã mở File 1 bằng Notepad: {self.file1_path.name}")
        except Exception as e:
            # Fallback: thử mở bằng ứng dụng mặc định
            try:
                file_path_str = str(self.file1_path.absolute())
                if sys.platform == "darwin":
                    subprocess.Popen(["open", file_path_str])
                else:
                    os.startfile(file_path_str)  # Windows
                self.log_message(
                    f"📝 Đã mở File 1 bằng ứng dụng mặc định: {self.file1_path.name}"
                )
            except:
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_path_str))
                    self.log_message(f"📝 Đã mở File 1: {self.file1_path.name}")
                except Exception as e2:
                    QMessageBox.critical(self, "Lỗi", f"Không thể mở file:\n{str(e2)}")
                    self.log_message(f"❌ Lỗi mở File 1: {str(e2)}")

    def open_file2_in_notepad(self):
        """Mở File 2 bằng TextEdit (macOS) hoặc Notepad (Windows)"""
        # Ưu tiên file 2, sau đó là file thaisub
        file_to_open = None
        if self.file2_path and self.file2_path.exists():
            file_to_open = self.file2_path
        elif (
            hasattr(self, "last_thaisub_file")
            and self.last_thaisub_file
            and self.last_thaisub_file.exists()
        ):
            file_to_open = self.last_thaisub_file
        else:
            QMessageBox.warning(
                self,
                "Cảnh báo",
                "Chưa có File 2 hoặc file thaisub để mở!\nVui lòng chọn File 2 hoặc tạo file thaisub trước.",
            )
            return

        try:
            file_path_str = str(file_to_open.absolute())
            if sys.platform == "darwin":
                # macOS: mở bằng TextEdit
                subprocess.Popen(["open", "-a", "TextEdit", file_path_str])
                self.log_message(f"📝 Đã mở file bằng TextEdit: {file_to_open.name}")
            else:
                # Windows: mở bằng Notepad
                subprocess.Popen(["notepad.exe", file_path_str])
                self.log_message(f"📝 Đã mở file bằng Notepad: {file_to_open.name}")
        except Exception as e:
            # Fallback: thử mở bằng ứng dụng mặc định
            try:
                file_path_str = str(file_to_open.absolute())
                if sys.platform == "darwin":
                    subprocess.Popen(["open", file_path_str])
                else:
                    os.startfile(file_path_str)  # Windows
                self.log_message(
                    f"📝 Đã mở file bằng ứng dụng mặc định: {file_to_open.name}"
                )
            except:
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_path_str))
                    self.log_message(f"📝 Đã mở file: {file_to_open.name}")
                except Exception as e2:
                    QMessageBox.critical(self, "Lỗi", f"Không thể mở file:\n{str(e2)}")
                    self.log_message(f"❌ Lỗi mở file: {str(e2)}")

    def auto_fix_errors(self):
        """Tự động sửa lỗi bằng cách đồng bộ thời gian"""
        if not self.file1_path or not self.file2_path:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn đủ 2 file SRT!")
            return

        if not self.compare_result or not self.compare_result["errors"]:
            QMessageBox.warning(
                self, "Cảnh báo", "Không có lỗi để sửa! Vui lòng so sánh trước."
            )
            return

        if (
            not self.auto_fix_radio1.isChecked()
            and not self.auto_fix_radio2.isChecked()
        ):
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn hướng đồng bộ!")
            return

        # Xác định file nguồn và file đích
        if self.auto_fix_radio1.isChecked():
            source_file = self.file1_path
            target_file = self.file2_path
        else:
            source_file = self.file2_path
            target_file = self.file1_path

        reply = QMessageBox.question(
            self,
            "Xác nhận",
            f"Bạn có chắc muốn đồng bộ thời gian từ {source_file.name} sang {target_file.name}?\n"
            f"Sẽ sửa {len(self.compare_result['errors'])} lỗi.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Load entries
            source_entries = parse_srt_file(source_file)
            target_entries = parse_srt_file(target_file)

            # Tạo dict để map entry index
            source_dict = {entry.index: entry for entry in source_entries}
            target_dict = {entry.index: entry for entry in target_entries}

            # Sửa các lỗi
            fixed_count = 0
            for error in self.compare_result["errors"]:
                entry_index = error["index"]
                if entry_index in source_dict and entry_index in target_dict:
                    source_entry = source_dict[entry_index]
                    target_entry = target_dict[entry_index]

                    if error["type"] == "start":
                        target_entry.start_time = source_entry.start_time
                        fixed_count += 1
                    elif error["type"] == "end":
                        target_entry.end_time = source_entry.end_time
                        fixed_count += 1

            # Lưu file đã sửa
            save_srt_file(target_entries, target_file)

            self.log_message(
                f"✅ Đã sửa {fixed_count} lỗi trong file {target_file.name}"
            )
            QMessageBox.information(
                self,
                "Thành công",
                f"Đã sửa {fixed_count} lỗi!\nFile đã được lưu: {target_file.name}",
            )

            # Tự động so sánh lại
            QTimer.singleShot(500, self.start_compare)

        except Exception as e:
            self.log_message(f"❌ Lỗi khi sửa: {str(e)}")
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi sửa file:\n{str(e)}")

    # ===== Chức năng dịch kiểm tra (Verify Translation) =====

    def _verify_translation(self, e1, e2):
        """Dịch cả 2 bên (gốc + dịch) sang tiếng Việt để user so sánh"""
        original_text = "\n".join(e1.content) if e1.content else ""
        translated_text = "\n".join(e2.content) if e2.content else ""

        if not original_text.strip() and not translated_text.strip():
            QMessageBox.information(
                self, "Dịch kiểm tra",
                "Cả 2 bên đều trống, không có gì để dịch."
            )
            return

        self.log_message(f"🔍 Đang dịch kiểm tra entry #{e1.index}...")
        self.status_label.setText("Trạng thái: Đang dịch kiểm tra...")

        # Lưu thông tin entry để hiển thị trong popup
        self._verify_entry_index = e1.index
        self._verify_original_text = original_text
        self._verify_translated_text = translated_text

        # Lấy trạng thái checkbox "Dịch cả File 1"
        translate_file1 = self.chk_translate_file1.isChecked() if hasattr(self, 'chk_translate_file1') else False
        self._verify_translate_file1 = translate_file1

        # Tạo worker thread
        self._verify_worker = TranslateVerifyWorker(
            original_text, translated_text, target_lang="vi",
            translate_file1=translate_file1, parent=self
        )
        self._verify_worker.finished.connect(self._show_verify_result)
        self._verify_worker.failed.connect(self._on_verify_failed)
        self._verify_worker.start()

    def _show_verify_result(self, original_vi: str, translated_vi: str):
        """Hiển thị kết quả dịch kiểm tra trong popup"""
        self.status_label.setText("Trạng thái: Hoàn thành dịch kiểm tra")
        idx = getattr(self, "_verify_entry_index", "?")
        original_text = getattr(self, "_verify_original_text", "")
        translated_text = getattr(self, "_verify_translated_text", "")

        self.log_message(f"✅ Đã dịch kiểm tra entry #{idx}")

        # Tạo dialog tùy chỉnh thay vì QMessageBox để hiển thị đẹp hơn
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle(f"🔍 Dịch kiểm tra — Entry #{idx}")
        dialog.setMinimumSize(700, 500)
        dialog.setStyleSheet("""
            QDialog {
                background: #1a1a2e;
                color: #F1F5F9;
            }
            QLabel {
                color: #F1F5F9;
            }
        """)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(16, 16, 16, 16)
        dlg_layout.setSpacing(12)

        # Title
        title = QLabel(f"So sánh bản dịch — Dòng #{idx}")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #8b5cf6; border: none;")
        dlg_layout.addWidget(title)

        # 2 cột song song
        columns = QHBoxLayout()
        columns.setSpacing(12)

        # --- Cột trái: Nội dung gốc ---
        left_frame = QFrame()
        left_frame.setStyleSheet("""
            QFrame {
                background: rgba(22, 33, 62, 0.7);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 10px;
            }
        """)
        left_vbox = QVBoxLayout(left_frame)
        left_vbox.setContentsMargins(12, 10, 12, 10)

        left_title = QLabel("📄 Nội dung gốc (File 1)")
        left_title.setStyleSheet("font-weight: 700; color: #22d3ee; border: none; font-size: 13px;")
        left_vbox.addWidget(left_title)

        left_original = QLabel(original_text)
        left_original.setWordWrap(True)
        left_original.setStyleSheet("color: #94a3b8; font-size: 12px; border: none; padding: 4px 0;")
        left_original.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left_vbox.addWidget(left_original)

        # Chỉ hiển thị bản dịch File 1 nếu user bật checkbox "Dịch cả File 1"
        if getattr(self, '_verify_translate_file1', False) and original_vi:
            left_translated_title = QLabel("🇻🇳 Dịch sang tiếng Việt")
            left_translated_title.setStyleSheet("font-weight: 700; color: #22c55e; border: none; font-size: 13px; margin-top: 8px;")
            left_vbox.addWidget(left_translated_title)

            left_result = QLabel(original_vi)
            left_result.setWordWrap(True)
            left_result.setStyleSheet("color: #F1F5F9; font-size: 13px; border: none; padding: 4px 0;")
            left_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
            left_vbox.addWidget(left_result)

        left_vbox.addStretch()
        columns.addWidget(left_frame, 1)

        # --- Cột phải: Bản dịch ---
        right_frame = QFrame()
        right_frame.setStyleSheet("""
            QFrame {
                background: rgba(22, 33, 62, 0.7);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 10px;
            }
        """)
        right_vbox = QVBoxLayout(right_frame)
        right_vbox.setContentsMargins(12, 10, 12, 10)

        right_title = QLabel("📝 Bản dịch (File 2)")
        right_title.setStyleSheet("font-weight: 700; color: #eab308; border: none; font-size: 13px;")
        right_vbox.addWidget(right_title)

        right_original = QLabel(translated_text)
        right_original.setWordWrap(True)
        right_original.setStyleSheet("color: #94a3b8; font-size: 12px; border: none; padding: 4px 0;")
        right_original.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_vbox.addWidget(right_original)

        right_translated_title = QLabel("🇻🇳 Dịch sang tiếng Việt")
        right_translated_title.setStyleSheet("font-weight: 700; color: #22c55e; border: none; font-size: 13px; margin-top: 8px;")
        right_vbox.addWidget(right_translated_title)

        right_result = QLabel(translated_vi if translated_vi else "(Trống)")
        right_result.setWordWrap(True)
        right_result.setStyleSheet("color: #F1F5F9; font-size: 13px; border: none; padding: 4px 0;")
        right_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_vbox.addWidget(right_result)

        right_vbox.addStretch()
        columns.addWidget(right_frame, 1)

        dlg_layout.addLayout(columns, 1)

        # Nút đóng
        close_btn = QPushButton("Đóng")
        close_btn.setStyleSheet("""
            QPushButton {
                background: #2563eb; color: white; padding: 8px 24px;
                border-radius: 8px; font-weight: 600; border: none;
            }
            QPushButton:hover { background: #3b82f6; }
        """)
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn, 0, Qt.AlignCenter)

        dialog.exec_()

    def _on_verify_failed(self, error_msg: str):
        """Xử lý lỗi khi dịch kiểm tra"""
        self.status_label.setText("Trạng thái: Lỗi dịch kiểm tra")
        idx = getattr(self, "_verify_entry_index", "?")
        self.log_message(f"❌ Lỗi dịch kiểm tra entry #{idx}: {error_msg}")
        QMessageBox.warning(
            self, "Lỗi dịch kiểm tra",
            f"Không thể dịch kiểm tra entry #{idx}:\n\n{error_msg}"
        )

    # ===== Các hàm hỗ trợ CapCut SRT trong tab riêng =====

    def capcut_log(self, message: str):
        """Ghi log cho trang CapCut"""
        if hasattr(self, "capcut_log_text") and self.capcut_log_text:
            self.capcut_log_text.append(message)
            self.capcut_log_text.verticalScrollBar().setValue(
                self.capcut_log_text.verticalScrollBar().maximum()
            )
        if hasattr(self, "capcut_progress_label") and self.capcut_progress_label:
            self.capcut_progress_label.setText(message)

    def capcut_clear_log(self):
        if hasattr(self, "capcut_log_text") and self.capcut_log_text:
            self.capcut_log_text.clear()

    def capcut_save_log(self):
        if not hasattr(self, "capcut_log_text") or not self.capcut_log_text:
            return
        text = self.capcut_log_text.toPlainText()
        if not text:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu log CapCut",
            "capcut_log.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(text)
                QMessageBox.information(
                    self, "Thành công", "Đã lưu log CapCut thành công!"
                )
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Lỗi khi lưu log CapCut:\n{e}")

    def capcut_choose_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục project CapCut (chứa draft_content.json hoặc draft_info.json)",
            "",
        )
        if directory:
            self.capcut_project_dir = directory
            if hasattr(self, "capcut_project_label") and self.capcut_project_label:
                from pathlib import Path as _Path

                name = _Path(directory).name
                self.capcut_project_label.setText(name)
                self.capcut_project_label.setStyleSheet("color: #4caf50;")

    def capcut_run_export(self):
        if capcut_srt is None:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.",
            )
            return

        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn thư mục project trước.")
            return
        if not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục không hợp lệ.")
            return

        try:
            if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
                self.capcut_progress_bar.setMaximum(100)
                self.capcut_progress_bar.setValue(5)
            self.capcut_log(f"Đang đọc draft trong: {project_dir}")
            data = capcut_srt.load_draft_json(project_dir)
            self.capcut_log("Đã đọc file draft thành công.")

            subtitles = capcut_srt.extract_subtitles_with_audio(data, project_dir)
            if not subtitles:
                self.capcut_log(
                    "Không tìm thấy phụ đề hoặc track thời gian trong file draft."
                )
                QMessageBox.warning(
                    self,
                    "Không có dữ liệu",
                    "Không tìm thấy phụ đề hoặc track thời gian trong file draft.",
                )
                return

            if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
                self.capcut_progress_bar.setValue(60)
            capcut_srt.write_outputs(project_dir, subtitles)
            if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
                self.capcut_progress_bar.setValue(100)
            self.capcut_log(
                "Đã tạo subtitles.srt, copy.txt và subtitles_with_audio.json trong thư mục project."
            )
            QMessageBox.information(
                self,
                "Hoàn tất",
                "Xuất SRT thành công.\nĐã tạo:\n- subtitles.srt\n- copy.txt\n- subtitles_with_audio.json",
            )
        except Exception as e:
            self.capcut_log(f"Lỗi: {e}")
            QMessageBox.critical(self, "Lỗi", str(e))
        finally:
            if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
                QTimer.singleShot(500, lambda: self.capcut_progress_bar.setValue(0))

    def capcut_run_trim_audio(self):
        if capcut_srt is None:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.",
            )
            return

        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn thư mục project trước.")
            return
        if not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục không hợp lệ.")
            return

        try:
            data = capcut_srt.load_draft_json(project_dir)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không đọc được file draft: {e}")
            return

        materials = data.get("materials") or {}
        audios = materials.get("audios") or []
        if not audios:
            QMessageBox.warning(
                self,
                "Không có audio",
                "Không tìm thấy danh sách audio trong file draft.",
            )
            return

        silence_thresh = (
            int(self.capcut_silence_thresh_spin.value())
            if hasattr(self, "capcut_silence_thresh_spin")
            and self.capcut_silence_thresh_spin
            else -40
        )
        min_silence_len = (
            int(self.capcut_min_silence_spin.value())
            if hasattr(self, "capcut_min_silence_spin") and self.capcut_min_silence_spin
            else 300
        )

        # Chuẩn bị danh sách file audio cần trim (dùng cùng logic với tool gốc)
        targets = []
        for a in audios:
            original_path = (
                a.get("path") or a.get("file_path") or a.get("local_material_path")
            )
            if not original_path:
                continue
            src_abs = capcut_srt.resolve_audio_path_from_original(
                original_path, project_dir
            )
            if not src_abs:
                continue
            if not os.path.isfile(src_abs):
                continue
            targets.append(src_abs)

        if not targets:
            QMessageBox.warning(
                self,
                "Không có file",
                "Không tìm thấy file audio nào trong thư mục textReading để trim.",
            )
            return

        self.capcut_log(
            f"Đang trim audio, ngưỡng {silence_thresh} dBFS, im lặng tối thiểu {min_silence_len} ms..."
        )

        processed = 0
        failed = 0
        logged_details = 0

        if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
            self.capcut_progress_bar.setMaximum(len(targets))
            self.capcut_progress_bar.setValue(0)

        for idx, src_abs in enumerate(targets, start=1):
            dst_dir = os.path.join(project_dir, "trimmed_audio")
            os.makedirs(dst_dir, exist_ok=True)
            dst_name = os.path.basename(src_abs)
            dst_path = os.path.join(dst_dir, dst_name)

            ok, err = capcut_srt.trim_audio_file(
                src_abs,
                dst_path,
                silence_thresh=silence_thresh,
                min_silence_len=min_silence_len,
            )
            if ok:
                processed += 1
            else:
                failed += 1
                if logged_details < 5:
                    short_err = (err or "").splitlines()[-1] if err else ""
                    self.capcut_log(f"Trim lỗi với file: {src_abs} ({short_err})")
                    logged_details += 1

            if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
                self.capcut_progress_bar.setValue(idx)
            QApplication.processEvents()

        self.capcut_log(f"Đã trim {processed} file audio, lỗi {failed} file.")
        if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
            QTimer.singleShot(500, lambda: self.capcut_progress_bar.setValue(0))

    def run_check_audio_overlap(self):
        if capcut_srt is None:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.",
            )
            return

        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn thư mục project trước.")
            return

        video_speed = (
            self.video_speed_spin.value()
            if hasattr(self, "video_speed_spin") and self.video_speed_spin
            else 1.0
        )
        audio_speed = (
            self.audio_speed_spin.value()
            if hasattr(self, "audio_speed_spin") and self.audio_speed_spin
            else 1.0
        )

        self.capcut_log(f"Bắt đầu kiểm tra overlap audio (Threaded)...")
        self.capcut_log(f"Video Speed: {video_speed}x, Audio Speed: {audio_speed}x")

        # Disable button during check
        if hasattr(self, "check_overlap_btn") and self.check_overlap_btn:
            self.check_overlap_btn.setEnabled(False)
            self.check_overlap_btn.setText("⏳ Đang kiểm tra...")
        if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
            self.capcut_progress_bar.setValue(0)

        # Start Thread
        self.check_overlap_thread = CheckOverlapThread(
            project_dir, video_speed, audio_speed
        )
        self.check_overlap_thread.finished.connect(self.on_check_overlap_finished)
        self.check_overlap_thread.error.connect(self.on_check_overlap_error)
        if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
            self.check_overlap_thread.progress.connect(
                self.capcut_progress_bar.setValue
            )
        self.check_overlap_thread.log_message.connect(self.capcut_log)
        self.check_overlap_thread.start()

    def on_check_overlap_finished(self, overlaps: list):
        if hasattr(self, "check_overlap_btn") and self.check_overlap_btn:
            self.check_overlap_btn.setEnabled(True)
            self.check_overlap_btn.setText("🔍 Kiểm tra Overlap")
        if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
            self.capcut_progress_bar.setValue(100)

        # Check for error dict in list
        if overlaps and "error" in overlaps[0]:
            QMessageBox.critical(self, "Lỗi", f"Lỗi: {overlaps[0]['error']}")
            self.capcut_log(f"❌ Lỗi: {overlaps[0]['error']}")
            return

        if hasattr(self, "overlap_table") and self.overlap_table:
            self.overlap_table.setRowCount(0)

        if not overlaps:
            self.capcut_log("✅ Không phát hiện overlap nào.")
            QMessageBox.information(self, "Kết quả", "Không phát hiện overlap nào!")
            return

        self.capcut_log(f"⚠️ Phát hiện {len(overlaps)} overlap!")

        if hasattr(self, "overlap_table") and self.overlap_table:
            for ov in overlaps:
                row = self.overlap_table.rowCount()
                self.overlap_table.insertRow(row)
                self.overlap_table.setItem(row, 0, QTableWidgetItem(str(ov["clip_A"])))
                self.overlap_table.setItem(row, 1, QTableWidgetItem(str(ov["clip_B"])))
                self.overlap_table.setItem(
                    row, 2, QTableWidgetItem(f"{ov['overlap_duration_ms']} ms")
                )
                self.overlap_table.setItem(
                    row, 3, QTableWidgetItem(str(ov["formatted_time"]))
                )

                # Highlight overlap rows
                for col in range(4):
                    item = self.overlap_table.item(row, col)
                    if item:
                        item.setBackground(QColor("#5a3b3b"))  # Dark redish background

            self.capcut_log(
                f"Overlap: '{ov['clip_A']}' đè lên '{ov['clip_B']}' ({ov['overlap_duration_ms']}ms) tại {ov['formatted_time']}"
            )

        QMessageBox.warning(
            self,
            "Phát hiện Overlap",
            f"Tìm thấy {len(overlaps)} vị trí bị chồng âm thanh.\nXem chi tiết trong bảng và log.",
        )

    def on_check_overlap_error(self, error_msg: str):
        if hasattr(self, "check_overlap_btn") and self.check_overlap_btn:
            self.check_overlap_btn.setEnabled(True)
            self.check_overlap_btn.setText("🔍 Kiểm tra Overlap")
        if hasattr(self, "capcut_progress_bar") and self.capcut_progress_bar:
            self.capcut_progress_bar.setValue(0)
        QMessageBox.critical(self, "Lỗi", f"Lỗi khi kiểm tra overlap: {error_msg}")
        self.capcut_log(f"❌ Lỗi: {error_msg}")

    def capcut_run_swap_folders(self):
        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Vui lòng chọn thư mục Draft CapCut hoặc textReading trước.",
            )
            return
        if not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục không hợp lệ.")
            return

        proj_lower = project_dir.lower().rstrip("/\\")
        if proj_lower.endswith("textreading"):
            draft_root = os.path.dirname(project_dir.rstrip("/\\"))
            text_reading_dir = project_dir
        else:
            draft_root = project_dir
            text_reading_dir = os.path.join(draft_root, "textReading")

        backup_dir = os.path.join(draft_root, "textReading - original")

        trimmed_candidates = [
            os.path.join(draft_root, "trimmed_audio"),
            os.path.join(text_reading_dir, "trimmed_audio"),
        ]
        trimmed_dir = next((p for p in trimmed_candidates if os.path.isdir(p)), None)

        if not os.path.isdir(text_reading_dir):
            QMessageBox.warning(
                self,
                "Không tìm thấy textReading",
                f"Không tìm thấy thư mục textReading tại:\n{text_reading_dir}",
            )
            return

        if not trimmed_dir:
            QMessageBox.warning(
                self,
                "Không có trimmed_audio",
                "Không tìm thấy thư mục trimmed_audio.\nHãy chạy Trim audio trước khi swap.",
            )
            return

        if os.path.exists(backup_dir):
            QMessageBox.warning(
                self,
                "Đã tồn tại backup",
                f"Thư mục '{backup_dir}' đã tồn tại.\n"
                "Vui lòng tự kiểm tra/đổi tên hoặc xóa trước khi thực hiện swap lần nữa.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Xác nhận",
            f"Thao tác này sẽ:\n\n"
            f"- Đổi tên '{text_reading_dir}' thành 'textReading - original'\n"
            f"- Đổi tên '{trimmed_dir}' thành 'textReading'\n\n"
            f"Bạn chắc chắn muốn tiếp tục?",
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.capcut_log("Đang swap thư mục textReading với trimmed_audio...")
            os.rename(text_reading_dir, backup_dir)
            os.rename(trimmed_dir, text_reading_dir)
            self.capcut_log("Đã swap thành công. CapCut sẽ dùng audio đã trim.")
            QMessageBox.information(
                self,
                "Hoàn tất",
                "Đã swap:\n- textReading -> textReading - original\n"
                "- trimmed_audio -> textReading\n\n"
                "Mở lại CapCut để thấy hiệu lực.",
            )
        except Exception as e:
            self.capcut_log(f"Lỗi khi swap thư mục: {e}")
            QMessageBox.critical(self, "Lỗi swap thư mục", f"Không thực hiện được: {e}")

    def capcut_run_restore_folders(self):
        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Vui lòng chọn thư mục Draft CapCut hoặc textReading trước.",
            )
            return
        if not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục không hợp lệ.")
            return

        proj_lower = project_dir.lower().rstrip("/\\")
        if proj_lower.endswith("textreading"):
            draft_root = os.path.dirname(project_dir.rstrip("/\\"))
            text_reading_dir = project_dir
        else:
            draft_root = project_dir
            text_reading_dir = os.path.join(draft_root, "textReading")

        backup_dir = os.path.join(draft_root, "textReading - original")
        trimmed_dir = os.path.join(draft_root, "trimmed_audio")

        if not os.path.isdir(backup_dir):
            QMessageBox.warning(
                self,
                "Không có backup",
                f"Không tìm thấy thư mục backup:\n{backup_dir}\n\n"
                "Chỉ có thể khôi phục nếu đã từng swap (tạo textReading - original).",
            )
            return

        if not os.path.isdir(text_reading_dir):
            QMessageBox.warning(
                self,
                "Không tìm thấy textReading",
                f"Không tìm thấy thư mục textReading hiện tại tại:\n{text_reading_dir}",
            )
            return

        reply = QMessageBox.question(
            self,
            "Xác nhận khôi phục",
            f"Thao tác này sẽ:\n\n"
            f"- Đổi tên textReading hiện tại thành 'trimmed_audio'\n"
            f"- Đổi tên 'textReading - original' thành 'textReading'\n\n"
            f"Bạn chắc chắn muốn tiếp tục?",
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.capcut_log("Đang khôi phục audio gốc (restore folders)...")

            if os.path.isdir(trimmed_dir):
                old_trimmed = trimmed_dir + "_old"
                os.rename(trimmed_dir, old_trimmed)

            os.rename(text_reading_dir, trimmed_dir)
            os.rename(backup_dir, text_reading_dir)

            self.capcut_log(
                "Đã khôi phục textReading gốc, audio trimmed lưu ở trimmed_audio."
            )
            QMessageBox.information(
                self,
                "Hoàn tất khôi phục",
                "Đã khôi phục:\n- textReading - original -> textReading\n"
                "- textReading hiện tại -> trimmed_audio\n\n"
                "Mở lại CapCut để thấy audio gốc.",
            )
        except Exception as e:
            self.capcut_log(f"Lỗi khi restore thư mục: {e}")
            QMessageBox.critical(
                self, "Lỗi restore thư mục", f"Không thực hiện được: {e}"
            )

    def capcut_run_align_audio_to_subtitles(self):
        if capcut_srt is None:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.",
            )
            return

        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Vui lòng chọn thư mục Draft CapCut hoặc textReading trước.",
            )
            return
        if not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục không hợp lệ.")
            return

        try:
            self.capcut_log(f"Đang đọc draft trong: {project_dir}")
            data = capcut_srt.load_draft_json(project_dir)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không đọc được file draft: {e}")
            return

        subtitles = capcut_srt.extract_subtitles_with_audio(data, project_dir)
        subtitles_with_audio = [s for s in subtitles if s.get("audio")]
        if not subtitles_with_audio:
            QMessageBox.warning(
                self,
                "Không có mapping",
                "Không tìm thấy phụ đề nào có thông tin audio để canh thời gian.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Xác nhận canh audio",
            "Thao tác này sẽ chỉnh thời gian của các đoạn audio text-reading\n"
            "trong draft để trùng với thời gian phụ đề (start / end).\n\n"
            "Bạn nên đóng CapCut và đã backup Draft trước khi tiếp tục.\n\n"
            "Bạn chắc chắn muốn tiếp tục?",
        )
        if reply != QMessageBox.Yes:
            return

        tracks = data.get("tracks") or []
        if not tracks:
            QMessageBox.warning(
                self, "Không có tracks", "Không tìm thấy danh sách tracks trong draft."
            )
            return

        # Gọi lại toàn bộ logic align trong module gốc, để tránh copy code dài.
        # Ở đây, ta sử dụng lại hàm speedup_audio_to_fit, get_audio_duration_ms, ...
        # nhưng triển khai align đơn giản hơn: luôn canh theo phụ đề (giống mode 0).

        # Sắp xếp theo thời gian
        subtitles_with_audio.sort(key=lambda s: int(s.get("start", 0)))

        # Phát hiện đơn vị thời gian (ms hay µs)
        unit_scale = 1
        dur_samples = []
        for s in subtitles_with_audio[:50]:
            if "start" in s and "end" in s:
                try:
                    st = int(s["start"])
                    en = int(s["end"])
                    dur_samples.append(en - st)
                except Exception:
                    continue
        if dur_samples:
            dur_samples.sort()
            median_dur = dur_samples[len(dur_samples) // 2]
            if median_dur >= 50000:
                unit_scale = 1000

        def ms_to_unit(ms: int) -> int:
            return int(ms * unit_scale)

        def unit_to_ms(u: int) -> int:
            return int(u / unit_scale)

        align_mode = (
            self.capcut_align_mode_combo.currentIndex()
            if hasattr(self, "capcut_align_mode_combo") and self.capcut_align_mode_combo
            else 0
        )

        if align_mode == 1:
            margin_ms = 20
            margin_u = ms_to_unit(margin_ms)
            prev_end_u: Optional[int] = None

            for i, s in enumerate(subtitles_with_audio):
                if "start" not in s or "end" not in s:
                    continue

                orig_start_u = int(s["start"])
                orig_end_u = int(s["end"])

                next_start_u = None
                if i < len(subtitles_with_audio) - 1:
                    ns = subtitles_with_audio[i + 1]
                    if "start" in ns:
                        next_start_u = int(ns["start"])

                end_u = orig_end_u
                if next_start_u is not None and end_u > next_start_u:
                    end_u = next_start_u

                start_u = orig_start_u
                if end_u <= start_u:
                    prev_end_u = end_u
                    continue
                slot_u = max(0, end_u - start_u)

                a = s.get("audio") or {}
                audio_path = a.get(
                    "resolved_path"
                ) or capcut_srt.resolve_audio_path_from_original(
                    a.get("path"), project_dir
                )

                if not audio_path or not os.path.isfile(audio_path) or slot_u <= 0:
                    s["start"] = start_u
                    s["end"] = end_u
                    prev_end_u = s["end"]
                    continue

                audio_len_ms = capcut_srt.get_audio_duration_ms(audio_path)
                if audio_len_ms <= 0:
                    s["start"] = start_u
                    s["end"] = end_u
                    prev_end_u = s["end"]
                    continue

                audio_len_u = ms_to_unit(audio_len_ms)

                if audio_len_u <= slot_u + margin_u:
                    s["start"] = start_u
                    s["end"] = end_u
                    prev_end_u = s["end"]
                    continue

                if prev_end_u is not None and prev_end_u < start_u:
                    gap_u = start_u - prev_end_u
                    need_u = audio_len_u - slot_u
                    shift_u = min(gap_u, need_u)
                    start_u = start_u - shift_u
                    slot_u = max(0, end_u - start_u)

                if slot_u <= 0:
                    s["start"] = orig_start_u
                    s["end"] = end_u
                    prev_end_u = s["end"]
                    continue

                if audio_len_u > slot_u + margin_u:
                    target_ms = max(1, unit_to_ms(slot_u))
                    capcut_srt.speedup_audio_to_fit(audio_path, target_ms)

                s["start"] = start_u
                s["end"] = end_u
                if next_start_u is not None and s["end"] > next_start_u:
                    s["end"] = next_start_u
                prev_end_u = s["end"]

        # Tạo mapping audio_id -> (start, duration)
        audio_timing = {}
        for s in subtitles_with_audio:
            a = s.get("audio") or {}
            aid = a.get("id")
            if not aid:
                continue
            if "start" not in s or "end" not in s:
                continue
            start = int(s["start"])
            end = int(s["end"])
            if end <= start:
                continue
            slot_u = end - start

            audio_path = a.get(
                "resolved_path"
            ) or capcut_srt.resolve_audio_path_from_original(a.get("path"), project_dir)
            audio_len_ms = (
                capcut_srt.get_audio_duration_ms(audio_path)
                if (audio_path and os.path.isfile(audio_path))
                else -1
            )
            audio_len_u = ms_to_unit(audio_len_ms) if audio_len_ms > 0 else -1

            if audio_len_u > 0:
                duration_u = min(slot_u, audio_len_u)
            else:
                duration_u = slot_u

            audio_timing[aid] = (start, duration_u)

        if not audio_timing:
            QMessageBox.warning(
                self,
                "Không có thời gian",
                "Không tìm thấy cặp phụ đề/audio nào có đủ start/end để canh.",
            )
            return

        total_segments = 0
        updated_segments = 0

        for track in tracks:
            segments = track.get("segments") or []
            for seg in segments:
                mat_id = seg.get("material_id")
                if not mat_id or mat_id not in audio_timing:
                    continue
                total_segments += 1
                start, duration = audio_timing[mat_id]
                tr = seg.get("target_timerange") or {}
                tr["start"] = start
                tr["duration"] = duration
                seg["target_timerange"] = tr
                updated_segments += 1

        if updated_segments == 0:
            QMessageBox.information(
                self,
                "Không có gì để chỉnh",
                "Không tìm thấy segment audio nào trùng với ID audio trong mapping.",
            )
            return

        draft_path = None
        for name in ("draft_content.json", "draft_info.json"):
            candidate = os.path.join(project_dir, name)
            if os.path.isfile(candidate):
                draft_path = candidate
                break
        if not draft_path:
            QMessageBox.critical(
                self,
                "Không tìm thấy file draft",
                "Đã chỉnh dữ liệu trong bộ nhớ nhưng không tìm được file draft để ghi.",
            )
            return

        backup_path = draft_path + ".bak_align_audio"
        try:
            if not os.path.isfile(backup_path):
                import shutil

                shutil.copyfile(draft_path, backup_path)

            with open(draft_path, "w", encoding="utf-8") as f:
                import json

                json.dump(data, f, ensure_ascii=False, indent=2)

            self.capcut_log(
                f"Đã canh thời gian cho {updated_segments} đoạn audio (từ {total_segments} segment tìm thấy)."
            )
            QMessageBox.information(
                self,
                "Hoàn tất",
                f"Đã chỉnh thời gian cho {updated_segments} đoạn audio.\n"
                f"File gốc đã được backup tại:\n{backup_path}\n\n"
                "Mở lại CapCut để thấy thay đổi.",
            )
        except Exception as e:
            self.capcut_log(f"Lỗi khi ghi file draft: {e}")
            QMessageBox.critical(
                self, "Lỗi ghi draft", f"Không ghi được file draft: {e}"
            )

    def log_message(self, message: str):
        """Thêm message vào log (ưu tiên dùng chung nhật ký của app chính)."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%H:%M:%S")
        text = f"[{timestamp}] {message}"

        # Ưu tiên đẩy sang nhật ký chung của MainWindow (bên phải)
        # Dò lên chuỗi parent đến khi gặp object có append_log
        try:
            p = self.parent()
            while p is not None and not hasattr(p, "append_log"):
                p = p.parent()
            if p is not None and hasattr(p, "append_log"):
                # Thêm prefix để dễ nhận biết log đến từ SRT tool
                p.append_log(f"[SRT] {message}")
        except Exception:
            # Nếu vì lý do gì đó append_log lỗi, bỏ qua để không crash
            pass

        # Nếu trong tương lai widget này chạy standalone và có log_text riêng thì vẫn hỗ trợ
        if hasattr(self, "log_text") and self.log_text:
            self.log_text.append(text)
            self.log_text.verticalScrollBar().setValue(
                self.log_text.verticalScrollBar().maximum()
            )

        # In ra console để debug
        print(text)

    def clear_log(self):
        """Xóa log"""
        if hasattr(self, "log_text") and self.log_text:
            self.log_text.clear()
            self.log_message("Log đã được làm sạch")

    def save_log(self):
        """Lưu log ra file"""
        if not hasattr(self, "log_text") or not self.log_text:
            QMessageBox.warning(self, "Cảnh báo", "Không có log để lưu!")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Lưu Log", "srt_compare_log.txt", "Text Files (*.txt);;All Files (*)"
        )

        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(self.log_text.toPlainText())
                self.log_message(f"✅ Đã lưu log: {file_path}")
                QMessageBox.information(self, "Thành công", "Đã lưu log thành công!")
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Lỗi khi lưu log:\n{str(e)}")

    def update_system_status(self):
        """Cập nhật trạng thái hệ thống"""
        import platform

        status_text = f"""
Hệ thống: {platform.system()} {platform.release()}
Python: {platform.python_version()}
Trạng thái: Sẵn sàng
File 1: {'Đã chọn' if self.file1_path else 'Chưa chọn'}
File 2: {'Đã chọn' if self.file2_path else 'Chưa chọn'}
        """
        self.system_status.setPlainText(status_text.strip())

    # ===== Chức năng chia nhỏ phụ đề SRT =====

    def split_select_file(self):
        """Chọn file SRT để chia nhỏ"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file SRT để chia", "", "SRT Files (*.srt);;All Files (*)"
        )
        if file_path:
            path = Path(file_path)
            self.split_file_path = path
            self.split_file_label.setText(path.name)
            self.split_file_label.setStyleSheet(
                "color: #4caf50; font-size: 15px; font-weight: bold;"
            )
            self.log_message(f"Đã chọn file chia phụ đề: {path.name}")

    def split_subtitles(self):
        """Chia file SRT thành nhiều nhóm theo số lượng phụ đề"""
        # Ưu tiên file chọn riêng ở tab Chia phụ đề, nếu không có thì dùng File 1 ở tab So sánh
        source_path = self.split_file_path or self.file1_path
        if not source_path or not source_path.exists():
            QMessageBox.warning(
                self,
                "Lỗi",
                "Vui lòng chọn File 1 ở tab 'So sánh SRT' (hoặc chọn file trong tab 'Chia phụ đề') trước khi chia!",
            )
            return

        group_size = self.split_size_spin.value()
        if group_size <= 0:
            QMessageBox.warning(self, "Lỗi", "Số phụ đề mỗi nhóm phải lớn hơn 0!")
            return

        try:
            self.split_entries = parse_srt_file(source_path)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi đọc file SRT:\n{e}")
            return

        if not self.split_entries:
            QMessageBox.warning(self, "Lỗi", "File SRT không có phụ đề nào để chia!")
            return

        total = len(self.split_entries)
        self.split_chunks = []
        self.split_chunks_list.clear()
        self.split_preview.clear()
        # Cập nhật label hiển thị file đang chia
        self.split_file_label.setText(source_path.name)
        self.split_file_label.setStyleSheet(
            "color: #4caf50; font-size: 15px; font-weight: bold;"
        )

        start = 0
        group_index = 1
        while start < total:
            end = min(start + group_size, total)
            self.split_chunks.append((start, end))
            item_text = (
                f"Nhóm {group_index}: {start + 1} - {end} ({end - start} phụ đề)"
            )
            self.split_chunks_list.addItem(item_text)
            start = end
            group_index += 1

        if self.split_chunks:
            self.split_chunks_list.setCurrentRow(0)

        self.log_message(
            f"Đã chia {total} phụ đề thành {len(self.split_chunks)} nhóm, mỗi nhóm tối đa {group_size} phụ đề."
        )

        # Tự động đồng bộ sang tab Dịch bằng Gemini
        self._sync_split_to_translate()

    def update_split_preview(self, index: int):
        """Cập nhật nội dung nhóm được chọn để tiện copy"""
        if index < 0 or index >= len(self.split_chunks):
            self.split_preview.clear()
            return

        start, end = self.split_chunks[index]
        entries = self.split_entries[start:end]

        lines = []
        for entry in entries:
            lines.append(entry.to_srt_format())
        text = "\n".join(lines)

        self.split_preview.setPlainText(text)

    def split_copy_current_chunk(self):
        """Copy toàn bộ nội dung nhóm hiện tại vào clipboard"""
        text = self.split_preview.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Thông báo", "Không có nội dung để copy.")
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.log_message("Đã copy nội dung nhóm phụ đề hiện tại vào clipboard.")

    def _sync_split_to_translate(self):
        """Đồng bộ dữ liệu từ tab Chia phụ đề sang tab Dịch bằng Gemini"""
        if not self.split_entries or not self.split_chunks:
            return

        # Copy dữ liệu từ split sang translate
        self.translate_entries = self.split_entries.copy()
        self.translate_chunks = self.split_chunks.copy()

        # Cập nhật UI tab translate
        self.translate_chunks_list.clear()
        for idx, (start, end) in enumerate(self.translate_chunks):
            item_text = f"Nhóm {idx + 1}: {start + 1} - {end} ({end - start} phụ đề)"
            self.translate_chunks_list.addItem(item_text)

        # Cập nhật file label
        if self.split_file_path:
            self.translate_file_label.setText(self.split_file_path.name)
            self.translate_file_label.setStyleSheet(
                "color: #4caf50; font-weight: bold;"
            )

        # Reset progress và kết quả
        self.translate_progress.setMaximum(len(self.translate_chunks))
        self.translate_progress.setValue(0)
        self.translated_results = {}
        self.translate_retry_counts = {}
        self.auto_retry_pending_chunks = set()
        self.auto_retry_round = 0
        self._auto_retry_running = False
        self.translate_result_preview.clear()
        self.translate_original_preview.clear()

        # Bật nút bắt đầu dịch
        self.btn_start_translate.setEnabled(True)
        self.btn_save_to_file2.setEnabled(False)

        self.log_message(
            f"[Dịch] Đã đồng bộ {len(self.translate_chunks)} nhóm từ tab Chia phụ đề"
        )

    def create_translate_page(self) -> QWidget:
        """Tạo trang dịch SRT bằng Gemini API"""
        page = QWidget()
        page.setStyleSheet("background-color: #252525; color: #ffffff;")

        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Cột trái: Cấu hình và điều khiển
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setSpacing(15)

        # Group: Chọn file
        file_group = QGroupBox("1. Chọn file SRT nguồn")
        file_group.setStyleSheet(
            """
            QGroupBox {
                color: #ffffff;
                border: 2px solid #444;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """
        )
        file_layout = QVBoxLayout(file_group)

        file_select_layout = QHBoxLayout()
        self.translate_file_label = QLabel("Chưa chọn file")
        self.translate_file_label.setStyleSheet("color: #888888;")
        translate_file_btn = QPushButton("📁 Chọn file SRT")
        translate_file_btn.clicked.connect(self.translate_select_file)
        file_select_layout.addWidget(self.translate_file_label, 1)
        file_select_layout.addWidget(translate_file_btn)
        file_layout.addLayout(file_select_layout)

        left_layout.addWidget(file_group)

        # Group: Cấu hình chia nhỏ
        config_group = QGroupBox("2. Cấu hình chia nhỏ")
        config_group.setStyleSheet(file_group.styleSheet())
        config_layout = QVBoxLayout(config_group)

        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("Số phụ đề mỗi nhóm:"))
        self.translate_chunk_size_spin = QSpinBox()
        self.translate_chunk_size_spin.setRange(10, 1000)
        self.translate_chunk_size_spin.setValue(100)
        self.translate_chunk_size_spin.setToolTip(
            "Mỗi nhóm sẽ được gửi đi dịch một lần. Giá trị càng nhỏ càng an toàn nhưng chậm hơn."
        )
        size_layout.addWidget(self.translate_chunk_size_spin)
        size_layout.addStretch()
        config_layout.addLayout(size_layout)

        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.translate_model_edit = QLineEdit("gemini-advanced")
        self.translate_model_edit.setPlaceholderText("gemini-advanced")
        model_layout.addWidget(self.translate_model_edit)
        model_layout.addStretch()
        config_layout.addLayout(model_layout)

        # Prompt tùy chỉnh
        config_layout.addWidget(QLabel("Prompt bổ sung (tùy chọn):"))
        self.translate_custom_prompt_edit = QPlainTextEdit()
        self.translate_custom_prompt_edit.setPlaceholderText(
            "Nhập hướng dẫn bổ sung gửi kèm cho Gemini (để trống nếu không cần).\n"
            "Ví dụ: Dịch sang tiếng Việt thay vì tiếng Thái."
        )
        self.translate_custom_prompt_edit.setMaximumHeight(75)
        config_layout.addWidget(self.translate_custom_prompt_edit)

        left_layout.addWidget(config_group)

        # Group: Điều khiển
        control_group = QGroupBox("3. Điều khiển")
        control_group.setStyleSheet(file_group.styleSheet())
        control_layout = QVBoxLayout(control_group)

        self.btn_prepare_chunks = QPushButton("📋 Chuẩn bị các nhóm")
        self.btn_prepare_chunks.setStyleSheet(
            """
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """
        )
        self.btn_prepare_chunks.clicked.connect(self.translate_prepare_chunks)
        control_layout.addWidget(self.btn_prepare_chunks)

        self.btn_start_translate = QPushButton("🚀 Bắt đầu dịch")
        self.btn_start_translate.setStyleSheet(
            """
            QPushButton {
                background-color: #4caf50;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """
        )
        self.btn_start_translate.clicked.connect(self.translate_start_translation)
        self.btn_start_translate.setEnabled(False)
        control_layout.addWidget(self.btn_start_translate)

        self.btn_stop_translate = QPushButton("⏹ Dừng dịch")
        self.btn_stop_translate.setStyleSheet(
            """
            QPushButton {
                background-color: #f44336;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """
        )
        self.btn_stop_translate.clicked.connect(self.translate_stop_translation)
        self.btn_stop_translate.setEnabled(False)
        control_layout.addWidget(self.btn_stop_translate)

        self.btn_save_to_file2 = QPushButton("💾 Lưu vào File 2")
        self.btn_save_to_file2.setStyleSheet(
            """
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """
        )
        self.btn_save_to_file2.clicked.connect(self.translate_save_to_file2)
        self.btn_save_to_file2.setEnabled(False)
        control_layout.addWidget(self.btn_save_to_file2)

        # Tùy chọn so sánh timeline
        compare_mode_layout = QHBoxLayout()
        compare_mode_layout.addWidget(QLabel("Chế độ so sánh:"))
        self.translate_compare_mode_combo = QComboBox()
        self.translate_compare_mode_combo.addItem("Các nhóm đã dịch", "translated")
        self.translate_compare_mode_combo.addItem("Toàn bộ nhóm", "all")
        self.translate_compare_mode_combo.setCurrentIndex(0)
        compare_mode_layout.addWidget(self.translate_compare_mode_combo, 1)
        control_layout.addLayout(compare_mode_layout)

        # Tùy chọn tự động dịch lại nhóm lỗi
        self.translate_auto_retry_check = QCheckBox(
            "Tự động dịch lại khi lỗi timeline/thiếu phụ đề"
        )
        self.translate_auto_retry_check.setChecked(True)
        control_layout.addWidget(self.translate_auto_retry_check)

        retry_cfg_layout = QHBoxLayout()
        retry_cfg_layout.addWidget(QLabel("Ngưỡng lỗi mỗi nhóm:"))
        self.translate_retry_error_spin = QSpinBox()
        self.translate_retry_error_spin.setRange(0, 200)
        self.translate_retry_error_spin.setValue(0)
        retry_cfg_layout.addWidget(self.translate_retry_error_spin)
        retry_cfg_layout.addWidget(QLabel("Tối đa retry:"))
        self.translate_retry_max_spin = QSpinBox()
        self.translate_retry_max_spin.setRange(1, 50)
        self.translate_retry_max_spin.setValue(3)
        self.translate_retry_max_spin.setToolTip(
            "Số lần tự động dịch lại tối đa cho mỗi nhóm bị lỗi (1-50)"
        )
        retry_cfg_layout.addWidget(self.translate_retry_max_spin)
        retry_cfg_layout.addStretch()
        control_layout.addLayout(retry_cfg_layout)

        # Chế độ lưu vào File 2
        sync_mode_layout = QHBoxLayout()
        sync_mode_layout.addWidget(QLabel("Chế độ lưu File 2:"))
        self.translate_save_mode_combo = QComboBox()
        self.translate_save_mode_combo.addItem(
            "Đồng bộ an toàn (Giữ gốc nếu lỗi số dòng)", "safe"
        )
        self.translate_save_mode_combo.addItem(
            "Tạo lại (Ghi đè bản dịch bất chấp lỗi)", "force"
        )
        sync_mode_layout.addWidget(self.translate_save_mode_combo, 1)
        control_layout.addLayout(sync_mode_layout)

        # Nút so sánh timeline
        self.btn_compare_timeline = QPushButton("🔍 So sánh Timeline")
        self.btn_compare_timeline.setStyleSheet(
            """
            QPushButton {
                background-color: #9C27B0;
                color: white;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """
        )
        self.btn_compare_timeline.clicked.connect(self.translate_compare_timeline)
        self.btn_compare_timeline.setEnabled(False)
        control_layout.addWidget(self.btn_compare_timeline)

        # Label hiển thị kết quả so sánh
        self.translate_compare_label = QLabel("")
        self.translate_compare_label.setWordWrap(True)
        self.translate_compare_label.setStyleSheet(
            "color: #4caf50; font-size: 12px; padding: 5px;"
        )
        control_layout.addWidget(self.translate_compare_label)

        left_layout.addWidget(control_group)

        # Xóa phần Thông tin (Quy trình) theo yêu cầu

        left_layout.addStretch()

        # Cột giữa: Danh sách các nhóm
        middle_col = QWidget()
        middle_layout = QVBoxLayout(middle_col)
        middle_layout.setSpacing(15)

        chunks_group = QGroupBox("Các nhóm phụ đề")
        chunks_group.setStyleSheet(file_group.styleSheet())
        chunks_layout = QVBoxLayout(chunks_group)

        self.translate_chunks_list = QListWidget()
        self.translate_chunks_list.setStyleSheet(
            """
            QListWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 6px 8px;
            }
            QListWidget::item:selected {
                background-color: #2196F3;
                color: #ffffff;
            }
            QListWidget::item:hover {
                background-color: #333333;
            }
        """
        )
        self.translate_chunks_list.currentRowChanged.connect(
            self.translate_update_preview
        )
        chunks_layout.addWidget(self.translate_chunks_list)

        self.translate_progress = QProgressBar()
        self.translate_progress.setRange(0, 100)
        self.translate_progress.setValue(0)
        self.translate_progress.setTextVisible(True)
        chunks_layout.addWidget(self.translate_progress)

        middle_layout.addWidget(chunks_group)

        # Cột phải: Nội dung nhóm (gốc và đã dịch)
        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setSpacing(15)

        # Nội dung gốc
        original_group = QGroupBox("Nội dung gốc")
        original_group.setStyleSheet(file_group.styleSheet())
        original_layout = QVBoxLayout(original_group)

        self.translate_original_preview = QPlainTextEdit()
        self.translate_original_preview.setReadOnly(True)
        self.translate_original_preview.setStyleSheet(
            """
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444;
                font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                font-size: 14px;
            }
        """
        )
        original_layout.addWidget(self.translate_original_preview)

        copy_original_btn = QPushButton("📋 Copy nội dung gốc")
        copy_original_btn.clicked.connect(self.translate_copy_original)
        original_layout.addWidget(copy_original_btn)

        right_layout.addWidget(original_group)

        # Nội dung đã dịch
        translated_group = QGroupBox("Nội dung đã dịch (tiếng Thái)")
        translated_group.setStyleSheet(file_group.styleSheet())
        translated_layout = QVBoxLayout(translated_group)

        self.translate_result_preview = QPlainTextEdit()
        self.translate_result_preview.setStyleSheet(
            """
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #4caf50;
                border: 1px solid #444;
                font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                font-size: 14px;
            }
        """
        )
        translated_layout.addWidget(self.translate_result_preview)

        # Grid layout cho 4 nút
        btn_grid = QGridLayout()
        btn_grid.setSpacing(10)

        self.btn_apply_translation = QPushButton("✅ Áp dụng")
        self.btn_apply_translation.setStyleSheet(
            """
            QPushButton {
                background-color: #4caf50; color: white; padding: 8px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background-color: #45a049; }
        """
        )
        self.btn_apply_translation.clicked.connect(self.translate_apply_current_chunk)
        btn_grid.addWidget(self.btn_apply_translation, 0, 0)

        self.btn_retry_chunk = QPushButton("🔄 Dịch lại")
        self.btn_retry_chunk.setStyleSheet(
            """
            QPushButton {
                background-color: #FF9800; color: white; padding: 8px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background-color: #F57C00; }
        """
        )
        self.btn_retry_chunk.clicked.connect(self.translate_retry_current_chunk)
        btn_grid.addWidget(self.btn_retry_chunk, 0, 1)

        self.btn_compare_chunk = QPushButton("🔍 So sánh")
        self.btn_compare_chunk.setStyleSheet(
            """
            QPushButton {
                background-color: #9C27B0; color: white; padding: 8px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background-color: #7B1FA2; }
        """
        )
        self.btn_compare_chunk.clicked.connect(self.translate_compare_current_chunk)
        btn_grid.addWidget(self.btn_compare_chunk, 1, 0)

        self.btn_sync_chunk = QPushButton("💾 Đồng bộ")
        self.btn_sync_chunk.setStyleSheet(
            """
            QPushButton {
                background-color: #2196F3; color: white; padding: 8px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background-color: #1976D2; }
        """
        )
        self.btn_sync_chunk.clicked.connect(self.translate_sync_current_chunk)
        btn_grid.addWidget(self.btn_sync_chunk, 1, 1)

        translated_layout.addLayout(btn_grid)

        right_layout.addWidget(translated_group)

        # Ghép 3 cột
        main_layout.addWidget(left_col, 1)
        main_layout.addWidget(middle_col, 1)
        main_layout.addWidget(right_col, 2)

        return page

    def translate_select_file(self):
        """Chọn file SRT để dịch"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file SRT để dịch", "", "SRT Files (*.srt);;All Files (*)"
        )
        if file_path:
            path = Path(file_path)
            try:
                self.translate_entries = parse_srt_file(path)
                self.translate_file_label.setText(path.name)
                self.translate_file_label.setStyleSheet(
                    "color: #4caf50; font-weight: bold;"
                )

                # Nếu file này trùng với file đã chia ở tab Chia phụ đề, tự động đồng bộ
                if (
                    self.split_file_path
                    and path.resolve() == self.split_file_path.resolve()
                ):
                    self._sync_split_to_translate()
                    self.log_message(
                        f"[Dịch] Đã chọn file: {path.name} - Tự động đồng bộ {len(self.translate_chunks)} nhóm từ tab Chia phụ đề"
                    )
                else:
                    self.log_message(
                        f"[Dịch] Đã chọn file: {path.name} ({len(self.translate_entries)} phụ đề)"
                    )

                QMessageBox.information(
                    self,
                    "Thành công",
                    f"Đã load {len(self.translate_entries)} phụ đề từ file.",
                )
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Không thể đọc file SRT:\n{str(e)}")

    def translate_prepare_chunks(self):
        """Chia file SRT thành các nhóm nhỏ để dịch"""
        if not self.translate_entries:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn file SRT trước!")
            return

        chunk_size = self.translate_chunk_size_spin.value()
        total = len(self.translate_entries)

        self.translate_chunks = []
        self.translate_chunks_list.clear()
        self.translated_results = {}
        self.translate_retry_counts = {}
        self.auto_retry_pending_chunks = set()
        self.auto_retry_round = 0
        self._auto_retry_running = False

        start = 0
        group_index = 0
        while start < total:
            end = min(start + chunk_size, total)
            self.translate_chunks.append((start, end))
            item_text = (
                f"Nhóm {group_index + 1}: {start + 1} - {end} ({end - start} phụ đề)"
            )
            self.translate_chunks_list.addItem(item_text)
            start = end
            group_index += 1

        self.translate_progress.setMaximum(len(self.translate_chunks))
        self.translate_progress.setValue(0)
        self.btn_start_translate.setEnabled(True)
        self.btn_save_to_file2.setEnabled(False)

        self.log_message(
            f"[Dịch] Đã chia {total} phụ đề thành {len(self.translate_chunks)} nhóm, mỗi nhóm tối đa {chunk_size} phụ đề."
        )

        # Hiển thị nhóm đầu tiên
        if self.translate_chunks:
            self.translate_chunks_list.setCurrentRow(0)

    def translate_update_preview(self, index: int):
        """Cập nhật preview khi chọn nhóm"""
        if index < 0 or index >= len(self.translate_chunks):
            self.translate_original_preview.clear()
            self.translate_result_preview.clear()
            return

        start, end = self.translate_chunks[index]
        entries = self.translate_entries[start:end]

        # Hiển thị nội dung gốc
        lines = []
        for entry in entries:
            lines.append(entry.to_srt_format())
        self.translate_original_preview.setPlainText("\n".join(lines))

        # Hiển thị kết quả đã dịch nếu có
        if index in self.translated_results:
            self.translate_result_preview.setPlainText(self.translated_results[index])
            self.translate_result_preview.setStyleSheet(
                """
                QPlainTextEdit {
                    background-color: #1e1e1e;
                    color: #4caf50;
                    border: 1px solid #444;
                    font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                    font-size: 14px;
                }
            """
            )
        else:
            self.translate_result_preview.clear()
            self.translate_result_preview.setStyleSheet(
                """
                QPlainTextEdit {
                    background-color: #1e1e1e;
                    color: #888888;
                    border: 1px solid #444;
                    font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                    font-size: 14px;
                }
            """
            )

    def translate_copy_original(self):
        """Copy nội dung gốc vào clipboard"""
        text = self.translate_original_preview.toPlainText()
        if text.strip():
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self.log_message("[Dịch] Đã copy nội dung gốc vào clipboard.")

    def translate_get_prompt_template(self) -> str:
        """Trả về prompt template cho Gemini"""
        template = """Bạn là một trợ lý dịch thuật phụ đề chuyên nghiệp.

Nhiệm vụ

Nhận đầu vào là phụ đề định dạng SRT, được trích xuất tự động từ audio nên có thể tồn tại lỗi nhận dạng giọng nói như sai từ, thiếu từ, ngắt câu không tự nhiên.

Trước khi dịch, âm thầm chỉnh sửa các lỗi nhận dạng giọng nói dựa trên ngữ cảnh hợp lý của lời thoại.

Xử lý tên riêng, tên địa danh, thuật ngữ chuyên ngành một cách cẩn thận để đảm bảo độ chính xác cao nhất có thể.

Với tên người, đặc biệt là tên Hán Việt, hãy chọn cách phiên âm hoặc chuyển sang tiếng Thái phù hợp.

Đảm bảo đồng nhất tuyệt đối cách gọi của từng nhân vật trong toàn bộ nội dung.

Không suy đoán hoặc thêm nội dung mới ngoài những gì có thể suy ra hợp lý từ ngữ cảnh.

Sau khi chỉnh sửa lỗi, dịch toàn bộ nội dung sang tiếng Thái tự nhiên, đúng ngữ cảnh hội thoại, phù hợp với lời nói.

Yêu cầu bắt buộc

Giữ nguyên hoàn toàn

Số thứ tự subtitle

Mốc thời gian

Chỉ thay đổi nội dung text của subtitle, không thay đổi cấu trúc.

Không thêm, không xóa bất kỳ dòng subtitle nào.

Không sử dụng các ký tự sau trong nội dung phụ đề

Dấu ngoặc tròn

Dấu ngoặc vuông

Dấu ngoặc kép

Dấu chấm

Văn phong tiếng Thái

Tự nhiên

Trung lập

Phù hợp với lời thoại nói thường ngày

Định dạng đầu ra

Chỉ trả về duy nhất nội dung SRT đã dịch

Toàn bộ kết quả phải được bọc trong một khối code block

Không có bất kỳ văn bản nào bên ngoài code block

Ví dụ định dạng đầu ra

```srt
1
00:00:01,000 --> 00:00:03,000
ข้อความภาษาไทย

2
00:00:03,500 --> 00:00:06,000
ข้อความภาษาไทย
```

Dưới đây là phụ đề cần dịch:

{}
"""
        # Chèn prompt tùy chỉnh của người dùng trước phần SRT nếu có
        if hasattr(self, "translate_custom_prompt_edit"):
            custom_text = self.translate_custom_prompt_edit.toPlainText().strip()
            if custom_text:
                marker = "Dưới đây là phụ đề cần dịch:"
                template = template.replace(
                    marker,
                    f"HƯỚNG DẪN BỔ SUNG TỪ NGƯỜI DÙNG:\n{custom_text}\n\n{marker}",
                    1,
                )
        return template

    def translate_start_translation(self):
        """Bắt đầu dịch các nhóm"""
        if not self.translate_chunks:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chuẩn bị các nhóm trước!")
            return

        if not self.gemini_sidecar:
            QMessageBox.warning(self, "Lỗi", "Không có kết nối đến Gemini Sidecar!")
            return

        # Kiểm tra sidecar
        ok, msg = self.gemini_sidecar.health()
        if not ok:
            self.log_message("[Dịch] Sidecar chưa chạy, đang khởi động...")
            ok_start, start_msg = self.gemini_sidecar.start()
            if not ok_start:
                QMessageBox.warning(
                    self, "Lỗi", f"Không thể khởi động sidecar:\n{start_msg}"
                )
                return
            self.log_message(f"[Dịch] {start_msg}")

        # Kiểm tra danh sách model — nếu rỗng thì cookie chưa hợp lệ, dừng ngay
        ok_models, model_ids = self.gemini_sidecar.list_models(timeout=8)
        if not model_ids:
            QMessageBox.critical(
                self,
                "Lỗi cookie Gemini",
                "Sidecar không có model nào khả dụng (Available models: []).\n\n"
                "Nguyên nhân: Cookie __Secure-1PSID / __Secure-1PSIDTS chưa được cấu hình "
                "hoặc đã hết hạn.\n\n"
                "Cách khắc phục:\n"
                "1. Vào tab 🤖 Gemini API → nhập lại cookie mới.\n"
                '2. Nhấn "Stop sidecar" → "Start sidecar".\n'
                '3. Nhấn "Kiểm tra kết nối" để xác nhận có model.\n\n'
                "Dịch đã bị huỷ để tránh retry vô ích.",
            )
            self.log_message(
                "[Dịch] ❌ Huỷ dịch: sidecar không có model nào. Hãy cập nhật cookie Gemini."
            )
            return

        # Chuẩn bị các chunk chưa được dịch
        chunks_to_translate = []
        prompt_template = self.translate_get_prompt_template()

        for idx, (start, end) in enumerate(self.translate_chunks):
            if idx not in self.translated_results:
                entries = self.translate_entries[start:end]
                srt_content = "\n".join([e.to_srt_format() for e in entries])
                full_prompt = prompt_template.format(srt_content)
                chunks_to_translate.append((idx, full_prompt))

        if not chunks_to_translate:
            QMessageBox.information(self, "Thông báo", "Tất cả các nhóm đã được dịch!")
            return

        # Bắt đầu worker
        model = self.translate_model_edit.text().strip() or "gemini-advanced"
        self.translate_worker = GeminiTranslateWorker(
            self.gemini_sidecar, chunks_to_translate, model
        )

        self.translate_worker.log.connect(self.log_message)
        self.translate_worker.progress.connect(self.translate_on_progress)
        self.translate_worker.chunk_done.connect(self.translate_on_chunk_done)
        self.translate_worker.finished_all.connect(self.translate_on_finished)
        self.translate_worker.failed.connect(self.translate_on_failed)
        # Cleanup worker sau khi kết thúc
        self.translate_worker.finished.connect(
            lambda: setattr(self, "translate_worker", None)
        )

        self.btn_start_translate.setEnabled(False)
        self.btn_stop_translate.setEnabled(True)
        self.btn_prepare_chunks.setEnabled(False)

        self.translate_worker.start()
        self.log_message(
            f"[Dịch] Bắt đầu dịch {len(chunks_to_translate)} nhóm còn lại..."
        )

    def translate_on_progress(self, current: int, total: int):
        """Cập nhật tiến trình"""
        self.translate_progress.setValue(current)

    def translate_on_chunk_done(self, chunk_index: int, translated_srt: str):
        """Xử lý khi một nhóm được dịch xong"""
        self.translated_results[chunk_index] = translated_srt

        # Cập nhật hiển thị nếu đang xem nhóm này
        current_row = self.translate_chunks_list.currentRow()
        if current_row == chunk_index:
            self.translate_result_preview.setPlainText(translated_srt)
            self.translate_result_preview.setStyleSheet(
                """
                QPlainTextEdit {
                    background-color: #1e1e1e;
                    color: #4caf50;
                    border: 1px solid #444;
                    font-family: 'Menlo', 'Consolas', 'Courier New', monospace;
                    font-size: 14px;
                }
            """
            )

        # ==========================================
        # KIỂM TRA LỖI NGAY LẬP TỨC ĐỂ RETRY TỨC THỜI
        # ==========================================
        auto_retry_enabled = (
            hasattr(self, "translate_auto_retry_check")
            and self.translate_auto_retry_check.isChecked()
        )
        if auto_retry_enabled:
            threshold = (
                self.translate_retry_error_spin.value()
                if hasattr(self, "translate_retry_error_spin")
                else 0
            )
            max_retry = (
                self.translate_retry_max_spin.value()
                if hasattr(self, "translate_retry_max_spin")
                else 2
            )

            analysis = self._analyze_chunk_timeline(chunk_index)
            need_retry = analysis["missing_subtitles"] or (
                analysis["error_count"] > threshold
            )

            if need_retry:
                current_retry = self.translate_retry_counts.get(chunk_index, 0)
                if current_retry < max_retry:
                    self.translate_retry_counts[chunk_index] = current_retry + 1
                    self.log_message(
                        f"[Dịch][AutoRetry] Nhóm {chunk_index + 1} lỗi (Số lỗi={analysis['error_count']}, Lệch={analysis['missing_subtitles']}). Lập tức đưa vào cuối hàng đợi (Lần {current_retry + 1}/{max_retry})"
                    )

                    # Cập nhật UI hiển thị đang lỗi
                    item = self.translate_chunks_list.item(chunk_index)
                    if item:
                        text = item.text().replace("✅ ", "")
                        item.setText(f"⚠️ {text}")

                    # Đẩy lại vào hàng đợi của worker hiện tại
                    if self.translate_worker and hasattr(
                        self.translate_worker, "chunks"
                    ):
                        # Cần build lại prompt hoặc tái sử dụng srt content gốc
                        prompt = self._build_translate_prompt_for_chunk(chunk_index)
                        self.translate_worker.chunks.append((chunk_index, prompt))
                    return
                else:
                    self.log_message(
                        f"[Dịch][AutoRetry] Nhóm {chunk_index + 1} lỗi nhưng ĐÃ ĐẠT MAX RETRY ({max_retry}). Chấp nhận kết quả."
                    )

        # ==========================================

        # Cập nhật trạng thái thành công trong list
        item = self.translate_chunks_list.item(chunk_index)
        if item:
            text = item.text().replace("⚠️ ", "")
            if "✅" not in text:
                item.setText(f"✅ {text}")

        # Bật nút so sánh timeline và cho phép lưu từng phần vào File 2
        self.btn_compare_timeline.setEnabled(True)
        self.btn_save_to_file2.setEnabled(True)

    def translate_on_finished(self):
        """Xử lý khi dịch xong tất cả"""
        self.btn_start_translate.setEnabled(True)
        self.btn_stop_translate.setEnabled(False)
        self.btn_prepare_chunks.setEnabled(True)
        self.btn_save_to_file2.setEnabled(True)
        self.btn_compare_timeline.setEnabled(True)

        # Tự động so sánh timeline
        self.translate_compare_timeline()

        # Lên lịch tự động retry nếu có lỗi và chức năng đang bật
        if self._schedule_auto_retry_if_needed():
            return

        self.log_message("[Dịch] Đã hoàn thành dịch tất cả các nhóm!")
        QMessageBox.information(
            self,
            "Hoàn thành",
            "Đã dịch xong tất cả các nhóm!\nĐã tự động so sánh timeline.",
        )

    def translate_on_failed(self, error_msg: str):
        """Xử lý khi có lỗi nghiêm trọng (sidecar không khởi động được, v.v.)"""
        self.btn_start_translate.setEnabled(True)
        self.btn_stop_translate.setEnabled(False)
        self.btn_prepare_chunks.setEnabled(True)

        self.log_message(f"[Dịch] ❌ Lỗi: {error_msg}")

        # Thử auto-retry nếu tính năng đang bật (xử lý các chunk chưa dịch được)
        if self._schedule_auto_retry_if_needed():
            return

        QMessageBox.critical(self, "Lỗi dịch thuật", f"Có lỗi xảy ra:\n{error_msg}")

    def translate_stop_translation(self):
        """Dừng dịch"""
        if self.translate_worker and self.translate_worker.isRunning():
            self.translate_worker.stop()
            if not self.translate_worker.wait(5000):  # Đợi tối đa 5 giây
                self.translate_worker.terminate()  # Force terminate nếu không dừng được
                self.translate_worker.wait(1000)
            self.translate_worker = None
            self.log_message("[Dịch] Đã dừng dịch thuật.")

        self.btn_start_translate.setEnabled(True)
        self.btn_stop_translate.setEnabled(False)
        self.btn_prepare_chunks.setEnabled(True)

    def translate_apply_current_chunk(self):
        """Áp dụng dịch cho nhóm hiện tại từ kết quả trong ô nhập"""
        current_row = self.translate_chunks_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn một nhóm!")
            return

        translated_text = self.translate_result_preview.toPlainText().strip()
        if not translated_text:
            QMessageBox.warning(self, "Lỗi", "Nội dung đã dịch trống!")
            return

        self.translated_results[current_row] = translated_text

        # Cập nhật trạng thái trong list
        item = self.translate_chunks_list.item(current_row)
        if item:
            text = item.text()
            if "✅" not in text:
                item.setText(f"✅ {text}")

        self.log_message(f"[Dịch] Đã áp dụng dịch cho nhóm {current_row + 1}")
        QMessageBox.information(
            self, "Thành công", f"Đã áp dụng dịch cho nhóm {current_row + 1}!"
        )

    def translate_retry_current_chunk(self):
        """Dịch lại nhóm hiện tại"""
        current_row = self.translate_chunks_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn một nhóm!")
            return

        if not self.gemini_sidecar:
            QMessageBox.warning(self, "Lỗi", "Không có kết nối đến Gemini Sidecar!")
            return

        # Xóa kết quả cũ
        if current_row in self.translated_results:
            del self.translated_results[current_row]

        # Cập nhật UI
        item = self.translate_chunks_list.item(current_row)
        if item:
            text = item.text().replace("✅ ", "")
            item.setText(text)

        start, end = self.translate_chunks[current_row]
        entries = self.translate_entries[start:end]
        srt_content = "\n".join([e.to_srt_format() for e in entries])
        prompt_template = self.translate_get_prompt_template()
        full_prompt = prompt_template.format(srt_content)

        model = self.translate_model_edit.text().strip() or "gemini-advanced"

        self.log_message(f"[Dịch] Đang dịch lại nhóm {current_row + 1}...")

        # Dừng worker thủ công cũ nếu có
        if (
            hasattr(self, "manual_translate_worker")
            and self.manual_translate_worker
            and self.manual_translate_worker.isRunning()
        ):
            self.manual_translate_worker.stop()
            self.manual_translate_worker.wait(1000)

        # Tạo worker cho một chunk duy nhất
        self.manual_translate_worker = GeminiTranslateWorker(
            self.gemini_sidecar, [(current_row, full_prompt)], model
        )

        def on_done(idx, result):
            self.translated_results[idx] = result
            if self.translate_chunks_list.currentRow() == idx:
                self.translate_result_preview.setPlainText(result)
            item = self.translate_chunks_list.item(idx)
            if item:
                text = item.text()
                if "✅" not in text:
                    item.setText(f"✅ {text}")
            self.log_message(f"[Dịch] Đã dịch xong nhóm {idx + 1}")
            QMessageBox.information(self, "Thành công", f"Đã dịch xong nhóm {idx + 1}!")

        def on_fail(msg):
            self.log_message(f"[Dịch] ❌ Lỗi dịch lại nhóm {current_row + 1}: {msg}")
            QMessageBox.critical(self, "Lỗi", f"Không thể dịch lại:\n{msg}")

        self.manual_translate_worker.chunk_done.connect(on_done)
        self.manual_translate_worker.failed.connect(on_fail)
        # Cleanup sau khi kết thúc
        self.manual_translate_worker.finished.connect(
            lambda: setattr(self, "manual_translate_worker", None)
        )
        self.manual_translate_worker.start()

    def translate_compare_current_chunk(self):
        """So sánh timeline của nhóm hiện tại"""
        current_row = self.translate_chunks_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn một nhóm!")
            return

        analysis = self._analyze_chunk_timeline(current_row)
        if not analysis.get("valid"):
            QMessageBox.warning(self, "Chưa dịch", "Nhóm này chưa được dịch!")
            return

        error_count = analysis.get("error_count", 0)
        detail = analysis.get("detail", "")
        if error_count == 0 and not analysis.get("missing_subtitles"):
            QMessageBox.information(
                self,
                "So sánh",
                f"✅ Khớp hoàn toàn!\nSố dòng: {analysis.get('original_count')}",
            )
        else:
            QMessageBox.warning(
                self, "So sánh", f"⚠️ Có {error_count} lỗi timeline!\nChi tiết: {detail}"
            )

    def translate_sync_current_chunk(self):
        """Đồng bộ timeline của nhóm hiện tại về timestamp của bản gốc"""
        current_row = self.translate_chunks_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn một nhóm!")
            return

        translated_text = self.translate_result_preview.toPlainText().strip()
        if not translated_text:
            QMessageBox.warning(self, "Lỗi", "Chưa có nội dung dịch!")
            return

        chunk_entries = self._parse_srt_from_text(translated_text)
        start, end = self.translate_chunks[current_row]
        original_chunk = self.translate_entries[start:end]

        if len(chunk_entries) != len(original_chunk):
            QMessageBox.warning(
                self,
                "Lỗi",
                f"Số lượng phụ đề không khớp ({len(original_chunk)} gốc vs {len(chunk_entries)} dịch).\nKhông thể đồng bộ timeline một cách tự động, vui lòng tự sửa text hoặc dịch lại (tạo lại)!",
            )
            return

        # Đồng bộ timestamp và index từ gốc
        synced_lines = []
        for i, orig in enumerate(original_chunk):
            synced_lines.append(str(orig.index))
            start_time = orig.start_time or "00:00:00,000"
            end_time = orig.end_time or "00:00:00,000"
            synced_lines.append(f"{start_time} --> {end_time}")
            for line in chunk_entries[i].content:
                synced_lines.append(line)
            synced_lines.append("")

        synced_text = "\n".join(synced_lines).strip()
        self.translate_result_preview.setPlainText(synced_text)
        self.translated_results[current_row] = synced_text

        item = self.translate_chunks_list.item(current_row)
        if item and "✅" not in item.text():
            item.setText(f"✅ {item.text()}")

        QMessageBox.information(self, "Thành công", "Đã đồng bộ timeline về bản gốc!")

    def _get_compare_chunk_indices(self):
        """Lấy danh sách nhóm cần so sánh theo option người dùng chọn"""
        mode = "translated"
        if hasattr(self, "translate_compare_mode_combo"):
            mode = self.translate_compare_mode_combo.currentData() or "translated"

        if mode == "all":
            return list(range(len(self.translate_chunks)))

        # mặc định: chỉ các nhóm đã dịch
        return sorted(self.translated_results.keys())

    def _analyze_chunk_timeline(self, chunk_idx: int) -> dict:
        """Phân tích timeline 1 nhóm, trả về số lỗi và trạng thái thiếu phụ đề"""
        if chunk_idx >= len(self.translate_chunks):
            return {
                "valid": False,
                "error_count": 0,
                "missing_subtitles": False,
                "detail": "Nhóm không hợp lệ",
            }

        start, end = self.translate_chunks[chunk_idx]
        original_entries = self.translate_entries[start:end]

        if chunk_idx not in self.translated_results:
            return {
                "valid": False,
                "error_count": len(original_entries),
                "missing_subtitles": True,
                "detail": "Chưa có bản dịch",
            }

        translated_srt = self.translated_results[chunk_idx]
        translated_entries = self._parse_srt_from_text(translated_srt)

        error_count = 0
        detail = []
        missing_subtitles = len(original_entries) != len(translated_entries)

        if missing_subtitles:
            detail.append(
                f"Số lượng khác ({len(original_entries)} vs {len(translated_entries)})"
            )

        max_len = max(len(original_entries), len(translated_entries))
        for i in range(max_len):
            if i >= len(original_entries) or i >= len(translated_entries):
                error_count += 1
                continue

            orig = original_entries[i]
            trans = translated_entries[i]
            has_error = False

            if orig.index != trans.index:
                has_error = True
                if len(detail) < 3:
                    detail.append(f"STT {orig.index} -> {trans.index}")

            if (orig.start_time or "").strip() != (trans.start_time or "").strip():
                has_error = True
                if len(detail) < 3:
                    detail.append(f"Start lệch tại STT {orig.index}")

            if (orig.end_time or "").strip() != (trans.end_time or "").strip():
                has_error = True
                if len(detail) < 3:
                    detail.append(f"End lệch tại STT {orig.index}")

            if has_error:
                error_count += 1

        return {
            "valid": True,
            "error_count": error_count,
            "missing_subtitles": missing_subtitles,
            "detail": "; ".join(detail),
            "original_count": len(original_entries),
            "translated_count": len(translated_entries),
        }

    def _build_translate_prompt_for_chunk(self, chunk_idx: int) -> str:
        start, end = self.translate_chunks[chunk_idx]
        entries = self.translate_entries[start:end]
        srt_content = "\n".join([e.to_srt_format() for e in entries])
        return self.translate_get_prompt_template().format(srt_content)

    def _start_auto_retry_for_chunks(self, chunk_indices: list):
        """Tự động dịch lại các nhóm lỗi theo danh sách"""
        if not chunk_indices:
            self._auto_retry_running = False
            return

        model = self.translate_model_edit.text().strip() or "gemini-advanced"
        chunks_to_translate = [
            (idx, self._build_translate_prompt_for_chunk(idx)) for idx in chunk_indices
        ]

        self._auto_retry_running = True
        self.translate_worker = GeminiTranslateWorker(
            self.gemini_sidecar, chunks_to_translate, model
        )
        self.translate_worker.log.connect(self.log_message)
        self.translate_worker.progress.connect(self.translate_on_progress)
        self.translate_worker.chunk_done.connect(self.translate_on_chunk_done)
        self.translate_worker.finished_all.connect(self.translate_on_finished)
        self.translate_worker.failed.connect(self.translate_on_failed)
        self.translate_worker.finished.connect(
            lambda: setattr(self, "translate_worker", None)
        )

        self.btn_start_translate.setEnabled(False)
        self.btn_stop_translate.setEnabled(True)
        self.btn_prepare_chunks.setEnabled(False)

        self.translate_worker.start()
        self.log_message(
            f"[Dịch][AutoRetry] Đang retry {len(chunk_indices)} nhóm lỗi..."
        )

    def _schedule_auto_retry_if_needed(self):
        """Lên lịch tự động retry sau khi hoàn tất một vòng dịch"""
        self._auto_retry_running = False

        if (
            not hasattr(self, "translate_auto_retry_check")
            or not self.translate_auto_retry_check.isChecked()
        ):
            self.log_message("[Dịch][AutoRetry] Tính năng tự động dịch lại đang TẮT.")
            return False

        threshold = (
            self.translate_retry_error_spin.value()
            if hasattr(self, "translate_retry_error_spin")
            else 0
        )
        max_retry = (
            self.translate_retry_max_spin.value()
            if hasattr(self, "translate_retry_max_spin")
            else 2
        )

        self.log_message(
            f"[Dịch][AutoRetry] Bắt đầu quét lỗi (Ngưỡng lỗi mỗi nhóm > {threshold}, Tối đa vòng = {max_retry})..."
        )

        retry_candidates = []
        for idx in range(len(self.translate_chunks)):
            if idx in self.translated_results:
                analysis = self._analyze_chunk_timeline(idx)
                need_retry = analysis["missing_subtitles"] or (
                    analysis["error_count"] > threshold
                )
            else:
                # Nếu chưa có kết quả dịch (bị lỗi timeout, lỗi API...), chắc chắn phải dịch lại
                analysis = {"error_count": "N/A", "missing_subtitles": True}
                need_retry = True

            if not need_retry:
                continue

            current_retry = self.translate_retry_counts.get(idx, 0)
            if current_retry >= max_retry:
                self.log_message(
                    f"[Dịch][AutoRetry] Nhóm {idx + 1} có lỗi nhưng ĐÃ ĐẠT MAX RETRY ({current_retry}/{max_retry}), bỏ qua."
                )
                continue

            self.translate_retry_counts[idx] = current_retry + 1
            retry_candidates.append(idx)
            self.log_message(
                f"[Dịch][AutoRetry] Phát hiện nhóm {idx + 1} lỗi (Số lỗi={analysis['error_count']}, Lệch số dòng={analysis['missing_subtitles']}), đưa vào danh sách retry lần thứ {current_retry + 1}"
            )

        if retry_candidates:
            self.auto_retry_round += 1
            self.log_message(
                f"[Dịch][AutoRetry] Vòng {self.auto_retry_round}: Tiến hành retry {len(retry_candidates)} nhóm: {', '.join(str(x + 1) for x in retry_candidates)}"
            )
            QTimer.singleShot(
                300, lambda: self._start_auto_retry_for_chunks(retry_candidates)
            )
            return True

        self.log_message(
            "[Dịch][AutoRetry] Quét xong, không có nhóm nào cần/được phép dịch lại."
        )
        return False

    def translate_compare_timeline(self):
        """So sánh timeline giữa file gốc và file đã dịch"""
        if not self.translate_chunks:
            self.translate_compare_label.setText("❌ Chưa có nhóm để so sánh")
            self.translate_compare_label.setStyleSheet(
                "color: #f44336; font-size: 12px; padding: 5px;"
            )
            return

        try:
            chunk_indices = self._get_compare_chunk_indices()
            if not chunk_indices:
                self.translate_compare_label.setText(
                    "❌ Không có nhóm phù hợp để so sánh"
                )
                self.translate_compare_label.setStyleSheet(
                    "color: #f44336; font-size: 12px; padding: 5px;"
                )
                return

            total_entries = 0
            perfect_match = 0
            mismatch_count = 0
            missing_translation_chunks = 0
            mismatch_details = []

            for chunk_idx in chunk_indices:
                analysis = self._analyze_chunk_timeline(chunk_idx)
                if not analysis.get("valid"):
                    missing_translation_chunks += 1
                    mismatch_count += analysis.get("error_count", 0)
                    total_entries += analysis.get("error_count", 0)
                    if len(mismatch_details) < 5:
                        mismatch_details.append(
                            f"Nhóm {chunk_idx + 1}: {analysis.get('detail', 'Lỗi')} "
                        )
                    continue

                start, end = self.translate_chunks[chunk_idx]
                original_entries = self.translate_entries[start:end]
                translated_count = analysis.get(
                    "translated_count", len(original_entries)
                )
                max_len = max(len(original_entries), translated_count)
                total_entries += max_len
                mismatch_count += analysis["error_count"]
                perfect_match += max(0, max_len - analysis["error_count"])

                if analysis["error_count"] > 0 and len(mismatch_details) < 5:
                    msg = analysis.get("detail") or "Timeline lệch"
                    mismatch_details.append(f"Nhóm {chunk_idx + 1}: {msg}")

            if total_entries == 0:
                self.translate_compare_label.setText("❌ Không có dữ liệu để so sánh")
                self.translate_compare_label.setStyleSheet(
                    "color: #f44336; font-size: 12px; padding: 5px;"
                )
                return

            percent_ok = perfect_match / total_entries * 100
            scope_text = f"Phạm vi: {len(chunk_indices)} nhóm"

            if mismatch_count == 0 and missing_translation_chunks == 0:
                self.translate_compare_label.setText(
                    f"✅ Khớp hoàn toàn! ({perfect_match}/{total_entries})\n{scope_text}"
                )
                self.translate_compare_label.setStyleSheet(
                    "color: #4caf50; font-size: 12px; padding: 5px;"
                )
                self.log_message(
                    f"[Dịch] ✅ Timeline khớp hoàn toàn: {perfect_match}/{total_entries} ({scope_text})"
                )
            else:
                detail_text = "\n".join(mismatch_details[:3])
                if len(mismatch_details) > 3:
                    detail_text += f"\n... và {len(mismatch_details) - 3} lỗi khác"

                extra = ""
                if missing_translation_chunks > 0:
                    extra = f"\nNhóm chưa dịch: {missing_translation_chunks}"

                self.translate_compare_label.setText(
                    f"⚠️ Đúng {perfect_match}/{total_entries} ({percent_ok:.1f}%)\n{scope_text}{extra}\n{detail_text}"
                )
                self.translate_compare_label.setStyleSheet(
                    "color: #FF9800; font-size: 11px; padding: 5px;"
                )
                self.log_message(
                    f"[Dịch] ⚠️ Timeline: Đúng {perfect_match}/{total_entries} ({percent_ok:.1f}%), "
                    f"mismatch={mismatch_count}, chưa dịch={missing_translation_chunks}"
                )

                # Hiển thị Bulk Action Dialog
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Xử lý lỗi hàng loạt")
                msg_box.setText(
                    f"Phát hiện {mismatch_count} lỗi lệch timeline và {missing_translation_chunks} nhóm chưa dịch.\nBạn muốn xử lý các nhóm lỗi này như thế nào?"
                )
                msg_box.setIcon(QMessageBox.Question)

                btn_sync_all = msg_box.addButton(
                    "Đồng bộ tất cả", QMessageBox.ActionRole
                )
                btn_retry_all = msg_box.addButton(
                    "Dịch lại toàn bộ lỗi", QMessageBox.ActionRole
                )
                btn_cancel = msg_box.addButton("Hủy", QMessageBox.RejectRole)

                msg_box.exec_()

                if msg_box.clickedButton() == btn_retry_all:
                    # Lấy danh sách index các nhóm lỗi
                    error_indices = []
                    for chunk_idx in chunk_indices:
                        analysis = self._analyze_chunk_timeline(chunk_idx)
                        if (
                            analysis.get("missing_subtitles")
                            or analysis.get("error_count", 0) > 0
                        ):
                            error_indices.append(chunk_idx)

                    if error_indices:
                        self.log_message(
                            f"[Dịch] Yêu cầu dịch lại thủ công {len(error_indices)} nhóm lỗi..."
                        )
                        self._start_auto_retry_for_chunks(error_indices)

                elif msg_box.clickedButton() == btn_sync_all:
                    # Đồng bộ tất cả nhóm lỗi
                    sync_count = 0
                    for chunk_idx in chunk_indices:
                        analysis = self._analyze_chunk_timeline(chunk_idx)
                        if (
                            analysis.get("missing_subtitles")
                            or analysis.get("error_count", 0) > 0
                        ):
                            if chunk_idx in self.translated_results:
                                translated_text = self.translated_results[chunk_idx]
                                chunk_entries = self._parse_srt_from_text(
                                    translated_text
                                )
                                start, end = self.translate_chunks[chunk_idx]
                                original_chunk = self.translate_entries[start:end]

                                if len(chunk_entries) == len(original_chunk):
                                    synced_lines = []
                                    for i, orig in enumerate(original_chunk):
                                        synced_lines.append(str(orig.index))
                                        start_time = orig.start_time or "00:00:00,000"
                                        end_time = orig.end_time or "00:00:00,000"
                                        synced_lines.append(
                                            f"{start_time} --> {end_time}"
                                        )
                                        for line in chunk_entries[i].content:
                                            synced_lines.append(line)
                                        synced_lines.append("")

                                    synced_text = "\n".join(synced_lines).strip()
                                    self.translated_results[chunk_idx] = synced_text
                                    sync_count += 1

                                    # Cập nhật UI list
                                    item = self.translate_chunks_list.item(chunk_idx)
                                    if item and "✅" not in item.text():
                                        item.setText(
                                            f"✅ {item.text().replace('⚠️ ', '')}"
                                        )

                    # Refresh UI preview nếu group hiện tại vừa được sync
                    current_row = self.translate_chunks_list.currentRow()
                    if current_row in self.translated_results:
                        self.translate_result_preview.setPlainText(
                            self.translated_results[current_row]
                        )

        except Exception as e:
            import traceback

            self.translate_compare_label.setText(f"❌ Lỗi khi so sánh: {str(e)}")
            self.translate_compare_label.setStyleSheet(
                "color: #f44336; font-size: 12px; padding: 5px;"
            )
            self.log_message(f"[Dịch] ❌ Lỗi so sánh timeline: {str(e)}")
            print(traceback.format_exc())

    def translate_save_to_file2(self):
        """Lưu file SRT đã dịch vào File 2, cho phép bỏ qua nhóm lỗi/chưa dịch nhưng vẫn giữ timeline chuẩn"""
        if not self.translate_entries:
            QMessageBox.warning(self, "Lỗi", "Chưa có dữ liệu SRT gốc để lưu!")
            return

        target_file = self.file2_path
        if not target_file:
            QMessageBox.warning(
                self,
                "Lỗi",
                "Chưa có File 2! Vui lòng chọn File 2 trước hoặc tạo file ThaiSub.",
            )
            return

        translated_group_count = len(self.translated_results)
        total_groups = len(self.translate_chunks)
        missing_groups = total_groups - translated_group_count

        reply = QMessageBox.question(
            self,
            "Xác nhận lưu",
            f"Sẽ lưu vào File 2:\n{target_file}\n\n"
            f"- Tổng nhóm: {total_groups}\n"
            f"- Nhóm đã dịch: {translated_group_count}\n"
            f"- Nhóm chưa dịch/bỏ qua: {missing_groups}\n\n"
            "Các nhóm chưa dịch sẽ giữ nguyên nội dung gốc để đảm bảo đúng thứ tự timeline.\n"
            "Tiếp tục?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            all_entries = []
            replaced_count = 0
            fallback_count = 0
            parse_error_groups = []

            save_mode = "safe"
            if hasattr(self, "translate_save_mode_combo"):
                save_mode = self.translate_save_mode_combo.currentData()

            for chunk_idx, (start, end) in enumerate(self.translate_chunks):
                original_entries = self.translate_entries[start:end]

                # Mặc định dùng bản gốc
                final_chunk_entries = original_entries

                if chunk_idx in self.translated_results:
                    translated_srt = self.translated_results[chunk_idx]
                    chunk_entries = self._parse_srt_from_text(translated_srt)

                    if save_mode == "safe":
                        # Chỉ chấp nhận thay thế nếu số lượng khớp để giữ đúng mapping từng dòng
                        if len(chunk_entries) == len(original_entries):
                            normalized_chunk = []
                            for i, orig in enumerate(original_entries):
                                trans = chunk_entries[i]
                                normalized_chunk.append(
                                    SubtitleEntry(
                                        index=orig.index,
                                        start_time=orig.start_time,
                                        end_time=orig.end_time,
                                        content=trans.content,
                                        line_number=orig.line_number,
                                    )
                                )
                            final_chunk_entries = normalized_chunk
                            replaced_count += len(normalized_chunk)
                        else:
                            fallback_count += len(original_entries)
                            parse_error_groups.append(
                                f"Nhóm {chunk_idx + 1}: lệch số dòng {len(original_entries)} vs {len(chunk_entries)}"
                            )
                    else:  # save_mode == "force"
                        # Dùng trực tiếp bản dịch bất chấp lệch số lượng (tạo lại timeline từ bản dịch)
                        final_chunk_entries = chunk_entries
                        replaced_count += len(chunk_entries)
                else:
                    fallback_count += len(original_entries)

                all_entries.extend(final_chunk_entries)

            # Chuẩn hóa index liên tục theo thứ tự tổng thể
            for i, entry in enumerate(all_entries, start=1):
                entry.index = i

            save_srt_file(all_entries, target_file)
            self.file2_entries = all_entries

            self.log_message(
                f"[Dịch] ✅ Đã lưu vào File 2: {target_file} | thay bằng bản dịch: {replaced_count}, giữ gốc: {fallback_count}"
            )

            detail = ""
            if parse_error_groups:
                preview = "\n".join(parse_error_groups[:3])
                more = ""
                if len(parse_error_groups) > 3:
                    more = f"\n... và {len(parse_error_groups) - 3} nhóm khác"
                detail = f"\n\nNhóm dùng lại bản gốc do lỗi parse/thiếu dòng:\n{preview}{more}"

            QMessageBox.information(
                self,
                "Thành công",
                f"Đã lưu vào File 2:\n{target_file}\n\n"
                f"- Tổng phụ đề: {len(all_entries)}\n"
                f"- Áp dụng bản dịch: {replaced_count}\n"
                f"- Giữ bản gốc: {fallback_count}"
                f"{detail}",
            )

        except Exception as e:
            self.log_message(f"[Dịch] ❌ Lỗi lưu file: {str(e)}")
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu file:\n{str(e)}")

    def _parse_srt_from_text(self, text: str) -> list:
        """Parse nội dung SRT từ text"""
        from srt_parser import SubtitleEntry
        import re

        entries = []
        lines = text.strip().split("\n")

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            if line.isdigit():
                index = int(line)

                if i + 1 < len(lines):
                    time_line = lines[i + 1].strip()
                    time_match = re.match(
                        r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})",
                        time_line,
                    )

                    if time_match:
                        start_time = time_match.group(1)
                        end_time = time_match.group(2)

                        content = []
                        j = i + 2
                        while j < len(lines) and lines[j].strip():
                            content.append(lines[j].strip())
                            j += 1

                        entry = SubtitleEntry(
                            index, start_time, end_time, content, i + 1
                        )
                        entries.append(entry)
                        i = j
                    else:
                        i += 1
                else:
                    i += 1
            else:
                i += 1

        return entries


def _main_standalone():
    """Hàm main"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Sử dụng Fusion style

    window = SRTCompareApp()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__" and False:
    main()
