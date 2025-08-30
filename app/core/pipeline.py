# details: end-to-end find → score → pick
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.config import get_cfg
from ..utils.ffmpeg import ensure_tools
from ..services.ig_fetch import get_reel_info
from ..services.yt_search import search_candidates_for_reel, YTCandidate
from ..services.matcher import rank_candidates, pick_best, CandidateScore


def _norm_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u.split("?")[0].rstrip("/") + "/"


def _key(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _cache_path(name: str) -> Path:
    c = get_cfg().cache_dir / "results"
    c.mkdir(parents=True, exist_ok=True)
    return c / f"{name}.json"


def _score_to_dict(s: CandidateScore) -> Dict[str, Any]:
    return {
        "video_id": s.video_id,
        "url": s.url,
        "title": s.title,
        "channel": s.channel,
        "yt_duration": s.yt_duration,
        "frame_median": s.frame_median,
        "frame_match_frac": s.frame_match_frac,
        "frame_score": s.frame_score,
        "audio_score": s.audio_score,
        "fused_score": s.fused_score,
        "title_ratio": s.title_ratio,
        "reason": s.reason,
    }


@dataclass
class PipelineResult:
    ok: bool
    reason: Optional[str]
    ig_url: str
    ig_title: Optional[str]
    ig_uploader: Optional[str]
    ig_duration: Optional[float]
    best: Optional[Dict[str, Any]]
    top: List[Dict[str, Any]]
    took_ms: int
    cached: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


def ensure_prereqs() -> Dict[str, bool]:
    return ensure_tools()


def find_youtube_for_reel(ig_url: str, force: bool = False, max_eval: int = 12) -> PipelineResult:
    t0 = time.time()
    cfg = get_cfg()

    # tool sanity
    tools = ensure_prereqs()
    if not (tools.get("ffmpeg") and tools.get("ffprobe") and tools.get("yt_dlp")):
        return PipelineResult(
            ok=False,
            reason="missing tools: ffmpeg/ffprobe/yt-dlp",
            ig_url=_norm_url(ig_url),
            ig_title=None,
            ig_uploader=None,
            ig_duration=None,
            best=None,
            top=[],
            took_ms=int((time.time() - t0) * 1000),
            cached=False,
        )

    u = _norm_url(ig_url)
    key = _key(u)
    cp = _cache_path(key)

    if cp.exists() and not force:
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            return PipelineResult(**data)
        except Exception:
            pass  # ignore bad cache

    # fetch IG
    reel = get_reel_info(u, sample_limit=40)

    # search YT
    cands: List[YTCandidate] = search_candidates_for_reel(
        title=reel.title,
        uploader=reel.uploader,
        duration=reel.duration,
        extra_terms=None,
    )

    # rank + pick
    scored = rank_candidates(reel, cands, max_eval=max_eval)
    best = pick_best(scored)

    top = [_score_to_dict(s) for s in scored[:6]]
    best_dict = _score_to_dict(best) if best else None

    res = PipelineResult(
        ok=bool(best_dict),
        reason=None if best_dict else "no confident match",
        ig_url=u,
        ig_title=reel.title,
        ig_uploader=reel.uploader,
        ig_duration=reel.duration,
        best=best_dict,
        top=top,
        took_ms=int((time.time() - t0) * 1000),
        cached=False,
    )

    try:
        cp.write_text(res.to_json(), encoding="utf-8")
    except Exception:
        pass

    return res
