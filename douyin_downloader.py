"""
Douyin Video Downloader — Selenium CDP approach
Bypass yt-dlp Douyin extractor bug bằng cách dùng Selenium + Chrome DevTools Protocol
để intercept API response và lấy video URL trực tiếp.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal


def is_douyin_url(url):
    """Kiểm tra URL có phải Douyin không."""
    return bool(re.search(r'(douyin\.com|iesdouyin\.com)', url))


def extract_douyin_video_id(url):
    """Extract video ID từ URL Douyin."""
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    return None


def _resolve_short_url(url):
    """Resolve short URL (v.douyin.com) thành URL đầy đủ."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        })
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.url
    except Exception:
        return url


class DouyinScanWorker(QThread):
    """
    Worker thread quét video Douyin bằng Selenium + CDP.
    Trả về danh sách items tương thích với ExpandWorker result format.
    """
    log = pyqtSignal(str)
    result = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, urls, cookies_json_str="", parent=None):
        super().__init__(parent)
        self.urls = urls
        self.cookies_json_str = cookies_json_str

    def run(self):
        try:
            items = []
            for url in self.urls:
                if not is_douyin_url(url):
                    continue
                self.log.emit(f"🔍 [Douyin] Đang quét: {url}")
                try:
                    item = self._scan_single(url)
                    if item:
                        items.append(item)
                        self.log.emit(f"✅ [Douyin] Tìm thấy: {item.get('title', 'N/A')}")
                except Exception as e:
                    self.log.emit(f"❌ [Douyin] Lỗi quét {url}: {e}")

            if items:
                self.result.emit(items)
            else:
                self.failed.emit("Không tìm thấy video Douyin nào.")
        except Exception as e:
            self.failed.emit(str(e))

    def _scan_single(self, url):
        """Quét một video Douyin bằng Selenium CDP."""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError:
            self.log.emit("❌ [Douyin] Thiếu thư viện selenium!")
            self.log.emit("  💡 Chạy: pip install selenium")
            return None

        # Resolve short URL
        if 'v.douyin.com' in url or 'iesdouyin.com' in url:
            self.log.emit(f"  ↪ Resolving short URL...")
            url = _resolve_short_url(url)
            self.log.emit(f"  ↪ Resolved: {url}")

        video_id = extract_douyin_video_id(url)
        if not video_id:
            self.log.emit(f"  ⚠ Không tìm thấy video ID trong URL")
            return None

        opts = Options()
        opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--window-size=1680,1050')
        opts.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
        opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = None
        try:
            self.log.emit("  🚀 Khởi động Chrome headless...")
            driver = webdriver.Chrome(options=opts)
        except Exception as e:
            self.log.emit(f"❌ [Douyin] Không thể khởi động Chrome: {e}")
            self.log.emit("  💡 Yêu cầu: Cài Google Chrome + ChromeDriver")
            self.log.emit("  💡 Hoặc chạy: pip install webdriver-manager")
            return None

        try:
            # Truy cập Douyin trước để set domain
            driver.get('https://www.douyin.com/')
            time.sleep(2)

            # Thêm cookies nếu có
            if self.cookies_json_str:
                self._add_cookies(driver, self.cookies_json_str)

            # Truy cập trang video
            video_url = f'https://www.douyin.com/video/{video_id}'
            self.log.emit(f"  🌐 Loading {video_url}...")
            driver.execute_cdp_cmd('Network.enable', {})
            driver.get(video_url)
            time.sleep(8)

            title = driver.title or ""
            # Xóa suffix " - 抖音"
            title = re.sub(r'\s*-\s*抖音\s*$', '', title)

            # Intercept network logs để tìm video URL
            video_info = self._extract_from_network(driver)

            if not video_info:
                self.log.emit("  ⚠ Không tìm được video URL từ network logs")
                return None

            # Build item compatible với app format
            item = {
                'id': video_id,
                'title': title or f"Douyin_{video_id}",
                'url': video_url,
                'webpage_url': video_url,
                'thumbnail': video_info.get('thumbnail', ''),
                'duration': video_info.get('duration', 0),
                'extractor': 'Douyin',
                'view_count': video_info.get('view_count', 0),
                'like_count': video_info.get('like_count', 0),
                'uploader': video_info.get('uploader', ''),
                'upload_date': video_info.get('upload_date', ''),
                # Custom fields cho Douyin download
                '_douyin_download_url': video_info.get('download_url', ''),
                '_douyin_play_url': video_info.get('play_url', ''),
                '_douyin_urls': video_info.get('all_urls', []),
            }
            return item

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _add_cookies(self, driver, cookies_json_str):
        """Thêm cookies vào Selenium driver."""
        try:
            data = json.loads(cookies_json_str)
            cookies = data.get('cookies', []) if isinstance(data, dict) else data
            for c in cookies:
                try:
                    cookie = {
                        'name': c.get('name', ''),
                        'value': c.get('value', ''),
                        'domain': c.get('domain', ''),
                        'path': c.get('path', '/'),
                        'secure': c.get('secure', False),
                    }
                    if c.get('expirationDate'):
                        cookie['expiry'] = int(c['expirationDate'])
                    if cookie['name']:
                        driver.add_cookie(cookie)
                except Exception:
                    pass
        except Exception:
            pass

    def _extract_from_network(self, driver):
        """Extract video info từ CDP network logs."""
        logs = driver.get_log('performance')
        info = {}

        for entry in logs:
            try:
                msg = json.loads(entry['message'])['message']
                if msg['method'] != 'Network.responseReceived':
                    continue
                url = msg['params']['response']['url']
                req_id = msg['params']['requestId']

                # Tìm aweme detail hoặc page turn offline
                if 'aweme' not in url:
                    continue
                if 'detail' not in url and 'turn' not in url:
                    continue

                try:
                    body = driver.execute_cdp_cmd(
                        'Network.getResponseBody', {'requestId': req_id}
                    )
                    body_str = body.get('body', '')
                    if not body_str:
                        continue
                    data = json.loads(body_str)
                    self._parse_aweme_data(data, info)
                except Exception:
                    pass
            except Exception:
                pass

        return info if info.get('download_url') or info.get('play_url') else info

    def _parse_aweme_data(self, data, info):
        """Parse aweme API response và extract video info."""

        def find_detail(obj, depth=0):
            if depth > 8 or not isinstance(obj, (dict, list)):
                return None
            if isinstance(obj, dict):
                if 'aweme_detail' in obj and isinstance(obj['aweme_detail'], dict):
                    return obj['aweme_detail']
                # Cũng check aweme_list
                if 'aweme_list' in obj and isinstance(obj['aweme_list'], list):
                    for item in obj['aweme_list']:
                        if isinstance(item, dict):
                            return item
                for v in obj.values():
                    r = find_detail(v, depth + 1)
                    if r:
                        return r
            elif isinstance(obj, list):
                for item in obj:
                    r = find_detail(item, depth + 1)
                    if r:
                        return r
            return None

        detail = find_detail(data)
        if not detail:
            return

        # Extract metadata
        desc = detail.get('desc', '')
        if desc and not info.get('title'):
            info['title'] = desc

        author = detail.get('author', {})
        if author:
            info['uploader'] = author.get('nickname', '')

        stats = detail.get('statistics', {})
        if stats:
            info['view_count'] = stats.get('play_count', 0)
            info['like_count'] = stats.get('digg_count', 0)

        video = detail.get('video', {})
        if not video:
            return

        info['duration'] = video.get('duration', 0) // 1000  # ms → s

        # Cover/thumbnail
        cover = video.get('cover', {}) or video.get('origin_cover', {})
        if cover and cover.get('url_list'):
            info['thumbnail'] = cover['url_list'][0]

        # Download URL (ưu tiên)
        download_addr = video.get('download_addr', {})
        if download_addr and download_addr.get('url_list'):
            info['download_url'] = download_addr['url_list'][0]

        # Play URL
        play_addr = video.get('play_addr', {})
        if play_addr and play_addr.get('url_list'):
            info['play_url'] = play_addr['url_list'][0]

        # Tất cả bitrate URLs
        all_urls = []
        bit_rate = video.get('bit_rate', [])
        for br in bit_rate:
            pa = br.get('play_addr', {})
            if pa and pa.get('url_list'):
                all_urls.append({
                    'quality': br.get('gear_name', ''),
                    'bitrate': br.get('bit_rate', 0),
                    'url': pa['url_list'][0],
                })
        info['all_urls'] = all_urls


class DouyinDownloadWorker(QThread):
    """Worker thread tải video Douyin."""
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, item, out_dir, parent=None):
        super().__init__(parent)
        self.item = item
        self.out_dir = out_dir

    def run(self):
        try:
            title = self.item.get('title', 'douyin_video')
            # Sanitize filename
            safe_title = re.sub(r'[<>:"/\\|?*#]', '_', title)[:100]
            video_id = self.item.get('id', 'unknown')
            out_path = os.path.join(self.out_dir, f"{safe_title}_{video_id}.mp4")

            # Chọn URL tốt nhất
            download_url = (
                self.item.get('_douyin_download_url')
                or self.item.get('_douyin_play_url')
            )

            # Nếu có all_urls, lấy bitrate cao nhất
            all_urls = self.item.get('_douyin_urls', [])
            if all_urls:
                best = max(all_urls, key=lambda x: x.get('bitrate', 0))
                download_url = best.get('url', download_url)

            if not download_url:
                self.finished.emit(False, "Không tìm được URL tải video")
                return

            self.log.emit(f"⬇ [Douyin] Đang tải: {safe_title}")
            self.log.emit(f"  📁 Lưu vào: {out_path}")

            # Download
            req = urllib.request.Request(download_url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/',
            })
            resp = urllib.request.urlopen(req, timeout=60)
            total = int(resp.headers.get('Content-Length', 0))

            os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
            downloaded = 0
            with open(out_path, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                        self.progress.emit(pct)

            size_mb = downloaded / (1024 * 1024)
            self.log.emit(f"✅ [Douyin] Tải xong: {safe_title} ({size_mb:.1f}MB)")
            self.finished.emit(True, out_path)

        except Exception as e:
            self.log.emit(f"❌ [Douyin] Lỗi tải: {e}")
            self.finished.emit(False, str(e))
