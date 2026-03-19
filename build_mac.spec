# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# Thư mục gốc dự án
project_root = os.path.dirname(os.path.abspath(SPEC))

# Thu thập binaries bên ngoài (ffmpeg, yt-dlp, libmpv)
extra_binaries = []
extra_datas = []

# ffmpeg
ffmpeg_path = os.path.join(project_root, 'ffmpeg')
if os.path.exists(ffmpeg_path):
    extra_binaries.append((ffmpeg_path, '.'))

# yt-dlp
ytdlp_path = os.path.join(project_root, 'yt-dlp')
if os.path.exists(ytdlp_path):
    extra_binaries.append((ytdlp_path, '.'))

# libmpv
for libname in ['libmpv.dylib', 'libmpv.2.dylib']:
    libpath = os.path.join(project_root, libname)
    if os.path.exists(libpath):
        extra_binaries.append((libpath, '.'))

# logo
if os.path.exists(os.path.join(project_root, 'logo.png')):
    extra_datas.append(('logo.png', '.'))

a = Analysis(
    ['app.py'],
    pathex=[project_root],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.QtMultimedia',
        'PyQt5.QtMultimediaWidgets',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'srt_parser',
        'reup_tool_widget',
        'video_trim_widget',
        'bilibili_api',
        'bilibili_workers',
        'capcut_srt_gui',
        'douyin_downloader',
        'selenium',
        'selenium.common',
        'selenium.common.exceptions',
        'selenium.webdriver',
        'selenium.webdriver.chrome',
        'selenium.webdriver.chrome.options',
        'selenium.webdriver.chrome.service',
        'selenium.webdriver.chrome.webdriver',
        'selenium.webdriver.chrome.remote_connection',
        'selenium.webdriver.chromium',
        'selenium.webdriver.chromium.remote_connection',
        'selenium.webdriver.common',
        'selenium.webdriver.common.by',
        'selenium.webdriver.common.desired_capabilities',
        'selenium.webdriver.common.keys',
        'selenium.webdriver.common.options',
        'selenium.webdriver.common.service',
        'selenium.webdriver.common.proxy',
        'selenium.webdriver.common.log',
        'selenium.webdriver.common.bidi',
        'selenium.webdriver.common.bidi.cdp',
        'selenium.webdriver.remote',
        'selenium.webdriver.remote.webdriver',
        'selenium.webdriver.remote.remote_connection',
        'selenium.webdriver.remote.command',
        'selenium.webdriver.remote.errorhandler',
        'selenium.webdriver.remote.webelement',
        'selenium.webdriver.support',
        'selenium.webdriver.support.ui',
        'selenium.webdriver.support.wait',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VideoDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo.icns' if os.path.exists(os.path.join(project_root, 'logo.icns')) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VideoDownloader',
)

app = BUNDLE(
    coll,
    name='VideoDownloader.app',
    icon='logo.icns' if os.path.exists(os.path.join(project_root, 'logo.icns')) else None,
    bundle_identifier='com.videotool.downloader',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'CFBundleShortVersionString': '2.0.0',
        'CFBundleName': 'Video Downloader',
        'NSRequiresAquaSystemAppearance': 'False',
    },
)
