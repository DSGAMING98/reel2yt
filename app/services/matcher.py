# details: IG ↔ YT scoring + selection
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict

from rapidfuzz import fuzz

from ..utils.config import get_cfg
from ..utils.ffmpeg import download_video, duration_sec, FFMpegError
from ..core.fingerprint import (
    frame_hashes_from_dir,
    audio_vec_from_wav,
    quick_video_fp,
    frame_similarity_score,
    audio_similarity_score,
    fuse_scores,
)
from .ig_fetch import ReelInfo
from .yt_search import YTCandidate


@dataclass
class CandidateScore:
    video_id: str
    url: str
    title: str
    channel: Optional[str]
    yt_duration: Optional[float]
    frame_median: float
    frame_match_frac: float
    frame_score: float
    audio_score: float
    fused_score: float
    title_ratio: float
    reason: str


def _title_boost(ig_title: str, yt_title: str) -> float:
    if not ig_title or not yt_title:
        return 0.0
    r = fuzz.partial_ratio(ig_title.lower(), yt_title.lower()) / 100.0
    return 0.15 * r


def _duration_ok(ig_dur: float, yt_dur: Optional[float]) -> bool:
    if not yt_dur:
        return True
    dd = abs(ig_dur - yt_dur)
    if ig_dur <= 60:
        return dd <= 8
    if ig_dur <= 180:
        return dd <= 12
    return dd <= 20


def _reason_line(stats, a, tboost) -> str:
    return f"frames:median={stats.frames.median:.1f}|match={stats.frames.match_frac:.2f} audio={a:.2f} tboost={tboost:.2f}"


def score_candidate(reel: ReelInfo, cand: YTCandidate) -> Optional[CandidateScore]:
    cfg = get_cfg()
    if not _duration_ok(reel.duration, cand.duration):
        return None

    dl_dir = cfg.downloads_dir / "yt"
    try:
        ypath = download_video(cand.url, dl_dir, ext="mp4")
    except FFMpegError:
        return None

    ig_hashes = frame_hashes_from_dir(reel.frames_dir, limit=40)
    yt_hashes, yt_avec = quick_video_fp(ypath, stride_sec=cfg.frame_stride_sec, limit=40)

    fstats = frame_similarity_score(ig_hashes, yt_hashes, threshold=cfg.hash_threshold)

    ig_avec = None
    if reel.audio_wav:
        ig_avec = audio_vec_from_wav(reel.audio_wav)
    a_sim = audio_similarity_score(ig_avec, yt_avec) if ig_avec and yt_avec else 0.0

    fused = fuse_scores(fstats, a_sim).fused

    tboost = _title_boost(reel.title, cand.title)
    fused2 = min(1.0, fused + tboost)

    return CandidateScore(
        video_id=cand.video_id,
        url=cand.url,
        title=cand.title,
        channel=cand.channel,
        yt_duration=cand.duration,
        frame_median=fstats.median,
        frame_match_frac=fstats.match_frac,
        frame_score=fstats.score,
        audio_score=a_sim,
        fused_score=fused2,
        title_ratio=tboost,
        reason=_reason_line(fuse_scores(fstats, a_sim), a_sim, tboost),
    )


def rank_candidates(reel: ReelInfo, candidates: List[YTCandidate], max_eval: int = 12) -> List[CandidateScore]:
    out: List[CandidateScore] = []
    seen = set()
    for c in candidates[:max_eval]:
        if c.video_id in seen:
            continue
        seen.add(c.video_id)
        sc = score_candidate(reel, c)
        if sc:
            out.append(sc)
    out.sort(key=lambda x: x.fused_score, reverse=True)
    return out


def pick_best(scored: List[CandidateScore]) -> Optional[CandidateScore]:
    if not scored:
        return None
    top = scored[0]

    if top.fused_score >= 0.62 and top.frame_match_frac >= 0.55:
        return top
    return None
