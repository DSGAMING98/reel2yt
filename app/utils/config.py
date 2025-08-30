# details: env, paths, tool checks, single source of truth
from __future__ import annotations
import os, sys, shutil, platform
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

def _find_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / "app").is_dir():
            return p
    return Path.cwd()

ROOT = _find_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_DIR = ROOT / "app"
DATA_DIR = APP_DIR / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
CACHE_DIR = DATA_DIR / "cache"
for d in (DATA_DIR, DOWNLOADS_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# .env is optional
load_dotenv(dotenv_path=ROOT / ".env")

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}

def _bin_ok(cmd: str) -> tuple[str, bool]:
    path = shutil.which(cmd)
    return (path or cmd), bool(path)

YT_API_KEY = (os.getenv("YT_API_KEY") or "").strip()
USE_YT_API = _env_bool("USE_YT_API", bool(YT_API_KEY))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "25"))
FRAME_STRIDE_SEC = float(os.getenv("FRAME_STRIDE_SEC", "0.8"))
HASH_THRESHOLD = int(os.getenv("HASH_THRESHOLD", "8"))
AUDIO_FP = _env_bool("AUDIO_FP", True)

FFMPEG, HAS_FFMPEG = _bin_ok("ffmpeg")
FFPROBE, HAS_FFPROBE = _bin_ok("ffprobe")
YTDLP, HAS_YTDLP = _bin_ok("yt-dlp")

@dataclass(frozen=True)
class CFG:
    root: Path = ROOT
    app_dir: Path = APP_DIR
    data_dir: Path = DATA_DIR
    downloads_dir: Path = DOWNLOADS_DIR
    cache_dir: Path = CACHE_DIR
    yt_api_key: str = YT_API_KEY
    use_yt_api: bool = USE_YT_API
    max_candidates: int = MAX_CANDIDATES
    frame_stride_sec: float = FRAME_STRIDE_SEC
    hash_threshold: int = HASH_THRESHOLD
    audio_fp: bool = AUDIO_FP
    ffmpeg: str = FFMPEG
    ffprobe: str = FFPROBE
    ytdlp: str = YTDLP
    has_ffmpeg: bool = HAS_FFMPEG
    has_ffprobe: bool = HAS_FFPROBE
    has_ytdlp: bool = HAS_YTDLP
    user_agent: str = f"reel2yt/unique-{platform.system().lower()} py{sys.version_info.major}.{sys.version_info.minor}"

_CFG: CFG | None = None

def get_cfg() -> CFG:
    global _CFG
    if _CFG is None:
        _CFG = CFG()
    return _CFG

def sanity() -> dict:
    c = get_cfg()
    return {
        "ffmpeg": c.has_ffmpeg,
        "ffprobe": c.has_ffprobe,
        "yt_dlp": c.has_ytdlp,
        "yt_api": c.use_yt_api,
    }
