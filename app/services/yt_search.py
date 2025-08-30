# details: YouTube search (API or no-API) → candidates (robust)
from __future__ import annotations
import re, time
from dataclasses import dataclass
from typing import List, Dict, Optional, Iterable

import requests

from ..utils.config import get_cfg

# optional libs (we'll gracefully fallback if missing)
try:
    from youtubesearchpython import VideosSearch  # may break with httpx>=1.x
except Exception:
    VideosSearch = None  # type: ignore

try:
    import yt_dlp as ytdlp_mod  # solid fallback for search
except Exception:
    ytdlp_mod = None  # type: ignore

_DUR_RX = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


@dataclass
class YTCandidate:
    video_id: str
    url: str
    title: str
    channel: Optional[str]
    duration: Optional[float]
    thumbnails: List[Dict]
    views: Optional[int]
    published_text: Optional[str]
    score_hint: float = 0.0


def _parse_views(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    v = str(v).replace(",", "").strip().lower()
    m = re.match(r"([\d\.]+)\s*([km]?)", v)
    if not m:
        try:
            return int(v)
        except Exception:
            return None
    num = float(m.group(1))
    suf = m.group(2)
    if suf == "k":
        num *= 1_000
    elif suf == "m":
        num *= 1_000_000
    return int(num)


def _parse_duration(d: Optional[str]) -> Optional[float]:
    if d is None:
        return None
    d = str(d).strip()
    if d.isdigit():
        return float(int(d))
    m = _DUR_RX.match(d)
    if not m:
        m2 = re.match(r"(\d+)\s*s", d.lower())
        if m2:
            return float(int(m2.group(1)))
        return None
    hh = int(m.group(1) or 0)
    mm = int(m.group(2))
    ss = int(m.group(3))
    return float(hh * 3600 + mm * 60 + ss)


def _clean_title(t: str) -> str:
    t = re.sub(r"#\w+", " ", t or "")
    t = re.sub(r"@[\w.]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _title_keywords(t: str, uploader: Optional[str]) -> List[str]:
    t = (t or "").lower()
    t = re.sub(r"(instagram|reel|shorts|tiktok)", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    parts = [w for w in t.split() if len(w) >= 3][:8]
    if uploader:
        parts.append(uploader.lower())
    # dedupe, keep order
    seen, out = set(), []
    for w in parts:
        if w not in seen:
            seen.add(w); out.append(w)
    return out


def _dedupe(cands: Iterable[YTCandidate]) -> List[YTCandidate]:
    seen = set(); out = []
    for c in cands:
        if c.video_id in seen:
            continue
        seen.add(c.video_id)
        out.append(c)
    return out


def _via_api(query: str, max_results: int) -> List[YTCandidate]:
    cfg = get_cfg()
    key = cfg.yt_api_key
    base = "https://www.googleapis.com/youtube/v3/search"
    r = requests.get(base, params=dict(
        part="snippet",
        q=query,
        type="video",
        maxResults=min(50, max_results),
        key=key,
        safeSearch="none",
    ), timeout=12)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        return []

    ids = ",".join(i["id"]["videoId"] for i in items if "id" in i and "videoId" in i["id"])
    vd = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params=dict(id=ids, key=key, part="contentDetails,statistics"),
        timeout=12,
    )
    vd.raise_for_status()
    dur_map: Dict[str, Optional[float]] = {}
    views_map: Dict[str, Optional[int]] = {}

    for it in vd.json().get("items", []):
        vid = it["id"]
        iso = (it.get("contentDetails", {}) or {}).get("duration")
        dur_map[vid] = _iso8601_to_seconds(iso) if iso else None
        views = (it.get("statistics", {}) or {}).get("viewCount")
        views_map[vid] = int(views) if views else None

    out: List[YTCandidate] = []
    for i in items:
        vid = i["id"]["videoId"]
        sn = i.get("snippet", {})
        out.append(
            YTCandidate(
                video_id=vid,
                url=f"https://www.youtube.com/watch?v={vid}",
                title=_clean_title(sn.get("title") or ""),
                channel=sn.get("channelTitle"),
                duration=dur_map.get(vid),
                thumbnails=[(sn.get("thumbnails", {}) or {}).get("default", {})],
                views=views_map.get(vid),
                published_text=sn.get("publishedAt"),
            )
        )
    return out


def _iso8601_to_seconds(s: Optional[str]) -> Optional[float]:
    if not s or not s.startswith("PT"):
        return None
    hrs = mins = secs = 0
    for part in re.findall(r"(\d+H|\d+M|\d+S)", s):
        if part.endswith("H"): hrs = int(part[:-1])
        elif part.endswith("M"): mins = int(part[:-1])
        elif part.endswith("S"): secs = int(part[:-1])
    return float(hrs * 3600 + mins * 60 + secs)


def _via_youtubesearchpython(query: str, max_results: int) -> List[YTCandidate]:
    if not VideosSearch:
        raise RuntimeError("youtubesearchpython not available")
    vs = VideosSearch(query, limit=min(50, max_results))
    data = vs.result()
    out: List[YTCandidate] = []
    for it in data.get("result", []):
        vid = it.get("id")
        if not vid:
            continue
        out.append(
            YTCandidate(
                video_id=vid,
                url=f"https://www.youtube.com/watch?v={vid}",
                title=_clean_title(it.get("title") or ""),
                channel=(it.get("channel", {}) or {}).get("name"),
                duration=_parse_duration(it.get("duration")),
                thumbnails=it.get("thumbnails") or [],
                views=_parse_views(((it.get("viewCount", {}) or {}).get("shortText"))),
                published_text=it.get("publishedTime"),
            )
        )
    return out


def _via_ytdlp_search(query: str, max_results: int) -> List[YTCandidate]:
    """Fallback that uses yt-dlp to search without downloading media."""
    if not ytdlp_mod:
        return []
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
    }
    out: List[YTCandidate] = []
    q = f"ytsearch{min(50, max_results)}:{query}"
    with ytdlp_mod.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(q, download=False)
            for e in (info.get("entries") or []):
                vid = e.get("id")
                if not vid:
                    continue
                out.append(
                    YTCandidate(
                        video_id=vid,
                        url=f"https://www.youtube.com/watch?v={vid}",
                        title=_clean_title(e.get("title") or ""),
                        channel=e.get("uploader"),
                        duration=float(e.get("duration")) if e.get("duration") else None,
                        thumbnails=e.get("thumbnails") or [],
                        views=int(e["view_count"]) if e.get("view_count") else None,
                        published_text=e.get("upload_date"),
                    )
                )
        except Exception:
            return []
    return out


def _via_scrape(query: str, max_results: int) -> List[YTCandidate]:
    # try youtubesearchpython first (fast), then yt-dlp search fallback
    try:
        if VideosSearch:
            return _via_youtubesearchpython(query, max_results)
    except Exception:
        pass
    return _via_ytdlp_search(query, max_results)


def yt_search(query: str, max_results: Optional[int] = None) -> List[YTCandidate]:
    cfg = get_cfg()
    n = max_results or cfg.max_candidates
    if cfg.use_yt_api and cfg.yt_api_key:
        try:
            return _via_api(query, n)
        except Exception:
            pass
    return _via_scrape(query, n)


def search_candidates_for_reel(title: str, uploader: Optional[str], duration: Optional[float], extra_terms: Optional[List[str]] = None) -> List[YTCandidate]:
    cfg = get_cfg()
    kws = _title_keywords(title or "", uploader)
    if extra_terms:
        kws += [t.lower() for t in extra_terms]
    # dedupe keep first 8
    seen, tmp = set(), []
    for w in kws:
        if w not in seen:
            seen.add(w); tmp.append(w)
        if len(tmp) >= 8:
            break
    kws = tmp

    queries = []
    if kws:
        queries.append(" ".join(kws))
    if uploader and title:
        queries.append(f"{uploader} {title}")
    if title:
        queries.append(title)
    if uploader:
        queries.append(uploader)

    all_cands: List[YTCandidate] = []
    for q in queries:
        got = yt_search(q, max_results=max(10, cfg.max_candidates // 2))
        all_cands.extend(got)
        time.sleep(0.4)

    # coarse scoring hints (duration/title/views)
    for c in all_cands:
        s = 0.0
        if duration and c.duration:
            dd = abs(c.duration - duration)
            if dd <= 2: s += 3.0
            elif dd <= 5: s += 2.0
            elif dd <= 10: s += 1.0
        t = (c.title or "").lower()
        if any(k in t for k in kws[:4]): s += 1.5
        if uploader and c.channel and uploader.lower() in c.channel.lower(): s += 1.0
        if c.views: s += min(2.0, (c.views / 1_000_000.0))
        c.score_hint = s

    uniq = _dedupe(all_cands)
    uniq.sort(key=lambda x: x.score_hint, reverse=True)
    return uniq[: cfg.max_candidates]
