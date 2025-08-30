# details: tiny disk KV cache (json/bytes) with optional TTL
from __future__ import annotations
import json, time, hashlib
from pathlib import Path
from typing import Optional, Any

from .config import get_cfg


def _ns_dir(ns: str) -> Path:
    d = get_cfg().cache_dir / "kv" / _safe(ns)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "default"
    if len(s) > 48:
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:24]
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in s)


def _key_path(ns: str, key: str, ext: str) -> Path:
    base = _safe(key)
    if len(base) > 64:
        base = hashlib.sha1(base.encode("utf-8")).hexdigest()[:32]
    return _ns_dir(ns) / f"{base}.{ext}"


def set_json(ns: str, key: str, obj: Any, ttl_sec: Optional[int] = None) -> Path:
    p = _key_path(ns, key, "json")
    payload = {"_ts": int(time.time()), "_ttl": int(ttl_sec or 0), "data": obj}
    p.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return p


def get_json(ns: str, key: str) -> Optional[Any]:
    p = _key_path(ns, key, "json")
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if _expired(payload.get("_ts"), payload.get("_ttl", 0)):
        _safe_unlink(p)
        return None
    return payload.get("data")


def set_bytes(ns: str, key: str, data: bytes, ttl_sec: Optional[int] = None) -> Path:
    meta = _key_path(ns, key, "meta")
    blob = _key_path(ns, key, "bin")
    meta.write_text(json.dumps({"_ts": int(time.time()), "_ttl": int(ttl_sec or 0)}), encoding="utf-8")
    blob.write_bytes(data)
    return blob


def get_bytes(ns: str, key: str) -> Optional[bytes]:
    meta = _key_path(ns, key, "meta")
    blob = _key_path(ns, key, "bin")
    if not (meta.exists() and blob.exists()):
        return None
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    if _expired(m.get("_ts"), m.get("_ttl", 0)):
        _safe_unlink(meta)
        _safe_unlink(blob)
        return None
    return blob.read_bytes()


def has(ns: str, key: str) -> bool:
    return _key_path(ns, key, "json").exists() or _key_path(ns, key, "bin").exists()


def purge(ns: Optional[str] = None) -> int:
    root = get_cfg().cache_dir / "kv"
    if ns:
        roots = [_ns_dir(ns)]
    else:
        roots = [p for p in root.glob("*") if p.is_dir()]
    removed = 0
    now = int(time.time())
    for d in roots:
        for p in d.glob("*"):
            if p.suffix == ".json":
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    if _expired(payload.get("_ts"), payload.get("_ttl", 0), now):
                        _safe_unlink(p); removed += 1
                except Exception:
                    continue
            elif p.suffix in {".bin", ".meta"}:
                try:
                    if p.suffix == ".meta":
                        m = json.loads(p.read_text(encoding="utf-8"))
                        if _expired(m.get("_ts"), m.get("_ttl", 0), now):
                            _safe_unlink(p.with_suffix(".bin"))
                            _safe_unlink(p); removed += 2
                except Exception:
                    continue
    return removed


def _expired(ts: Optional[int], ttl: int, now: Optional[int] = None) -> bool:
    if not ts or not ttl:
        return False
    n = now or int(time.time())
    return (n - int(ts)) > int(ttl)


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass

