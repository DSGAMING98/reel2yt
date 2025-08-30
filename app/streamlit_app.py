# details: Streamlit UI (paste IG URL → check YT twin) + diagnostics, no 'from app.*' imports
from __future__ import annotations
import sys, os, time, json, subprocess, importlib, types
from pathlib import Path
from typing import Dict, Any, List, Optional

import streamlit as st

# paths
_THIS = Path(__file__).resolve()
_ROOT = _THIS.parents[1]
APP_DIR = _ROOT / "app"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# create a synthetic 'app' package pointing to our APP_DIR so relative imports inside submodules work
def _ensure_app_pkg():
    pkg_dir = str(APP_DIR)
    mod = sys.modules.get("app")
    need = True
    if mod and hasattr(mod, "__path__"):
        paths = list(getattr(mod, "__path__", []))
        if pkg_dir in paths:
            need = False
    if need:
        sys.modules.pop("app", None)
        pkg = types.ModuleType("app")
        pkg.__path__ = [pkg_dir]  # namespace-style
        sys.modules["app"] = pkg

_ensure_app_pkg()

# import modules via importlib (no 'from app.*' anywhere)
pipeline = importlib.import_module("app.core.pipeline")
config = importlib.import_module("app.utils.config")
ig_fetch = importlib.import_module("app.services.ig_fetch")

find_youtube_for_reel = pipeline.find_youtube_for_reel
ensure_prereqs = pipeline.ensure_prereqs
get_cfg = config.get_cfg
sanity = config.sanity
quick_peek = ig_fetch.quick_peek

st.set_page_config(page_title="Reel → YouTube Match", page_icon="🎯", layout="centered")


def _badge(ok: bool) -> str:
    return "✅" if ok else "❌"


def _fmt_s(s: Optional[float]) -> str:
    if s is None:
        return "-"
    return f"{s:.2f}"


def _shell_ver(cmd: List[str]) -> str:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=6)
        out = (p.stdout or p.stderr or "").splitlines()
        return out[0].strip() if out else "n/a"
    except Exception as e:
        return f"n/a ({e.__class__.__name__})"


def _toolbox():
    c = get_cfg()
    ok = ensure_prereqs()
    with st.sidebar:
        st.markdown("### Tools")
        col1, col2, col3 = st.columns(3)
        col1.metric("ffmpeg", _badge(ok.get("ffmpeg", False)))
        col2.metric("ffprobe", _badge(ok.get("ffprobe", False)))
        col3.metric("yt-dlp", _badge(ok.get("yt_dlp", False)))
        st.caption(f"user-agent: {c.user_agent}")
        st.divider()
        st.markdown("### Settings")
        force = st.toggle("force fresh check", value=False, help="ignore cached result")
        max_eval = st.slider("max candidates to evaluate", 6, 24, 12, 2)
        st.caption("tune if needed; higher = slower, sometimes more accurate")

        st.divider()
        diag_on = st.toggle("diagnostics mode", value=False, help="prints detailed IG/yt-dlp info below")
        if diag_on:
            if st.button("Run diagnostics", use_container_width=True):
                st.session_state["_run_diag"] = True
        else:
            st.session_state["_run_diag"] = False
        return force, max_eval, ok, diag_on


def _header():
    st.markdown("## Reel → YouTube")
    st.caption("Paste an Instagram Reel/TV/Post URL. I’ll hunt for the same video on YouTube and verify by frames+audio.")


def _result_card(res: Dict[str, Any]):
    if res.get("ok") and res.get("best"):
        best = res["best"]
        st.success("Match found")
        st.markdown(f"**YouTube:** [{best['title']}]({best['url']})")
        cols = st.columns(4)
        cols[0].metric("fused", _fmt_s(best.get("fused_score")))
        cols[1].metric("frames", _fmt_s(best.get("frame_score")))
        cols[2].metric("audio", _fmt_s(best.get("audio_score")))
        cols[3].metric("match frac", _fmt_s(best.get("frame_match_frac")))
        st.caption(best.get("reason", ""))
    else:
        st.warning("No confident match")


def _top_table(top: List[Dict[str, Any]]):
    if not top:
        return
    st.markdown("#### Top candidates")
    rows = []
    for t in top:
        rows.append({
            "score": f"{t.get('fused_score', 0):.2f}",
            "title": t.get("title", "")[:80],
            "channel": t.get("channel") or "-",
            "url": t.get("url"),
            "frames": f"{t.get('frame_score', 0):.2f}",
            "audio": f"{t.get('audio_score', 0):.2f}",
            "match": f"{t.get('frame_match_frac', 0):.2f}",
            "dur(s)": f"{t.get('yt_duration', 0) or 0:.0f}",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _diagnostics(url: str, ok_tools: Dict[str, bool]):
    st.markdown("### Diagnostics")
    cfg = get_cfg()

    cols = st.columns(3)
    cols[0].write(f"ffmpeg: **{_badge(ok_tools.get('ffmpeg', False))}** — {_shell_ver([cfg.ffmpeg, '-version']) if ok_tools.get('ffmpeg') else 'missing'}")
    cols[1].write(f"ffprobe: **{_badge(ok_tools.get('ffprobe', False))}** — {_shell_ver([cfg.ffprobe, '-version']) if ok_tools.get('ffprobe') else 'missing'}")
    cols[2].write(f"yt-dlp: **{_badge(ok_tools.get('yt_dlp', False))}** — {_shell_ver(['yt-dlp', '--version']) if ok_tools.get('yt_dlp') else 'missing'}")

    st.caption(f"cookies mode: {'ON' if os.getenv('USE_BROWSER_COOKIES','').lower() in {'1','true','yes','on'} else 'off'} • browser: {os.getenv('BROWSER','chrome')}")
    if not url.strip():
        st.info("Paste an Instagram URL above, then click Run diagnostics.")
        return

    st.write(f"URL: `{url}`")

    try:
        qp = quick_peek(url)
        st.code(json.dumps(qp, ensure_ascii=False, indent=2), language="json")
        if not qp.get("title") and not qp.get("duration"):
            st.warning("No title/duration from probe — likely login/region/age-gated or yt-dlp needs update/cookies.")
    except Exception as e:
        st.error(f"quick_peek failed: {e}")

    st.markdown("**If probe empty:**")
    st.markdown("- Update yt-dlp (`pip install -U yt-dlp`)")
    st.markdown("- Turn ON cookies in `.env` (USE_BROWSER_COOKIES=true, BROWSER=chrome), then restart")
    st.markdown("- Test in terminal: `yt-dlp -v -j \"<URL>\"`")


def main():
    _header()
    force, max_eval, ok_tools, diag_on = _toolbox()
    if not all(ok_tools.values()):
        st.info("Install ffmpeg/ffprobe and yt-dlp, then reload.")

    url = st.text_input("Instagram URL", placeholder="https://www.instagram.com/reel/XXXXXXXX/")
    go = st.button("Find YouTube match", type="primary", use_container_width=True)

    if st.session_state.get("_run_diag"):
        _diagnostics(url, ok_tools)

    if go:
        if not url.strip():
            st.error("Paste a valid Instagram URL")
            st.stop()
        with st.spinner("Crunching frames and audio…"):
            try:
                res_obj = find_youtube_for_reel(url, force=force, max_eval=max_eval)
                res = {
                    "ok": res_obj.ok,
                    "reason": res_obj.reason,
                    "ig_url": res_obj.ig_url,
                    "ig_title": res_obj.ig_title,
                    "ig_uploader": res_obj.ig_uploader,
                    "ig_duration": res_obj.ig_duration,
                    "best": res_obj.best,
                    "top": res_obj.top,
                    "took_ms": res_obj.took_ms,
                    "cached": res_obj.cached,
                }
            except Exception as e:
                st.exception(e)
                st.stop()

        st.markdown("---")
        st.markdown(f"**IG title:** {res.get('ig_title') or '-'}")
        st.caption(f"uploader: {res.get('ig_uploader') or '-'} • duration(s): {int(res.get('ig_duration') or 0)} • time: {res.get('took_ms')} ms")

        _result_card(res)
        _top_table(res.get("top", []))


if __name__ == "__main__":
    main()
