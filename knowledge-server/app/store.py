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
from typing import Literal
from urllib.parse import quote

import httpx
import jwt

from . import ai
from .pdf import EXTRACTION_VERSION, extract_pdf, pdf_page_count

log = logging.getLogger("knowledge")

ROOT = Path(os.getenv("KNOWLEDGE_ROOT", "/data/knowledge")).resolve()
STATE = ROOT / ".state"
TEXT_CACHE = STATE / "text"
AUDIO_DIR = STATE / "audio"
INDEX_FILE = STATE / "index.json"
STATUS_FILE = STATE / "document-status.json"
RAG_API_URL = os.getenv("RAG_API_URL", "http://rag_api:8000").rstrip("/")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8100").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET", "")
OWNER_ID = "smart-city-knowledge"
# Changes when the embedding model/collection changes; then everything is re-embedded once.
INDEX_VERSION = os.getenv("INDEX_VERSION", "")
VERSION_FILE = STATE / "index_version"
# pgvector cosine distance. Lexical matches still qualify independently.
SEARCH_MAX_DISTANCE = float(os.getenv("KNOWLEDGE_SEARCH_MAX_DISTANCE", "0.60"))
SEARCH_TEXT_VERSION = 1

TOP_FOLDERS = ["files", "speech", "interviews", "_guides"]
TEXT_EXT = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}
EDITABLE_EXT = {".md", ".markdown", ".txt"}
UPLOAD_EXT = EDITABLE_EXT | {".pdf"}
DocumentStatus = Literal["active", "inactive", "draft"]
DOCUMENT_STATUSES = {"active", "inactive", "draft"}
GUIDE_FOLDER = "_guides"
# Interview sessions build on each other: 1 workday walkthrough, 2 incidents, 3 plan vs. reality, 4+ deep dive
SESSION_GUIDES = {
    1: "_guides/01_workday-walkthrough.md",
    2: "_guides/02_exceptions-and-incidents.md",
    3: "_guides/03_plan-vs-reality.md",
    4: "_guides/04_deep-dive.md",
}

STOPWORDS = set(
    "der die das und oder ein eine einer eines einem einen ist sind war wer wie was wo wann "
    "warum mit von für fur auf aus bei den dem des im in zu zum zur nicht auch noch nur über "
    "uber dass es sie er wir ich du ihr man sich hat haben wird werden kann können konnen "
    "the and for with what who how".split()
)
WEB_EXPORT_FOOTER = re.compile(
    r"^[ \t]*-->[ \t]*\n[ \t]*Feedback[ \t]*\n[ \t]*-->[ \t]*\n"
    r"[ \t]*Verwendung von Cookies\..*?(?=^[ \t]*---[ \t]*$|^#{1,6}[ \t]|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
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


def section_items(body: str, headings: tuple[str, ...], limit: int = 8) -> list[str]:
    """Bullet points of the first Markdown section whose heading starts with one of `headings`."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().lower().startswith(headings):
            items = []
            for item in lines[i + 1:]:
                if item.startswith("## "):
                    break
                text = item.strip()
                if text.startswith(("- ", "* ")):
                    items.append(text[2:].strip())
            return items[:limit]
    return []


def search_body(body: str) -> str:
    # This export appends site controls between the law text and the next extract.
    return WEB_EXPORT_FOOTER.sub("\n", body)


def validate_upload(filename: str, data: bytes) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in UPLOAD_EXT:
        raise ValueError("Only PDF, Markdown and TXT files are supported")
    if ext == ".pdf":
        pdf_page_count(data)
    elif b"\0" in data:
        raise ValueError("Markdown and TXT uploads must contain plain text")


# --------------------------------------------------------------------------- index


class KnowledgeStore:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}
        self.document_statuses: dict[str, DocumentStatus] = {}
        self._pdf_processing: dict[str, dict] = {}
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
        if STATUS_FILE.exists():
            self.document_statuses = json.loads(STATUS_FILE.read_text("utf-8"))
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
        for path, e in self.entries.items():
            if self.is_active(path) and e.get("status") in ("text_only", "error"):
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

    def document_status(self, path: str) -> DocumentStatus:
        return self.document_statuses.get(path, "active")

    def is_active(self, path: str) -> bool:
        return self.document_status(path) == "active" and (ROOT / path).is_file()

    def _save_statuses(self, statuses: dict[str, DocumentStatus]) -> None:
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(statuses, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(STATUS_FILE)
        self.document_statuses = statuses

    def _set_status(self, path: str, status: DocumentStatus) -> None:
        if status not in DOCUMENT_STATUSES:
            raise ValueError("Choose Active, Not Active or Draft")
        if self.document_status(path) == status:
            return
        statuses = dict(self.document_statuses)
        if status == "active":
            statuses.pop(path, None)
        else:
            statuses[path] = status
        self._save_statuses(statuses)

    def set_status(self, path: str, status: DocumentStatus) -> None:
        p = safe_path(path)
        if not p.is_file():
            raise FileNotFoundError(path)
        self._set_status(rel(p), status)
        entry = self.entries.get(rel(p))
        if status == "active" and entry and entry.get("status") in ("error", "text_only"):
            entry["status"] = "pending"
        self.wake.set()

    def index_status(self, path: str, entry: dict) -> str:
        if self.document_status(path) != "active":
            return "excluded"
        if Path(path).suffix.lower() == ".pdf" and entry.get("pdf_version") != EXTRACTION_VERSION:
            return "error" if entry.get("status") == "error" else "pending"
        if (
            entry.get("status") == "indexed"
            and Path(path).suffix.lower() in EDITABLE_EXT
            and entry.get("search_text_version") != SEARCH_TEXT_VERSION
        ):
            return "pending"
        return entry.get("status", "pending")

    def pdf_progress(self, path: str) -> dict:
        status = self.index_status(path, self.entries.get(path, {}))
        if status != "pending":
            return {"status": status}
        return {"status": status, **self._pdf_processing.get(path, {"phase": "queued"})}

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
                if entry.get("status") == "indexed" or entry.get("was_indexed"):
                    try:
                        await self._rag("DELETE", "/documents", json=[entry["id"]])
                    except Exception:  # noqa: BLE001
                        pass
        # neue / geänderte Dateien
        todo = []
        for relpath, st in found.items():
            entry = self.entries.get(relpath)
            if not entry or entry.get("mtime") != st.st_mtime or entry.get("size") != st.st_size:
                entry = {
                    "id": file_id_for(relpath),
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "status": "pending",
                    "error": "",
                    "was_indexed": bool(entry and (entry.get("status") == "indexed" or entry.get("was_indexed"))),
                }
                self.entries[relpath] = entry
                changed = True
            if self.is_active(relpath) and self.index_status(relpath, entry) == "pending":
                todo.append(relpath)
        if changed:
            self._save()
        for relpath in todo:
            try:
                await self._process(relpath)
            finally:
                self._pdf_processing.pop(relpath, None)
            self._save()

    async def _process(self, relpath: str) -> None:
        entry = self.entries.get(relpath)
        if not entry or not self.is_active(relpath):
            return
        p = ROOT / relpath
        data = p.read_bytes()
        filename = p.name
        ext = p.suffix.lower()
        pdf = ext == ".pdf"
        cache = TEXT_CACHE / f"{entry['id']}.txt"
        entry["was_indexed"] = entry.get("status") == "indexed" or entry.get("was_indexed", False)
        entry["status"] = "pending"
        entry["error"] = ""
        # 1) Text extrahieren (für Vorschau, Fundstellen und Stichwortsuche)
        text = ""
        try:
            if pdf:
                if entry.get("pdf_version") == EXTRACTION_VERSION and cache.exists():
                    text = cache.read_text("utf-8")
                else:
                    self._pdf_processing[relpath] = {"phase": "rendering"}

                    def on_progress(completed: int, total: int) -> None:
                        self._pdf_processing[relpath] = {
                            "phase": "extracting",
                            "completed_pages": completed,
                            "total_pages": total,
                        }

                    text = await extract_pdf(data, on_progress=on_progress)
                data = text.encode("utf-8")
                filename = f"{p.stem}.txt"
            elif ext in TEXT_EXT:
                text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
                if ext in EDITABLE_EXT:
                    meta, body = split_frontmatter(text)
                    cleaned = search_body(body if meta else text)
                    if cleaned != text:
                        data = cleaned.encode("utf-8")
                        filename = f"{p.stem}.txt"
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
        if pdf and not text:
            entry["status"] = "error"
            log.warning("%s -> %s", relpath, entry["error"])
            return
        cache.write_text(text, "utf-8")
        if pdf:
            entry["pdf_version"] = EXTRACTION_VERSION
            self._pdf_processing[relpath] = {"phase": "indexing"}
        if not self.is_active(relpath):
            return

        # 2) In pgvector einbetten
        try:
            if entry.get("was_indexed"):
                deleted = await self._rag("DELETE", "/documents", json=[entry["id"]])
                if deleted.status_code != 404:
                    deleted.raise_for_status()
            if not self.is_active(relpath):
                return
            if ext in EDITABLE_EXT and not data.strip():
                entry["status"] = "text_only"
                entry.pop("was_indexed", None)
                return
            # The write may succeed even if its response is lost. Clear it before a retry.
            entry["was_indexed"] = True
            self._save()
            r = await self._rag(
                "POST", "/embed", data={"file_id": entry["id"]}, files={"file": (filename, data)}
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code < 300 and (not isinstance(body, dict) or body.get("status", True)):
                entry["status"] = "indexed"
                if ext in EDITABLE_EXT:
                    entry["search_text_version"] = SEARCH_TEXT_VERSION
                entry.pop("was_indexed", None)
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
            return p.read_text("utf-8-sig", errors="replace")
        entry = self.entries.get(rel(p))
        if entry:
            if p.suffix.lower() == ".pdf" and entry.get("pdf_version") != EXTRACTION_VERSION:
                return ""
            cache = TEXT_CACHE / f"{entry['id']}.txt"
            if cache.exists():
                return cache.read_text("utf-8")
        return ""

    def tree(self, relpath: str = "", active_only: bool = False) -> dict:
        base = safe_path(relpath)
        if active_only and base.is_file() and not self.is_active(rel(base)):
            raise FileNotFoundError(relpath)

        def node(p: Path) -> dict:
            r = rel(p) if p != ROOT else ""
            if p.is_dir():
                children = [
                    node(c)
                    for c in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
                    if not c.name.startswith(".") and (not active_only or c.is_dir() or self.is_active(rel(c)))
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
                "status": self.index_status(r, entry),
                "document_status": self.document_status(r),
                "error": entry.get("error", ""),
            }

        return node(base)

    def status(self) -> dict:
        counts: dict[str, int] = {}
        for path, entry in self.entries.copy().items():
            status = self.index_status(path, entry)
            counts[status] = counts.get(status, 0) + 1
        return {"files": len(self.entries), "counts": counts, "rag_ok": self.rag_ok, "rag_error": self.rag_error}

    def interview_person(self, path: str) -> str:
        """Person of an interview file, read from its metadata (folders are topics, not people)."""
        try:
            meta, _ = split_frontmatter(self.get_text(path))
        except Exception:  # noqa: BLE001
            return ""
        person = meta.get("person") or ""
        if not person and meta.get("teilnehmende"):
            person = meta["teilnehmende"].strip("[]").split("(")[0].strip().strip('"')
        return person.strip()

    def interview_sessions(self, person: str) -> list[dict]:
        """Recorded interview sessions of a person, oldest first, with their session number."""
        slug = slugify(person)
        out = []
        for path in sorted(self.entries):
            # Drafts and inactive protocols still count as completed sessions
            if not path.startswith("interviews/") or not (ROOT / path).is_file():
                continue
            if slugify(self.interview_person(path)) != slug:
                continue
            meta, body = split_frontmatter(self.get_text(path))
            try:
                number = int(meta.get("session") or 0)
            except ValueError:
                number = 0
            out.append(
                {
                    "path": path,
                    "session": number,
                    "date": meta.get("date") or meta.get("datum", ""),
                    "topic": meta.get("topic") or meta.get("thema") or meta.get("title") or meta.get("titel") or "",
                    "open_points": section_items(body, ("offene punkte", "open points")),
                    "deviations": section_items(body, ("abweichungen", "deviations")),
                }
            )
        return sorted(out, key=lambda s: (s["date"], s["path"]))

    def next_session(self, person: str) -> tuple[int, str]:
        """Which interview session comes next for a person, and its guide (sessions 1–3, then 4+)."""
        done = max((s["session"] for s in self.interview_sessions(person)), default=0)
        number = done + 1
        return number, SESSION_GUIDES[min(number, len(SESSION_GUIDES))]

    def people(self) -> list[dict]:
        sessions: dict[str, dict] = {}
        for path in sorted(self.entries):
            if not path.startswith("interviews/") or Path(path).suffix.lower() not in (".md", ".markdown"):
                continue
            name = self.interview_person(path)
            if not name:
                continue
            slug = slugify(name)
            entry = sessions.setdefault(slug, {"slug": slug, "name": name, "sessions": 0, "paths": []})
            entry["sessions"] += 1
            entry["paths"].append(path)
        return list(sessions.values())

    def guides(self) -> list[dict]:
        base = ROOT / GUIDE_FOLDER
        out = []
        for f in sorted(base.glob("*.md")) if base.exists() else []:
            if not self.is_active(rel(f)):
                continue
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
                    "status": self.index_status(path, entry),
                    "document_status": self.document_status(path),
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
        for path in list(self.entries):
            if Path(path).suffix.lower() not in (".md", ".markdown"):
                continue
            try:
                meta, _ = split_frontmatter(self.get_text(path))
            except Exception:  # noqa: BLE001
                continue
            doc_id = meta.get("id", "").strip()
            if doc_id:
                ids[path] = doc_id
            if not self.is_active(path):
                continue
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
    async def search(
        self,
        query: str,
        folder: str = "",
        k: int = 6,
        *,
        typ: str = "",
        person: str = "",
        rerank_limit: int | None = None,
    ) -> dict:
        folder = (folder or "").strip().strip("/")
        include_guides = folder == GUIDE_FOLDER or folder.startswith(GUIDE_FOLDER + "/") or typ == "Guide"
        filtered_paths = None
        if typ or person:
            filtered_paths = {
                doc["path"]
                for doc in self.documents()
                if (not typ or doc["typ"] == typ) and (not person or doc["person"] == person)
            }

        def allowed(relpath: str) -> bool:
            if filtered_paths is not None and relpath not in filtered_paths:
                return False
            if folder and not (relpath == folder or relpath.startswith(folder + "/")):
                return False
            if not include_guides and relpath.startswith(GUIDE_FOLDER + "/"):
                return False
            return self.is_active(relpath)

        candidates = {p: e for p, e in self.entries.items() if allowed(p)}
        by_id = {e["id"]: p for p, e in candidates.items() if self.index_status(p, e) == "indexed"}
        hits: list[dict] = []
        mode = "semantic"
        ids = list(by_id)
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
                        if not path or not allowed(path) or score is None or float(score) > SEARCH_MAX_DISTANCE:
                            continue
                        snippet = doc.get("page_content", "").strip()
                        if not snippet:
                            continue
                        hits.append(
                            {
                                "path": path,
                                "snippet": snippet,
                                "page": meta.get("page"),
                            }
                        )
            except Exception as e:  # noqa: BLE001
                log.warning("Semantic search failed: %s", e)
        keyword_hits = self._keyword_search(query, [p for p in candidates if allowed(p)], k * 2)
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
        if rerank_limit is not None:
            indices = await ai.rerank(query, [hit["snippet"] for hit in hits], rerank_limit)
            hits = [hits[index] for index in indices if allowed(hits[index]["path"])]
        updates, ids = self.update_map() if hits else ({}, {})
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
        terms = {t for t in re.findall(r"[\wäöüß]+", q) if len(t) > 2 and t not in STOPWORDS}
        if not terms:
            return []
        min_matches = min(2, len(terms))
        scored = []
        for path in paths:
            try:
                text = self.get_text(path)
            except Exception:  # noqa: BLE001
                continue
            meta, body = split_frontmatter(text)
            body = search_body(body)
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
                if distinct < min_matches:
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
    def write_text(
        self, relpath: str, content: str, overwrite: bool = False, document_status: DocumentStatus = "active"
    ) -> str:
        p = safe_path(relpath)
        if not p.suffix:
            p = p.with_suffix(".md")
        if p.suffix.lower() not in EDITABLE_EXT:
            raise ValueError("Only Markdown and TXT files can be written as text")
        if p.parent == ROOT:
            raise ValueError("Please specify a folder, e.g. files/…")
        p.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite:
            p = unique_path(p)
        status = self.document_status(rel(p)) if overwrite and p.exists() else document_status
        return self._write(p, content.encode("utf-8"), status)

    async def save_uploads(self, folder: str, files: list[tuple[str, bytes, DocumentStatus]]) -> list[str]:
        def validate_batch() -> None:
            for name, data, status in files:
                validate_upload(name, data)
                if status not in DOCUMENT_STATUSES:
                    raise ValueError("Choose Active, Not Active or Draft")

        await asyncio.to_thread(validate_batch)
        d = safe_path(folder or "files")
        if d == ROOT:
            d = ROOT / "files"
        d.mkdir(parents=True, exist_ok=True)
        return [
            self._write(unique_path(d / safe_filename(name)), data, status)
            for name, data, status in files
        ]

    def _write(self, path: Path, data: bytes, status: DocumentStatus) -> str:
        relative = rel(path)
        previous = self.document_status(relative)
        self._set_status(relative, status)
        temporary = path.with_name(f".{path.name}.uploading")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)
            self._set_status(relative, previous)
            raise
        if relative not in self.entries:
            stat = path.stat()
            self.entries[relative] = {
                "id": file_id_for(relative), "mtime": stat.st_mtime, "size": stat.st_size, "status": "pending",
            }
        self.wake.set()
        return relative

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
        prefix = rel(p)
        statuses = {k: v for k, v in self.document_statuses.items() if k != prefix and not k.startswith(prefix + "/")}
        if statuses != self.document_statuses:
            self._save_statuses(statuses)
        self.wake.set()

    def save_note(
        self, title: str, text: str, person: str = "", source: str = "speech", document_status: DocumentStatus = "active"
    ) -> str:
        meta = {
            "title": title or "Voice note",
            "type": "voice note",
            "date": today(),
            "person": person,
            "source": source,
        }
        body = frontmatter(meta) + f"# {meta['title']}\n\n{text.strip()}\n"
        return self.write_text(f"speech/{today()}_{slugify(title or 'note')}.md", body, document_status=document_status)

    def save_voice_note(
        self,
        transcript: str,
        structured: dict | None,
        person: str = "",
        title: str = "",
        audio: str = "",
        transcriber: str = "",
        document_status: DocumentStatus = "active",
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
            "topic": st.get("topic") or "",
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
        # All voice notes sit directly in speech/ (no sub-folders); the topic is metadata.
        return self.write_text(f"speech/{today()}_{slugify(title)}.md", body, document_status=document_status)

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
        document_status: DocumentStatus = "active",
        session: int = 0,
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
            "session": str(session) if session else "",
        }
        body = content.strip()
        if body.startswith("---"):
            _, body = split_frontmatter(body)
        if not body.lstrip().startswith("#"):
            body = f"# {meta['title']}\n\n{body}"
        # All interviews sit directly in interviews/ (no sub-folders); topic and person are
        # metadata and filterable in Memory.
        relpath = f"interviews/{today()}_{slugify(topic)}_{slugify(person)}.md"
        return self.write_text(relpath, frontmatter(meta) + body + "\n", document_status=document_status)


store = KnowledgeStore()
