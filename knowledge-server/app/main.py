"""Knowledge server: REST API for the Memory overlay, document viewer and MCP endpoint (/mcp)."""

from __future__ import annotations

import asyncio
import contextlib
import html
import re
import json
import logging
from pathlib import Path
from urllib.parse import quote

import jwt
import markdown
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import ai
from .mcp_tools import mcp
from .places import places
from .store import (
    AUDIO_DIR, EDITABLE_EXT, JWT_SECRET, DocumentStatus, rel, safe_path, split_frontmatter, store,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

mcp_app = mcp.streamable_http_app()  # erzeugt auch mcp.session_manager


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    store.start()
    warm = asyncio.create_task(places.warm())
    async with mcp.session_manager.run():
        yield
    warm.cancel()
    await store.stop()


app = FastAPI(title="Smart City Knowledge", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _guard(fn):
    try:
        return fn()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, "File not found") from e


def require_user(authorization: str = Header(default="")) -> dict:
    """Write access only with a valid LibreChat login (access token signed with JWT_SECRET)."""
    if not JWT_SECRET:
        return {}
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in to LibreChat")
    try:
        return jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Session expired – reload the page") from e


# --------------------------------------------------------------------------- REST


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    return store.status()


@app.post("/api/reindex", dependencies=[Depends(require_user)])
async def reindex():
    return {"retrying": store.retry_unembedded()}


@app.get("/api/documents")
def documents():
    return store.documents()


@app.get("/api/tree")
def tree(path: str = ""):
    return _guard(lambda: store.tree(path))


@app.get("/api/raw")
def raw(path: str):
    p = _guard(lambda: safe_path(path))
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(p, filename=p.name, content_disposition_type="inline")


@app.get("/api/text")
def text(path: str):
    return {"path": path, "text": _guard(lambda: store.get_text(path))}


@app.put("/api/file", dependencies=[Depends(require_user)])
async def put_file(payload: dict = Body(...)):
    path, content = payload.get("path", ""), payload.get("content", "")
    if Path(path).suffix.lower() not in EDITABLE_EXT:
        raise HTTPException(400, "Only Markdown/text files can be edited")
    return {"path": _guard(lambda: store.write_text(path, content, overwrite=True))}


@app.post("/api/upload", dependencies=[Depends(require_user)])
async def upload(
    folder: str = Form("files"),
    files: list[UploadFile] = File(...),
    document_statuses: list[DocumentStatus] = Form(default=[]),
):
    if document_statuses and len(document_statuses) != len(files):
        raise HTTPException(400, "Choose one status for each file")
    statuses = document_statuses or ["active"] * len(files)
    uploads = [(file.filename or "file", await file.read(), status) for file, status in zip(files, statuses)]
    try:
        saved = await store.save_uploads(folder, uploads)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"saved": saved}


class StatusChange(BaseModel):
    path: str
    document_status: DocumentStatus


@app.patch("/api/document-status", dependencies=[Depends(require_user)])
async def document_status(payload: StatusChange):
    _guard(lambda: store.set_status(payload.path, payload.document_status))
    return {"path": payload.path, "document_status": payload.document_status}


@app.post("/api/folder", dependencies=[Depends(require_user)])
def folder(payload: dict = Body(...)):
    return {"path": _guard(lambda: store.make_dir(payload.get("path", "")))}


@app.delete("/api/item", dependencies=[Depends(require_user)])
async def delete_item(path: str):
    _guard(lambda: store.delete(path))
    return {"deleted": path}


@app.get("/api/search")
async def search(q: str, folder: str = "", k: int = Query(8, ge=1, le=20), typ: str = "", person: str = ""):
    return await store.search(q, folder, k, typ=typ, person=person)


class Note(BaseModel):
    title: str = ""
    text: str
    person: str = ""
    document_status: DocumentStatus = "active"


@app.post("/api/note", dependencies=[Depends(require_user)])
async def note(payload: Note):
    if not payload.text.strip():
        raise HTTPException(400, "No text")
    path = _guard(
        lambda: store.save_note(payload.title, payload.text, payload.person, document_status=payload.document_status)
    )
    return {"path": path}


AUDIO_TYPES = {"webm": "audio/webm", "ogg": "audio/ogg", "m4a": "audio/mp4"}
MAX_RECORDING_BYTES = 25_000_000


async def _read_recording(audio: UploadFile) -> tuple[bytes, str, str]:
    ext = Path(audio.filename or "").suffix.lower().lstrip(".")
    media_type = AUDIO_TYPES.get(ext, "")
    if not media_type:
        raise HTTPException(400, "Unsupported browser recording format")
    data = await audio.read(MAX_RECORDING_BYTES + 1)
    if not data:
        raise HTTPException(400, "Empty recording")
    if len(data) > MAX_RECORDING_BYTES:
        raise HTTPException(413, "Recordings must be at most 25 MB")
    return data, f"recording.{ext}", media_type


@app.post("/api/transcribe", dependencies=[Depends(require_user)])
async def transcribe(audio: UploadFile = File(...)):
    """Transcription only (Scribe v2) – used for the live transcript while recording."""
    if not ai.enabled():
        raise HTTPException(503, "OPENROUTER_KEY is not configured on the server")
    data, filename, media_type = await _read_recording(audio)
    try:
        return {"text": await ai.transcribe(data, filename, media_type)}
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/speech", dependencies=[Depends(require_user)])
async def speech(
    audio: UploadFile | None = File(None),
    text: str = Form(""),
    title: str = Form(""),
    person: str = Form(""),
    document_status: DocumentStatus = Form("active"),
):
    """Voice note: structure with an LLM and save to speech/.

    With audio and text, the text is the (possibly corrected) transcript from the live
    transcription and the audio is only stored. With audio only, it is transcribed first.
    """
    if not ai.enabled():
        raise HTTPException(503, "OPENROUTER_KEY is not configured on the server")
    data = b""
    filename, media_type = "", ""
    audio_name, transcriber, transcript = "", "", text.strip()
    if audio is not None:
        data, filename, media_type = await _read_recording(audio)
        if not transcript:
            try:
                transcript = await ai.transcribe(data, filename, media_type)
            except RuntimeError as e:
                raise HTTPException(502, str(e)) from e
        transcriber = ai.TRANSCRIBE_MODEL
    if not transcript:
        raise HTTPException(400, "Nothing was recognized – please try again")
    structured, warning = None, ""
    try:
        structured = await ai.structure(transcript, title, person)
    except RuntimeError as e:
        warning = str(e)  # still save the raw transcript
    if data:
        audio_name = store.save_audio(filename, data)
    try:
        path = store.save_voice_note(
            transcript, structured, person, title, audio_name, transcriber, document_status,
        )
    except Exception:
        if audio_name:
            (AUDIO_DIR / audio_name).unlink(missing_ok=True)
        raise
    return {"path": path, "transcript": transcript, "structured": structured is not None, "warning": warning}


@app.get("/api/audio", dependencies=[Depends(require_user)])
def audio_file(name: str):
    p = (AUDIO_DIR / Path(name).name).resolve()
    media_type = AUDIO_TYPES.get(p.suffix.lower().lstrip("."))
    if not media_type or AUDIO_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(p, media_type=media_type, headers={"Cache-Control": "private, no-store"})


@app.get("/api/people")
def people():
    return store.people()


@app.get("/api/guides")
def guides():
    return store.guides()


@app.get("/api/places")
async def map_places(names: str = ""):
    """Pins for the map: places from document metadata with coordinates and short hints.

    names: optional "A; B" filter (used by the map preview in chat answers).
    """
    return await places.payload(names)


# --------------------------------------------------------------------------- Viewer

VIEW_CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--border:#d8dee4;--card:#f6f8fa;--mark:#fff3a3;--accent:#0f766e}
@media (prefers-color-scheme: dark){:root{--bg:#171717;--fg:#e6e6e6;--muted:#9a9a9a;--border:#333;--card:#212121;--mark:#5c4b00;--accent:#2dd4bf}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;z-index:2}
.crumb{font-weight:600;word-break:break-all;flex:1;min-width:200px}.crumb span{color:var(--muted);font-weight:400}
.btn{border:1px solid var(--border);background:var(--card);color:var(--fg);border-radius:8px;padding:5px 10px;font-size:13px;cursor:pointer;text-decoration:none}
.btn:hover{border-color:var(--accent)}.badge{font-size:12px;border-radius:99px;padding:2px 8px;background:var(--card);border:1px solid var(--border)}
main{max-width:860px;margin:0 auto;padding:16px}
.meta{border:1px solid var(--border);background:var(--card);border-radius:10px;padding:8px 12px;margin-bottom:16px;font-size:13px}
.meta td{padding:2px 10px 2px 0;vertical-align:top}.meta td:first-child{color:var(--muted);white-space:nowrap}
.hitbox{border-left:4px solid var(--accent);background:var(--card);padding:8px 12px;border-radius:6px;margin-bottom:16px;font-size:13px}
.hit{background:var(--mark);border-radius:4px;box-shadow:0 0 0 4px var(--mark)}
pre{white-space:pre-wrap;background:var(--card);padding:10px;border-radius:8px}
table{border-collapse:collapse}th,td{border:1px solid var(--border);padding:4px 8px}
textarea{width:100%;min-height:70vh;background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:10px;font:13px/1.5 ui-monospace,monospace}
iframe.pdf{width:100%;height:80vh;border:1px solid var(--border);border-radius:8px}
.tabs{display:flex;gap:6px;margin-bottom:12px}.tabs .on{border-color:var(--accent);color:var(--accent)}
.processing{display:grid;gap:8px;margin-bottom:16px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--card)}
.processing progress{width:100%;accent-color:var(--accent)}
.hidden{display:none}
"""

VIEW_JS = """
const params=new URLSearchParams(location.search);const hl=params.get('hl');
const norm=s=>(s||'').toLowerCase().replace(/[*_#>`|\\[\\]()-]/g,' ').replace(/\\s+/g,' ').trim();
if(hl){const H=norm(hl);const head=H.slice(0,50);let first=null;
 document.querySelectorAll('#content p,#content li,#content td,#content h1,#content h2,#content h3,#content h4,#content blockquote,#content pre').forEach(el=>{
  if(el.querySelector('p,li'))return;const t=norm(el.innerText);if(t.length<12)return;
  if(H.includes(t.slice(0,80))||t.includes(head)){el.classList.add('hit');first=first||el;}});
 const box=document.getElementById('hitbox');if(box){box.classList.remove('hidden');box.querySelector('q').textContent=hl;}
 if(first){const go=()=>window.scrollTo(0,first.getBoundingClientRect().top+window.scrollY-140);setTimeout(go,100);setTimeout(go,600);}
}
function tab(n){document.querySelectorAll('[data-tab]').forEach(e=>e.classList.toggle('hidden',e.dataset.tab!==n));
 document.querySelectorAll('.tabs .btn').forEach(b=>b.classList.toggle('on',b.dataset.t===n));
 history.replaceState(null,'',n==='orig'?'#original':location.pathname+location.search);}
if(location.hash==='#original'&&document.querySelector('[data-tab="orig"]'))tab('orig');
function watchPdfProcessing(){
 const panel=document.getElementById('pdf-processing');
 if(!panel)return;
 const message=panel.querySelector('[role="status"]');
 const bar=panel.querySelector('progress');
 async function poll(){
  try{
   const res=await fetch('api/pdf-progress?path='+encodeURIComponent(PATH),{cache:'no-store'});
   if(res.status===404){
    message.textContent='This PDF is no longer available.';
    bar.hidden=true;
    return;
   }
   if(res.ok){
    const state=await res.json();
    if(state.status!=='pending'||(state.phase==='indexing'&&panel.dataset.textReady==='false')){
     location.reload();
     return;
    }
    if(message.textContent!==state.message)message.textContent=state.message;
    if(state.phase==='extracting'){
     bar.max=state.total_pages;
     bar.value=state.completed_pages;
    }else bar.removeAttribute('value');
   }
  }catch{
   // Keep the previous stage visible during a temporary connection failure.
  }
  setTimeout(poll,2000);
 }
 setTimeout(poll,2000);
}
watchPdfProcessing();
function parentToken(){return new Promise(res=>{if(window.parent===window)return res('');
 const origin=document.referrer?new URL(document.referrer).origin:location.origin;
 function receive(e){if(e.origin===origin&&e.source===window.parent&&e.data?.type==='kb-token'){
  clearTimeout(timer);window.removeEventListener('message',receive);res(e.data.token||'');}}
 const timer=setTimeout(()=>{window.removeEventListener('message',receive);res('');},1500);
 window.addEventListener('message',receive);window.parent.postMessage({type:'kb-token-request'},origin);});}
async function loadRecording(button){
 button.disabled=true;const notice=document.getElementById('recording-notice');
 try{
  const token=await parentToken();
  if(!token){notice.textContent='Open this note in Memory while signed in to play the recording.';return;}
  const res=await fetch('api/audio?name='+encodeURIComponent(button.dataset.name),{headers:{Authorization:token}});
  if(!res.ok)throw Error('Unable to load recording');
  const audio=document.getElementById('original-recording');
  const src=URL.createObjectURL(await res.blob());
  window.addEventListener('pagehide',()=>URL.revokeObjectURL(src),{once:true});
  audio.src=src;audio.hidden=false;button.hidden=true;notice.textContent='';
  audio.play().catch(()=>{});
 }catch{notice.textContent='Unable to load the recording.';}
 finally{button.disabled=false;}
}
async function save(){const token=await parentToken();const headers={'Content-Type':'application/json'};if(token)headers.Authorization=token;
 const r=await fetch('api/file',{method:'PUT',headers,body:JSON.stringify({path:PATH,content:document.getElementById('editor').value})});
 if(r.ok){location.href=location.pathname+'?path='+encodeURIComponent(PATH)+(params.get('embed')?'&embed=1':'');}
 else if(r.status===401){alert('Editing is only possible in the Memory overlay while signed in to LibreChat.');}else{alert('Saving failed');}}
function edit(){document.getElementById('content').classList.add('hidden');document.getElementById('editwrap').classList.remove('hidden');}
"""

STATUS_LABEL = {
    "indexed": "✅ indexed in RAG",
    "text_only": "🔎 keyword search only (not embedded)",
    "pending": "⏳ indexing",
    "error": "⚠️ error",
    "excluded": "Excluded from search",
}

PDF_PROCESSING_LABEL = {
    "queued": "Waiting to process PDF…",
    "rendering": "Preparing PDF pages…",
    "indexing": "Text extracted. Indexing for search…",
}


def _pdf_progress(path: str) -> dict:
    progress = store.pdf_progress(path)
    if progress["status"] != "pending":
        return progress
    phase = progress["phase"]
    if phase == "extracting":
        message = (
            f"Extracting PDF text… {progress['completed_pages']} of "
            f"{progress['total_pages']} pages processed."
        )
    else:
        message = PDF_PROCESSING_LABEL[phase]
    return {**progress, "message": message}


@app.get("/api/pdf-progress")
def pdf_progress(path: str):
    p = _guard(lambda: safe_path(path))
    if p.suffix.lower() != ".pdf" or not p.is_file():
        raise HTTPException(404, "PDF not found")
    return JSONResponse(_pdf_progress(rel(p)), headers={"Cache-Control": "no-store"})


def _render_md(text: str) -> str:
    safe = text.replace("&", "&amp;").replace("<", "&lt;")
    return markdown.markdown(safe, extensions=["tables", "fenced_code", "sane_lists"])


@app.get("/view", response_class=HTMLResponse)
def view(path: str, hl: str = "", embed: int = 0):
    p = _guard(lambda: safe_path(path))
    if not p.is_file() and rel(p) in {
        "interviews/klaus-behrens/T01_uebergabe_winterdienst.md",
        "interviews/klaus-behrens/T02_nachtrag_aenderungen.md",
    }:
        p = safe_path(f"interviews/{p.name}")
    if not p.is_file():
        raise HTTPException(404, "File not found")
    relative = rel(p)
    ext = p.suffix.lower()
    progress = _pdf_progress(relative) if ext == ".pdf" else None
    status = progress["status"] if progress else store.index_status(relative, store.entries.get(relative, {}))
    status_txt = "⏳ processing PDF" if progress and status == "pending" else STATUS_LABEL.get(status, "")
    text_content = store.get_text(relative)
    meta, body = split_frontmatter(text_content) if ext in (".md", ".markdown") else ({}, text_content)

    audio_html = ""
    if meta.get("audio"):
        audio_html = (
            f'<div class="meta"><button class="btn" type="button" onclick="loadRecording(this)"'
            f' data-name="{html.escape(meta["audio"])}">Play original recording</button>'
            '<span id="recording-notice" role="status"></span>'
            '<audio id="original-recording" controls hidden style="width:100%"></audio></div>'
        )
    meta_html = ""
    if meta:
        def pretty(v: str) -> str:
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                v = ", ".join(x.strip().strip('"') for x in v[1:-1].split(",") if x.strip())
            return v

        rows = "".join(
            f"<tr><td>{html.escape(k)}</td><td>{html.escape(pretty(v))}</td></tr>"
            for k, v in meta.items()
            if pretty(v) and k not in ("audio", "structured")
        )
        meta_html = f'<table class="meta">{rows}</table>'

    if ext in (".md", ".markdown"):
        body_html = _render_md(body)
    elif ext in (".txt", ".csv", ".json", ".yaml", ".yml"):
        body_html = f"<pre>{html.escape(body)}</pre>"
    else:
        paras = []
        for x in (x.strip() for x in body.split("\n\n")):
            if not x:
                continue
            if len(x) <= 500:
                paras.append(x)
                continue
            # split long PDF blocks so highlighted passages stay precise
            buf = ""
            for sent in re.split(r"(?<=[.!?:])\s+(?=[A-ZÄÖÜ0-9§(–-])", x):
                buf = f"{buf} {sent}".strip()
                if len(buf) > 280:
                    paras.append(buf)
                    buf = ""
            if buf:
                paras.append(buf)
        body_html = "".join(f"<p>{html.escape(x)}</p>" for x in paras)
        if not body_html and not (progress and status == "pending"):
            if progress and status == "error":
                message = "PDF text extraction failed. You can still open the original PDF."
            elif progress and status == "excluded":
                message = "Text extraction starts when this document is Active."
            else:
                message = "No text extracted."
            body_html = f"<p><em>{message}</em></p>"

    if progress and status == "pending":
        pages = ""
        if progress["phase"] == "extracting":
            pages = f' max="{progress["total_pages"]}" value="{progress["completed_pages"]}"'
        text_ready = "true" if body.strip() else "false"
        body_html = (
            f'<div class="processing" id="pdf-processing" data-text-ready="{text_ready}">'
            f'<span role="status">{html.escape(progress["message"])}</span>'
            f'<progress aria-label="PDF processing progress"{pages}></progress>'
            f"</div>{body_html}"
        )

    raw_url = f"api/raw?path={quote(path)}"
    content = f'<div id="content">{meta_html}{audio_html}{body_html}</div>'
    tabs = ""
    if ext == ".pdf":
        tabs = (
            '<div class="tabs"><button class="btn on" data-t="text" onclick="tab(\'text\')">Text &amp; passage</button>'
            '<button class="btn" data-t="orig" onclick="tab(\'orig\')">Original PDF</button></div>'
        )
        content = (
            f'<div data-tab="text">{content}</div>'
            f'<div data-tab="orig" class="hidden"><iframe class="pdf" src="{html.escape(raw_url)}"></iframe></div>'
        )
    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        content = f'<div id="content"><img src="{html.escape(raw_url)}" style="max-width:100%"></div>'

    editor = ""
    edit_btn = ""
    if ext in EDITABLE_EXT:
        edit_btn = '<button class="btn" onclick="edit()">Edit</button>'
        editor = (
            f'<div id="editwrap" class="hidden"><textarea id="editor">{html.escape(text_content)}</textarea>'
            '<p><button class="btn" onclick="save()">Save</button> '
            '<button class="btn" onclick="location.reload()">Cancel</button></p></div>'
        )

    folder, _, name = path.rpartition("/")
    script_path = json.dumps(path).replace("<", "\\u003c")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)}</title>
<style>{VIEW_CSS}</style></head><body>
<header><div class="crumb"><span>{html.escape(folder + '/' if folder else '')}</span>{html.escape(name)}</div>
<span class="badge">{status_txt}</span>{edit_btn}
<a class="btn" href="{html.escape(raw_url)}" target="_blank">Original</a>
{'' if embed else f'<a class="btn" href="{html.escape(raw_url)}" download>Download</a>'}</header>
<main><div id="hitbox" class="hitbox hidden"><strong>Passage:</strong> “<q></q>”</div>
{tabs}{content}{editor}</main>
<script>const PATH={script_path};{VIEW_JS}</script></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"} if progress else None)


# MCP (streamable HTTP) under /mcp – mounted last so the API routes take precedence
app.mount("/", mcp_app)
