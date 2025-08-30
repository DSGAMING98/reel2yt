from __future__ import annotations
import hashlib, json, re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List

try:
    import yt_dlp as ytdlp_mod
except Exception:
    ytdlp_mod = None

from ..utils.config import get_cfg
from ..utils.ffmpeg import (
    download_video,
    duration_sec,
    extract_frames,
    extract_audio_wav,
    temp_media,
    FFMpegError,
)


@dataclass
class ReelInfo:
    url: str
    local_path: Path
    title: str
    uploader: Optional[str]
    duration: float
    frames_dir: Path
    audio_wav: Optional[Path]
    meta: Dict


_IG_RE = re.compile(r"(instagram\.com|instagr\.am)/(reel|p|tv)/", re.I)


def _normalize_url(url: str) -> str:
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    # strip query junk for cache stability
    u = u.split("?")[0].rstrip("/") + "/"
    return u


def _is_instagram(u: str) -> bool:
    return bool(_IG_RE.search(u))


def _cache_key(u: str) -> str:
    return hashlib.sha1(u.encode("utf-8")).hexdigest()[:16]


def _probe_ytdlp(url: str) -> Dict:
    if not ytdlp_mod:
        return {}
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "retries": 2,
    }
    with ytdlp_mod.YoutubeDL(ydl_opts) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except Exception:
            return {}


def get_reel_info(url: str, sample_limit: int = 40) -> ReelInfo:
    """Download the reel and prepare analysis assets."""
    cfg = get_cfg()
    u = _normalize_url(url)
    if not _is_instagram(u):
        raise ValueError("not an Instagram reel/post URL")

    key = _cache_key(u)
    dl_dir = cfg.downloads_dir / "ig"
    frames_dir = cfg.cache_dir / "frames" / key
    audio_dir = cfg.cache_dir / "audio"
    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # meta via yt-dlp (no download)
    meta = _probe_ytdlp(u)

    # download best mp4
    try:
        fpath = download_video(u, dl_dir, ext="mp4")
    except FFMpegError as e:
        raise RuntimeError(f"download failed: {e}") from e

    # duration
    dur = duration_sec(fpath)

    # title/uploader fallbacks
    title = str(meta.get("title") or fpath.stem.split("-")[0] or "instagram")
    uploader = meta.get("uploader") or meta.get("channel") or None

    # frames
    if not any(frames_dir.glob("frame_*.jpg")):
        extract_frames(
            fpath,
            frames_dir,
            stride_sec=cfg.frame_stride_sec,
            limit=sample_limit,
        )
    # audio (optional)
    audio_wav = None
    if cfg.audio_fp:
        audio_wav = audio_dir / f"{key}.wav"
        if not audio_wav.exists():
            try:
                extract_audio_wav(fpath, audio_wav, sr=22050)
            except FFMpegError:
                audio_wav = None

    return ReelInfo(
        url=u,
        local_path=fpath,
        title=title,
        uploader=uploader,
        duration=dur,
        frames_dir=frames_dir,
        audio_wav=audio_wav if audio_wav and audio_wav.exists() else None,
        meta=meta,
    )


def quick_peek(url: str) -> Dict:
    """Lightweight info without downloading media."""
    u = _normalize_url(url)
    if not _is_instagram(u):
        raise ValueError("not an Instagram reel/post URL")
    info = _probe_ytdlp(u)
    return {
        "url": u,
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "ext": info.get("ext"),
        "id": info.get("id"),
        "thumbnails": info.get("thumbnails"),
    }
