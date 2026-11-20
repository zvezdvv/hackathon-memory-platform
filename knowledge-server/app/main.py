"""Knowledge server: REST API for the Memory overlay, document viewer and MCP endpoint (/mcp)."""

from __future__ import annotations

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
from fastapi.responses import FileResponse, HTMLResponse

from .mcp_tools import mcp
from .store import EDITABLE_EXT, JWT_SECRET, safe_path, split_frontmatter, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

mcp_app = mcp.streamable_http_app()  # erzeugt auch mcp.session_manager


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    store.start()
    async with mcp.session_manager.run():
        yield
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
def reindex():
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
def put_file(payload: dict = Body(...)):
    path, content = payload.get("path", ""), payload.get("content", "")
    if Path(path).suffix.lower() not in EDITABLE_EXT:
        raise HTTPException(400, "Only Markdown/text files can be edited")
    return {"path": _guard(lambda: store.write_text(path, content, overwrite=True))}


@app.post("/api/upload", dependencies=[Depends(require_user)])
async def upload(folder: str = Form("files"), files: list[UploadFile] = File(...)):
    saved = []
    for f in files:
        data = await f.read()
        saved.append(_guard(lambda: store.save_upload(folder, f.filename or "file", data)))
    return {"saved": saved}


@app.post("/api/folder", dependencies=[Depends(require_user)])
def folder(payload: dict = Body(...)):
    return {"path": _guard(lambda: store.make_dir(payload.get("path", "")))}


@app.delete("/api/item", dependencies=[Depends(require_user)])
def delete_item(path: str):
    _guard(lambda: store.delete(path))
    return {"deleted": path}


@app.get("/api/search")
async def search(q: str, folder: str = "", k: int = Query(8, ge=1, le=20)):
    return await store.search(q, folder, k)


@app.post("/api/note", dependencies=[Depends(require_user)])
def note(payload: dict = Body(...)):
    if not (payload.get("text") or "").strip():
        raise HTTPException(400, "No text")
    path = _guard(
        lambda: store.save_note(payload.get("title", ""), payload["text"], payload.get("person", ""))
    )
    return {"path": path}


@app.get("/api/people")
def people():
    return store.people()


@app.get("/api/guides")
def guides():
    return store.guides()


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
 document.querySelectorAll('.tabs .btn').forEach(b=>b.classList.toggle('on',b.dataset.t===n));}
function parentToken(){return new Promise(res=>{if(window.parent===window)return res('');
 const t=setTimeout(()=>res(''),1500);window.addEventListener('message',function h(e){if(e.data&&e.data.type==='kb-token'){clearTimeout(t);window.removeEventListener('message',h);res(e.data.token||'');}});
 window.parent.postMessage({type:'kb-token-request'},'*');});}
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
}


def _render_md(text: str) -> str:
    safe = text.replace("&", "&amp;").replace("<", "&lt;")
    return markdown.markdown(safe, extensions=["tables", "fenced_code", "sane_lists"])


@app.get("/view", response_class=HTMLResponse)
def view(path: str, hl: str = "", embed: int = 0):
    p = _guard(lambda: safe_path(path))
    if not p.is_file():
        raise HTTPException(404, "File not found")
    entry = store.entries.get(path, {})
    status_txt = STATUS_LABEL.get(entry.get("status", "pending"), "")
    ext = p.suffix.lower()
    text_content = store.get_text(path)
    meta, body = split_frontmatter(text_content) if ext in (".md", ".markdown") else ({}, text_content)

    meta_html = ""
    if meta:
        rows = "".join(
            f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in meta.items()
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
        body_html = "".join(f"<p>{html.escape(x)}</p>" for x in paras) or "<p><em>No text extracted.</em></p>"

    raw_url = f"api/raw?path={quote(path)}"
    content = f'<div id="content">{meta_html}{body_html}</div>'
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
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)}</title>
<style>{VIEW_CSS}</style></head><body>
<header><div class="crumb"><span>{html.escape(folder + '/' if folder else '')}</span>{html.escape(name)}</div>
<span class="badge">{status_txt}</span>{edit_btn}
<a class="btn" href="{html.escape(raw_url)}" target="_blank">Original</a>
{'' if embed else f'<a class="btn" href="{html.escape(raw_url)}" download>Download</a>'}</header>
<main><div id="hitbox" class="hitbox hidden"><strong>Passage:</strong> “<q></q>”</div>
{tabs}{content}{editor}</main>
<script>const PATH={json.dumps(path)};{VIEW_JS}</script></body></html>"""
    return HTMLResponse(page)


# MCP (streamable HTTP) under /mcp – mounted last so the API routes take precedence
app.mount("/", mcp_app)
