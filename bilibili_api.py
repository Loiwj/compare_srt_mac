"""
Bilibili API Module - QR Login + Native Download Engine.

Module này xử lý:
1. QR Code Login: Tạo QR, poll trạng thái, lưu session cookies
2. Native Download Engine: Tải video trực tiếp từ Bilibili API (fallback khi yt-dlp lỗi)
3. Cookie Refresh: Tự động refresh cookie bằng RSA-OAEP

Tham khảo: https://github.com/j4rviscmd/bilibili-downloader-gui
"""

import hashlib
import json
import os
import re
import subprocess
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
except ImportError:
    hashes = None
    serialization = None
    asym_padding = None


# ============================================================================
# Constants
# ============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com"

# QR Login endpoints
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
BUVID_URL = "https://api.bilibili.com/x/frontend/finger/spi"
USER_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# Cookie refresh endpoints
COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
COOKIE_REFRESH_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/refresh"
CONFIRM_REFRESH_URL = "https://passport.bilibili.com/x/passport-login/web/confirm/refresh"
CORRESPOND_URL_PREFIX = "https://www.bilibili.com/correspond/1/"

# Video API endpoints
VIDEO_INFO_URL = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_URL = "https://api.bilibili.com/x/player/wbi/playurl"

# QR Login status codes
QR_STATUS_NOT_SCANNED = 86101
QR_STATUS_SCANNED_WAIT_CONFIRM = 86090
QR_STATUS_SUCCESS = 0
QR_STATUS_EXPIRED = 86038

# Quality mappings (Bilibili quality ID → label)
QUALITY_MAP = {
    127: "8K 超高清",
    126: "Dolby Vision",
    125: "HDR 真彩",
    120: "4K 超清",
    116: "1080P 60帧",
    112: "1080P 高码率",
    80: "1080P 高清",
    64: "720P 高清",
    32: "480P 清晰",
    16: "360P 流畅",
}

# Bilibili RSA Public Key (cho cookie refresh)
BILIBILI_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""


def _default_headers(cookies=None):
    """Tạo headers mặc định cho request Bilibili."""
    h = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
    }
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        h["Cookie"] = cookie_str
    return h


def _subprocess_no_console_kwargs():
    """Tránh hiện console window khi chạy subprocess trên Windows."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# ============================================================================
# QR Code Login
# ============================================================================


class BilibiliQRLogin:
    """Xử lý flow đăng nhập Bilibili bằng QR code."""

    def generate_qr(self):
        """
        Gọi Bilibili API để tạo QR code mới.

        Returns:
            tuple: (qr_image_bytes: bytes, qrcode_key: str)
                - qr_image_bytes: PNG image bytes của QR code
                - qrcode_key: key dùng để poll trạng thái

        Raises:
            RuntimeError: Nếu API trả về lỗi hoặc thiếu thư viện qrcode
        """
        if qrcode is None:
            raise RuntimeError(
                "Thiếu thư viện qrcode. Chạy: pip install qrcode[pil]"
            )

        resp = requests.get(QR_GENERATE_URL, headers=_default_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"QR generate API error: {data.get('message', 'unknown')}")

        qr_data = data.get("data", {})
        url = qr_data.get("url", "")
        qrcode_key = qr_data.get("qrcode_key", "")

        if not url or not qrcode_key:
            raise RuntimeError("Không nhận được URL hoặc qrcode_key từ API")

        # Tạo QR code image
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_image_bytes = buf.getvalue()

        return qr_image_bytes, qrcode_key

    def poll_status(self, qrcode_key):
        """
        Poll trạng thái quét QR code.

        Args:
            qrcode_key: Key từ generate_qr()

        Returns:
            tuple: (status_code: int, cookies: dict or None)
                - status_code: 86101 (chờ), 86090 (đã quét), 0 (thành công), 86038 (hết hạn)
                - cookies: dict cookies nếu thành công, None nếu chưa

        Raises:
            RuntimeError: Nếu API trả về lỗi
        """
        resp = requests.get(
            QR_POLL_URL,
            params={"qrcode_key": qrcode_key},
            headers=_default_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"QR poll API error: {data.get('message', 'unknown')}")

        poll_data = data.get("data", {})
        status_code = poll_data.get("code", -1)

        if status_code == QR_STATUS_SUCCESS:
            # Parse cookies từ URL
            login_url = poll_data.get("url", "")
            refresh_token = poll_data.get("refresh_token", "")
            cookies = self._extract_cookies_from_url(login_url)
            cookies["refresh_token"] = refresh_token

            # Lấy buvid3/buvid4
            try:
                buvid3, buvid4 = self.fetch_buvid()
                cookies["buvid3"] = buvid3
                cookies["buvid4"] = buvid4
            except Exception:
                pass  # Không critical, tiếp tục

            return status_code, cookies

        return status_code, None

    def _extract_cookies_from_url(self, url):
        """Trích xuất cookies từ login URL."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return {
            "SESSDATA": params.get("SESSDATA", [""])[0],
            "bili_jct": params.get("bili_jct", [""])[0],
            "DedeUserID": params.get("DedeUserID", [""])[0],
            "DedeUserID__ckMd5": params.get("DedeUserID__ckMd5", [""])[0],
        }

    def fetch_buvid(self):
        """
        Lấy buvid3 và buvid4 từ Bilibili API.
        Cần thiết cho WBI authentication.

        Returns:
            tuple: (buvid3: str, buvid4: str)
        """
        resp = requests.get(
            BUVID_URL, headers=_default_headers(), timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Buvid API error: {data.get('message')}")

        buvid_data = data.get("data", {})
        return buvid_data.get("b_3", ""), buvid_data.get("b_4", "")

    def fetch_user_info(self, cookies):
        """
        Lấy thông tin user đã đăng nhập.

        Args:
            cookies: dict cookies từ login

        Returns:
            dict: {"uname": str, "mid": int, "face": str} hoặc {} nếu lỗi
        """
        try:
            resp = requests.get(
                USER_NAV_URL, headers=_default_headers(cookies), timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 0:
                nav_data = data.get("data", {})
                return {
                    "uname": nav_data.get("uname", ""),
                    "mid": nav_data.get("mid", 0),
                    "face": nav_data.get("face", ""),
                    "isLogin": nav_data.get("isLogin", False),
                }
        except Exception:
            pass
        return {}


# ============================================================================
# Cookie Refresh
# ============================================================================


class BilibiliCookieRefresh:
    """Refresh Bilibili cookies khi hết hạn."""

    def check_need_refresh(self, cookies):
        """
        Kiểm tra cookie có cần refresh không.

        Returns:
            tuple: (need_refresh: bool, timestamp: int)
        """
        try:
            resp = requests.get(
                COOKIE_INFO_URL, headers=_default_headers(cookies), timeout=10
            )
            data = resp.json()
            if data.get("code") == 0:
                info = data.get("data", {})
                return info.get("refresh", False), info.get("timestamp", 0)
            elif data.get("code") == -101:
                # Session expired
                return False, 0
        except Exception:
            pass
        return False, 0

    def refresh(self, cookies):
        """
        Refresh cookie flow đầy đủ.

        Args:
            cookies: dict cookies hiện tại (phải có refresh_token, bili_jct)

        Returns:
            dict: cookies mới nếu thành công, None nếu lỗi
        """
        if serialization is None or hashes is None or asym_padding is None:
            return None  # Thiếu thư viện cryptography

        need_refresh, timestamp = self.check_need_refresh(cookies)
        if not need_refresh:
            return None

        refresh_token = cookies.get("refresh_token", "")
        bili_jct = cookies.get("bili_jct", "")

        if not refresh_token or not bili_jct:
            return None

        try:
            # Step 1: Generate CorrespondPath
            correspond_path = self._generate_correspond_path(timestamp)

            # Step 2: Fetch refresh_csrf
            refresh_csrf = self._fetch_refresh_csrf(cookies, correspond_path)
            if not refresh_csrf:
                return None

            # Step 3: Call refresh API
            resp = requests.post(
                COOKIE_REFRESH_URL,
                headers=_default_headers(cookies),
                data={
                    "csrf": bili_jct,
                    "refresh_csrf": refresh_csrf,
                    "source": "main_web",
                    "refresh_token": refresh_token,
                },
                timeout=10,
            )
            resp_data = resp.json()
            if resp_data.get("code") != 0:
                return None

            # Trích xuất cookies mới từ Set-Cookie headers
            new_cookies = {}
            for cookie_header in resp.headers.get("Set-Cookie", "").split(","):
                for part in cookie_header.split(";"):
                    part = part.strip()
                    if "=" in part:
                        name, val = part.split("=", 1)
                        name = name.strip()
                        if name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5"):
                            new_cookies[name] = val.strip()

            new_refresh_token = resp_data.get("data", {}).get("refresh_token", "")

            # Step 4: Confirm refresh
            confirm_cookies = {**cookies, **new_cookies}
            requests.post(
                CONFIRM_REFRESH_URL,
                headers=_default_headers(confirm_cookies),
                data={
                    "csrf": new_cookies.get("bili_jct", ""),
                    "refresh_token": refresh_token,
                },
                timeout=10,
            )

            # Build final cookies
            final = {**cookies, **new_cookies}
            if new_refresh_token:
                final["refresh_token"] = new_refresh_token
            return final

        except Exception:
            return None

    def _generate_correspond_path(self, timestamp):
        """Mã hóa 'refresh_{timestamp}' bằng RSA-OAEP."""
        public_key = serialization.load_pem_public_key(
            BILIBILI_PUBLIC_KEY_PEM.encode()
        )
        message = f"refresh_{timestamp}".encode()
        encrypted = public_key.encrypt(
            message,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return encrypted.hex()

    def _fetch_refresh_csrf(self, cookies, correspond_path):
        """Lấy refresh_csrf từ correspond endpoint."""
        try:
            url = f"{CORRESPOND_URL_PREFIX}{correspond_path}"
            resp = requests.get(
                url, headers=_default_headers(cookies), timeout=10
            )
            html = resp.text

            # Parse: <div id="1-name">{refresh_csrf}</div>
            match = re.search(r'<div id="1-name">([^<]+)</div>', html)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None


# ============================================================================
# Native Download Engine (Fallback cho yt-dlp)
# ============================================================================


class BilibiliDownloader:
    """
    Download engine trực tiếp từ Bilibili API.
    Dùng làm fallback khi yt-dlp lỗi.
    """

    def __init__(self, ffmpeg_path=None):
        self.ffmpeg_path = ffmpeg_path or "ffmpeg"

    def get_video_info(self, url_or_bvid, cookies=None):
        """
        Lấy thông tin video từ Bilibili.

        Args:
            url_or_bvid: URL bilibili hoặc BV id
            cookies: dict cookies (optional)

        Returns:
            dict: {
                "bvid": str, "title": str, "description": str,
                "thumbnail": str, "duration": int,
                "parts": [{"cid": int, "title": str, "page": int}],
            }
        """
        bvid = self._extract_bvid(url_or_bvid)
        if not bvid:
            raise RuntimeError(f"Không thể trích xuất BV ID từ: {url_or_bvid}")

        resp = requests.get(
            VIDEO_INFO_URL,
            params={"bvid": bvid},
            headers=_default_headers(cookies),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"Bilibili API error (code {data.get('code')}): {data.get('message', '')}"
            )

        video_data = data.get("data", {})
        pages = video_data.get("pages", [])

        parts = []
        for p in pages:
            parts.append({
                "cid": p.get("cid", 0),
                "title": p.get("part", ""),
                "page": p.get("page", 1),
                "duration": p.get("duration", 0),
            })

        return {
            "bvid": bvid,
            "title": video_data.get("title", ""),
            "description": video_data.get("desc", ""),
            "thumbnail": video_data.get("pic", ""),
            "duration": video_data.get("duration", 0),
            "owner": video_data.get("owner", {}).get("name", ""),
            "parts": parts or [{"cid": video_data.get("cid", 0), "title": "", "page": 1}],
        }

    def get_stream_urls(self, bvid, cid, quality=80, cookies=None):
        """
        Lấy stream URLs (DASH format) cho video.

        Args:
            bvid: BV ID
            cid: Content ID
            quality: Quality ID (mặc định 80 = 1080P)
            cookies: dict cookies

        Returns:
            dict: {
                "video_url": str, "audio_url": str,
                "video_backup": [str], "audio_backup": [str],
                "quality": int, "quality_label": str,
            }
        """
        params = {
            "bvid": bvid,
            "cid": cid,
            "qn": quality,
            "fnval": 4048,  # DASH format flags
            "fnver": 0,
            "fourk": 1,
        }

        resp = requests.get(
            PLAYURL_URL,
            params=params,
            headers=_default_headers(cookies),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"Playurl API error (code {data.get('code')}): {data.get('message', '')}"
            )

        play_data = data.get("data", {})
        dash = play_data.get("dash")

        if dash:
            return self._parse_dash(dash, quality)

        # Fallback: durl format (video + audio gộp)
        durls = play_data.get("durl", [])
        if durls:
            return self._parse_durl(durls, play_data.get("quality", quality))

        raise RuntimeError("Không tìm thấy stream URLs (DASH hoặc durl)")

    def _parse_dash(self, dash, requested_quality):
        """Parse DASH format response."""
        videos = dash.get("video", [])
        audios = dash.get("audio", [])

        if not videos:
            raise RuntimeError("Không có video stream trong DASH response")

        # Chọn video stream theo quality
        video_stream = None
        for v in videos:
            if v.get("id") == requested_quality:
                video_stream = v
                break
        if not video_stream:
            # Fallback: lấy best quality
            video_stream = videos[0]

        # Chọn audio stream (best quality)
        audio_stream = None
        if audios:
            # Sắp xếp theo bandwidth giảm dần
            sorted_audios = sorted(audios, key=lambda a: a.get("bandwidth", 0), reverse=True)
            audio_stream = sorted_audios[0]

        resolved_quality = video_stream.get("id", requested_quality)

        result = {
            "format": "dash",
            "video_url": video_stream.get("baseUrl") or video_stream.get("base_url", ""),
            "video_backup": video_stream.get("backupUrl") or video_stream.get("backup_url", []),
            "quality": resolved_quality,
            "quality_label": QUALITY_MAP.get(resolved_quality, f"{resolved_quality}"),
            "video_codecs": video_stream.get("codecs", ""),
        }

        if audio_stream:
            result["audio_url"] = audio_stream.get("baseUrl") or audio_stream.get("base_url", "")
            result["audio_backup"] = audio_stream.get("backupUrl") or audio_stream.get("backup_url", [])
        else:
            result["audio_url"] = ""
            result["audio_backup"] = []

        return result

    def _parse_durl(self, durls, quality):
        """Parse durl format response (video+audio gộp)."""
        if not durls:
            raise RuntimeError("Không có durl stream")

        durl = durls[0]
        return {
            "format": "durl",
            "video_url": durl.get("url", ""),
            "video_backup": durl.get("backup_url", []),
            "audio_url": "",
            "audio_backup": [],
            "quality": quality,
            "quality_label": QUALITY_MAP.get(quality, f"{quality}"),
        }

    def download_stream(self, url, output_path, cookies=None, progress_callback=None):
        """
        Tải stream (video hoặc audio) với progress.

        Args:
            url: Stream URL
            output_path: Path lưu file
            cookies: dict cookies
            progress_callback: callable(downloaded_bytes, total_bytes) hoặc None

        Returns:
            str: Path file đã tải
        """
        headers = _default_headers(cookies)
        headers["Accept"] = "*/*"
        headers["Accept-Encoding"] = "identity"

        resp = requests.get(url, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()

        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024  # 1MB chunks

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)

        return str(output_path)

    def merge_streams(self, video_path, audio_path, output_path):
        """
        Merge video và audio bằng FFmpeg.

        Args:
            video_path: Path đến file video
            audio_path: Path đến file audio
            output_path: Path output

        Returns:
            str: Path file output
        """
        cmd = [
            self.ffmpeg_path,
            "-y",  # Overwrite
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 phút
            **_subprocess_no_console_kwargs(),
        )

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg merge thất bại: {proc.stderr[:500]}")

        return str(output_path)

    def download_video(
        self,
        url_or_bvid,
        output_dir,
        quality=80,
        cookies=None,
        progress_callback=None,
        log_callback=None,
    ):
        """
        Download video đầy đủ: fetch info → get streams → download → merge.

        Args:
            url_or_bvid: URL hoặc BV ID
            output_dir: Thư mục output
            quality: Quality ID
            cookies: dict cookies
            progress_callback: callable(downloaded, total, phase) hoặc None
            log_callback: callable(message) hoặc None

        Returns:
            str: Path file output
        """
        def _log(msg):
            if log_callback:
                log_callback(msg)

        # 1. Fetch video info
        _log("📋 Đang lấy thông tin video...")
        info = self.get_video_info(url_or_bvid, cookies)
        title = info["title"]
        bvid = info["bvid"]
        _log(f"📋 Tiêu đề: {title}")

        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)[:150]

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for part in info["parts"]:
            cid = part["cid"]
            page = part["page"]
            part_title = part.get("title", "")

            if len(info["parts"]) > 1:
                filename = f"{safe_title}_P{page}_{part_title}"
                filename = re.sub(r'[<>:"/\\|?*]', "_", filename)[:200]
                _log(f"📥 Đang tải P{page}: {part_title}")
            else:
                filename = safe_title
                _log(f"📥 Đang tải: {title}")

            # 2. Get stream URLs
            streams = self.get_stream_urls(bvid, cid, quality, cookies)
            _log(f"🎬 Chất lượng: {streams['quality_label']} ({streams['quality']})")

            output_path = output_dir / f"{filename} [{bvid}].mp4"

            if streams["format"] == "dash" and streams.get("audio_url"):
                # DASH: tải video + audio riêng rồi merge
                temp_video = output_dir / f".{filename}.video.m4s"
                temp_audio = output_dir / f".{filename}.audio.m4s"

                try:
                    _log("⬇️  Đang tải video stream...")
                    self.download_stream(
                        streams["video_url"],
                        temp_video,
                        cookies,
                        lambda d, t: progress_callback(d, t, "video") if progress_callback else None,
                    )

                    _log("⬇️  Đang tải audio stream...")
                    self.download_stream(
                        streams["audio_url"],
                        temp_audio,
                        cookies,
                        lambda d, t: progress_callback(d, t, "audio") if progress_callback else None,
                    )

                    _log("🔗 Đang merge video + audio...")
                    self.merge_streams(temp_video, temp_audio, output_path)
                    _log(f"✅ Hoàn tất: {output_path.name}")

                finally:
                    # Clean up temp files
                    for tmp in (temp_video, temp_audio):
                        try:
                            if tmp.exists():
                                tmp.unlink()
                        except Exception:
                            pass

            else:
                # durl hoặc DASH không có audio: tải trực tiếp
                _log("⬇️  Đang tải video...")
                self.download_stream(
                    streams["video_url"],
                    output_path,
                    cookies,
                    lambda d, t: progress_callback(d, t, "video") if progress_callback else None,
                )
                _log(f"✅ Hoàn tất: {output_path.name}")

            results.append(str(output_path))

        return results

    def _extract_bvid(self, url_or_bvid):
        """Trích xuất BV ID từ URL hoặc chuỗi."""
        # Nếu đã là BV ID
        if url_or_bvid.startswith("BV"):
            return url_or_bvid

        # Trích xuất từ URL
        match = re.search(r"(BV[a-zA-Z0-9]+)", url_or_bvid)
        if match:
            return match.group(1)

        return None


# ============================================================================
# Cookie Persistence (lưu/load cookies)
# ============================================================================


def save_bilibili_cookies(cookies, profiles_path):
    """
    Lưu Bilibili cookies vào cookie_profiles.json.

    Args:
        cookies: dict cookies
        profiles_path: Path đến cookie_profiles.json
    """
    profiles = {}
    profiles_path = Path(profiles_path)

    if profiles_path.exists():
        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = json.load(f)
        except Exception:
            profiles = {}

    # Lưu dưới key "bilibili_qr"
    profiles["bilibili_qr"] = json.dumps(cookies, ensure_ascii=False)

    with open(profiles_path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


def load_bilibili_cookies(profiles_path):
    """
    Load Bilibili cookies từ cookie_profiles.json.
    Hỗ trợ cả 2 format:
    - Key "bilibili_qr": dict đơn giản {"SESSDATA": ..., "bili_jct": ...}
    - Key "www.bilibili.com": JSON profile {"url": ..., "cookies": [{name, value}...]}

    Args:
        profiles_path: Path đến cookie_profiles.json

    Returns:
        dict: cookies hoặc {} nếu chưa đăng nhập
    """
    profiles_path = Path(profiles_path)
    if not profiles_path.exists():
        return {}

    try:
        with open(profiles_path, "r", encoding="utf-8") as f:
            profiles = json.load(f)

        # Thử key "bilibili_qr" trước (format đơn giản)
        raw = profiles.get("bilibili_qr", "")
        if raw:
            cookies = json.loads(raw)
            if cookies and cookies.get("SESSDATA"):
                return cookies

        # Fallback: thử key "www.bilibili.com" (format từ QR login trong app)
        raw2 = profiles.get("www.bilibili.com", "")
        if raw2:
            data = json.loads(raw2)
            cookies_list = data.get("cookies", [])
            if cookies_list:
                flat = {}
                for c in cookies_list:
                    name = c.get("name", "")
                    value = c.get("value", "")
                    if name and value:
                        flat[name] = value
                if flat.get("SESSDATA"):
                    return flat

    except Exception:
        pass

    return {}



def cookies_to_netscape_file(cookies, output_path):
    """
    Chuyển dict cookies thành file Netscape format (cho yt-dlp --cookies).

    Args:
        cookies: dict cookies
        output_path: Path file output
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Netscape HTTP Cookie File\n"]
    for name, value in cookies.items():
        if name in ("refresh_token",):
            continue  # Không đưa refresh_token vào cookie file
        # domain, flag, path, secure, expiry, name, value
        lines.append(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return str(output_path)
