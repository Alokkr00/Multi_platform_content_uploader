"""
downloader.py — yt-dlp + ffmpeg Video Downloader & Transcoder

Downloads videos via yt-dlp, transcodes them to X-compatible specs using ffmpeg,
and provides metadata extraction for any supported URL.
"""

import os
import re
import json
import shutil
import logging
import subprocess
import asyncio
import time
import functools
from pathlib import Path

import yt_dlp

from db import get_setting

logger = logging.getLogger("x_automation.downloader")

_active_processes = set()

def get_active_processes():
    return _active_processes

def kill_active_processes():
    """Kill all currently running ffmpeg/ffprobe subprocesses (called during server shutdown)."""
    logger.info(f"Shutting down: killing {len(_active_processes)} active subprocess(es)")
    for proc in list(_active_processes):
        try:
            if hasattr(proc, "kill"):
                proc.kill()
            else:
                proc.terminate()
        except Exception as e:
            logger.warning(f"Error terminating process: {e}")
    _active_processes.clear()

# ── Constants ─────────────────────────────────────────────────────────

TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_media")

# X (Twitter) video constraints
MAX_RESOLUTION_WIDTH = 1920
MAX_RESOLUTION_HEIGHT = 1200
MAX_DURATION_SECONDS = 140
MAX_FILE_SIZE_BYTES = 512 * 1024 * 1024  # 512 MB
VIDEO_CODEC = "libx264"
PIXEL_FORMAT = "yuv420p"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "128k"


# ── Helpers ───────────────────────────────────────────────────────────

def _ensure_temp_dir(directory: str = TEMP_DIR):
    """Create temp media directory if it doesn't exist."""
    os.makedirs(directory, exist_ok=True)


def _add_proxy_and_ua(ydl_opts: dict):
    """Add proxy and user-agent settings to ydl_opts from DB settings."""
    try:
        proxy = get_setting("proxy_url", "").strip()
        if proxy:
            ydl_opts["proxy"] = proxy
    except Exception as e:
        logger.warning(f"Failed to fetch proxy_url setting: {e}")
        
    try:
        ua = get_setting("user_agent", "").strip()
        if ua:
            ydl_opts["user_agent"] = ua
    except Exception as e:
        logger.warning(f"Failed to fetch user_agent setting: {e}")


def _retry(max_retries: int = 3, base_delay: float = 1.0, exceptions=(Exception,)):
    """Retry decorator with exponential backoff for network calls."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


def _find_ffmpeg() -> str:
    """Find ffmpeg executable on PATH or common Windows install locations."""
    # Check PATH first
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    # Common Windows install locations
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\ffmpeg\bin\ffmpeg.exe"),
        os.path.expanduser(r"~\scoop\shims\ffmpeg.exe"),
        r"C:\tools\ffmpeg\bin\ffmpeg.exe",
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    ]

    for path in common_paths:
        if os.path.isfile(path):
            return path

    raise FileNotFoundError(
        "ffmpeg not found. Install it and ensure it's on PATH, or place it in a standard location. "
        "Download from https://ffmpeg.org/download.html"
    )


def _find_ffprobe() -> str:
    """Find ffprobe executable on PATH or common Windows install locations."""
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        return ffprobe_path

    # Try same directory as ffmpeg
    try:
        ffmpeg = _find_ffmpeg()
        ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + os.path.splitext(ffmpeg)[1])
        if os.path.isfile(ffprobe):
            return ffprobe
    except FileNotFoundError:
        pass

    raise FileNotFoundError("ffprobe not found. It is usually bundled with ffmpeg.")


async def _get_video_info_async(filepath: str) -> dict:
    """Get video metadata using ffprobe asynchronously."""
    ffprobe = _find_ffprobe()
    cmd = [
        "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", filepath
    ]
    process = await asyncio.create_subprocess_exec(
        ffprobe, *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _active_processes.add(process)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except asyncio.TimeoutError:
        raise RuntimeError("ffprobe timed out after 30 seconds")
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except OSError:
                pass
            await process.wait()
        _active_processes.discard(process)

    if process.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='replace')}")
    return json.loads(stdout.decode('utf-8', errors='replace'))


def _sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in filenames."""
    # Remove characters that are problematic on Windows
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple underscores and strip
    sanitized = re.sub(r'_+', '_', sanitized).strip('_. ')
    # Limit length
    return sanitized[:100] if sanitized else "video"


# ── Core Functions ────────────────────────────────────────────────────

@_retry(max_retries=3, base_delay=2.0, exceptions=(yt_dlp.utils.DownloadError, Exception))
def fetch_metadata(url: str) -> dict:
    """
    Fetch video metadata without downloading.

    Args:
        url: Video URL (YouTube, TikTok, Instagram, etc.)

    Returns:
        dict with keys: video_id, title, description, duration, upload_date, thumbnail_url
    """
    logger.info(f"Fetching metadata for: {url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "no_color": True,
    }
    _add_proxy_and_ua(ydl_opts)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise ValueError(f"Could not extract metadata from: {url}")

    title = info.get("title", "")
    uploader = info.get("uploader", "")
    uploader_id = info.get("uploader_id", "")
    extractor = (info.get("extractor") or info.get("extractor_key") or "").lower()

    if extractor == "twitter":
        if uploader and title.startswith(f"{uploader} - "):
            title = title[len(uploader) + 3:]
        elif uploader_id and title.startswith(f"{uploader_id} - "):
            title = title[len(uploader_id) + 3:]
        title = title.strip()

    metadata = {
        "video_id": info.get("id", ""),
        "title": title,
        "description": info.get("description", ""),
        "duration": info.get("duration", 0),
        "upload_date": info.get("upload_date", ""),
        "thumbnail_url": info.get("thumbnail", ""),
    }

    logger.info(f"Metadata fetched: '{metadata['title']}' ({metadata['duration']}s)")
    return metadata


@_retry(max_retries=3, base_delay=2.0, exceptions=(yt_dlp.utils.DownloadError, Exception))
def fetch_latest_video(channel_url: str) -> dict:
    """
    Fetch metadata for the most recent video from a channel or feed URL.

    Args:
        channel_url: Channel/playlist/feed URL

    Returns:
        dict with video metadata (same structure as fetch_metadata)
    """
    logger.info(f"Fetching latest video from: {channel_url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "no_color": True,
        "playlist_items": "1",  # Only the first (latest) item
        "extract_flat": False,
    }
    _add_proxy_and_ua(ydl_opts)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if info is None:
        raise ValueError(f"Could not extract info from: {channel_url}")

    # If it's a playlist/channel, get the first entry
    if info.get("_type") == "playlist" or "entries" in info:
        entries = list(info.get("entries", []))
        if not entries:
            raise ValueError(f"No videos found at: {channel_url}")
        video_info = entries[0]
        # If extract_flat returned a stub, do a full extract
        if video_info and not video_info.get("duration"):
            video_url = video_info.get("url") or video_info.get("webpage_url", "")
            if video_url:
                return fetch_metadata(video_url)
    else:
        video_info = info

    title = video_info.get("title", "")
    uploader = video_info.get("uploader", "")
    uploader_id = video_info.get("uploader_id", "")
    extractor = (video_info.get("extractor") or video_info.get("extractor_key") or "").lower()

    if extractor == "twitter":
        if uploader and title.startswith(f"{uploader} - "):
            title = title[len(uploader) + 3:]
        elif uploader_id and title.startswith(f"{uploader_id} - "):
            title = title[len(uploader_id) + 3:]
        title = title.strip()

    metadata = {
        "video_id": video_info.get("id", ""),
        "title": title,
        "description": video_info.get("description", ""),
        "duration": video_info.get("duration", 0),
        "upload_date": video_info.get("upload_date", ""),
        "thumbnail_url": video_info.get("thumbnail", ""),
    }

    logger.info(f"Latest video: '{metadata['title']}' ({metadata['duration']}s)")
    return metadata


@_retry(max_retries=3, base_delay=2.0, exceptions=(yt_dlp.utils.DownloadError, Exception))
def download_video(url: str, output_dir: str = TEMP_DIR) -> str:
    """
    Download best quality video from URL.

    Args:
        url: Video URL
        output_dir: Directory to save the downloaded file (default: temp_media/)

    Returns:
        Absolute path to the downloaded file
    """
    _ensure_temp_dir(output_dir)

    logger.info(f"Downloading video: {url}")

    # Use a template that includes video ID for uniqueness
    output_template = os.path.join(output_dir, "%(id)s_%(title).50s.%(ext)s")

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_color": True,
        "restrictfilenames": True,  # Safe filenames
        "progress_hooks": [_download_progress_hook],
    }
    _add_proxy_and_ua(ydl_opts)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise ValueError(f"Download failed for: {url}")

        # yt-dlp fills in the actual filename
        filepath = ydl.prepare_filename(info)

        # If merge happened, the extension might differ
        if not os.path.isfile(filepath):
            # Try with .mp4 extension (merge_output_format)
            base, _ = os.path.splitext(filepath)
            filepath = base + ".mp4"

        if not os.path.isfile(filepath):
            # Search output_dir for a file matching the video ID
            video_id = info.get("id", "")
            for f in os.listdir(output_dir):
                if video_id and video_id in f:
                    filepath = os.path.join(output_dir, f)
                    break

    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Downloaded file not found at expected path")

    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    return os.path.abspath(filepath)


def _download_progress_hook(d: dict):
    """Progress hook for yt-dlp downloads."""
    if d.get("status") == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        downloaded = d.get("downloaded_bytes", 0)
        if total > 0:
            pct = (downloaded / total) * 100
            if int(pct) % 25 == 0:  # Log at 25% increments
                logger.info(f"Download progress: {pct:.0f}%")
    elif d.get("status") == "finished":
        logger.info("Download finished, post-processing...")


async def transcode_for_x(input_path: str, output_path: str = None) -> str:
    """
    Transcode video to X-compatible specs asynchronously.

    Specs:
        - MP4 container, H.264 (yuv420p), AAC audio 128kbps
        - Max resolution: 1920x1200
        - Max duration: 140 seconds (trimmed if longer)
        - Max file size: 512MB

    Args:
        input_path: Path to input video file
        output_path: Optional output path. If None, auto-generated.

    Returns:
        Absolute path to the transcoded file
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    ffmpeg = _find_ffmpeg()

    # Generate output path if not provided
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}_x.mp4"

    # Check for caching hit
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        logger.info(f"Cache HIT: Using already transcoded file: {output_path}")
        return os.path.abspath(output_path)

    logger.info(f"Transcoding for X: {os.path.basename(input_path)}")

    # Get source video info
    try:
        probe_info = await _get_video_info_async(input_path)
        src_duration = float(probe_info.get("format", {}).get("duration", 0))

        # Find video stream dimensions
        src_width, src_height = 0, 0
        for stream in probe_info.get("streams", []):
            if stream.get("codec_type") == "video":
                src_width = int(stream.get("width", 0))
                src_height = int(stream.get("height", 0))
                break
    except Exception as e:
        logger.warning(f"Could not probe source file: {e}")
        src_duration = 0
        src_width, src_height = 0, 0

    # Build ffmpeg command
    cmd = ["-y", "-i", input_path]

    # Duration trim
    if src_duration > MAX_DURATION_SECONDS:
        cmd.extend(["-t", str(MAX_DURATION_SECONDS)])
        logger.info(f"Trimming from {src_duration:.0f}s to {MAX_DURATION_SECONDS}s")

    # Video filters — scale down if needed, maintain aspect ratio
    vf_filters = []
    if src_width > MAX_RESOLUTION_WIDTH or src_height > MAX_RESOLUTION_HEIGHT:
        # Scale to fit within max bounds while maintaining aspect ratio
        # Use -2 to ensure even dimensions (required by H.264)
        vf_filters.append(
            f"scale='min({MAX_RESOLUTION_WIDTH},iw)':'min({MAX_RESOLUTION_HEIGHT},ih)'"
            f":force_original_aspect_ratio=decrease"
        )
        logger.info(f"Scaling down from {src_width}x{src_height}")

    # Ensure even dimensions (H.264 requirement)
    vf_filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")

    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])

    # Video codec
    cmd.extend([
        "-c:v", VIDEO_CODEC,
        "-pix_fmt", PIXEL_FORMAT,
        "-preset", "medium",
        "-crf", "23",
    ])

    # Audio codec
    cmd.extend([
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
    ])

    # Output
    cmd.extend([
        "-movflags", "+faststart",  # Web-optimized MP4
        "-map_metadata", "-1",
        "-bitexact",
        output_path
    ])

    logger.info(f"Running ffmpeg transcode...")

    process = await asyncio.create_subprocess_exec(
        ffmpeg, *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _active_processes.add(process)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
    except asyncio.TimeoutError:
        logger.error("ffmpeg transcode timed out after 600s")
        raise RuntimeError("ffmpeg transcode timed out after 10 minutes")
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except OSError:
                pass
            await process.wait()
        _active_processes.discard(process)

    if process.returncode != 0:
        error_msg = stderr.decode(errors='replace')[-500:] if stderr else "Unknown ffmpeg error"
        logger.error(f"ffmpeg failed: {error_msg}")
        raise RuntimeError(f"ffmpeg transcode failed (exit code {process.returncode}): {error_msg}")

    # Verify output
    if not os.path.isfile(output_path):
        raise FileNotFoundError(f"Transcoded file not found: {output_path}")

    output_size = os.path.getsize(output_path)
    if output_size > MAX_FILE_SIZE_BYTES:
        logger.warning(
            f"Transcoded file ({output_size / (1024*1024):.1f} MB) exceeds "
            f"{MAX_FILE_SIZE_BYTES / (1024*1024):.0f} MB limit. "
            f"Re-encoding with lower bitrate..."
        )

        # Re-encode with a constrained bitrate
        duration = min(src_duration, MAX_DURATION_SECONDS) if src_duration > 0 else 60
        target_bitrate_kbps = int(
            (MAX_FILE_SIZE_BYTES * 8) / duration / 1000 * 0.9
        )
        reencoded_path = output_path.replace("_x.mp4", "_x2.mp4")

        cmd_reencode = [
            "-y", "-i", output_path,
            "-c:v", VIDEO_CODEC, "-pix_fmt", PIXEL_FORMAT,
            "-b:v", f"{target_bitrate_kbps}k", "-maxrate", f"{target_bitrate_kbps}k",
            "-bufsize", f"{target_bitrate_kbps * 2}k",
            "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
            "-movflags", "+faststart",
            "-map_metadata", "-1",
            "-bitexact",
            reencoded_path
        ]
        process_re = await asyncio.create_subprocess_exec(
            ffmpeg, *cmd_reencode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _active_processes.add(process_re)
        try:
            await asyncio.wait_for(process_re.communicate(), timeout=600)
            if process_re.returncode == 0 and os.path.isfile(reencoded_path):
                cleanup(output_path)
                output_path = reencoded_path
        except asyncio.TimeoutError:
            pass
        finally:
            if process_re.returncode is None:
                try:
                    process_re.kill()
                except OSError:
                    pass
                await process_re.wait()
            _active_processes.discard(process_re)

    output_size_mb = os.path.getsize(output_path) / (1024 * 1024) if os.path.isfile(output_path) else 0.0
    logger.info(f"Transcode complete: {os.path.basename(output_path)} ({output_size_mb:.1f} MB)")
    return os.path.abspath(output_path)


async def transcode_for_platform(input_path: str, platform: str = "x", output_path: str = None) -> str:
    """
    Transcode video according to the target platform requirements.
    
    Platforms:
      - 'x': Max 140s, max 1920x1200, original aspect ratio.
      - 'instagram': Max 90s, max 1080x1920 (9:16 vertical center crop).
      - 'tiktok': Max 600s, max 1080x1920 (9:16 vertical center crop).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Generate suffix based on platform
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}_{platform}.mp4"

    # Check for caching hit
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        logger.info(f"Cache HIT: Using already transcoded file: {output_path}")
        return os.path.abspath(output_path)

    # Route X to X-specific transcoder
    if platform == "x":
        return await transcode_for_x(input_path, output_path)

    # Set parameters based on platform
    if platform == "instagram":
        max_duration = 90
        target_w, target_h = 1080, 1920
    elif platform == "tiktok":
        max_duration = 600
        target_w, target_h = 1080, 1920
    elif platform == "youtube":
        max_duration = 60
        target_w, target_h = 1080, 1920
    else:
        max_duration = 140
        target_w, target_h = 1920, 1080

    ffmpeg = _find_ffmpeg()
    logger.info(f"Transcoding for {platform.upper()}: {os.path.basename(input_path)}")

    # Get source video info
    try:
        probe_info = await _get_video_info_async(input_path)
        src_duration = float(probe_info.get("format", {}).get("duration", 0))

        src_width, src_height = 0, 0
        for stream in probe_info.get("streams", []):
            if stream.get("codec_type") == "video":
                src_width = int(stream.get("width", 0))
                src_height = int(stream.get("height", 0))
                break
    except Exception as e:
        logger.warning(f"Could not probe source file: {e}")
        src_duration = 0
        src_width, src_height = 0, 0

    # Build ffmpeg command
    cmd = ["-y", "-i", input_path]

    # Duration trim
    if src_duration > max_duration:
        cmd.extend(["-t", str(max_duration)])
        logger.info(f"Trimming from {src_duration:.0f}s to {max_duration}s")

    # Aspect ratio adjustment to 9:16 vertical (crop/pad/blur_background filter)
    vf_filters = []
    if platform in ("instagram", "tiktok", "youtube"):
        vertical_pad_mode = "blur_background"
        try:
            vertical_pad_mode = get_setting("vertical_pad_mode", "blur_background").strip().lower()
        except Exception as e:
            logger.warning(f"Failed to fetch vertical_pad_mode setting: {e}")
            
        if vertical_pad_mode == "blur_background":
            vf_filters.append(
                "split[original][copy];"
                "[copy]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[blurred];"
                "[original]scale=1080:1920:force_original_aspect_ratio=decrease[scaled];"
                "[blurred][scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
            )
        elif vertical_pad_mode == "letterbox":
            vf_filters.append(
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            )
        else:  # crop
            vf_filters.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1")
    else:
        if src_width > target_w or src_height > target_h:
            vf_filters.append(f"scale='min({target_w},iw)':'min({target_h},ih)':force_original_aspect_ratio=decrease")
        vf_filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")

    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])

    # Video codec
    cmd.extend([
        "-c:v", VIDEO_CODEC,
        "-pix_fmt", PIXEL_FORMAT,
        "-preset", "medium",
        "-crf", "23",
    ])

    # Audio codec
    cmd.extend([
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
    ])

    # Output
    cmd.extend([
        "-movflags", "+faststart",
        "-map_metadata", "-1",
        "-bitexact",
        output_path
    ])

    logger.info(f"Running ffmpeg transcode for {platform}...")

    process = await asyncio.create_subprocess_exec(
        ffmpeg, *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _active_processes.add(process)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
    except asyncio.TimeoutError:
        logger.error(f"ffmpeg transcode ({platform}) timed out after 600s")
        raise RuntimeError(f"ffmpeg transcode ({platform}) timed out")
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except OSError:
                pass
            await process.wait()
        _active_processes.discard(process)

    if process.returncode != 0:
        error_msg = stderr.decode(errors='replace')[-500:] if stderr else "Unknown ffmpeg error"
        logger.error(f"ffmpeg failed: {error_msg}")
        raise RuntimeError(f"ffmpeg transcode ({platform}) failed: {error_msg}")

    if not os.path.isfile(output_path):
        raise FileNotFoundError(f"Transcoded file not found: {output_path}")

    output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"Transcode complete: {os.path.basename(output_path)} ({output_size_mb:.1f} MB)")
    return os.path.abspath(output_path)


def cleanup(filepath: str):
    """
    Delete a file if it exists.

    Args:
        filepath: Path to the file to delete
    """
    try:
        if filepath and os.path.isfile(filepath):
            os.remove(filepath)
            logger.debug(f"Cleaned up: {filepath}")
    except OSError as e:
        logger.warning(f"Failed to clean up {filepath}: {e}")
