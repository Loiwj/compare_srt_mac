#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module xử lý file SRT (phụ đề)
Parse, so sánh và chỉnh sửa file SRT
"""

import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple


class SubtitleEntry:
    """Lưu thông tin một subtitle entry"""
    def __init__(self, index: int, start_time: str, end_time: str, content: List[str], line_number: int):
        self.index = index
        self.start_time = start_time
        self.end_time = end_time
        self.content = content
        self.line_number = line_number  # Dòng bắt đầu trong file
    
    def time_to_milliseconds(self, time_str: str) -> int:
        """Chuyển đổi thời gian từ format HH:MM:SS,mmm sang milliseconds"""
        match = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})', time_str)
        if not match:
            return 0
        hours, minutes, seconds, milliseconds = map(int, match.groups())
        return hours * 3600000 + minutes * 60000 + seconds * 1000 + milliseconds
    
    def get_start_ms(self) -> int:
        """Lấy thời gian bắt đầu tính bằng milliseconds"""
        return self.time_to_milliseconds(self.start_time)
    
    def get_end_ms(self) -> int:
        """Lấy thời gian kết thúc tính bằng milliseconds"""
        return self.time_to_milliseconds(self.end_time)
    
    def to_srt_format(self) -> str:
        """Chuyển đổi entry thành format SRT"""
        lines = [str(self.index), f"{self.start_time} --> {self.end_time}"]
        # Thêm nội dung, giữ nguyên tất cả các dòng (kể cả dòng trống)
        if self.content:
            lines.extend(self.content)
        lines.append("")  # Dòng trống kết thúc entry
        return "\n".join(lines)


def parse_srt_file(file_path: Path) -> List[SubtitleEntry]:
    """Parse file SRT và trả về danh sách các subtitle entry"""
    entries = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        raise Exception(f"Lỗi đọc file {file_path}: {e}")
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Bỏ qua dòng trống
        if not line:
            i += 1
            continue
        
        # Kiểm tra xem có phải số thứ tự không
        if line.isdigit():
            index = int(line)
            start_line = i + 1  # Dòng bắt đầu trong file (1-based)
            
            # Đọc dòng thời gian
            if i + 1 < len(lines):
                time_line = lines[i + 1].strip()
                time_match = re.match(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})', time_line)
                
                if time_match:
                    start_time = time_match.group(1)
                    end_time = time_match.group(2)
                    
                    # Đọc nội dung subtitle
                    content = []
                    j = i + 2
                    while j < len(lines) and lines[j].strip():
                        content.append(lines[j].strip())
                        j += 1
                    
                    entry = SubtitleEntry(index, start_time, end_time, content, start_line)
                    entries.append(entry)
                    i = j
                else:
                    i += 1
            else:
                i += 1
        else:
            i += 1
    
    return entries


def compare_srt_files(file1: Path, file2: Path, tolerance_ms: int = 0) -> Dict:
    """
    So sánh hai file SRT và trả về kết quả
    
    Returns:
        Dict chứa:
            - errors: List các lỗi lệch thời gian
            - file1_entries: Số entries file 1
            - file2_entries: Số entries file 2
            - matched: Số entries khớp
            - total_compared: Tổng số entries được so sánh
    """
    try:
        file1_entries = parse_srt_file(file1)
        file2_entries = parse_srt_file(file2)
    except Exception as e:
        raise Exception(str(e))
    
    errors = []
    per_entry_stats = []
    min_len = min(len(file1_entries), len(file2_entries))
    
    # So sánh từng entry
    for i in range(min_len):
        entry1 = file1_entries[i]
        entry2 = file2_entries[i]
        
        entry1_start = entry1.get_start_ms()
        entry1_end = entry1.get_end_ms()
        entry2_start = entry2.get_start_ms()
        entry2_end = entry2.get_end_ms()
        
        # Kiểm tra lệch thời gian bắt đầu
        start_diff = abs(entry1_start - entry2_start)
        start_ok = start_diff <= tolerance_ms
        if start_diff > tolerance_ms:
            errors.append({
                'type': 'start',
                'index': entry1.index,
                'file1_line': entry1.line_number,
                'file2_line': entry2.line_number,
                'file1_time': entry1.start_time,
                'file2_time': entry2.start_time,
                'diff_ms': start_diff,
                'entry_index': i
            })
        
        # Kiểm tra lệch thời gian kết thúc
        end_diff = abs(entry1_end - entry2_end)
        end_ok = end_diff <= tolerance_ms
        if end_diff > tolerance_ms:
            errors.append({
                'type': 'end',
                'index': entry1.index,
                'file1_line': entry1.line_number,
                'file2_line': entry2.line_number,
                'file1_time': entry1.end_time,
                'file2_time': entry2.end_time,
                'diff_ms': end_diff,
                'entry_index': i
            })

        # Lưu thống kê từng entry để hiển thị chi tiết nếu cần
        per_entry_stats.append({
            'index': entry1.index,
            'start_diff': start_diff,
            'end_diff': end_diff,
            'start_ok': start_ok,
            'end_ok': end_ok,
        })
    
    # Số entry "đúng" được tính là entry mà cả start và end đều trong tolerance
    correct_entries = sum(1 for s in per_entry_stats if s['start_ok'] and s['end_ok'])

    return {
        'errors': errors,
        'file1_entries': len(file1_entries),
        'file2_entries': len(file2_entries),
        'matched': correct_entries,
        'total_compared': min_len,
        'per_entry_stats': per_entry_stats,
        'file1_extra': file1_entries[min_len:] if len(file1_entries) > min_len else [],
        'file2_extra': file2_entries[min_len:] if len(file2_entries) > min_len else []
    }


def create_thaisub_file(source_file: Path, output_file: Optional[Path] = None) -> Path:
    """
    Tạo file thaisub từ file nguồn (sao chép cấu trúc nhưng để trống nội dung)
    Nếu file "done" tồn tại, sẽ tự động tìm và sử dụng
    
    Args:
        source_file: File SRT nguồn
        output_file: File output (nếu None thì tự động tạo tên)
    
    Returns:
        Path đến file đã tạo
    """
    if output_file is None:
        # Kiểm tra xem có file "done" không
        base_name = source_file.stem
        done_file = source_file.parent / f"{base_name} done.srt"
        
        if done_file.exists():
            # Sử dụng file done làm nguồn
            source_file = done_file
            base_name = source_file.stem.replace(" done", "")
        
        # Tạo tên file mới: tên_file_thaisub.srt
        output_file = source_file.parent / f"{base_name}_thaisub.srt"
    
    entries = parse_srt_file(source_file)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for entry in entries:
            # Giữ nguyên thời gian, để trống nội dung
            f.write(f"{entry.index}\n")
            f.write(f"{entry.start_time} --> {entry.end_time}\n")
            f.write("\n")  # Nội dung trống
            f.write("\n")  # Dòng trống kết thúc entry
    
    return output_file


def save_srt_file(entries: List[SubtitleEntry], output_file: Path):
    """Lưu danh sách entries thành file SRT"""
    with open(output_file, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(entry.to_srt_format())
            f.write("\n")


def fix_srt_entry(entries: List[SubtitleEntry], entry_index: int, 
                  new_start_time: Optional[str] = None, 
                  new_end_time: Optional[str] = None,
                  new_content: Optional[List[str]] = None) -> bool:
    """
    Sửa một entry trong danh sách
    
    Args:
        entries: Danh sách entries
        entry_index: Index của entry cần sửa (0-based)
        new_start_time: Thời gian bắt đầu mới (None nếu không đổi)
        new_end_time: Thời gian kết thúc mới (None nếu không đổi)
        new_content: Nội dung mới (None nếu không đổi)
    
    Returns:
        True nếu thành công
    """
    if entry_index < 0 or entry_index >= len(entries):
        return False
    
    entry = entries[entry_index]
    if new_start_time:
        entry.start_time = new_start_time
    if new_end_time:
        entry.end_time = new_end_time
    if new_content is not None:
        entry.content = new_content
    
    return True
