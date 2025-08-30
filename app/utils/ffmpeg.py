# details: ffmpeg/ffprobe + yt-dlp helpers (legacy-ffmpeg safe)
from __future__ import annotations
import json, re, subprocess, sys, tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

try:
    import yt_dlp as ytdlp_mod
except Exception:
    ytdlp_mod = None

from .config import get_cfg

PathLike = Union[str, Path]


class FFMpegError(RuntimeError):
    pass


# detect if ffmpeg supports -hide_banner (old 2013 builds do NOT)
def _supports_hide_banner() -> bool:
    c = get_cfg()
    if not c.has_ffmpeg:
        return False
    code, out, err = _run([c.ffmpeg, "-hide_banner", "-version"])
    return code == 0

_HIDE_OK: Optional[bool] = None


def _hb() -> List[str]:
    global _HIDE_OK
    if _HIDE_OK is None:
        _HIDE_OK = _supports_hide_banner()
    return ["-hide_banner"] if _HIDE_OK else []


def _run(cmd: List[str], timeout: Optional[int] = None) -> Tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        text=True,
        encoding="utf-8",   # force UTF-8
        errors="replace",   # never blow up on weird bytes
    )
    return p.returncode, p.stdout, p.stderr



def run_ffprobe(args: List[str]) -> str:
    c = get_cfg()
    if not c.has_ffprobe:
        raise FFMpegError("ffprobe not found")
    code, out, err = _run([c.ffprobe] + _hb() + args)
    if code != 0:
        raise FFMpegError(err.strip() or "ffprobe failed")
    return out


def run_ffmpeg(args: List[str]) -> str:
    c = get_cfg()
    if not c.has_ffmpeg:
        raise FFMpegError("ffmpeg not found")
    code, out, err = _run([c.ffmpeg] + _hb() + args)
    if code != 0:
        raise FFMpegError(err.strip() or "ffmpeg failed")
    return out


def probe_json(path: PathLike) -> Dict:
    p = str(Path(path).resolve())
    out = run_ffprobe([
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-print_format", "json",
        p,
    ])
    return json.loads(out)


def _valid_media(path: PathLike) -> bool:
    try:
        meta = probe_json(path)
        streams = meta.get("streams", [])
        if not streams:
            return False
        dur = None
        if "format" in meta and "duration" in meta["format"]:
            dur = float(meta["format"]["duration"])
        if dur is None:
            for st in streams:
                if "duration" in st:
                    dur = float(st["duration"])
                    break
        return bool(dur and dur > 0.5)
    except Exception:
        return False


def duration_sec(path: PathLike) -> float:
    meta = probe_json(path)
    dur = None
    if "format" in meta and "duration" in meta["format"]:
        dur = float(meta["format"]["duration"])
    if dur is None:
        for st in meta.get("streams", []):
            if "duration" in st:
                dur = float(st["duration"])
                break
    return float(dur or 0.0)


def extract_frames(path: PathLike, out_dir: PathLike, stride_sec: float = 1.0, limit: Optional[int] = None) -> List[Path]:
    outd = Path(out_dir)
    outd.mkdir(parents=True, exist_ok=True)
    pattern = str(outd / "frame_%06d.jpg")
    vf = f"fps=1/{max(stride_sec, 0.1):.3f}"
    args = [
        "-y",
        "-i", str(Path(path).resolve()),
        "-vf", vf,
        "-qscale:v", "2",
    ]
    if limit and limit > 0:
        args += ["-frames:v", str(limit)]
    args += [pattern]
    run_ffmpeg(args)
    frames = sorted(outd.glob("frame_*.jpg"))
    if limit:
        frames = frames[:limit]
    return frames


def extract_audio_wav(path: PathLike, out_path: PathLike, sr: int = 22050) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-y",
        "-i", str(Path(path).resolve()),
        "-vn",
        "-ac", "1",
        "-ar", str(sr),
        "-f", "wav",
        str(out),
    ])
    return out


def cut_clip(path: PathLike, start: float, dur: float, out_path: PathLike) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-y",
        "-ss", f"{max(0.0, start):.3f}",
        "-t", f"{max(0.01, dur):.3f}",
        "-i", str(Path(path).resolve()),
        "-c", "copy",
        str(out),
    ])
    return out


def _safe_slug(text: str, limit: int = 64) -> str:
    base = re.sub(r"[^a-zA-Z0-9\-_.]+", "_", text.strip())[:limit]
    return base or "file"


def _repair_remux(in_path: Path) -> Optional[Path]:
    out_path = in_path.with_suffix(".fixed.mp4")
    try:
        run_ffmpeg([
            "-y",
            "-i", str(in_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ])
        if _valid_media(out_path):
            return out_path
    except Exception:
        pass
    return None


def _repair_reencode(in_path: Path) -> Optional[Path]:
    out_path = in_path.with_suffix(".reenc.mp4")
    try:
        run_ffmpeg([
            "-y",
            "-i", str(in_path),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ])
        if _valid_media(out_path):
            return out_path
    except Exception:
        pass
    return None


def _finalize_download(path: Path) -> Path:
    if _valid_media(path):
        return path
    fixed = _repair_remux(path)
    if fixed and _valid_media(fixed):
        try:
            path.unlink(missing_ok=True)
            fixed.rename(path)
        except Exception:
            path = fixed
        return path
    reenc = _repair_reencode(path)
    if reenc and _valid_media(reenc):
        try:
            path.unlink(missing_ok=True)
            reenc.rename(path)
        except Exception:
            path = reenc
        return path
    raise FFMpegError("downloaded file broken: could not repair/remux")


def download_video(url: str, out_dir: PathLike, ext: str = "mp4") -> Path:
    c = get_cfg()
    outd = Path(out_dir)
    outd.mkdir(parents=True, exist_ok=True)

    fmt_pref = (
        "bestvideo[ext=mp4][vcodec^=avc1]/"
        "bestvideo*[vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"b[ext={ext}]/b"
    )

    if ytdlp_mod is not None:
        templ = str(outd / f"%(title).80s-%(id)s.%(ext)s")
        ydl_opts = {
            "outtmpl": templ,
            "quiet": True,
            "noplaylist": True,
            "retries": 3,
            "consoletitle": False,
            "nocheckcertificate": True,
            "user_agent": c.user_agent,
            "format": fmt_pref,
            "merge_output_format": ext,
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": ext},
            ],
            "postprocessor_args": {
                "FFmpegVideoRemuxer": ["-movflags", "+faststart"]
            },
        }
        with ytdlp_mod.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fpath = Path(ydl.prepare_filename(info))
    else:
        if not c.has_ytdlp:
            raise FFMpegError("yt-dlp not available (pip or binary)")
        templ = str(outd / "%(title).80s-%(id)s.%(ext)s")
        cmd = [
            c.ytdlp,
            "-o", templ,
            "-f", fmt_pref,
            "--no-playlist",
            "--retries", "3",
            "--no-progress",
            "--merge-output-format", ext,
            "--postprocessor-args", "FFmpegVideoRemuxer:-movflags +faststart",
            "--user-agent", c.user_agent,
            url,
        ]
        code, out, err = _run(cmd)
        if code != 0:
            raise FFMpegError(err.strip() or "yt-dlp failed")
        latest = sorted(Path(out_dir).glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not latest:
            raise FFMpegError("downloaded file not found")
        fpath = latest[0]

    return _finalize_download(Path(fpath))


def temp_media(prefix: str = "clip_", suffix: str = ".mp4") -> Path:
    f = tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, delete=False)
    Path(f.name).unlink(missing_ok=True)
    return Path(f.name)


def ensure_tools() -> Dict[str, bool]:
    return {
        "ffmpeg": get_cfg().has_ffmpeg,
        "ffprobe": get_cfg().has_ffprobe,
        "yt_dlp": bool(ytdlp_mod or get_cfg().has_ytdlp),
    }
