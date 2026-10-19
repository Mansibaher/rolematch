# Deploying RoleMatch

RoleMatch is a personal tool: one small server is enough. The guide below targets a single VM (2 vCPU, 4 GB RAM,
~10 GB disk) running Docker Compose behind HTTPS. Nothing here requires paid APIs.

> These steps were written alongside the Compose file but have not been executed end to end (the build
> environment had no Docker daemon). Run them on a staging VM first.

## 1. Prepare the host

```bash
# Ubuntu 24.04
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 caddy
sudo usermod -aG docker $USER   # log out and back in
git clone <your-fork> rolematch && cd rolematch
cp .env.example .env
```

Edit `.env`:
- `POSTGRES_PASSWORD`: a long random value (`openssl rand -base64 32`).
- `ROLEMATCH_COOKIE_SECURE=true` (you will serve HTTPS).
- `ROLEMATCH_CORS_ORIGINS=["https://jobs.example.com"]`.
- `ROLEMATCH_USER_AGENT`: include a contact address so employer boards can reach you.
- `ROLEMATCH_ALLOW_REGISTRATION=false` once your own account exists, if the instance is just for you.

For reproducible builds, pin the model: `docker compose build --build-arg MODEL_REVISION=<commit sha> --build-arg ROLEMATCH_MODEL_SHA256=<digest printed by fetch_model.py>`.

## 2. Start the stack

```bash
docker compose up -d --build
docker compose exec api python -m app.cli sync-watchlist
docker compose exec api python -m app.cli seed     # optional: demo account + fixtures (skip on a private instance)
docker compose logs -f api worker
```

Only `web` (3000) needs to be public. Keep `8000`, Postgres and Redis on localhost: remove the `ports` entry of
`api` in production, since the web container reaches the API over the internal network.

## 3. HTTPS with Caddy

`/etc/caddy/Caddyfile`:

```
jobs.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:3000
}
```

`sudo systemctl reload caddy`. Caddy obtains and renews certificates automatically.

## 4. Operations

- **Migrations** run on API start (`python -m app.cli migrate`). Back up before upgrading.
- **Backups:** `docker compose exec db pg_dump -U rolematch rolematch | gzip > rolematch-$(date +%F).sql.gz` daily,
  plus the `uploads` volume. Test a restore.
- **Ingestion schedule:** Celery beat runs `ingest_all` at minute 17 every 6 hours. Run one now with
  `docker compose exec api python -m app.cli ingest --all`.
- **Health:** `GET /api/health`; source health is on the Companies & sources page (and `GET /api/sources`).
- **Re-enrichment** after changing extraction rules or the embedding model:
  `docker compose exec worker celery -A app.workers.celery_app call app.workers.tasks.reenrich_stale`.
- **Logs** contain request lines and errors, not resume text; still treat them as sensitive.

## 5. Managed alternatives

Any host that runs containers works: put Postgres on a managed service with pgvector enabled (create the
extension once as an admin), Redis on a managed instance, and run `api`, `worker`, `beat` and `web` as services.
Keep a single `beat` instance. The `uploads` volume must be persistent and private; for multiple API replicas,
move uploads to object storage first.

## 6. Security checklist

- [ ] HTTPS only, `ROLEMATCH_COOKIE_SECURE=true`
- [ ] Strong `POSTGRES_PASSWORD`; database not exposed publicly
- [ ] Registration disabled or restricted on a personal instance
- [ ] Demo account not seeded on a public instance
- [ ] `ROLEMATCH_LLM_*` left empty unless you intend to use an external provider
- [ ] Backups configured and a restore tested
