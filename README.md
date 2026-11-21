# Hackathon Memory Platform

[LibreChat](https://github.com/LibreChat-AI/LibreChat/tree/4700c44eb77c142b4f9f8b87476924f22890eb39) is in `frontend/`. Its official Docker image serves the web UI and LibreChat API. [LibreChat RAG API](https://github.com/LibreChat-AI/rag-api/tree/97633ad3c837c8ee1a2b3cd3e2281ea09d78b5c4) is in `backend/` and builds from the included `Dockerfile.lite`.

## Deploy

On a Linux host with Docker Compose and OpenSSL, make TCP ports 80 and 443 reachable from the internet. Run the following from the repository root, using your public IPv4 address or DNS name.

```sh
bash deploy/init-env.sh <public-host>
```

Set `OPENROUTER_KEY` in the generated `.env`. The chat endpoint uses `openai/gpt-6-luna` and file indexing and search use `mongodb/voyage-4`, all through OpenRouter. Voyage 4 uses the `smartcity-voyage-4` pgvector collection. With an empty key, LibreChat can accept a per-user OpenRouter key for chat, and file indexing requires the server-side key.

Use `PUBLIC_HOST` for the canonical URL. When switching an IP-based deployment to a domain, set `PUBLIC_HOST` to the DNS name and `REDIRECT_IP` to the previous IPv4 address in `.env`. Old HTTP and HTTPS links redirect to the canonical domain with their path and query intact.

If Nginx already listens on port 80, set `CADDY_HTTP_BIND=127.0.0.1:8080` in `.env`. Enable a separate Nginx virtual host using `deploy/nginx-http.conf.template` and replace `__PUBLIC_HOST__` with the value of `PUBLIC_HOST`. If `REDIRECT_IP` is set, include that address in `server_name` as well. Run `nginx -t` before reloading it. Nginx passes HTTP and ACME validation to Caddy, which serves HTTPS directly on port 443.

```sh
docker compose up -d --build --wait
docker compose exec frontend npm run create-user
```

Open `https://<public-host>` and sign in with the account you created. Registration is disabled by default. Caddy terminates TLS, redirects HTTP to HTTPS, and renews the Let's Encrypt certificate automatically. The RAG API, PostgreSQL, MongoDB, and Meilisearch are reachable over Docker networks.

`deploy/librechat.yaml` configures the Smart City interface with chat, model selection, image attachments, file search, and file citations. It hides the agent, prompt, and other auxiliary controls.

## Check file search

Upload a small text file using **Upload for File Search**, then ask a question that relies on information in the file. Check both services if indexing or retrieval fails.

```sh
docker compose ps
docker compose logs --tail 80 frontend backend proxy
```

## Memory (knowledge base)

The **Memory** tab opens the document workspace beside the LibreChat sidebar. It shows metadata, filters, sorting, grouping and a reading pane for files under `knowledge/`.

| Folder | Content |
|---|---|
| `knowledge/files/` | Documents (laws, PDFs, notes) – upload in the tab or copy into the folder |
| `knowledge/speech/` | Voice notes (transcripts) |
| `knowledge/interviews/` | Interview protocols, all in one folder (topic, person and session are metadata) |
| `knowledge/_guides/` | Markdown guides that tell the interview assistant how to ask |

Memory accepts PDF (up to 10 pages), Markdown and TXT. Active documents are indexed automatically. Each PDF page is rendered and sent as an image to GPT-6.1 Terra via OpenRouter. The RAG API embeds the transcript. Not Active and Draft documents stay in the list and are excluded from search. Chat citations open the document with the passage highlighted.

The `knowledge` service also exposes an MCP server that LibreChat uses internally (`http://knowledge:8100/mcp/`, blocked at the proxy). Two assistants in the model selector use it: **Smart City Assistant** (answers with inline source links) and **Knowledge Interview** (asks targeted questions and saves the protocol). Uploads, edits and deletions require a LibreChat login.

```sh
docker compose up -d --build knowledge   # after changes to knowledge-server/
docker compose up -d --build frontend    # after changes to the Memory tab
```
