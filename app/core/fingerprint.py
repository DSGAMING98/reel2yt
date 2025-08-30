# details: frame pHash + audio chroma; caching; similarity
from __future__ import annotations
import hashlib, json, math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import numpy as np
from PIL import Image
import imagehash
import librosa

from ..utils.config import get_cfg
from ..utils.ffmpeg import extract_frames, extract_audio_wav, duration_sec

_HASH_SIZE = 8  # 64-bit pHash
_VER = "fp_v1"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _fp_dir() -> Path:
    d = get_cfg().cache_dir / "fp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _video_sig(path: Path, extra: str = "") -> str:
    p = Path(path).resolve()
    mt = str(p.stat().st_mtime_ns)
    return _sha1(f"{p}|{mt}|{extra}|{_VER}")


def _save_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")


def _load_json(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _hash_image(p: Path) -> str:
    img = Image.open(p).convert("L")
    h = imagehash.phash(img, hash_size=_HASH_SIZE)
    return h.__str__()  # hex


def _hex_to_hash(hx: str) -> imagehash.ImageHash:
    return imagehash.hex_to_hash(hx)


def _hamming(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    return a - b  # imagehash defines __sub__ as Hamming


def frame_hashes_from_dir(frames_dir: Path, limit: Optional[int] = None) -> List[str]:
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if limit:
        frames = frames[:limit]
    return [_hash_image(p) for p in frames]


def video_to_frame_hashes(video_path: Path, stride_sec: float = 0.8, limit: Optional[int] = 40) -> List[str]:
    cfg = get_cfg()
    sig = _video_sig(video_path, f"stride={stride_sec}")
    cache = _fp_dir() / f"frames_{sig}.json"
    if cache.exists():
        data = _load_json(cache)
        if data and data.get("ver") == _VER:
            return data.get("hashes", [])
    tmp_dir = cfg.cache_dir / "tmp_frames" / sig
    tmp_dir.mkdir(parents=True, exist_ok=True)
    extract_frames(video_path, tmp_dir, stride_sec=stride_sec, limit=limit)
    hashes = frame_hashes_from_dir(tmp_dir, limit=limit)
    _save_json(cache, {"ver": _VER, "hashes": hashes, "hash_size": _HASH_SIZE})
    return hashes


def audio_vec_from_wav(wav_path: Path) -> Optional[List[float]]:
    sig = _video_sig(wav_path)
    cache = _fp_dir() / f"audio_{sig}.json"
    if cache.exists():
        data = _load_json(cache)
        if data and data.get("ver") == _VER:
            return data.get("vec", None)

    try:
        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        if y.size == 0:
            return None
        C = librosa.feature.chroma_cens(y=y, sr=sr)  # (12, T)
        v = np.mean(C, axis=1)  # (12,)
        n = np.linalg.norm(v)
        if n == 0:
            return None
        v = (v / n).astype(np.float32)
        vec = v.tolist()
        _save_json(cache, {"ver": _VER, "vec": vec})
        return vec
    except Exception:
        return None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    da = float(np.linalg.norm(a))
    db = float(np.linalg.norm(b))
    if da == 0 or db == 0:
        return 0.0
    return float(np.dot(a, b) / (da * db))


@dataclass
class FrameMatchStats:
    median: float
    mean: float
    p25: float
    p75: float
    match_frac: float
    score: float  # 0..1


def frame_similarity_score(hashes_a: List[str], hashes_b: List[str], threshold: Optional[int] = None) -> FrameMatchStats:
    cfg = get_cfg()
    t = threshold if threshold is not None else cfg.hash_threshold
    if not hashes_a or not hashes_b:
        return FrameMatchStats(64.0, 64.0, 64.0, 64.0, 0.0, 0.0)

    A = [_hex_to_hash(h) for h in hashes_a]
    B = [_hex_to_hash(h) for h in hashes_b]

    dists: List[int] = []
    for ha in A:
        md = min(_hamming(ha, hb) for hb in B)
        dists.append(int(md))

    arr = np.array(dists, dtype=np.float32)
    median = float(np.median(arr))
    mean = float(np.mean(arr))
    p25 = float(np.percentile(arr, 25))
    p75 = float(np.percentile(arr, 75))
    match_frac = float(np.mean(arr <= t))

    closeness = max(0.0, 1.0 - (median / 64.0))
    score = 0.6 * match_frac + 0.4 * closeness
    return FrameMatchStats(median, mean, p25, p75, match_frac, score)


def audio_similarity_score(vec_a: Optional[List[float]], vec_b: Optional[List[float]]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    return max(0.0, min(1.0, cosine(a, b)))


@dataclass
class CombinedScore:
    frames: FrameMatchStats
    audio: float
    fused: float  # 0..1


def fuse_scores(frame_stats: FrameMatchStats, audio_sim: float) -> CombinedScore:
    if audio_sim > 0:
        fused = 0.7 * frame_stats.score + 0.3 * audio_sim
    else:
        fused = frame_stats.score
    return CombinedScore(frame_stats, audio_sim, fused)


def prepare_audio_vec_for_video(video_path: Path) -> Optional[List[float]]:
    cfg = get_cfg()
    tmp_wav = cfg.cache_dir / "audio_tmp" / f"{_video_sig(video_path)}.wav"
    tmp_wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        extract_audio_wav(video_path, tmp_wav, sr=22050)
    except Exception:
        return None
    return audio_vec_from_wav(tmp_wav)


def quick_video_fp(video_path: Path, stride_sec: Optional[float] = None, limit: Optional[int] = 40) -> Tuple[List[str], Optional[List[float]]]:
    cfg = get_cfg()
    s = stride_sec if stride_sec is not None else cfg.frame_stride_sec
    fhashes = video_to_frame_hashes(video_path, stride_sec=s, limit=limit)
    avec = prepare_audio_vec_for_video(video_path) if cfg.audio_fp else None
    return fhashes, avec
