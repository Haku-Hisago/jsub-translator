"""
Video downloader using yt-dlp.
Supports downloading from YouTube, Niconico, Bilibili, and other platforms.
Also handles local video files.
"""

import re
from pathlib import Path
from typing import Optional, Callable
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from .config import (
    OUTPUT_DIR, YTDLP_COOKIES_FILE, YTDLP_COOKIES_FROM_BROWSER, YTDLP_RETRIES,
)


# 触发「需要登录 / 机器人校验」的典型报错片段。
# 命中时说明站点拒绝匿名访问，唯一可靠的解法是带上 cookies。
_AUTH_ERROR_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "no video formats found",
    "confirm your age",
    "login required",
    "private video",
    "members-only",
    "this video is available to this channel's members",
)


def _cookie_opts() -> dict:
    """把 .env 里的 cookies 配置翻译成 yt-dlp 选项。

    留空则返回 {}，行为与未配置时完全一致（不发送任何 cookies）。
    """
    if YTDLP_COOKIES_FILE:
        p = Path(YTDLP_COOKIES_FILE)
        if not p.is_file():
            # 配了但文件不在 —— 明确报错，不要静默忽略（否则用户以为生效了）
            raise FileNotFoundError(
                f"YTDLP_COOKIES_FILE 指向的文件不存在：{p}\n"
                f"  请确认路径，或把 .env 里的 YTDLP_COOKIES_FILE 留空以关闭该功能。"
            )
        return {"cookiefile": str(p)}

    if YTDLP_COOKIES_FROM_BROWSER:
        # (browser, profile, keyring, container) —— 后三项交给 yt-dlp 默认处理
        return {"cookiesfrombrowser": (YTDLP_COOKIES_FROM_BROWSER, None, None, None)}

    return {}


def _looks_like_auth_error(err: Exception) -> bool:
    """该异常是否属于「需要登录 / 机器人校验」。"""
    msg = str(err).lower()
    return any(m in msg for m in _AUTH_ERROR_MARKERS)


def _auth_help(url: str, configured: bool) -> str:
    """生成「需要 cookies」场景下的可操作提示。"""
    host = ""
    m = re.match(r'^https?://([^/]+)', url.strip())
    if m:
        host = m.group(1)

    lines = [f"站点 {host} 拒绝了匿名下载（需要登录 / 机器人校验）。"]
    if configured:
        lines.append("  当前已配置 cookies，但仍然被拒。可能是 cookies 已过期，请重新导出。")
    else:
        lines.append("  解决办法：给 yt-dlp 配置浏览器 cookies（.env 里二选一）")
    lines += [
        "",
        "  方式 1（推荐，最稳）：导出 cookies.txt 文件",
        "    1. 浏览器装扩展 “Get cookies.txt LOCALLY”",
        "    2. 打开并登录该站点，用扩展导出 cookies.txt",
        "    3. 放到程序目录下，然后在 .env 里写：",
        "         YTDLP_COOKIES_FILE=cookies.txt",
        "",
        "  方式 2：直接读浏览器（可能失败，见下）",
        "         YTDLP_COOKIES_FROM_BROWSER=edge      # 或 chrome / firefox",
        "    注意：Chrome/Edge 新版启用了 App-Bound 加密，且浏览器运行时",
        "    cookie 库会被锁定，读取经常报 DPAPI / Could not copy 错误。",
        "",
        "  改完 .env 需要重启程序才会生效。",
    ]
    return "\n".join(lines)


def cookie_status() -> dict:
    """报告 cookies 配置状态，供 /api/health 自检使用。

    不发起网络请求、不抛异常 —— 自检端点必须始终能返回。
    对 cookies.txt 会顺手数一下条数，让用户能确认「文件确实被读到了」，
    而不是只看到一句「已配置」却不知道有没有生效。
    """
    if not YTDLP_COOKIES_FILE and not YTDLP_COOKIES_FROM_BROWSER:
        return {
            "configured": False,
            "hint": "YouTube 需要 cookies（Bilibili / Niconico 不需要）。"
                    "在 .env 里设置 YTDLP_COOKIES_FILE 或 YTDLP_COOKIES_FROM_BROWSER。",
        }

    if YTDLP_COOKIES_FILE:
        p = Path(YTDLP_COOKIES_FILE)
        info = {"configured": True, "source": "file", "path": str(p),
                "exists": p.is_file()}
        if not p.is_file():
            info["hint"] = "文件不存在，请检查路径（相对路径基于程序所在目录）。"
            return info
        try:
            total = 0
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if len(line.split("\t")) >= 7:
                    total += 1
            info["cookie_count"] = total
            if total == 0:
                info["hint"] = "文件存在但解析不出 cookie，可能不是 Netscape 格式。"
            else:
                info["hint"] = "已配置。若仍被拒，多半是 cookies 已过期，请重新导出。"
        except OSError as e:
            info["hint"] = f"读取失败：{e}"
        return info

    return {
        "configured": True,
        "source": "browser",
        "browser": YTDLP_COOKIES_FROM_BROWSER,
        "hint": "Chrome / Edge 新版常因 App-Bound 加密或 cookie 库被占用而读取失败；"
                "若报 DPAPI / Could not copy 错误，请改用 YTDLP_COOKIES_FILE。",
    }


def _find_ffmpeg_path() -> str:
    """给 yt-dlp 用的 ffmpeg 所在**目录**（yt-dlp 的 ffmpeg_location 要目录）。

    复用 `audio.find_ffmpeg()`，不再自己维护一份搜索顺序。
    早先这里只搜 3 个位置，而 audio 搜 6 个（多出 Program Files (x86)、
    Chocolatey、Scoop）—— 于是用 Chocolatey / Scoop 装的 ffmpeg 会出现
    「能提取音频，但 yt-dlp 合并不了」的怪现象：音频提取走 audio 的搜索、
    合并走这里的搜索，两者看到的不是同一个 ffmpeg。

    找不到时返回 ""，交由 yt-dlp 自行处理（与改动前行为一致）。
    """
    try:
        from .audio import find_ffmpeg
        return str(Path(find_ffmpeg()).parent)
    except Exception:
        return ""


def is_url(text: str) -> bool:
    """Check if the input string is a URL."""
    url_pattern = re.compile(
        r'^https?://'           # http:// or https://
        r'[\w\-]+(\.[\w\-]+)+'  # domain
        r'[/#?&=\.\-\w]*$'      # path/query
    )
    return bool(url_pattern.match(text.strip()))


def _progress_hook(info: dict, callback: Optional[Callable] = None):
    """yt-dlp progress hook."""
    if info['status'] == 'downloading' and callback:
        percent = info.get('_percent_str', '0%').strip().replace('%', '')
        try:
            pct = float(percent)
            callback(pct / 100, "下载中...")
        except ValueError:
            pass
    elif info['status'] == 'finished' and callback:
        callback(1.0, "下载完成，正在处理...")


def download_video(
    url: str,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Path:
    """
    Download a video from a URL.

    Args:
        url: Video URL to download.
        output_dir: Directory to save the video. Defaults to OUTPUT_DIR.
        progress_callback: Optional callback(progress: float 0-1, status: str).

    Returns:
        Path to the downloaded video file.
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR / "videos"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Output template: video title + id
    outtmpl = str(output_dir / "%(title)s [%(id)s].%(ext)s")

    # Find FFmpeg for yt-dlp format merging
    ffmpeg_dir = _find_ffmpeg_path()

    ydl_opts = {
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'outtmpl': outtmpl,
        'progress_hooks': [lambda info: _progress_hook(info, progress_callback)],
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'ignore_no_formats_error': True,
        # 站点偶发抽风时自动重试，避免整单失败
        'retries': YTDLP_RETRIES,
        'fragment_retries': YTDLP_RETRIES,
        'extractor_retries': YTDLP_RETRIES,
    }

    if ffmpeg_dir:
        ydl_opts['ffmpeg_location'] = ffmpeg_dir

    # 可选的浏览器 cookies（未配置时为空，行为与之前完全一致）
    cookies_configured = False
    try:
        copts = _cookie_opts()
        if copts:
            ydl_opts.update(copts)
            cookies_configured = True
    except FileNotFoundError as e:
        raise RuntimeError(str(e)) from e

    if progress_callback:
        progress_callback(0.0, "开始下载视频...")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
    except (DownloadError, ExtractorError) as e:
        if _looks_like_auth_error(e):
            raise RuntimeError(_auth_help(url, cookies_configured)) from e
        raise

    # yt-dlp may change the extension depending on format merging
    # Find the actual downloaded file
    video_id = info.get('id', '')
    candidates = list(output_dir.glob(f"*[{video_id}].*"))
    if candidates:
        # Return the largest file (likely the merged one)
        video_path = max(candidates, key=lambda p: p.stat().st_size)
    else:
        # Fallback to the expected filename
        video_path = Path(filename)
        if not video_path.exists():
            # Try mp4 extension
            video_path = video_path.with_suffix('.mp4')

    if not video_path.exists():
        raise FileNotFoundError(f"Downloaded video not found: {video_path}")

    if progress_callback:
        progress_callback(1.0, "视频下载完成")

    return video_path


def resolve_video_input(
    input_source: str,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Path:
    """
    Resolve video input: download if URL, return path if local file.

    Args:
        input_source: URL string or local file path.
        output_dir: Output directory for downloads.
        progress_callback: Progress callback.

    Returns:
        Path to the video file.
    """
    if is_url(input_source):
        return download_video(input_source, output_dir, progress_callback)
    else:
        path = Path(input_source.strip())
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {input_source}")
        if progress_callback:
            progress_callback(0.5, "使用本地视频文件")
        return path
