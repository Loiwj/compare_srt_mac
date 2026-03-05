#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ứng dụng so sánh và chỉnh sửa file SRT
Giao diện Qt5 với sidebar và main content area
"""

import sys
from pathlib import Path
from typing import Optional
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QTextEdit,
                             QFileDialog, QListWidget, QListWidgetItem,
                             QProgressBar, QMessageBox, QLineEdit, QSpinBox,
                             QGroupBox, QRadioButton, QCheckBox, QSplitter,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QAbstractItemView, QPlainTextEdit, QDoubleSpinBox,
                             QStackedWidget)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon, QDesktopServices, QPixmap
import subprocess
import os

from srt_parser import (parse_srt_file, compare_srt_files, create_thaisub_file,
                       save_srt_file, SubtitleEntry, fix_srt_entry)

try:
    import capcut_srt_gui as capcut_srt
except ImportError:
    capcut_srt = None


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
                progress_callback=self.progress.emit
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
        self.sidebar.setFixedWidth(250)
        main_layout.addWidget(self.sidebar)
        
        # Main content area
        content_area = self.create_content_area()
        main_layout.addWidget(content_area, 1)
    
    def create_sidebar(self) -> QWidget:
        """Tạo sidebar navigation"""
        sidebar = QWidget()
        sidebar.setStyleSheet("""
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
        """)
        
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
            ("Chia phụ đề", "split")
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
        content_widget.setStyleSheet("background-color: #252525;")
        
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Stack widget để chuyển đổi giữa các trang
        self.stacked_widget = QStackedWidget()
        layout.addWidget(self.stacked_widget)
        
        # Tạo các trang
        self.compare_page = self.create_compare_page()
        self.split_page = self.create_split_page()
        
        self.stacked_widget.addWidget(self.compare_page)
        self.stacked_widget.addWidget(self.split_page)
        
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
        """Tạo trang so sánh SRT"""
        page = QWidget()
        page.setStyleSheet("background-color: #252525; color: #ffffff;")
        
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)
        
        # Top: Results table (full width)
        results_group = QGroupBox("Kết quả so sánh")
        group_style = """
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
        results_group.setStyleSheet(group_style)
        results_layout = QVBoxLayout(results_group)
        
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels([
            "STT", "Loại", "File 1", "File 2", "Lệch (ms)"
        ])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                gridline-color: #444;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #ffffff;
                padding: 5px;
            }
        """)
        results_layout.addWidget(self.results_table)
        main_layout.addWidget(results_group, 1)  # Stretch factor 1 để chiếm không gian
        
        # Bottom: 2 columns - Left: So sánh SRT, Right: Auto fix
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(20)
        
        # Left column: So sánh SRT section
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setSpacing(15)
        
        compare_group = QGroupBox("So sánh File SRT")
        compare_group.setStyleSheet(group_style)
        compare_layout = QVBoxLayout(compare_group)
        compare_layout.setSpacing(10)
        
        # File 1
        file1_layout = QHBoxLayout()
        self.file1_label = QLabel("Chưa chọn file")
        self.file1_label.setStyleSheet("color: #888888;")
        file1_btn = QPushButton("📁 Chọn File 1")
        file1_btn.clicked.connect(lambda: self.select_file(1))
        file1_layout.addWidget(self.file1_label, 1)
        file1_layout.addWidget(file1_btn)
        compare_layout.addLayout(file1_layout)
        
        # File 2
        file2_layout = QHBoxLayout()
        self.file2_label = QLabel("Chưa chọn file")
        self.file2_label.setStyleSheet("color: #888888;")
        file2_btn = QPushButton("📁 Chọn File 2")
        file2_btn.clicked.connect(lambda: self.select_file(2))
        file2_layout.addWidget(self.file2_label, 1)
        file2_layout.addWidget(file2_btn)
        compare_layout.addLayout(file2_layout)
        
        # Tolerance
        tolerance_layout = QHBoxLayout()
        tolerance_layout.addWidget(QLabel("Độ lệch cho phép (ms):"))
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 10000)
        self.tolerance_spin.setValue(0)
        tolerance_layout.addWidget(self.tolerance_spin)
        compare_layout.addLayout(tolerance_layout)
        
        # Buttons
        compare_btn = QPushButton("⚡ So sánh")
        compare_btn.setStyleSheet("""
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
        """)
        compare_btn.clicked.connect(self.start_compare)
        compare_layout.addWidget(compare_btn)
        
        # Tạo file thaisub
        create_thaisub_btn = QPushButton("📝 Tạo File ThaiSub")
        create_thaisub_btn.setStyleSheet("""
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
        """)
        create_thaisub_btn.clicked.connect(self.create_thaisub)
        compare_layout.addWidget(create_thaisub_btn)
        
        # Mở bằng trình soạn thảo văn bản mặc định (TextEdit trên macOS, Notepad trên Windows)
        open_notepad_btn1 = QPushButton("📝 Mở File 1")
        open_notepad_btn1.setStyleSheet("""
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
        """)
        open_notepad_btn1.clicked.connect(self.open_file1_in_notepad)
        compare_layout.addWidget(open_notepad_btn1)
        
        open_notepad_btn2 = QPushButton("📝 Mở File 2")
        open_notepad_btn2.setStyleSheet("""
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
        """)
        open_notepad_btn2.clicked.connect(self.open_file2_in_notepad)
        compare_layout.addWidget(open_notepad_btn2)
        
        # Info
        info_label = QLabel("💡 Chọn 2 file SRT để so sánh thời gian\n💡 Sau khi so sánh, có thể dùng tính năng tự động sửa lỗi")
        info_label.setStyleSheet("color: #ffc107; font-size: 15px;")
        info_label.setWordWrap(True)
        compare_layout.addWidget(info_label)
        
        left_layout.addWidget(compare_group)
        left_layout.addStretch()
        
        # Right column: Auto fix section
        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setSpacing(15)
        
        auto_fix_group = QGroupBox("Tự động sửa lỗi")
        auto_fix_group.setStyleSheet(group_style)
        auto_fix_layout = QVBoxLayout(auto_fix_group)
        
        self.auto_fix_radio1 = QRadioButton("Đồng bộ từ File 1 → File 2")
        self.auto_fix_radio1.setStyleSheet("font-size: 15px;")
        self.auto_fix_radio1.setChecked(True)
        self.auto_fix_radio2 = QRadioButton("Đồng bộ từ File 2 → File 1")
        self.auto_fix_radio2.setStyleSheet("font-size: 15px;")
        auto_fix_layout.addWidget(self.auto_fix_radio1)
        auto_fix_layout.addWidget(self.auto_fix_radio2)
        
        auto_fix_btn = QPushButton("🔧 Tự động sửa lỗi")
        auto_fix_btn.setStyleSheet("""
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
        """)
        auto_fix_btn.clicked.connect(self.auto_fix_errors)
        auto_fix_layout.addWidget(auto_fix_btn)
        
        right_layout.addWidget(auto_fix_group)
        right_layout.addStretch()
        
        # Combine bottom columns
        bottom_layout.addWidget(left_col, 1)
        bottom_layout.addWidget(right_col, 1)
        
        main_layout.addLayout(bottom_layout)
        
        return page

    def create_split_page(self) -> QWidget:
        """Tạo trang chia nhỏ phụ đề SRT để dễ copy"""
        page = QWidget()
        page.setStyleSheet("background-color: #252525; color: #ffffff;")

        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # Cột trái: chọn file & cấu hình chia
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setSpacing(15)

        split_group = QGroupBox("Chia nhỏ phụ đề SRT")
        split_group.setStyleSheet("""
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
        """)
        split_layout = QVBoxLayout(split_group)

        # Chọn file SRT để chia
        file_layout = QHBoxLayout()
        self.split_file_label = QLabel("Chưa chọn file")
        self.split_file_label.setStyleSheet("color: #888888; font-size: 15px; font-weight: 500;")
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
        split_btn.setStyleSheet("""
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
        """)
        split_btn.clicked.connect(self.split_subtitles)
        split_layout.addWidget(split_btn)

        # Gợi ý sử dụng
        split_info = QLabel("💡 Ví dụ: có 3000 phụ đề, chia mỗi nhóm 400–500 để dễ copy từng phần.")
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
        self.split_chunks_list.setStyleSheet("""
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
        """)
        self.split_chunks_list.currentRowChanged.connect(self.update_split_preview)
        chunks_layout.addWidget(self.split_chunks_list)

        right_layout.addWidget(chunks_group)

        # Nội dung nhóm để copy
        preview_group = QGroupBox("Nội dung nhóm (bôi đen rồi Ctrl+C để copy)")
        preview_group.setStyleSheet(split_group.styleSheet())
        preview_layout = QVBoxLayout(preview_group)

        self.split_preview = QPlainTextEdit()
        self.split_preview.setReadOnly(True)
        self.split_preview.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 18px;
            }
        """)
        preview_layout.addWidget(self.split_preview)

        # Nút copy nhanh nội dung nhóm hiện tại
        copy_btn_layout = QHBoxLayout()
        copy_btn_layout.addStretch()
        self.split_copy_btn = QPushButton("📋 Copy nhóm hiện tại")
        self.split_copy_btn.setStyleSheet("""
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
        """)
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
        page.setStyleSheet("background-color: #252525; color: #ffffff;")
        
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        
        status_label = QLabel("Trạng thái hệ thống")
        status_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(status_label)
        
        self.system_status = QTextEdit()
        self.system_status.setReadOnly(True)
        self.system_status.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #00ff00;
                border: 1px solid #444;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 15px;
            }
        """)
        layout.addWidget(self.system_status)
        
        self.update_system_status()
        
        return page
    
    def apply_dark_theme(self):
        """Áp dụng dark theme"""
        self.is_dark_mode = True
        self.setStyleSheet("""
            QMainWindow {
                background-color: #252525;
            }
            QWidget {
                color: #ffffff;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #ffffff;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:pressed {
                background-color: #2d2d2d;
            }
            QLineEdit, QSpinBox {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444;
                padding: 5px;
                border-radius: 3px;
            }
            QMessageBox {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMessageBox QLabel {
                color: #ffffff;
                font-size: 15px;
            }
            QMessageBox QPushButton {
                background-color: #d32f2f;
                color: #ffffff;
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 15px;
                font-weight: bold;
            }
            QMessageBox QPushButton:hover {
                background-color: #b71c1c;
            }
        """)
        self.update_widgets_theme()
    
    def apply_light_theme(self):
        """Áp dụng light theme"""
        self.is_dark_mode = False
        self.setStyleSheet("""
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
        """)
        self.update_widgets_theme()
    
    def switch_to_dark(self):
        """Chuyển sang Dark Mode"""
        if not self.is_dark_mode:
            self.apply_dark_theme()
            if hasattr(self, 'dark_mode_btn') and hasattr(self, 'light_mode_btn'):
                self.dark_mode_btn.setChecked(True)
                self.light_mode_btn.setChecked(False)
            self.log_message("Đã chuyển sang Dark Mode")
    
    def switch_to_light(self):
        """Chuyển sang Light Mode"""
        if self.is_dark_mode:
            self.apply_light_theme()
            if hasattr(self, 'dark_mode_btn') and hasattr(self, 'light_mode_btn'):
                self.dark_mode_btn.setChecked(False)
                self.light_mode_btn.setChecked(True)
            self.log_message("Đã chuyển sang Light Mode")
    
    def update_widgets_theme(self):
        """Cập nhật theme cho tất cả các widget"""
        if self.is_dark_mode:
            # Dark theme colors
            bg_main = "#252525"
            bg_sidebar = "#1e1e1e"
            bg_widget = "#1e1e1e"
            bg_group = "#2d2d2d"
            text_color = "#ffffff"
            text_secondary = "#888888"
            text_warning = "#ffc107"
            text_success = "#00ff00"
            border_color = "#444"
            log_bg = "#1e1e1e"
            log_text = "#00ff00"
            table_bg = "#1e1e1e"
            table_header = "#2d2d2d"
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
        if hasattr(self, 'sidebar') and self.sidebar:
            hover_bg = bg_group if self.is_dark_mode else "#E3F2FD"
            pressed_bg = bg_widget if self.is_dark_mode else "#BBDEFB"
            self.sidebar.setStyleSheet(f"""
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
            """)
        
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
        if hasattr(self, 'log_text'):
            log_border = border_color if self.is_dark_mode else "#1976D2"
            self.log_text.setStyleSheet(f"""
                QTextEdit {{
                    background-color: {log_bg};
                    color: {log_text};
                    border: 2px solid {log_border};
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 17px;
                }}
            """)
        
        # Update results table
        if hasattr(self, 'results_table'):
            table_grid = border_color if self.is_dark_mode else "#90CAF9"
            self.results_table.setStyleSheet(f"""
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
            """)
        
        # Update edit text
        if hasattr(self, 'edit_text'):
            edit_border = border_color if self.is_dark_mode else "#1976D2"
            self.edit_text.setStyleSheet(f"""
                QTextEdit {{
                    background-color: {bg_widget};
                    color: {text_color};
                    border: 2px solid {edit_border};
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 15px;
                    font-weight: 500;
                }}
            """)
        
        # Update system status
        if hasattr(self, 'system_status'):
            status_border = border_color if self.is_dark_mode else "#1976D2"
            self.system_status.setStyleSheet(f"""
                QTextEdit {{
                    background-color: {bg_widget};
                    color: {text_success};
                    border: 2px solid {status_border};
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 15px;
                    font-weight: 500;
                }}
            """)
        
        # Update labels
        if hasattr(self, 'file1_label'):
            if self.file1_path:
                self.file1_label.setStyleSheet(f"color: {text_success}; font-size: 15px; font-weight: bold;")
            else:
                self.file1_label.setStyleSheet(f"color: {text_secondary}; font-size: 15px; font-weight: 500;")
        
        if hasattr(self, 'file2_label'):
            if self.file2_path:
                self.file2_label.setStyleSheet(f"color: {text_success}; font-size: 15px; font-weight: bold;")
            else:
                self.file2_label.setStyleSheet(f"color: {text_secondary}; font-size: 15px; font-weight: 500;")
        
        if hasattr(self, 'capcut_project_label'):
            if self.capcut_project_dir:
                self.capcut_project_label.setStyleSheet(f"color: {text_success}; font-size: 15px; font-weight: bold;")
            else:
                self.capcut_project_label.setStyleSheet(f"color: {text_secondary}; font-size: 15px; font-weight: 500;")
        
        if hasattr(self, 'edit_file_label'):
            if hasattr(self, 'edit_file_path') and self.edit_file_path:
                self.edit_file_label.setStyleSheet(f"color: {text_success}; font-size: 15px; font-weight: bold;")
            else:
                self.edit_file_label.setStyleSheet(f"color: {text_secondary}; font-size: 15px; font-weight: 500;")
        
        if hasattr(self, 'status_label'):
            self.status_label.setStyleSheet(f"color: {text_warning}; padding: 10px; font-size: 15px; font-weight: bold;")
        
        # Update radio buttons
        if hasattr(self, 'auto_fix_radio1'):
            radio_color = text_color
            self.auto_fix_radio1.setStyleSheet(f"color: {radio_color}; font-size: 15px; font-weight: 500;")
            self.auto_fix_radio2.setStyleSheet(f"color: {radio_color}; font-size: 15px; font-weight: 500;")
        
        # Update group boxes
        for group in self.findChildren(QGroupBox):
            if group.title() != "Nhật ký hoạt động":  # Skip log group, handled separately
                group_border = border_color if self.is_dark_mode else "#1976D2"
                group.setStyleSheet(f"""
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
                """)
        
        # Update active menu style
        if hasattr(self, 'active_menu_key'):
            self.update_active_menu_style()
        
        # Update log group box (special red border)
        log_group = None
        for group in self.findChildren(QGroupBox):
            if group.title() == "Nhật ký hoạt động":
                log_group = group
                break
        
        if log_group:
            if self.is_dark_mode:
                log_group.setStyleSheet("""
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
                """)
            else:
                log_group.setStyleSheet("""
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
                """)
    
    def update_active_menu_style(self):
        """Cập nhật style cho menu item đang active"""
        # Reset tất cả menu buttons
        for btn in self.menu_items.values():
            btn.setStyleSheet("")
            btn.setChecked(False)
        
        # Highlight active button
        if hasattr(self, 'active_menu_key') and self.active_menu_key in self.menu_items:
            self.menu_items[self.active_menu_key].setChecked(True)
            self.menu_items[self.active_menu_key].setStyleSheet("""
                QPushButton {
                    background-color: #d32f2f;
                    color: #ffffff;
                }
            """)
    
    def switch_page(self, page_key: str):
        """Chuyển đổi giữa các trang"""
        page_map = {
            "compare": 0,
            "split": 1
        }
        
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
            self, 
            f"Chọn File SRT {file_num}",
            "",
            "SRT Files (*.srt);;All Files (*)"
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
                    self.split_file_label.setStyleSheet("color: #4caf50; font-size: 15px; font-weight: bold;")
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
        
        tolerance = self.tolerance_spin.value()
        
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
        
        total = result.get('total_compared', 0)
        errors_count = len(result.get('errors', []))
        matched = result.get('matched', 0)

        # Log thêm dòng tổng quan kiểu "Đã so sánh X/Y, đúng A, sai B"
        if total > 0:
            self.log_message(
                f"📊 Đã so sánh {total}/{max(result.get('file1_entries', 0), result.get('file2_entries', 0))} phụ đề "
                f"→ ĐÚNG {matched}, LỆCH {errors_count}"
            )

        # Hiển thị kết quả trong bảng
        self.results_table.setRowCount(0)
        
        if result['errors']:
            self.log_message(f"Phát hiện {errors_count} lỗi lệch thời gian (tính theo từng mốc start/end)")
            for error in result['errors']:
                row = self.results_table.rowCount()
                self.results_table.insertRow(row)
                
                error_type = "BẮT ĐẦU" if error['type'] == 'start' else "KẾT THÚC"
                self.results_table.setItem(row, 0, QTableWidgetItem(str(error['index'])))
                self.results_table.setItem(row, 1, QTableWidgetItem(error_type))
                self.results_table.setItem(row, 2, QTableWidgetItem(error['file1_time']))
                self.results_table.setItem(row, 3, QTableWidgetItem(error['file2_time']))
                self.results_table.setItem(row, 4, QTableWidgetItem(str(error['diff_ms'])))
                
                # Tô màu dòng lỗi
                for col in range(5):
                    item = self.results_table.item(row, col)
                    if item:
                        item.setBackground(QColor("#3d1e1e"))
        else:
            self.log_message("✅ Tất cả thời gian đều khớp!")
            QMessageBox.information(self, "Thành công", "Tất cả thời gian đều khớp nhau!")
        
        # Tổng kết
        summary = f"Tổng kết: {result['matched']}/{result['total_compared']} khớp, {len(result['errors'])} lệch"
        self.log_message(summary)
        
        if result['file1_extra']:
            self.log_message(f"⚠️ File 1 có thêm {len(result['file1_extra'])} entries")
        if result['file2_extra']:
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
                    QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    self.log_message("Đã hủy tạo file thaisub")
                    return
            
            # Load entries từ file nguồn
            source_entries = parse_srt_file(source_file)
            
            # Sử dụng toàn bộ entries của File 1 (hoặc file \"done\") để tạo thaisub.
            # Trước đây có logic cắt theo số lượng entries của File 2, nhưng điều này
            # dễ gây nhầm lẫn khi người dùng thay File 1 mà vẫn giữ File 2 cũ
            # (dẫn đến file thaisub mới chỉ có đúng số dòng của file cũ).
            entries_to_use = source_entries
            
            # Tạo file thaisub với số lượng entries đúng
            with open(output_file, 'w', encoding='utf-8') as f:
                for entry in entries_to_use:
                    # Giữ nguyên thời gian, để trống nội dung
                    f.write(f"{entry.index}\n")
                    f.write(f"{entry.start_time} --> {entry.end_time}\n")
                    f.write("\n")  # Nội dung trống
                    f.write("\n")  # Dòng trống kết thúc entry
            
            self.last_thaisub_file = output_file  # Lưu file thaisub vừa tạo
            
            # Tự động chọn làm File 2
            self.file2_path = output_file
            self.file2_label.setText(output_file.name)
            self.file2_label.setStyleSheet("color: #4caf50;")
            
            # Lưu entries để dùng sau
            self.file2_entries = []
            for entry in entries_to_use:
                new_entry = SubtitleEntry(entry.index, entry.start_time, entry.end_time, [], entry.line_number)
                self.file2_entries.append(new_entry)
            
            self.log_message(f"✅ Đã tạo file thaisub: {output_file.name} ({len(entries_to_use)} entries)")
            self.log_message(f"✅ Đã tự động chọn làm File 2")
            QMessageBox.information(
                self, 
                "Thành công", 
                f"Đã tạo file:\n{output_file}\n\n{len(entries_to_use)} entries\n\nĐã tự động chọn làm File 2!"
            )
        except Exception as e:
            self.log_message(f"❌ Lỗi tạo file: {str(e)}")
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi tạo file:\n{str(e)}")
    
    def open_file1_in_notepad(self):
        """Mở File 1 bằng trình soạn thảo mặc định (TextEdit trên macOS, Notepad trên Windows, xdg-open trên Linux)."""
        if not self.file1_path or not self.file1_path.exists():
            QMessageBox.warning(self, "Cảnh báo", "Chưa có File 1 để mở!\nVui lòng chọn File 1 trước.")
            return

        file_path_str = str(self.file1_path.absolute())
        try:
            system_name = platform.system().lower()
            if system_name == "darwin":
                # macOS: ưu tiên TextEdit, nếu lỗi thì dùng 'open' mặc định
                try:
                    subprocess.Popen(["open", "-a", "TextEdit", file_path_str])
                except Exception:
                    subprocess.Popen(["open", file_path_str])
            elif system_name == "windows":
                # Windows: dùng os.startfile (mặc định là Notepad cho .txt/.srt)
                os.startfile(file_path_str)
            else:
                # Linux: dùng xdg-open
                subprocess.Popen(["xdg-open", file_path_str])
            self.log_message(f"📝 Đã mở File 1: {self.file1_path.name}")
            return
        except Exception:
            pass

        # Fallback Qt
        if QDesktopServices.openUrl(QUrl.fromLocalFile(file_path_str)):
            self.log_message(f"📝 Đã mở File 1 (fallback Qt): {self.file1_path.name}")
        else:
            QMessageBox.critical(self, "Lỗi", "Không thể mở file bằng ứng dụng mặc định.")
            self.log_message(f"❌ Lỗi mở File 1: {self.file1_path.name}")
    
    def open_file2_in_notepad(self):
        """Mở File 2 (hoặc file thaisub) bằng trình soạn thảo mặc định (TextEdit/Notepad/xdg-open)."""
        # Ưu tiên file 2, sau đó là file thaisub
        file_to_open = None
        if self.file2_path and self.file2_path.exists():
            file_to_open = self.file2_path
        elif hasattr(self, 'last_thaisub_file') and self.last_thaisub_file and self.last_thaisub_file.exists():
            file_to_open = self.last_thaisub_file
        else:
            QMessageBox.warning(self, "Cảnh báo", "Chưa có File 2 hoặc file thaisub để mở!\nVui lòng chọn File 2 hoặc tạo file thaisub trước.")
            return
        
        file_path_str = str(file_to_open.absolute())
        try:
            system_name = platform.system().lower()
            if system_name == "darwin":
                try:
                    subprocess.Popen(["open", "-a", "TextEdit", file_path_str])
                except Exception:
                    subprocess.Popen(["open", file_path_str])
            elif system_name == "windows":
                os.startfile(file_path_str)
            else:
                subprocess.Popen(["xdg-open", file_path_str])
            self.log_message(f"📝 Đã mở file: {file_to_open.name}")
            return
        except Exception:
            pass

        if QDesktopServices.openUrl(QUrl.fromLocalFile(file_path_str)):
            self.log_message(f"📝 Đã mở file (fallback Qt): {file_to_open.name}")
        else:
            QMessageBox.critical(self, "Lỗi", "Không thể mở file bằng ứng dụng mặc định.")
            self.log_message(f"❌ Lỗi mở file: {file_to_open.name}")

    def auto_fix_errors(self):
        """Tự động sửa lỗi bằng cách đồng bộ thời gian"""
        if not self.file1_path or not self.file2_path:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn đủ 2 file SRT!")
            return
        
        if not self.compare_result or not self.compare_result['errors']:
            QMessageBox.warning(self, "Cảnh báo", "Không có lỗi để sửa! Vui lòng so sánh trước.")
            return
        
        if not self.auto_fix_radio1.isChecked() and not self.auto_fix_radio2.isChecked():
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
            QMessageBox.Yes | QMessageBox.No
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
            for error in self.compare_result['errors']:
                entry_index = error['index']
                if entry_index in source_dict and entry_index in target_dict:
                    source_entry = source_dict[entry_index]
                    target_entry = target_dict[entry_index]
                    
                    if error['type'] == 'start':
                        target_entry.start_time = source_entry.start_time
                        fixed_count += 1
                    elif error['type'] == 'end':
                        target_entry.end_time = source_entry.end_time
                        fixed_count += 1
            
            # Lưu file đã sửa
            save_srt_file(target_entries, target_file)
            
            self.log_message(f"✅ Đã sửa {fixed_count} lỗi trong file {target_file.name}")
            QMessageBox.information(
                self,
                "Thành công",
                f"Đã sửa {fixed_count} lỗi!\nFile đã được lưu: {target_file.name}"
            )
            
            # Tự động so sánh lại
            QTimer.singleShot(500, self.start_compare)
            
        except Exception as e:
            self.log_message(f"❌ Lỗi khi sửa: {str(e)}")
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi sửa file:\n{str(e)}")
    
    # ===== Các hàm hỗ trợ CapCut SRT trong tab riêng =====

    def capcut_log(self, message: str):
        """Ghi log cho trang CapCut"""
        if hasattr(self, 'capcut_log_text') and self.capcut_log_text:
            self.capcut_log_text.append(message)
            self.capcut_log_text.verticalScrollBar().setValue(
                self.capcut_log_text.verticalScrollBar().maximum()
            )
        if hasattr(self, 'capcut_progress_label') and self.capcut_progress_label:
            self.capcut_progress_label.setText(message)

    def capcut_clear_log(self):
        if hasattr(self, 'capcut_log_text') and self.capcut_log_text:
            self.capcut_log_text.clear()

    def capcut_save_log(self):
        if not hasattr(self, 'capcut_log_text') or not self.capcut_log_text:
            return
        text = self.capcut_log_text.toPlainText()
        if not text:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu log CapCut",
            "capcut_log.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                QMessageBox.information(self, "Thành công", "Đã lưu log CapCut thành công!")
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Lỗi khi lưu log CapCut:\n{e}")

    def capcut_choose_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục project CapCut (chứa draft_content.json hoặc draft_info.json)",
            ""
        )
        if directory:
            self.capcut_project_dir = directory
            if hasattr(self, 'capcut_project_label') and self.capcut_project_label:
                from pathlib import Path as _Path
                name = _Path(directory).name
                self.capcut_project_label.setText(name)
                self.capcut_project_label.setStyleSheet("color: #4caf50;")

    def capcut_run_export(self):
        if capcut_srt is None:
            QMessageBox.warning(self, "Lỗi", "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.")
            return
        
        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn thư mục project trước.")
            return
        if not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Lỗi", "Thư mục không hợp lệ.")
            return

        try:
            if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
                self.capcut_progress_bar.setMaximum(100)
                self.capcut_progress_bar.setValue(5)
            self.capcut_log(f"Đang đọc draft trong: {project_dir}")
            data = capcut_srt.load_draft_json(project_dir)
            self.capcut_log("Đã đọc file draft thành công.")

            subtitles = capcut_srt.extract_subtitles_with_audio(data, project_dir)
            if not subtitles:
                self.capcut_log("Không tìm thấy phụ đề hoặc track thời gian trong file draft.")
                QMessageBox.warning(
                    self,
                    "Không có dữ liệu",
                    "Không tìm thấy phụ đề hoặc track thời gian trong file draft.",
                )
                return

            if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
                self.capcut_progress_bar.setValue(60)
            capcut_srt.write_outputs(project_dir, subtitles)
            if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
                self.capcut_progress_bar.setValue(100)
            self.capcut_log("Đã tạo subtitles.srt, copy.txt và subtitles_with_audio.json trong thư mục project.")
            QMessageBox.information(
                self,
                "Hoàn tất",
                "Xuất SRT thành công.\nĐã tạo:\n- subtitles.srt\n- copy.txt\n- subtitles_with_audio.json",
            )
        except Exception as e:
            self.capcut_log(f"Lỗi: {e}")
            QMessageBox.critical(self, "Lỗi", str(e))
        finally:
            if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
                QTimer.singleShot(500, lambda: self.capcut_progress_bar.setValue(0))

    def capcut_run_trim_audio(self):
        if capcut_srt is None:
            QMessageBox.warning(self, "Lỗi", "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.")
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

        silence_thresh = int(self.capcut_silence_thresh_spin.value()) if hasattr(self, 'capcut_silence_thresh_spin') and self.capcut_silence_thresh_spin else -40
        min_silence_len = int(self.capcut_min_silence_spin.value()) if hasattr(self, 'capcut_min_silence_spin') and self.capcut_min_silence_spin else 300

        # Chuẩn bị danh sách file audio cần trim (dùng cùng logic với tool gốc)
        targets = []
        for a in audios:
            original_path = (
                a.get("path") or a.get("file_path") or a.get("local_material_path")
            )
            if not original_path:
                continue
            src_abs = capcut_srt.resolve_audio_path_from_original(original_path, project_dir)
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

        if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
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

            if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
                self.capcut_progress_bar.setValue(idx)
            QApplication.processEvents()

        self.capcut_log(f"Đã trim {processed} file audio, lỗi {failed} file.")
        if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
            QTimer.singleShot(500, lambda: self.capcut_progress_bar.setValue(0))

    def run_check_audio_overlap(self):
        if capcut_srt is None:
            QMessageBox.warning(self, "Lỗi", "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.")
            return
        
        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn thư mục project trước.")
            return

        video_speed = self.video_speed_spin.value() if hasattr(self, 'video_speed_spin') and self.video_speed_spin else 1.0
        audio_speed = self.audio_speed_spin.value() if hasattr(self, 'audio_speed_spin') and self.audio_speed_spin else 1.0

        self.capcut_log(f"Bắt đầu kiểm tra overlap audio (Threaded)...")
        self.capcut_log(f"Video Speed: {video_speed}x, Audio Speed: {audio_speed}x")
        
        # Disable button during check
        if hasattr(self, 'check_overlap_btn') and self.check_overlap_btn:
            self.check_overlap_btn.setEnabled(False)
            self.check_overlap_btn.setText("⏳ Đang kiểm tra...")
        if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
            self.capcut_progress_bar.setValue(0)
        
        # Start Thread
        self.check_overlap_thread = CheckOverlapThread(project_dir, video_speed, audio_speed)
        self.check_overlap_thread.finished.connect(self.on_check_overlap_finished)
        self.check_overlap_thread.error.connect(self.on_check_overlap_error)
        if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
            self.check_overlap_thread.progress.connect(self.capcut_progress_bar.setValue)
        self.check_overlap_thread.log_message.connect(self.capcut_log)
        self.check_overlap_thread.start()

    def on_check_overlap_finished(self, overlaps: list):
        if hasattr(self, 'check_overlap_btn') and self.check_overlap_btn:
            self.check_overlap_btn.setEnabled(True)
            self.check_overlap_btn.setText("🔍 Kiểm tra Overlap")
        if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
            self.capcut_progress_bar.setValue(100)
        
        # Check for error dict in list
        if overlaps and "error" in overlaps[0]:
             QMessageBox.critical(self, "Lỗi", f"Lỗi: {overlaps[0]['error']}")
             self.capcut_log(f"❌ Lỗi: {overlaps[0]['error']}")
             return

        if hasattr(self, 'overlap_table') and self.overlap_table:
            self.overlap_table.setRowCount(0)
        
        if not overlaps:
            self.capcut_log("✅ Không phát hiện overlap nào.")
            QMessageBox.information(self, "Kết quả", "Không phát hiện overlap nào!")
            return
            
        self.capcut_log(f"⚠️ Phát hiện {len(overlaps)} overlap!")
        
        if hasattr(self, 'overlap_table') and self.overlap_table:
            for ov in overlaps:
                row = self.overlap_table.rowCount()
                self.overlap_table.insertRow(row)
                self.overlap_table.setItem(row, 0, QTableWidgetItem(str(ov['clip_A'])))
                self.overlap_table.setItem(row, 1, QTableWidgetItem(str(ov['clip_B'])))
                self.overlap_table.setItem(row, 2, QTableWidgetItem(f"{ov['overlap_duration_ms']} ms"))
                self.overlap_table.setItem(row, 3, QTableWidgetItem(str(ov['formatted_time'])))
                
                # Highlight overlap rows
                for col in range(4):
                     item = self.overlap_table.item(row, col)
                     if item:
                          item.setBackground(QColor("#5a3b3b")) # Dark redish background

            self.capcut_log(f"Overlap: '{ov['clip_A']}' đè lên '{ov['clip_B']}' ({ov['overlap_duration_ms']}ms) tại {ov['formatted_time']}")
            
        QMessageBox.warning(self, "Phát hiện Overlap", f"Tìm thấy {len(overlaps)} vị trí bị chồng âm thanh.\nXem chi tiết trong bảng và log.")

    def on_check_overlap_error(self, error_msg: str):
        if hasattr(self, 'check_overlap_btn') and self.check_overlap_btn:
            self.check_overlap_btn.setEnabled(True)
            self.check_overlap_btn.setText("🔍 Kiểm tra Overlap")
        if hasattr(self, 'capcut_progress_bar') and self.capcut_progress_bar:
            self.capcut_progress_bar.setValue(0)
        QMessageBox.critical(self, "Lỗi", f"Lỗi khi kiểm tra overlap: {error_msg}")
        self.capcut_log(f"❌ Lỗi: {error_msg}")

    def capcut_run_swap_folders(self):
        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(
                self, "Lỗi", "Vui lòng chọn thư mục Draft CapCut hoặc textReading trước."
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
            QMessageBox.critical(
                self, "Lỗi swap thư mục", f"Không thực hiện được: {e}"
            )

    def capcut_run_restore_folders(self):
        project_dir = (self.capcut_project_dir or "").strip()
        if not project_dir:
            QMessageBox.warning(
                self, "Lỗi", "Vui lòng chọn thư mục Draft CapCut hoặc textReading trước."
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

            self.capcut_log("Đã khôi phục textReading gốc, audio trimmed lưu ở trimmed_audio.")
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
            QMessageBox.warning(self, "Lỗi", "Module capcut_srt_gui không khả dụng. Vui lòng cài đặt pydub.")
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

        align_mode = self.capcut_align_mode_combo.currentIndex() if hasattr(self, 'capcut_align_mode_combo') and self.capcut_align_mode_combo else 0

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
                audio_path = a.get("resolved_path") or capcut_srt.resolve_audio_path_from_original(
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

            audio_path = a.get("resolved_path") or capcut_srt.resolve_audio_path_from_original(
                a.get("path"), project_dir
            )
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
        if hasattr(self, 'log_text') and self.log_text:
            self.log_text.append(text)
            self.log_text.verticalScrollBar().setValue(
                self.log_text.verticalScrollBar().maximum()
            )

        # In ra console để debug
        print(text)
    
    def clear_log(self):
        """Xóa log"""
        if hasattr(self, 'log_text') and self.log_text:
            self.log_text.clear()
            self.log_message("Log đã được làm sạch")
    
    def save_log(self):
        """Lưu log ra file"""
        if not hasattr(self, 'log_text') or not self.log_text:
            QMessageBox.warning(self, "Cảnh báo", "Không có log để lưu!")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu Log",
            "srt_compare_log.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
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
            self,
            "Chọn file SRT để chia",
            "",
            "SRT Files (*.srt);;All Files (*)"
        )
        if file_path:
            path = Path(file_path)
            self.split_file_path = path
            self.split_file_label.setText(path.name)
            self.split_file_label.setStyleSheet("color: #4caf50; font-size: 15px; font-weight: bold;")
            self.log_message(f"Đã chọn file chia phụ đề: {path.name}")

    def split_subtitles(self):
        """Chia file SRT thành nhiều nhóm theo số lượng phụ đề"""
        # Ưu tiên file chọn riêng ở tab Chia phụ đề, nếu không có thì dùng File 1 ở tab So sánh
        source_path = self.split_file_path or self.file1_path
        if not source_path or not source_path.exists():
            QMessageBox.warning(
                self,
                "Lỗi",
                "Vui lòng chọn File 1 ở tab 'So sánh SRT' (hoặc chọn file trong tab 'Chia phụ đề') trước khi chia!"
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
        self.split_file_label.setStyleSheet("color: #4caf50; font-size: 15px; font-weight: bold;")

        start = 0
        group_index = 1
        while start < total:
            end = min(start + group_size, total)
            self.split_chunks.append((start, end))
            item_text = f"Nhóm {group_index}: {start + 1} - {end} ({end - start} phụ đề)"
            self.split_chunks_list.addItem(item_text)
            start = end
            group_index += 1

        if self.split_chunks:
            self.split_chunks_list.setCurrentRow(0)

        self.log_message(
            f"Đã chia {total} phụ đề thành {len(self.split_chunks)} nhóm, mỗi nhóm tối đa {group_size} phụ đề."
        )

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

def _main_standalone():
    """Hàm main"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Sử dụng Fusion style
    
    window = SRTCompareApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__" and False:
    main()
