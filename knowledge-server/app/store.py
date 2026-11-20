"""Wissensspeicher: echtes Dateisystem + automatischer RAG-Index über die LibreChat rag_api.

Das Dateisystem unter KNOWLEDGE_ROOT ist die einzige Quelle. Ein Hintergrund-Worker
beobachtet den Ordner, extrahiert Text und lässt jede Datei von der rag_api in pgvector
einbetten. Klappt das Einbetten nicht (z. B. kein Embedding-Key), bleibt die Datei über
eine Stichwortsuche auffindbar ("text_only").
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt

log = logging.getLogger("knowledge")

ROOT = Path(os.getenv("KNOWLEDGE_ROOT", "/data/knowledge")).resolve()
STATE = ROOT / ".state"
TEXT_CACHE = STATE / "text"
AUDIO_DIR = STATE / "audio"
INDEX_FILE = STATE / "index.json"
RAG_API_URL = os.getenv("RAG_API_URL", "http://rag_api:8000").rstrip("/")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8100").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET", "")
OWNER_ID = "smart-city-knowledge"
# Changes when the embedding model/collection changes; then everything is re-embedded once.
INDEX_VERSION = os.getenv("INDEX_VERSION", "")
VERSION_FILE = STATE / "index_version"

TOP_FOLDERS = ["files", "speech", "interviews", "_guides"]
TEXT_EXT = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}
EDITABLE_EXT = {".md", ".markdown", ".txt"}
GUIDE_FOLDER = "_guides"

STOPWORDS = set(
    "der die das und oder ein eine einer eines einem einen ist sind war wer wie was wo wann "
    "warum mit von für fur auf aus bei den dem des im in zu zum zur nicht auch noch nur über "
    "uber dass es sie er wir ich du ihr man sich hat haben wird werden kann können konnen "
    "the and for with what who how".split()
)


# --------------------------------------------------------------------------- helpers


def safe_path(relpath: str) -> Path:
    relpath = (relpath or "").strip().replace("\\", "/").lstrip("/")
    p = (ROOT / relpath).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise ValueError("Path is outside the knowledge base")
    if any(part.startswith(".") for part in p.relative_to(ROOT).parts):
        raise ValueError("Hidden paths are not allowed")
    return p


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def file_id_for(relpath: str) -> str:
    return "kn_" + hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:24]


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or "untitled"


def safe_filename(name: str) -> str:
    name = Path(name or "file").name
    stem, ext = os.path.splitext(name)
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext.lower())[:10]
    return f"{slugify(stem)}{ext}"


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, ext = p.stem, p.suffix
    i = 2
    while True:
        cand = p.with_name(f"{stem}-{i}{ext}")
        if not cand.exists():
            return cand
        i += 1


def view_url(relpath: str, highlight: str | None = None) -> str:
    url = f"{PUBLIC_URL}/view?path={quote(relpath)}"
    if highlight:
        # Kurz halten (Anfang der Fundstelle ohne Markdown/Frontmatter), damit Sprachmodelle den
        # Link unverändert übernehmen; der Viewer markiert ab diesem Anfang.
        _, body = split_frontmatter(highlight)
        hl = re.sub(r"[*_#>`|]+", " ", body or highlight)
        hl = re.sub(r"\s+", " ", hl).strip()
        if len(hl) > 70:
            hl = hl[:70].rsplit(" ", 1)[0]
        url += "&hl=" + quote(hl)
    return url


def today() -> str:
    return dt.date.today().isoformat()


def frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(json.dumps(x, ensure_ascii=False) for x in v)}]")
        else:
            lines.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
        elif line.strip().startswith("- ") and meta:
            last = list(meta)[-1]
            meta[last] = (meta[last] + " " + line.strip()[2:].strip('"')).strip()
    body = text[end + 4 :].lstrip("\n")
    return meta, body


# --------------------------------------------------------------------------- index


class KnowledgeStore:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}
        self.wake = asyncio.Event()
        self.rag_ok: bool | None = None
        self.rag_error: str = ""
        self._task: asyncio.Task | None = None

    # ---- lifecycle
    def ensure_layout(self) -> None:
        for folder in TOP_FOLDERS:
            (ROOT / folder).mkdir(parents=True, exist_ok=True)
        TEXT_CACHE.mkdir(parents=True, exist_ok=True)
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        if INDEX_FILE.exists():
            try:
                self.entries = json.loads(INDEX_FILE.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                self.entries = {}
        # New embedding model / collection: the old vectors are gone, so re-embed everything.
        stored = VERSION_FILE.read_text("utf-8").strip() if VERSION_FILE.exists() else ""
        if stored != INDEX_VERSION:
            for e in self.entries.values():
                e["status"] = "pending"
                e["was_indexed"] = False
            VERSION_FILE.write_text(INDEX_VERSION, "utf-8")
            log.info("Index version changed (%r -> %r): re-embedding all files", stored, INDEX_VERSION)
        # On startup, retry everything that is not embedded yet (e.g. after adding an embedding key).
        self.retry_unembedded()

    def retry_unembedded(self) -> int:
        n = 0
        for e in self.entries.values():
            if e.get("status") in ("text_only", "error"):
                e["status"] = "pending"
                n += 1
        self.wake.set()
        return n

    def start(self) -> None:
        self.ensure_layout()
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def _save(self) -> None:
        tmp = INDEX_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(INDEX_FILE)

    # ---- rag_api
    def _headers(self) -> dict:
        if not JWT_SECRET:
            return {}
        token = jwt.encode(
            {"id": OWNER_ID, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm="HS256"
        )
        return {"Authorization": f"Bearer {token}"}

    async def _rag(self, method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(timeout=180) as client:
            return await client.request(method, f"{RAG_API_URL}{path}", headers=self._headers(), **kw)

    # ---- worker
    async def _worker(self) -> None:
        while True:
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Sync failed")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    def _scan(self) -> dict[str, os.stat_result]:
        found: dict[str, os.stat_result] = {}
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                found[rel(p)] = p.stat()
        return found

    async def sync(self) -> None:
        found = self._scan()
        changed = False
        # entfernte Dateien
        for relpath in list(self.entries):
            if relpath not in found:
                entry = self.entries.pop(relpath)
                changed = True
                (TEXT_CACHE / f"{entry['id']}.txt").unlink(missing_ok=True)
                if entry.get("status") == "indexed":
                    try:
                        await self._rag("DELETE", "/documents", json=[entry["id"]])
                    except Exception:  # noqa: BLE001
                        pass
        # neue / geänderte Dateien
        todo = []
        for relpath, st in found.items():
            entry = self.entries.get(relpath)
            if entry and entry.get("mtime") == st.st_mtime and entry.get("size") == st.st_size:
                if entry.get("status") != "pending":
                    continue
            self.entries[relpath] = {
                "id": file_id_for(relpath),
                "mtime": st.st_mtime,
                "size": st.st_size,
                "status": "pending",
                "error": "",
                "was_indexed": bool(entry and entry.get("status") == "indexed"),
            }
            todo.append(relpath)
            changed = True
        if changed:
            self._save()
        for relpath in todo:
            await self._process(relpath)
            self._save()

    async def _process(self, relpath: str) -> None:
        entry = self.entries.get(relpath)
        if not entry:
            return
        p = ROOT / relpath
        data = p.read_bytes()
        # 1) Text extrahieren (für Vorschau, Fundstellen und Stichwortsuche)
        text = ""
        try:
            if p.suffix.lower() in TEXT_EXT:
                text = data.decode("utf-8", errors="replace")
            else:
                r = await self._rag(
                    "POST", "/text", data={"file_id": entry["id"]}, files={"file": (p.name, data)}
                )
                if r.status_code < 300:
                    body = r.json()
                    text = body.get("text", "") if isinstance(body, dict) else str(body)
                else:
                    entry["error"] = f"Text extraction: HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"Text extraction: {e}"
        (TEXT_CACHE / f"{entry['id']}.txt").write_text(text, "utf-8")

        # 2) In pgvector einbetten
        try:
            if entry.pop("was_indexed", False):
                await self._rag("DELETE", "/documents", json=[entry["id"]])
            r = await self._rag(
                "POST", "/embed", data={"file_id": entry["id"]}, files={"file": (p.name, data)}
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code < 300 and (not isinstance(body, dict) or body.get("status", True)):
                entry["status"] = "indexed"
                entry["error"] = ""
                self.rag_ok, self.rag_error = True, ""
            else:
                detail = body.get("detail") if isinstance(body, dict) else ""
                raise RuntimeError(f"HTTP {r.status_code} {detail or r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            entry["status"] = "text_only" if text else "error"
            entry["error"] = f"Embedding failed: {str(e)[:300]}"
            self.rag_ok, self.rag_error = False, entry["error"]
        entry["processed_at"] = dt.datetime.now().isoformat(timespec="seconds")
        log.info("%s -> %s %s", relpath, entry["status"], entry.get("error", ""))

    # ---- lesen
    def get_text(self, relpath: str) -> str:
        p = safe_path(relpath)
        if p.suffix.lower() in TEXT_EXT:
            return p.read_text("utf-8", errors="replace")
        entry = self.entries.get(relpath)
        if entry:
            cache = TEXT_CACHE / f"{entry['id']}.txt"
            if cache.exists():
                return cache.read_text("utf-8")
        return ""

    def tree(self, relpath: str = "") -> dict:
        base = safe_path(relpath)

        def node(p: Path) -> dict:
            r = rel(p) if p != ROOT else ""
            if p.is_dir():
                children = [
                    node(c)
                    for c in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
                    if not c.name.startswith(".")
                ]
                return {"name": p.name if r else "knowledge", "path": r, "type": "dir", "children": children}
            entry = self.entries.get(r, {})
            st = p.stat()
            return {
                "name": p.name,
                "path": r,
                "type": "file",
                "size": st.st_size,
                "mtime": st.st_mtime,
                "status": entry.get("status", "pending"),
                "error": entry.get("error", ""),
            }

        return node(base)

    def status(self) -> dict:
        counts: dict[str, int] = {}
        for e in self.entries.values():
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        return {"files": len(self.entries), "counts": counts, "rag_ok": self.rag_ok, "rag_error": self.rag_error}

    def people(self) -> list[dict]:
        base = ROOT / "interviews"
        out = []
        for d in sorted(base.iterdir()) if base.exists() else []:
            if d.is_dir() and not d.name.startswith("."):
                files = sorted(f for f in d.iterdir() if f.is_file())
                name = d.name.replace("-", " ").title()
                for f in files:
                    meta, _ = split_frontmatter(self.get_text(rel(f)))
                    if meta.get("person"):
                        name = meta["person"]
                        break
                out.append({"slug": d.name, "name": name, "sessions": len(files), "path": rel(d)})
        return out

    def guides(self) -> list[dict]:
        base = ROOT / GUIDE_FOLDER
        out = []
        for f in sorted(base.glob("*.md")) if base.exists() else []:
            meta, body = split_frontmatter(f.read_text("utf-8", errors="replace"))
            title = meta.get("title") or meta.get("titel") or f.stem
            out.append({"path": rel(f), "title": title, "beschreibung": meta.get("description") or meta.get("beschreibung", "")})
        return out

    # ---- Dokumentliste mit Metadaten (für die Memory-Übersicht)
    def documents(self) -> list[dict]:
        updates, ids = self.update_map()
        updated_ids = {}
        for target, lst in updates.items():
            for u in lst:
                updated_ids.setdefault(u["path"], []).append(target)
        out = []
        for path, entry in sorted(self.entries.items()):
            p = ROOT / path
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            meta: dict = {}
            first_heading = ""
            if ext in (".md", ".markdown"):
                try:
                    meta, body = split_frontmatter(self.get_text(path))
                    m = re.search(r"^#\s+(.+)$", body, re.M)
                    first_heading = m.group(1).strip() if m else ""
                except Exception:  # noqa: BLE001
                    meta = {}
            top = path.split("/", 1)[0]
            typ_raw = (meta.get("type") or meta.get("typ") or "").lower()
            if top == GUIDE_FOLDER:
                typ = "Guide"
            elif "interview" in typ_raw or "gespraech" in typ_raw or top == "interviews":
                typ = "Interview"
            elif "sprach" in typ_raw or "voice" in typ_raw or top == "speech":
                typ = "Voice note"
            elif meta.get("gesetz") or meta.get("paragraph"):
                typ = "Law"
            elif "notiz" in typ_raw or typ_raw == "note":
                typ = "Note"
            elif ext == ".pdf":
                typ = "PDF"
            else:
                typ = "Document"
            person = meta.get("person") or meta.get("autor") or ""
            rolle = ""
            if "(" in person:
                person, rolle = person.split("(", 1)
                person, rolle = person.strip(), rolle.rstrip(") ").strip()
            if not person and meta.get("teilnehmende"):
                person = meta["teilnehmende"].split("(")[0].strip().strip('"')
            if not person and top == "interviews" and "/" in path[len("interviews/"):]:
                person = path.split("/")[1].replace("-", " ").title()
            datum = next(
                (
                    v[:10]
                    for v in (meta.get(k, "") for k in ("date", "datum", "stand", "abgerufen"))
                    if re.match(r"^\d{4}-\d{2}-\d{2}", v or "")
                ),
                "",
            )
            date_source = "meta" if datum else "file"
            if not datum:
                datum = dt.date.fromtimestamp(p.stat().st_mtime).isoformat()
            tags_raw = meta.get("tags", "")
            tags = [t.strip().strip('"') for t in tags_raw.strip("[]").split(",") if t.strip()]
            title = (
                meta.get("title") or meta.get("titel") or meta.get("gesetz") or first_heading
                or p.stem.replace("_", " ")
            )
            doc_id = ids.get(path, "")
            out.append(
                {
                    "path": path,
                    "folder": path.rsplit("/", 1)[0] if "/" in path else "",
                    "name": p.name,
                    "ext": ext.lstrip("."),
                    "size": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                    "status": entry.get("status", "pending"),
                    "id": doc_id,
                    "title": title,
                    "typ": typ,
                    "person": person,
                    "rolle": rolle,
                    "datum": datum,
                    "date_source": date_source,
                    "ort": meta.get("ort") or meta.get("places", "").strip("[]").replace('"', ""),
                    "tags": tags,
                    "thema": meta.get("topic") or meta.get("thema", ""),
                    "updated_by": [u["path"] for u in updates.get(doc_id, []) if u["path"] != path] if doc_id else [],
                    "updates": sorted(updated_ids.get(path, [])),
                }
            )
        return out

    # ---- Aktualisierungen erkennen
    def update_map(self) -> tuple[dict[str, list[dict]], dict[str, str]]:
        """Findet Dokumente, die laut Kopfdaten andere Quellen aktualisieren.

        Beispiel T02: hinweis "Aktualisiert Angaben aus T01 und den Notizen N01, N06 und N12".
        Liefert {ziel_id: [{path, id, datum}]} und {path: id}.
        """
        updates: dict[str, list[dict]] = {}
        ids: dict[str, str] = {}
        for path in self.entries:
            if Path(path).suffix.lower() not in (".md", ".markdown"):
                continue
            try:
                meta, _ = split_frontmatter(self.get_text(path))
            except Exception:  # noqa: BLE001
                continue
            doc_id = meta.get("id", "").strip()
            if doc_id:
                ids[path] = doc_id
            explicit = " ".join(meta.get(k, "") for k in ("updates", "supersedes"))
            note = " ".join(meta.get(k, "") for k in ("hinweis", "aktualisiert", "ersetzt", "note"))
            if re.search(r"aktualisier|ersetzt|gilt dieser stand|update|supersede", note.lower()):
                explicit += " " + note
            if not explicit.strip():
                continue
            note = explicit
            for target in set(re.findall(r"\b([A-Z]{1,2}\d{2})\b", note)) - {doc_id}:
                updates.setdefault(target, []).append(
                    {"path": path, "id": doc_id, "datum": meta.get("date") or meta.get("datum", "")}
                )
        return updates, ids

    # ---- suchen
    async def search(self, query: str, folder: str = "", k: int = 6) -> dict:
        folder = (folder or "").strip().strip("/")
        include_guides = folder.startswith(GUIDE_FOLDER)

        def allowed(relpath: str) -> bool:
            if folder and not (relpath == folder or relpath.startswith(folder + "/")):
                return False
            if not include_guides and relpath.startswith(GUIDE_FOLDER + "/"):
                return False
            return True

        candidates = {p: e for p, e in self.entries.items() if allowed(p)}
        by_id = {e["id"]: p for p, e in candidates.items()}
        hits: list[dict] = []
        mode = "semantic"
        ids = [e["id"] for e in candidates.values() if e.get("status") == "indexed"]
        if ids:
            try:
                r = await self._rag(
                    "POST", "/query_multiple", json={"query": query, "file_ids": ids, "k": k * 2}
                )
                if r.status_code < 300:
                    for item in r.json():
                        doc, score = (item[0], item[1]) if isinstance(item, list) else (item, None)
                        meta = doc.get("metadata", {}) or {}
                        path = by_id.get(meta.get("file_id"))
                        if not path:
                            continue
                        hits.append(
                            {
                                "path": path,
                                "snippet": doc.get("page_content", "").strip(),
                                "score": round(1 - float(score), 3) if score is not None else None,
                                "page": meta.get("page"),
                            }
                        )
            except Exception as e:  # noqa: BLE001
                log.warning("Semantic search failed: %s", e)
        keyword_hits = self._keyword_search(query, list(candidates), k * 2)
        if hits:
            # Hybrid: semantische Treffer und Stichwort-Treffer per Reciprocal Rank Fusion mischen.
            # So finden auch exakte Begriffe (Namen, Straßen, Paragraphen) zuverlässig ihre Quelle.
            fused: dict[tuple, dict] = {}
            for ranking in (hits, keyword_hits):
                for rank, h in enumerate(ranking):
                    key = (h["path"], re.sub(r"\W+", "", h["snippet"])[:80])
                    entry = fused.setdefault(key, {**h, "rrf": 0.0})
                    entry["rrf"] += 1.0 / (60 + rank)
            ordered = sorted(fused.values(), key=lambda h: -h["rrf"])
            hits, per_file = [], {}
            for h in ordered:
                if per_file.get(h["path"], 0) >= 2:
                    continue
                per_file[h["path"]] = per_file.get(h["path"], 0) + 1
                h["score"] = round(h.pop("rrf"), 4)
                hits.append(h)
                if len(hits) >= k:
                    break
        else:
            mode = "keyword"
            hits = keyword_hits[:k]
        updates, ids = self.update_map()
        for h in hits:
            if ids.get(h["path"]):
                h["doc_id"] = ids[h["path"]]
            newer = [u for u in updates.get(ids.get(h["path"], ""), []) if u["path"] != h["path"]]
            if newer:
                h["updated_by"] = newer
        for h in hits:
            h["mode"] = mode
            h["title"] = Path(h["path"]).name
            h["url"] = view_url(h["path"], h["snippet"])
            if len(h["snippet"]) > 900:
                h["snippet"] = h["snippet"][:900].rsplit(" ", 1)[0] + " …"
        return {"query": query, "mode": mode, "results": hits}

    def _keyword_search(self, query: str, paths: list[str], k: int) -> list[dict]:
        q = query.lower()
        terms = [t for t in re.findall(r"[\wäöüß]+", q) if len(t) > 2 and t not in STOPWORDS]
        if not terms:
            return []
        scored = []
        for path in paths:
            try:
                text = self.get_text(path)
            except Exception:  # noqa: BLE001
                continue
            meta, body = split_frontmatter(text)
            # Treffer im Dateinamen/Titel/Tags zählen extra (kurze Notizen sind sonst benachteiligt)
            head = (path + " " + " ".join(meta.values())).lower()
            head_bonus = sum(3 for t in terms if t in head)
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
            # Überschrift + ersten Absatz zusammenfassen, damit Notizen als Ganzes treffen
            if len(paragraphs) >= 2 and paragraphs[0].startswith("#"):
                paragraphs = [paragraphs[0] + "\n\n" + paragraphs[1]] + paragraphs[2:]
            for para in paragraphs:
                low = para.lower()
                score = sum(min(low.count(t), 3) for t in terms)
                distinct = sum(1 for t in terms if t in low)
                if not distinct:
                    continue
                score += distinct * 2 + (5 if q in low else 0) + head_bonus
                scored.append((score, path, para))
        scored.sort(key=lambda x: -x[0])
        out, per_file = [], {}
        for score, path, para in scored:
            if per_file.get(path, 0) >= 2:
                continue
            per_file[path] = per_file.get(path, 0) + 1
            out.append({"path": path, "snippet": para, "score": score, "page": None})
            if len(out) >= k:
                break
        return out

    # ---- schreiben
    def write_text(self, relpath: str, content: str, overwrite: bool = False) -> str:
        p = safe_path(relpath)
        if p.suffix.lower() not in EDITABLE_EXT:
            p = p.with_suffix(".md")
        if p.parent == ROOT:
            raise ValueError("Please specify a folder, e.g. files/…")
        p.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite:
            p = unique_path(p)
        p.write_text(content, "utf-8")
        self.wake.set()
        return rel(p)

    def save_upload(self, folder: str, filename: str, data: bytes) -> str:
        d = safe_path(folder or "files")
        if d == ROOT:
            d = ROOT / "files"
        d.mkdir(parents=True, exist_ok=True)
        p = unique_path(d / safe_filename(filename))
        p.write_bytes(data)
        self.wake.set()
        return rel(p)

    def make_dir(self, relpath: str) -> str:
        p = safe_path(relpath)
        p.mkdir(parents=True, exist_ok=True)
        return rel(p)

    def delete(self, relpath: str) -> None:
        p = safe_path(relpath)
        if p == ROOT or rel(p) in TOP_FOLDERS:
            raise ValueError("Top-level folders cannot be deleted")
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        self.wake.set()

    def save_note(self, title: str, text: str, person: str = "", source: str = "speech") -> str:
        meta = {
            "title": title or "Voice note",
            "type": "voice note",
            "date": today(),
            "person": person,
            "source": source,
        }
        body = frontmatter(meta) + f"# {meta['title']}\n\n{text.strip()}\n"
        return self.write_text(f"speech/{today()}_{slugify(title or 'note')}.md", body)

    def save_voice_note(
        self,
        transcript: str,
        structured: dict | None,
        person: str = "",
        title: str = "",
        audio: str = "",
        transcriber: str = "",
    ) -> str:
        """Saves a voice note: structured knowledge on top, verbatim transcript below."""
        st = structured or {}
        title = (st.get("title") or title or "Voice note").strip()
        people = [p for p in st.get("people", []) if isinstance(p, str)]
        meta = {
            "title": title,
            "type": "voice note",
            "date": today(),
            "language": st.get("language") or "",
            "person": person,
            "people": people,
            "places": [p for p in st.get("places", []) if isinstance(p, str)],
            "tags": [t for t in st.get("tags", []) if isinstance(t, str)],
            "refers_to_date": st.get("date") or "",
            "updates": [u for u in st.get("updates", []) if isinstance(u, str)],
            "audio": audio,
            "transcribed_with": transcriber,
            "structured": "yes" if st else "no",
        }
        labels = st.get("labels") if isinstance(st.get("labels"), dict) else {}
        lines = [f"# {title}", ""]
        if st.get("summary"):
            lines += [f"## {labels.get('summary') or 'Summary'}", *[f"- {b}" for b in st["summary"] if isinstance(b, str)], ""]
        for sec in st.get("sections", []) or []:
            if not isinstance(sec, dict) or not sec.get("points"):
                continue
            lines += [f"## {sec.get('heading') or 'Details'}"]
            lines += [f"- {pt}" for pt in sec["points"] if isinstance(pt, str)]
            lines += [""]
        quoted = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in transcript.strip().splitlines())
        lines += [f"## {labels.get('transcript') or 'Original transcript'}", quoted, ""]
        body = frontmatter(meta) + "\n".join(lines)
        return self.write_text(f"speech/{today()}_{slugify(title)}.md", body)

    def save_audio(self, filename: str, data: bytes) -> str:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        name = unique_path(AUDIO_DIR / f"{today()}_{safe_filename(filename)}").name
        (AUDIO_DIR / name).write_bytes(data)
        return name

    def save_interview(
        self,
        person: str,
        topic: str,
        content: str,
        interviewer: str = "",
        guide: str = "",
        places: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        rules = len(re.findall(r"\*\*(?:RULE|REGEL)[:\*]", content, re.I))
        meta = {
            "title": f"Interview {person} – {topic}",
            "type": "interview",
            "person": person,
            "topic": topic,
            "date": today(),
            "interviewer": interviewer,
            "guide": guide,
            "places": [p for p in (places or []) if isinstance(p, str) and p.strip()],
            "tags": [t for t in (tags or []) if isinstance(t, str) and t.strip()],
            "rules": str(rules) if rules else "",
        }
        body = content.strip()
        if body.startswith("---"):
            _, body = split_frontmatter(body)
        if not body.lstrip().startswith("#"):
            body = f"# {meta['title']}\n\n{body}"
        relpath = f"interviews/{slugify(person)}/{today()}_{slugify(topic)}.md"
        return self.write_text(relpath, frontmatter(meta) + body + "\n")


store = KnowledgeStore()
