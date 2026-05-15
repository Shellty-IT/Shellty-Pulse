# Shellty Pulse — Service Health Monitor

A lightweight, self-hosted service health monitor with an intelligent keep-alive mechanism for free-tier hosting (Render, Railway, Fly.io). Prevents application sleep by sending configurable periodic health checks, controlled by a GitHub Actions cron schedule with Business Hours support.

---

## Features

- **Real-time dashboard** — live status for all monitored services with response time and uptime tracking
- **Keep-alive engine** — 3-phase cold-start strategy (fire → wait → verify) optimised for Render free tier
- **Business Hours** — restrict wake-up calls to configured time windows (CET/CEST, overnight windows supported)
- **GitHub Actions integration** — cron-driven wake mechanism that survives container restarts; settings persist via GitHub Variables
- **Manual check** — trigger a full check from the dashboard, bypassing business hours
- **Auto-ping fallback** — APScheduler-based local fallback for development or self-hosted deployments
- **SSRF protection** — blocks requests to localhost, link-local addresses, and cloud metadata endpoints
- **Infrastructure as Code** — Docker Compose for local deployment, Ansible playbook for server provisioning
- **CI/CD pipeline** — GitHub Actions: lint → unit tests → Docker build → integration tests → Docker Compose test

---

## How It Works

### Production (Render free tier)

```
GitHub Actions cron (every 10 min)
        │
        ├─ Check business hours (GitHub Variables: BH_ENABLED / BH_START / BH_END)
        │
        ├─ [Phase 1] Fire: GET all service URLs simultaneously → kicks cold starts
        │
        ├─ [Phase 2] Wait: 90 s — allows Render instances to boot
        │
        └─ [Phase 3] Verify: GET each URL, update Pulse state via /api/wake-and-check
```

### Local / Self-hosted

APScheduler runs `scheduled_check()` on the configured interval. Business Hours are respected identically to the GitHub Actions logic.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask 3.1 |
| Server | Gunicorn 23 |
| Scheduler | APScheduler 3.10 |
| HTTP client | Requests 2.32 |
| Frontend | Vanilla JS, HTML/CSS (no framework) |
| Containerisation | Docker, Docker Compose |
| Provisioning | Ansible |
| CI/CD | GitHub Actions |
| Hosting | Render (free tier) |

---

## Quick Start

### Docker Compose

```bash
git clone https://github.com/Shellty-IT/Shellty-Pulse.git
cd Shellty-Pulse
```

Edit the `SERVICES` variable in `docker-compose.yml`, then:

```bash
docker compose up --build
```

Dashboard: [http://localhost:5000](http://localhost:5000)

### Local Development

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env           # edit SERVICES and optional vars
python app.py
```

---

## Configuration

All configuration is provided via environment variables.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | HTTP port |
| `PING_INTERVAL` | `900` | Auto-ping interval in seconds (local fallback) |
| `REQUEST_TIMEOUT` | `90` | Per-request timeout in seconds |
| `MAX_SERVICES` | `50` | Maximum number of monitored services |
| `SERVICES` | `[]` | JSON array of services to preload on startup |
| `GITHUB_TOKEN` | — | Personal access token with `repo` scope (Variables read/write) |
| `GITHUB_REPO` | — | Repository in `owner/repo` format |
| `WAKE_SECRET` | — | Shared secret for `/api/wake-and-check` (optional but recommended) |

### `SERVICES` format

```json
[
  {
    "name": "My API",
    "url": "https://my-api.onrender.com/health",
    "frontend_url": "https://my-app.com"
  }
]
```

`frontend_url` is optional — when provided, the service name on the dashboard becomes a clickable link.

### Available ping intervals

`600` (10 min) · `900` (15 min) · `1800` (30 min) · `3600` (1 h) · `86400` (24 h) · `172800` (48 h)

---

## Business Hours

Business Hours restrict GitHub Actions wake-up calls to a configured time window (Europe/Warsaw timezone). Outside this window services are allowed to sleep, conserving Render free-tier hours.

Settings are stored as **GitHub Actions Variables** so they survive container restarts on Render:

| Variable | Example | Description |
|---|---|---|
| `BH_ENABLED` | `true` | Enable/disable business hours |
| `BH_START` | `9` | Start hour (0–23, inclusive) |
| `BH_END` | `15` | End hour (0–23, exclusive + 15 min buffer) |

Overnight windows are supported — set `BH_START > BH_END` (e.g. `23` → `1` means 23:00–01:15).

Settings can be changed live from the dashboard and are synced back to GitHub Variables automatically.

---

## GitHub Actions Setup

### 1. Add a repository secret

Go to **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
|---|---|
| `WAKE_SECRET` | Random string matching the `WAKE_SECRET` env var on Render |

### 2. Set initial Variables (optional)

Go to **Settings → Secrets and variables → Actions → Variables**:

| Variable | Default |
|---|---|
| `BH_ENABLED` | `false` |
| `BH_START` | `9` |
| `BH_END` | `15` |

Variables are managed automatically by the dashboard after the first manual save.

### 3. Set the app URL

Edit `.github/workflows/wake-shellty-pulse.yml` and update:

```yaml
env:
  APP_URL: https://your-app.onrender.com
```

---

## Deployment

### Render

1. Connect your GitHub repository in the Render dashboard
2. Set environment variables: `SERVICES`, `GITHUB_TOKEN`, `GITHUB_REPO`, `WAKE_SECRET`
3. Deploy — GitHub Actions will handle scheduled wake-ups automatically

### Ansible (VPS / bare metal)

Requires Ubuntu 20.04 / 22.04 / 24.04, sudo access, and the `community.docker` collection:

```bash
ansible-galaxy collection install community.docker

# Local deployment
ansible-playbook ansible/playbook.yml -i "localhost," -c local

# Remote server
ansible-playbook ansible/playbook.yml -i inventory.ini
```

The playbook installs Docker CE, builds the image, and starts the container with a health check.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Application health check (version, uptime, scheduler state) |
| `GET` | `/` | Dashboard |
| `GET` | `/api/services` | List all services with status and meta |
| `POST` | `/api/services` | Add a service `{name, url, frontend_url?}` |
| `DELETE` | `/api/services/<id>` | Remove a service |
| `POST` | `/api/services/<id>/check` | Manual single-service check |
| `POST` | `/api/check-all` | Legacy: check all services locally |
| `POST` | `/api/fire-all` | Phase 1: fire all services (cold-start kick) |
| `POST` | `/api/verify-all` | Phase 3: verify all services (call 120 s after fire) |
| `POST` | `/api/wake-and-check` | Called by GitHub Actions — requires `X-Wake-Secret` header if configured |
| `POST` | `/api/toggle-auto-ping` | Toggle local APScheduler on/off |
| `POST` | `/api/ping-interval` | Set interval `{interval: <seconds>}` |
| `POST` | `/api/trigger-manual-check` | Trigger GitHub Actions workflow from dashboard |
| `POST` | `/api/business-hours` | Configure BH `{enabled, start, end}` — syncs to GitHub Variables |

---

## Service Status Levels

| Status | Condition |
|---|---|
| Operational | HTTP 200, response time < 1 s |
| Degraded | HTTP 200, response time 1–3 s |
| Slow | HTTP 200, response time > 3 s |
| Down | Non-200 response, timeout, or connection error |
| Unknown | Service has not been checked yet |

---

## CI/CD Pipeline

The pipeline runs on every push and pull request to `main`:

```
lint
 └─ py_compile + flake8 + unit tests (factory, SSRF, validation)
      │
      ├─ build-and-test
      │    └─ Docker build → container start → health check → API integration tests
      │
      └─ compose-test
           └─ docker compose up → health check → service preload verification
```

---

## Project Structure

```
Shellty-Pulse/
├── app.py                   # Application entry point
├── startup.sh               # Gunicorn start + service preload
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── ansible/
│   └── playbook.yml         # VPS provisioning
├── .github/
│   └── workflows/
│       ├── ci.yml           # CI pipeline
│       └── wake-shellty-pulse.yml  # Scheduled keep-alive
└── pulse/
    ├── config.py            # All constants and env vars
    ├── state.py             # In-memory application state
    ├── models.py            # Service model and status logic
    ├── checker.py           # Health-check engine (fire / verify)
    ├── scheduler.py         # APScheduler lifecycle + GitHub Variables sync
    ├── validators.py        # Input validation and SSRF protection
    ├── routes/
    │   ├── api.py           # REST API blueprint (/api/*)
    │   └── dashboard.py     # Dashboard route (/)
    └── templates/
        └── dashboard.html   # Single-page dashboard
```

---

## Security

- **SSRF protection** — rejects URLs resolving to `localhost`, `127.0.0.1`, `0.0.0.0`, `169.254.169.254`, and `metadata.google.internal`
- **Wake secret** — `/api/wake-and-check` validates `X-Wake-Secret` header when `WAKE_SECRET` env var is set
- **Non-root container** — application runs as a dedicated `pulse` user
- **Read-only filesystem** — Docker Compose mounts container filesystem as read-only (`tmpfs` for `/tmp`)
- **No new privileges** — `security_opt: no-new-privileges:true`
- **Input validation** — name and URL length limits enforced on all service endpoints

---

## License

MIT

---

*Built by [Shellty IT](https://shellty-it.github.io)*
