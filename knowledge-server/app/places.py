"""Map data: places named in document metadata, with coordinates and one short hint per place.

Coordinates come from places.json (checked-in demo seed). Unknown names are looked up once via
OpenStreetMap Nominatim and cached in .state/geocode.json. Hints ("what to watch out for here")
are extracted per document by the LLM and cached by content hash, so only new or changed
documents cost a call. Without an LLM the document title is used as the hint.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

from . import ai
from .store import STATE, slugify, split_frontmatter, store, view_url

log = logging.getLogger("knowledge")

SEED_FILE = Path(__file__).with_name("places.json")
GEO_CACHE = STATE / "geocode.json"
HINT_CACHE = STATE / "place-hints.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
CITY = os.getenv("MAP_CITY", "Hildesheim")
# lon_min,lat_max,lon_max,lat_min – results outside are ignored
VIEWBOX = os.getenv("MAP_VIEWBOX", "9.88,52.18,10.02,52.11")
# Most severe first: a place takes the most severe category of its current hints.
CATEGORIES = ("danger", "traffic", "priority", "info")
MAP_TYPES = {"Note", "Interview", "Voice note", "Document", "PDF"}
MISS_RETRY_SECONDS = 24 * 3600
NEW_DAYS = 1

HINT_PROMPT = """You turn a knowledge document of a city administration (winter service, street
operations) into short map hints for drivers and successors.

For EACH place in the given list write ONE hint of max. 15 words: what to watch out for or do AT
that place, based only on the document. Write in the same language as the document. Concrete
beats general ("Halteverbot 72 h vorher beantragen", not "Hier aufpassen").

Pick one category per hint:
- danger: ice, slipperiness, accidents, hazards, bridges freezing
- traffic: parking, buses, closures, access, rescue routes
- priority: what must be done first or by a certain time
- info: anything else

Return JSON: {"hints": [{"place": "<place name exactly as given>", "hint": "...", "category": "..."}]}"""


def split_places(raw: str) -> list[str]:
    names = [n.strip().strip('"').strip("'").strip() for n in re.split(r"[;,]", raw or "")]
    return [n for n in names if n]


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _store_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


class Places:
    def __init__(self) -> None:
        seed = {k: v for k, v in _load(SEED_FILE).items() if not k.startswith("_")}
        self.seed = seed
        self.alias = {a: k for k, v in seed.items() for a in v.get("aliases", [])}
        self.geo = _load(GEO_CACHE)
        self.hints = _load(HINT_CACHE)
        self.tasks: dict[str, asyncio.Task] = {}
        self.geo_lock = asyncio.Lock()

    # ---- coordinates
    def resolve(self, name: str) -> dict | None:
        """Pin for a place name from the seed or the geocode cache (never hits the network)."""
        key = slugify(name)
        key = self.alias.get(key, key)
        if key in self.seed:
            s = self.seed[key]
            if s.get("skip"):
                return None
            return {"key": key, "name": s["name"], "lat": s["lat"], "lon": s["lon"]}
        g = self.geo.get(key)
        if g and "lat" in g:
            return {"key": key, "name": g.get("name") or name, "lat": g["lat"], "lon": g["lon"]}
        return None

    async def geocode_missing(self, names: set[str], limit: int = 3) -> None:
        """Looks up a few unknown names via Nominatim (max. 1 request/second, results cached)."""
        todo = []
        for name in sorted(names):
            key = slugify(name)
            if self.resolve(name) or key in todo or self.seed.get(self.alias.get(key, key), {}).get("skip"):
                continue
            miss = self.geo.get(key, {}).get("miss")
            if miss and time.time() - miss < MISS_RETRY_SECONDS:
                continue
            todo.append(key)
            if len(todo) >= limit:
                break
        if not todo:
            return
        by_key = {slugify(n): n for n in names}
        async with self.geo_lock:
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "smart-city-knowledge-map/1.0"}) as client:
                for i, key in enumerate(todo):
                    name = by_key[key]
                    if i:
                        await asyncio.sleep(1.1)
                    try:
                        r = await client.get(
                            NOMINATIM,
                            params={"q": f"{name}, {CITY}", "format": "jsonv2", "limit": 1,
                                    "viewbox": VIEWBOX, "bounded": 1},
                        )
                        hit = r.json()[0] if r.status_code < 300 and r.json() else None
                    except (httpx.HTTPError, ValueError, IndexError):
                        hit = None
                    self.geo[key] = (
                        {"name": name, "lat": float(hit["lat"]), "lon": float(hit["lon"])}
                        if hit else {"miss": time.time()}
                    )
            _store_json(GEO_CACHE, self.geo)

    # ---- hints
    @staticmethod
    def _fingerprint(text: str, names: list[str]) -> str:
        return hashlib.sha1((text + "\n" + "|".join(names)).encode()).hexdigest()

    def _cached_hints(self, path: str, fingerprint: str) -> dict | None:
        c = self.hints.get(path)
        return c["hints"] if c and c.get("hash") == fingerprint else None

    async def _extract(self, path: str, title: str, names: list[str], fingerprint: str) -> None:
        _, body = split_frontmatter(store.get_text(path))
        out: dict[str, dict] = {}
        try:
            data = await ai.json_completion(
                HINT_PROMPT,
                f"Places: {json.dumps(names, ensure_ascii=False)}\nDocument title: {title}\n\n{body[:8000]}",
            )
            for h in data.get("hints", []):
                if not isinstance(h, dict) or not h.get("place") or not h.get("hint"):
                    continue
                cat = h.get("category") if h.get("category") in CATEGORIES else "info"
                out[slugify(str(h["place"]))] = {"hint": str(h["hint"]).strip(), "category": cat}
        except RuntimeError as e:
            log.warning("Place hints for %s failed: %s", path, e)
            return  # not cached → retried next time
        self.hints[path] = {"hash": fingerprint, "hints": out}
        _store_json(HINT_CACHE, self.hints)

    def _ensure_task(self, path: str, title: str, names: list[str], fingerprint: str) -> asyncio.Task:
        key = f"{path}:{fingerprint}"
        task = self.tasks.get(key)
        if task is None or (task.done() and task.exception() is not None):
            task = asyncio.create_task(self._extract(path, title, names, fingerprint))
            task.add_done_callback(lambda _t, k=key: self.tasks.pop(k, None))
            self.tasks[key] = task
        return task

    # ---- public
    def mappable(self, path: str) -> list[str]:
        """Place names of a document that have a pin (used to tell the chat model which names to map)."""
        meta, _ = split_frontmatter(store.get_text(path))
        raw = meta.get("ort") or str(meta.get("places", "")).strip("[]")
        out = []
        for name in split_places(raw):
            loc = self.resolve(name)
            if loc and loc["name"] not in out:
                out.append(loc["name"])
        return out

    async def payload(self, names: str = "", wait: float = 15.0) -> dict:
        docs = [
            d for d in store.documents()
            if d["document_status"] == "active" and d["typ"] in MAP_TYPES and d.get("ort")
        ]
        all_names = {n for d in docs for n in split_places(d["ort"])}
        await self.geocode_missing(all_names)

        pending, prepared = [], []
        for d in docs:
            doc_names = split_places(d["ort"])
            fp = self._fingerprint(store.get_text(d["path"]), doc_names)
            if self._cached_hints(d["path"], fp) is None and ai.enabled():
                pending.append(self._ensure_task(d["path"], d["title"], doc_names, fp))
            prepared.append((d, doc_names, fp))
        if pending:
            await asyncio.wait(pending, timeout=wait)

        cutoff = (dt.date.today() - dt.timedelta(days=NEW_DAYS)).isoformat()
        places: dict[str, dict] = {}
        unlocated: set[str] = set()
        for d, doc_names, fp in prepared:
            hints = self._cached_hints(d["path"], fp) or {}
            for name in doc_names:
                loc = self.resolve(name)
                if not loc:
                    unlocated.add(name)
                    continue
                p = places.setdefault(loc["key"], {**loc, "hints": []})
                if any(h["path"] == d["path"] for h in p["hints"]):
                    continue
                h = hints.get(slugify(name)) or hints.get(loc["key"]) or {}
                p["hints"].append({
                    "text": h.get("hint") or d["title"],
                    "category": h.get("category", "info"),
                    "doc_id": d.get("id") or "",
                    "title": d["title"],
                    "path": d["path"],
                    "url": view_url(d["path"]),
                    "typ": d["typ"],
                    "person": d["person"],
                    "date": d["datum"],
                    "outdated": bool(d.get("updated_by")),
                    "new": d["datum"] >= cutoff,
                })

        wanted = None
        if names.strip():
            wanted = {loc["key"] for loc in (self.resolve(n) for n in split_places(names)) if loc}
        out = []
        for p in places.values():
            if wanted is not None and p["key"] not in wanted:
                continue
            # Newest first, outdated (updated by a newer document) last
            p["hints"].sort(key=lambda h: h["date"], reverse=True)
            p["hints"].sort(key=lambda h: h["outdated"])
            current = [h for h in p["hints"] if not h["outdated"]] or p["hints"]
            p["category"] = min((h["category"] for h in current), key=CATEGORIES.index)
            p["new"] = any(h["new"] for h in p["hints"])
            out.append(p)
        out.sort(key=lambda p: (CATEGORIES.index(p["category"]), p["name"]))
        return {
            "city": CITY,
            "places": out,
            "unlocated": sorted(unlocated) if wanted is None else [],
            "pending": sum(1 for t in pending if not t.done()),
        }

    async def warm(self) -> None:
        """Fills the hint cache in the background after start-up."""
        await asyncio.sleep(5)
        try:
            await self.payload(wait=300)
        except Exception as e:  # noqa: BLE001
            log.warning("Warming the map failed: %s", e)


places = Places()
