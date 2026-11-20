"""MCP tools that LibreChat uses to read, search and fill the knowledge base."""


from mcp.server.fastmcp import FastMCP

from .store import GUIDE_FOLDER, slugify, split_frontmatter, store, view_url

INSTRUCTIONS = """Smart City knowledge base (file system + RAG search).

Folders:
- files/                 uploaded documents (PDFs, laws, notes, data)
- speech/                voice notes (transcripts)
- interviews/<person>/   knowledge interview protocols
- _guides/               instructions on HOW to run interviews (not domain knowledge)

Most source documents are written in German; answer in the user's language.

Rules:
1. ALWAYS answer domain questions with search_knowledge first and rely on the results.
2. Cite every statement inline with a short Markdown link to its source, e.g.
   "… 72 hours in advance ([N01](https://…))." Copy URLs exactly as provided, never invent links.
3. If nothing is found, say so instead of guessing.
"""

mcp = FastMCP(
    "smart-city-knowledge",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
)


def _fmt_hits(res: dict) -> str:
    if not res["results"]:
        return f"No results for “{res['query']}” in the knowledge base."
    mode = "hybrid search: RAG + keyword" if res["mode"] == "semantic" else "keyword search"
    lines = [f"{len(res['results'])} results ({mode}) for “{res['query']}”:\n"]
    for i, h in enumerate(res["results"], 1):
        page = f", page {h['page']}" if h.get("page") is not None else ""
        if h.get("path_note"):
            page += f" – {h['path_note']}"
        label = h.get("doc_id") or h["title"]
        lines.append(f"[{i}] Source ({label}): [{h['path']}{page}]({h['url']})")
        for u in h.get("updated_by", []):
            lines.append(
                f"⚠️ POSSIBLY OUTDATED: this source is updated by "
                f"[{u['path']}]({view_url(u['path'])}) (as of {u['datum']}). "
                "If they conflict, the newer version applies – point this out explicitly."
            )
        snippet = h["snippet"].replace("\n", "\n> ")
        lines.append(f"> {snippet}\n")
    lines.append(
        "Cite every result you use inline, right after the statement, as a short Markdown link, "
        "e.g. ([N01](URL)). Copy the URL completely and unchanged (including &hl=…) – it jumps "
        "to the highlighted passage."
    )
    return "\n".join(lines)


@mcp.tool()
async def search_knowledge(query: str, folder: str = "", max_results: int = 6) -> str:
    """Searches the knowledge base (RAG) for relevant passages.

    Args:
        query: Question or search terms in natural language (German terms work best).
        folder: Optionally search only in this folder, e.g. "interviews/klaus-behrens" or "files".
        max_results: Number of results (1-12).
    """
    res = await store.search(query, folder, max(1, min(int(max_results), 12)))
    # Deliver newer versions (e.g. a follow-up talk that updates a note) in full and FIRST,
    # so the model does not miss them.
    newer: dict[str, dict] = {}
    for h in res["results"]:
        for u in h.get("updated_by", []):
            newer.setdefault(u["path"], u)
    if not newer:
        return _fmt_hits(res)
    blocks = []
    for path, u in newer.items():
        _, body = split_frontmatter(store.get_text(path))
        if len(body) > 6000:
            body = body[:6000] + "\n[… truncated]"
        blocks.append(
            f"### NEWER VERSION – takes precedence: [{u['id'] or path}]({view_url(path)}) (as of {u['datum']})\n"
            "This document updates older sources in the results below. Check whether it changes "
            "the requested information; if so, give the NEW value and mention the old one as outdated.\n\n"
            f"{body}\n"
        )
    return "\n".join(blocks) + "\n---\n\n" + _fmt_hits(res)


@mcp.tool()
async def list_folder(path: str = "") -> str:
    """Shows the folders and files of the knowledge base as a tree. Empty path = everything."""
    tree = store.tree(path)
    lines: list[str] = []

    def walk(n: dict, depth: int) -> None:
        icon = "📁" if n["type"] == "dir" else "📄"
        suffix = "" if n["type"] == "dir" else f"  ({n.get('status')})"
        lines.append(f"{'  ' * depth}{icon} {n['name']}{suffix}")
        for c in n.get("children", []):
            walk(c, depth + 1)

    walk(tree, 0)
    return "\n".join(lines)


@mcp.tool()
async def read_file(path: str) -> str:
    """Reads a knowledge base file completely (text or extracted PDF text)."""
    text = store.get_text(path)
    if not text:
        return f"File {path} is empty or not readable (yet)."
    if len(text) > 60000:
        text = text[:60000] + "\n\n[… truncated]"
    return f"Source: [{path}]({view_url(path)})\n\n{text}"


@mcp.tool()
async def list_interview_guides() -> str:
    """Lists the interview guides in _guides/."""
    guides = store.guides()
    if not guides:
        return "No guides available."
    return "\n".join(f"- {g['path']}: {g['title']} – {g['beschreibung']}" for g in guides)


@mcp.tool()
async def person_overview(person: str) -> str:
    """Summarizes what the knowledge base already holds about a person.

    Returns previous interview sessions (with topics) and other mentions, so an interview
    can focus on the knowledge gaps.
    """
    slug = slugify(person)
    sessions = []
    for p in sorted(store.entries):
        if p.startswith(f"interviews/{slug}/"):
            meta, _ = split_frontmatter(store.get_text(p))
            topic = meta.get("topic") or meta.get("thema") or meta.get("title") or meta.get("titel") or ""
            date = meta.get("date") or meta.get("datum", "")
            sessions.append(f"- [{p}]({view_url(p)}) – {date} – {topic}".rstrip())
    res = await store.search(person, "", 6)
    out = [f"Previous interview sessions with {person}: {len(sessions)}"]
    out += sessions or ["- none yet"]
    out.append("\nOther mentions in the knowledge base:")
    out.append(_fmt_hits(res))
    return "\n".join(out)


@mcp.tool()
async def save_interview(person: str, topic: str, content: str, interviewer: str = "", guide: str = "") -> str:
    """Saves an interview protocol under interviews/<person>/ and indexes it for search.

    Args:
        person: Name of the interviewed person.
        topic: Topic of the session.
        content: Protocol in Markdown (summary, questions & answers, open points).
        interviewer: Optional, who conducted the interview.
        guide: Optional, guide used (path).
    """
    path = store.save_interview(person, topic, content, interviewer, guide)
    return f"Saved: [{path}]({view_url(path)}). The file is now indexed automatically."


@mcp.tool()
async def save_note(title: str, text: str, person: str = "") -> str:
    """Saves a (cleaned-up) voice note under speech/ and indexes it."""
    path = store.save_note(title, text, person, source="chat")
    return f"Saved: [{path}]({view_url(path)})."


@mcp.tool()
async def save_document(path: str, content: str) -> str:
    """Saves a Markdown document at a path, e.g. "files/procurement/checklist.md".

    Existing files are never overwritten (a new name is chosen).
    """
    if path.strip("/").startswith(GUIDE_FOLDER):
        return "Interview guides are maintained by people in the Memory overlay only."
    saved = store.write_text(path, content)
    return f"Saved: [{saved}]({view_url(saved)})."
