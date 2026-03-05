import json
import os
import subprocess
from typing import List, Dict, Any, Tuple, Optional

from pydub import AudioSegment
from pydub import silence as pydub_silence

# Nếu bạn đã cài ffmpeg nhưng không có trong PATH, có thể đặt đường dẫn tuyệt đối tại đây
# Ví dụ:
# AudioSegment.converter = r"C:\tools\ffmpeg\bin\ffmpeg.exe"


def ms_to_srt(time_in_ms: int) -> str:
    ms = time_in_ms % 1000
    total_seconds = (time_in_ms - ms) // 1000
    seconds = total_seconds % 60
    total_minutes = (total_seconds - seconds) // 60
    minutes = total_minutes % 60
    hour = (total_minutes - minutes) // 60
    return f"{hour:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def load_draft_json(project_dir: str) -> Dict[str, Any]:
    """
    Tự động tìm file draft của CapCut trong thư mục project:
    - Windows: draft_content.json
    - Mac / Linux: draft_info.json
    Nếu người dùng copy thẳng từ CapCut Drafts thì thường sẽ có draft_content.json.
    """
    candidates = [
        os.path.join(project_dir, "draft_content.json"),
        os.path.join(project_dir, "draft_info.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(
        "Không tìm thấy file draft_content.json hoặc draft_info.json trong thư mục đã chọn."
    )


def extract_subtitles_with_audio(data: Dict[str, Any], project_dir: str) -> List[Dict[str, Any]]:
    materials = data.get("materials", {})
    tracks = data.get("tracks", [])

    texts = materials.get("texts", [])
    if not texts or not tracks:
        return []

    # Lấy timing từ track phụ đề (giống index.js: mặc định track thứ 2 – index 1)
    sub_track_number = 1
    if len(tracks) <= sub_track_number:
        sub_track_number = 0
    sub_timing = tracks[sub_track_number].get("segments", [])

    subtitles_info: List[Dict[str, Any]] = []
    for t in texts:
        raw_content = t.get("content", "")
        # Giống logic JS: loại bỏ tag <> và [] nếu là text thường
        content = (
            raw_content.replace("<", " <")
            .replace(">", "> ")
            .replace("<", "")
            .replace(">", "")
            .replace("[", "")
            .replace("]", "")
        )
        # Thử parse dạng JSON (V3)
        try:
            content_v3 = json.loads(raw_content)
            if isinstance(content_v3, dict) and "text" in content_v3:
                content = content_v3["text"]
        except Exception:
            pass
        subtitles_info.append(
            {
                "content": content,
                "id": t.get("id"),
            }
        )

    # Gán thời gian cho phụ đề
    for idx, s in enumerate(subtitles_info):
        segment = next(
            (seg for seg in sub_timing if seg.get("material_id") == s["id"]), None
        )
        while not segment:
            sub_track_number += 1
            if sub_track_number >= len(tracks):
                break
            sub_timing = tracks[sub_track_number].get("segments", [])
            segment = next(
                (seg for seg in sub_timing if seg.get("material_id") == s["id"]), None
            )
        if not segment:
            # không tìm được timing, bỏ qua thời gian nhưng vẫn giữ text
            continue
        start = segment.get("target_timerange", {}).get("start", 0)
        duration = segment.get("target_timerange", {}).get("duration", 0)
        end = start + duration

        # Detect nhanh đơn vị để xuất SRT cho đúng (ms vs µs)
        # Nếu duration quá lớn (>= 50_000) coi là µs và chuyển về ms.
        if duration >= 50_000:
            start_ms = start // 1000
            end_ms = end // 1000
        else:
            start_ms = start
            end_ms = end

        s["start"] = start
        s["end"] = end
        s["srtStart"] = ms_to_srt(start_ms)
        s["srtEnd"] = ms_to_srt(end_ms)
        s["subNumber"] = idx + 1
        s["srtTiming"] = f'{s["srtStart"]} --> {s["srtEnd"]}'

    # Gán audio tương ứng (nếu có)
    audios = materials.get("audios", [])
    if audios:
        audio_by_id = {}
        audio_by_text_id = {}
        audio_by_attached_text_id = {}

        for a in audios:
            aid = a.get("id")
            if aid:
                audio_by_id[aid] = a
            text_id = a.get("text_id")
            if text_id:
                audio_by_text_id[text_id] = a
            attached_text_id = a.get("attached_text_id")
            if attached_text_id:
                audio_by_attached_text_id[attached_text_id] = a
            extra_info = a.get("extra_info") or {}
            extra_text_id = extra_info.get("text_id")
            if extra_text_id:
                audio_by_text_id[extra_text_id] = a

        for s in subtitles_info:
            sid = s.get("id")
            audio = (
                audio_by_text_id.get(sid)
                or audio_by_attached_text_id.get(sid)
                or audio_by_id.get(sid)
            )
            if audio:
                original_path = (
                    audio.get("path")
                    or audio.get("file_path")
                    or audio.get("local_material_path")
                )
                resolved_path = resolve_audio_path_from_original(
                    original_path, project_dir
                )
                s["audio"] = {
                    "id": audio.get("id"),
                    "path": original_path,
                    "resolved_path": resolved_path,
                    "material_name": audio.get("material_name"),
                }

    return subtitles_info


def resolve_audio_path_from_original(original_path: str, project_dir: str) -> str:
    """
    Chuẩn hóa đường dẫn audio từ đường dẫn trong draft CapCut.
    Hỗ trợ placeholder ##_draftpath_placeholder_...##/textReading/...
    và trường hợp path chỉ là tên file tương đối.
    """
    if not original_path:
        return ""

    # Nếu là đường dẫn tuyệt đối thì dùng luôn
    if os.path.isabs(original_path):
        return os.path.normpath(original_path)

    # Placeholder do CapCut dùng
    if "##_draftpath_placeholder_" in original_path:
        marker = "##_draftpath_placeholder_"
        idx = original_path.find(marker)
        end_marker = "##/textReading"
        end_idx = original_path.find(end_marker, idx)
        if idx != -1 and end_idx != -1:
            suffix = original_path[end_idx + len(end_marker) :].lstrip("/\\")
            proj_lower = project_dir.lower().rstrip("/\\")
            # Nếu project_dir đã là folder textReading → dùng luôn
            if proj_lower.endswith("textreading"):
                base_dir = project_dir
            else:
                # Thử ưu tiên project_dir/textReading (như cấu trúc thật của bạn)
                base_dir = os.path.join(project_dir, "textReading")
            return os.path.normpath(os.path.join(base_dir, suffix))

    # Nếu project_dir đã là textReading
    proj_lower = project_dir.lower().rstrip("/\\")
    if proj_lower.endswith("textreading"):
        return os.path.normpath(os.path.join(project_dir, original_path))

    # project_dir là Draft gốc → ưu tiên project_dir/textReading
    candidate = os.path.join(project_dir, "textReading", original_path)
    # Không kiểm tra tồn tại ở đây vì tool có thể chạy trên máy khác,
    # việc tồn tại sẽ được kiểm tra ở chỗ gọi.
    return os.path.normpath(candidate)


def write_outputs(project_dir: str, subtitles: List[Dict[str, Any]]) -> None:
    srt_out_lines: List[str] = []
    copy_out_lines: List[str] = []

    for s in subtitles:
        if "srtTiming" in s and "subNumber" in s:
            srt_out_lines.append(str(s["subNumber"]))
            srt_out_lines.append(s["srtTiming"])
            srt_out_lines.append(s.get("content", ""))
            srt_out_lines.append("")  # dòng trống
        copy_out_lines.append(s.get("content", ""))

    srt_out = "\n".join(srt_out_lines)
    copy_out = "\n".join(copy_out_lines)

    srt_path = os.path.join(project_dir, "subtitles.srt")
    copy_path = os.path.join(project_dir, "copy.txt")
    json_path = os.path.join(project_dir, "subtitles_with_audio.json")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_out)
    with open(copy_path, "w", encoding="utf-8") as f:
        f.write(copy_out)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(subtitles, f, ensure_ascii=False, indent=2)


def detect_active_region(
    audio: AudioSegment, silence_thresh: int, min_silence_len: int
) -> Tuple[int, int]:
    """
    Tìm đoạn có âm thanh (không im lặng) trong file audio.
    Trả về (start_ms, end_ms). Nếu không tìm được thì trả về (0, len_audio).
    """
    non_silent_ranges = pydub_silence.detect_nonsilent(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
    )
    if not non_silent_ranges:
        return 0, len(audio)
    start = min(r[0] for r in non_silent_ranges)
    end = max(r[1] for r in non_silent_ranges)
    return start, end


def trim_audio_file(
    src_path: str,
    dst_path: str,
    silence_thresh: int = -40,
    min_silence_len: int = 300,
) -> Tuple[bool, str]:
    """
    Trim im lặng bằng ffmpeg filter silenceremove, tập trung chủ yếu ở cuối file.

    Tương đương với:
      ffmpeg -i input.wav -af "silenceremove=stop_periods=-1:stop_duration=0.2:stop_threshold=-40dB" output.wav

    Trong đó:
      - stop_threshold (dB) lấy từ silence_thresh (thường -35 đến -45)
      - stop_duration (giây) = min_silence_len / 1000
    """
    if not os.path.isfile(src_path):
        return False, "not_found"

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)

    # Xác định đường dẫn ffmpeg: ưu tiên cấu hình trong AudioSegment.converter, nếu không thì dùng "ffmpeg"
    ffmpeg_path = getattr(AudioSegment, "converter", None) or "ffmpeg"

    stop_duration_sec = max(min_silence_len / 1000.0, 0.05)
    # Chỉ cắt im lặng ở CUỐI file:
    # start_periods=0  → không đụng phần đầu
    # stop_periods=1   → chỉ trim đoạn im lặng cuối cùng thỏa điều kiện
    filter_str = (
        f"silenceremove=start_periods=0:stop_periods=1:"
        f"stop_duration={stop_duration_sec}:"
        f"stop_threshold={silence_thresh}dB"
    )

    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        src_path,
        "-af",
        filter_str,
        dst_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or "ffmpeg_error")
    except Exception as e:
        return False, str(e)


def get_audio_duration_ms(path: str) -> int:
    """
    Lấy độ dài audio (ms) bằng ffprobe.
    Trả về -1 nếu không đo được.
    """
    ffmpeg_path = getattr(AudioSegment, "converter", None) or "ffmpeg"
    ffprobe = (
        os.path.join(os.path.dirname(ffmpeg_path), "ffprobe")
        if os.path.dirname(ffmpeg_path)
        else "ffprobe"
    )
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return -1
        seconds = float(result.stdout.strip())
        return int(seconds * 1000)
    except Exception:
        return -1


def build_atempo_chain(speed: float) -> str:
    """
    Tách speed thành chuỗi atempo (mỗi bước trong [0.5, 2.0]) để ffmpeg chấp nhận.
    """
    factors = []
    x = speed
    while x > 2.0:
        factors.append(2.0)
        x /= 2.0
    while x < 0.5:
        factors.append(0.5)
        x /= 0.5
    factors.append(x)
    return ",".join(f"atempo={f:.6f}" for f in factors)


def speedup_audio_to_fit(path: str, target_duration_ms: int) -> bool:
    """
    Tăng tốc file audio để độ dài ~ target_duration_ms (ms) mà không cắt tiếng.
    Nếu audio ngắn hơn hoặc gần bằng target thì giữ nguyên.
    Ghi đè lên file gốc (sử dụng file tạm).
    """
    if target_duration_ms <= 0 or not os.path.isfile(path):
        return False

    current_ms = get_audio_duration_ms(path)
    if current_ms <= 0:
        return False

    # Nếu audio đã ngắn hơn hoặc gần bằng slot cho phép (chênh < 20ms) thì khỏi làm gì
    if current_ms <= target_duration_ms + 20:
        return False

    speed = current_ms / float(target_duration_ms)
    if speed <= 1.0:
        # Chỉ xử lý trường hợp phải nhanh hơn
        return False

    ffmpeg_path = getattr(AudioSegment, "converter", None) or "ffmpeg"
    atempo_filter = build_atempo_chain(speed)

    tmp_path = path + ".speedtmp.wav"
    try:
        result = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                path,
                "-filter:a",
                atempo_filter,
                tmp_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0 or not os.path.isfile(tmp_path):
            return False
        # Ghi đè file gốc
        os.replace(tmp_path, path)
        return True
    except Exception:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False


def get_effective_audio_range(
    segment: Dict[str, Any],
    project_dir: str,
    materials_map: Dict[str, Any],
    video_speed: float,
    audio_speed: float,
) -> Optional[Dict[str, Any]]:
    """
    Calculate the effective start/end time of an audio segment on the timeline,
    considering silence removal and speed adjustments.
    """
    mat_id = segment.get("material_id")
    material = materials_map.get(mat_id)
    if not material:
        return None

    # Get Paths
    path = material.get("path")
    if not path:
        return None
    resolved_path = resolve_audio_path_from_original(path, project_dir)
    if not os.path.isfile(resolved_path):
        # Fallback or error? For now skip
        return None

    # Get Source Timerange (in microseconds -> Convert to ms)
    source_range = segment.get("source_timerange", {})
    src_start = source_range.get("start", 0) // 1000
    src_duration = source_range.get("duration", 0) // 1000

    # Get Target Timerange (Where it is placed on timeline)
    target_range = segment.get("target_timerange", {})
    timeline_start_raw = target_range.get("start", 0) // 1000
    
    # Load Audio and Slice
    try:
        # Optimization: We could use pydub.utils.mediainfo to check duration first,
        # but we need to scan content for silence anyway.
        # Use simple caching or just load. fast_load=True if possible?
        audio = AudioSegment.from_file(resolved_path)
        
        # Slice the segment used
        audio_slice = audio[src_start : src_start + src_duration]
        
        # Detect Silence in this slice
        # silence_thresh=-50dB, min_silence_len=100ms
        non_silent_ranges = pydub_silence.detect_nonsilent(
            audio_slice, min_silence_len=100, silence_thresh=-50
        )
        
        if not non_silent_ranges:
            # Entirely silent?
            return None
        
        # First non-silent start and last non-silent end relative to the SLICE
        sound_start_rel = min(r[0] for r in non_silent_ranges)
        sound_end_rel = max(r[1] for r in non_silent_ranges)
        
        # "True" Duration of sound
        sound_duration = sound_end_rel - sound_start_rel
        
        # Calculate Shift on Timeline
        # The segment starts at timeline_start_raw. 
        # But the SOUND starts at timeline_start_raw + sound_start_rel
        
        effective_start_raw = timeline_start_raw + sound_start_rel
        effective_end_raw = effective_start_raw + sound_duration
        
        # --- Apply Speed Logic ---
        
        # 1. Video Speed: Stretches the timeline position.
        # If video is 0.5x (slow), everything moves further apart.
        # New Start = Original Start / Video Speed
        final_start = effective_start_raw / video_speed
        
        # 2. Audio Speed: Stretches/Shrinks duration.
        # If audio is 2.0x (fast), it plays quicker -> shorter duration.
        # Duration = Original Duration / Audio Speed
        final_duration = sound_duration / audio_speed
        
        final_end = final_start + final_duration
        
        return {
            "id": segment.get("id"),
            "name": material.get("name", "Unknown"),
            "start": final_start,
            "end": final_end,
            "duration": final_duration,
            "raw_start": timeline_start_raw,
            "silence_trimmed_start_ms": sound_start_rel,
            "silence_trimmed_end_ms": (src_duration - sound_end_rel)
        }

    except Exception as e:
        print(f"Error processing audio {resolved_path}: {e}")
        return None


def check_audio_overlap(
    project_dir: str, video_speed: float = 1.0, audio_speed: float = 1.0, progress_callback=None
) -> List[Dict[str, Any]]:
    """
    Check for overlapping audio clips with speed adjustments and silence detection.
    """
    try:
        data = load_draft_json(project_dir)
    except Exception as e:
        return [{"error": str(e)}]

    materials = data.get("materials", {})
    tracks = data.get("tracks", [])
    
    audios = materials.get("audios", [])
    # Build Map
    materials_map = {a["id"]: a for a in audios}
    
    # Pre-calculate total segments for progress
    total_segments = 0
    for track in tracks:
        total_segments += len(track.get("segments", []))
    
    segments_to_check = []
    processed_count = 0

    for track in tracks:
        # Check all tracks, iterate segments
        for seg in track.get("segments", []):
            mat_id = seg.get("material_id")
            if mat_id in materials_map:
                # It is an audio segment
                # Process with "Smart" logic
                res = get_effective_audio_range(
                    seg, project_dir, materials_map, video_speed, audio_speed
                )
                if res:
                    segments_to_check.append(res)
            
            processed_count += 1
            if progress_callback:
                # Report progress up to 90%, reserve last 10% for overlap check
                progress = int((processed_count / total_segments) * 90)
                progress_callback(progress)

    # Sort by new start time
    segments_to_check.sort(key=lambda x: x["start"])

    overlaps = []
    
    # Check for overlaps
    count = len(segments_to_check)
    for i in range(count):
        current = segments_to_check[i]
        
        for j in range(i + 1, count):
            next_clip = segments_to_check[j]
            
            # Start of next >= End of current? -> No Overlap (and we can break optimization)
            if next_clip["start"] >= current["end"]:
                break
            
            # Found overlap
            overlap_val = current["end"] - next_clip["start"]
            
            # Tolerance 10ms
            if overlap_val > 10:
                overlaps.append({
                    "clip_A": current["name"],
                    "clip_B": next_clip["name"],
                    "start_overlap_ms": next_clip["start"],
                    "end_overlap_ms": current["end"],
                    "overlap_duration_ms": overlap_val,
                    "formatted_time": ms_to_srt(int(next_clip['start'])),
                    "info": "Silence ignored"
                })
        
        if progress_callback:
             # Map remaining 10%
             if count > 0:
                progress = 90 + int((i / count) * 10)
                progress_callback(progress)

    if progress_callback:
        progress_callback(100)

    return overlaps


