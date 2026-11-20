# Hackathon Memory Platform

[LibreChat](https://github.com/LibreChat-AI/LibreChat/tree/4700c44eb77c142b4f9f8b87476924f22890eb39) is in `frontend/`. Its official Docker image serves the web UI and LibreChat API. [LibreChat RAG API](https://github.com/LibreChat-AI/rag-api/tree/97633ad3c837c8ee1a2b3cd3e2281ea09d78b5c4) is in `backend/` and builds from the included `Dockerfile.lite`.

## Deploy

On a Linux host with Docker Compose and OpenSSL, make TCP ports 80 and 443 reachable from the internet. Run the following from the repository root, using your public IPv4 address or DNS name.

```sh
bash deploy/init-env.sh <public-host>
```

Set `OPENROUTER_KEY` in the generated `.env`. The chat endpoint uses `openai/gpt-4.1-mini` and file indexing and search use `openai/text-embedding-3-small`, all through OpenRouter. With an empty key, LibreChat can accept a per-user OpenRouter key for chat, and file indexing requires the server-side key.

If Nginx already listens on port 80, set `CADDY_HTTP_BIND=127.0.0.1:8080` in `.env`. Enable a separate Nginx virtual host using `deploy/nginx-http.conf.template` and replace `__PUBLIC_HOST__` with the same address as `PUBLIC_HOST`. Run `nginx -t` before reloading it. Nginx passes HTTP and ACME validation for this host to Caddy. Caddy serves HTTPS directly on port 443.

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
