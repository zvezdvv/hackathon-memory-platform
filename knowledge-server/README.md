# Smart City knowledge base ("Memory")

The Memory workspace opens beside the LibreChat sidebar. It exposes documents and their
metadata, RAG search and source links.

```
LibreChat  ── Memory overlay (list, metadata, upload, speech, interview) ──► REST  /knowledge/api/*
           ── Chat (Smart City Assistant / Knowledge Interview)          ──► MCP   knowledge:8100/mcp/ (internal)
                                                                                  │
knowledge/  (folder = single source of truth)  ──auto──► rag_api ──► pgvector     │
           ▲                                                                      │
           └──────────── viewer with highlighted passage  /knowledge/view?path=…&hl=… ◄┘
```

## Folders

| Folder | Content |
|---|---|
| `knowledge/files/` | Documents (laws, PDFs, notes) – upload in the overlay or copy into the folder |
| `knowledge/speech/` | Voice notes (transcripts) |
| `knowledge/interviews/<person>/` | Interview protocols |
| `knowledge/_guides/` | Markdown instructions that tell the interview assistant how to ask |
| `knowledge/.state/` | Internal index state and text cache (do not touch) |

Uploads accept PDF, Markdown (`.md`, `.markdown`) and TXT. A document's status is stored
under `knowledge/.state/` and can be changed in its viewer. Existing files default to Active.
Only Active documents participate in semantic and keyword search, including via MCP.

Metadata (title, type, ID, person, date, tags, place) is read from each Markdown file's
front matter. A document whose front matter says it updates others (e.g. `note: "Updates
N01, N06"` or the German `hinweis: "Aktualisiert … N01, N06"`) is detected as the newer
version: search results and the overlay flag the older sources.

## PDF processing and embeddings

Set `OPENROUTER_KEY` in the repository's `.env`. The knowledge server sends the original
PDF as a native file input to `openai/gpt-6.1-terra` and stores its transcription for the
viewer and RAG index. The RAG API embeds the text using `mongodb/voyage-4`. Existing active PDFs
are reprocessed automatically when the extraction version changes. Draft and inactive PDFs
are processed when activated.

```bash
docker compose up -d --force-recreate backend knowledge frontend
```

## MCP tools

`search_knowledge`, `read_file`, `list_folder`, `list_interview_guides`, `person_overview`,
`save_interview`, `save_note`, `save_document`

## Development

```bash
docker compose up -d --build knowledge   # after changes to the server
docker compose up -d --build frontend    # after changes to the Memory overlay
```

Memory overlay: `frontend/client/src/components/SidePanel/Knowledge/KnowledgePanel.tsx`.
Behind the proxy the server is reachable at `https://<host>/knowledge/`; `/knowledge/mcp`
is blocked from outside. Write access (upload, edit, delete) requires a valid LibreChat
login token.
