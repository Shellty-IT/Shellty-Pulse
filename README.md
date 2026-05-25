# Shellty Pulse — Service Health Monitor & Keep-Alive

[![CI/CD](https://github.com/Shellty-IT/Shellty-Pulse/actions/workflows/ci.yml/badge.svg)](https://github.com/Shellty-IT/Shellty-Pulse/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)
![License](https://img.shields.io/badge/License-MIT-green)

A professional-grade Service Health Monitor designed for real-time tracking of web service availability. Acts as an intelligent **keep-alive** mechanism for applications on free-tier platforms (e.g., Render), preventing idle sleep through automated, configurable pings.

---

## Key Features

- **Real-time Dashboard** — live status for all monitored services with response time and uptime tracking
- **Auto-Ping** — APScheduler-based periodic health checks with configurable intervals
- **Business Hours** — restrict auto-ping to configured time windows (Europe/Warsaw timezone)
- **Per-Service Toggle** — enable/disable individual services without removing them
- **SSRF Protection** — blocks requests to localhost, link-local, and cloud metadata endpoints
- **REST API** — full CRUD for services, toggle auto-ping, configure business hours
- **Infrastructure as Code** — Docker Compose + Ansible for automated provisioning
- **CI/CD Pipeline** — GitHub Actions: lint → test → build → deploy to Oracle Cloud

---

## Architecture Overview

```
┌──────────────────────────────────────────────────┐
│              Oracle Cloud VM (24/7)               │
│                                                    │
│  ┌──────────────────────────────────────────┐     │
│  │          Docker Container                 │     │
│  │                                           │     │
│  │  Flask App ──── APScheduler              │     │
│  │     │              │                      │     │
│  │     │         scheduled_check()           │     │
│  │     │              │                      │     │
│  │  REST API    check_all_services()         │     │
│  │  Dashboard        │                       │     │
│  │     │         ┌───┴───┐                   │     │
│  │     │         │ HTTP  │                   │     │
│  │     │         │  GET  │                   │     │
│  │     │         └───┬───┘                   │     │
│  └─────┼─────────────┼──────────────────────┘     │
│        │             │                             │
└────────┼─────────────┼─────────────────────────────┘
         │             │
    :5000 port    External services
   (dashboard)    (Render, etc.)
```

**GitHub Actions** acts purely as a quality gate — no external service pinging in CI.

---

## Service Status Legend

| Status | Icon | Condition |
|---|---|---|
| Operational | 🟢 | HTTP 200, response time < 1s |
| Degraded | 🟡 | HTTP 200, response time 1–3s |
| Slow | 🟠 | HTTP 200, response time > 3s |
| Down | 🔴 | Non-200 response, timeout, or connection error |
| Unknown | ⚪ | Service has not been checked yet |
| Disabled | ⏸ | Service is toggled off (skipped by auto-ping) |

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Application health check (version, uptime, scheduler) |
| `GET` | `/` | Dashboard |
| `GET` | `/api/services` | List all services with status and meta |
| `POST` | `/api/services` | Add a service `{name, url, frontend_url?}` |
| `DELETE` | `/api/services/<id>` | Remove a service |
| `POST` | `/api/services/<id>/check` | Manual single-service check |
| `POST` | `/api/services/<id>/toggle-enabled` | Enable/disable a service |
| `POST` | `/api/check-all` | Check all enabled services |
| `POST` | `/api/toggle-auto-ping` | Toggle auto-ping on/off |
| `POST` | `/api/ping-interval` | Set interval `{interval: <seconds>}` |
| `POST` | `/api/business-hours` | Configure `{enabled, start, end}` |

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Git
- Python 3.12 (for local development)

### Docker Compose (recommended)

```bash
git clone https://github.com/Shellty-IT/Shellty-Pulse.git
cd Shellty-Pulse
docker compose up --build -d
```

Dashboard: [http://localhost:5000](http://localhost:5000)

### Local Development

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | HTTP port |
| `PING_INTERVAL` | `900` | Auto-ping interval in seconds |
| `REQUEST_TIMEOUT` | `90` | Per-request timeout in seconds |
| `MAX_SERVICES` | `50` | Maximum number of monitored services |
| `SERVICES` | `[]` | JSON array of services to preload |
| `DISABLE_SCHEDULER` | `false` | Set to `true` to disable APScheduler (used in CI) |
| `BUSINESS_HOURS_ENABLED` | `false` | Enable business hours on startup |
| `BUSINESS_HOURS_START` | `8` | Start hour (0–23) |
| `BUSINESS_HOURS_END` | `20` | End hour (0–23) |
| `BUSINESS_HOURS_TIMEZONE` | `Europe/Warsaw` | Timezone for business hours |
| `TESTING` | `false` | Enable test mode (no scheduler) |

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

### Available ping intervals

`600` (10 min) · `900` (15 min) · `1800` (30 min) · `3600` (1 h) · `86400` (24 h) · `172800` (48 h)

---

## Oracle Cloud Server Setup

### 1. Create the VM

- **Shape**: VM.Standard.E2.1.Micro (Always Free)
- **OS**: Ubuntu 22.04 or 24.04
- **VCN**: Add Ingress Rule for TCP port 5000 (source: `0.0.0.0/0`)

### 2. Generate SSH key for GitHub Actions

On your local machine:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/oracle_deploy
```

Add the public key to the server:

```bash
ssh ubuntu@YOUR_IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" < ~/.ssh/oracle_deploy.pub
```

### 3. Configure GitHub Secrets

Go to **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
|---|---|
| `ORACLE_HOST` | Server public IP address |
| `ORACLE_USER` | `ubuntu` |
| `ORACLE_SSH_KEY` | Contents of `~/.ssh/oracle_deploy` (private key) |
| `ORACLE_PORT` | `22` |

### 4. First deployment

```bash
ssh ubuntu@YOUR_IP
git clone https://github.com/Shellty-IT/Shellty-Pulse.git
cd Shellty-Pulse
sudo usermod -aG docker ubuntu   # allow docker without sudo
# log out and log back in for group change to take effect
docker compose up --build -d
```

After this, every push to `main` triggers automatic deployment via CI/CD.

---

## Development

### Running tests

```bash
pip install -r requirements.txt pytest
TESTING=1 DISABLE_SCHEDULER=true pytest tests/ --tb=short -v
```

### Code quality

```bash
pip install black isort flake8
black app.py pulse/
isort --profile black app.py pulse/
flake8 app.py pulse/ --max-line-length=120 --extend-ignore=E501,W503
```

### Adding a new service

Edit the `SERVICES` environment variable in `docker-compose.yml`:

```json
{"name": "My New Service", "url": "https://my-service.com/health", "frontend_url": "https://my-service.com"}
```

Or add dynamically via the dashboard or API:

```bash
curl -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name": "My Service", "url": "https://my-service.com/health"}'
```

---

## CI/CD Pipeline

```
push/PR to main
       │
       ▼
┌─────────────┐
│ Lint & Format│  black, isort, flake8
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Unit Tests  │  pytest (mocked, no external requests)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Docker Build │  build image → run → health check
└──────┬──────┘
       │
       ▼  (only on push to main)
┌─────────────┐
│   Deploy     │  SSH → git pull → docker compose rebuild
└─────────────┘
```

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Backend | Python, Flask | 3.12, 3.1.1 |
| Server | Gunicorn | 23.0.0 |
| Scheduler | APScheduler | 3.10.4 |
| HTTP Client | Requests | 2.32.3 |
| Frontend | Vanilla JS, HTML/CSS | — |
| Containerisation | Docker, Docker Compose | — |
| Provisioning | Ansible | — |
| CI/CD | GitHub Actions | — |
| Hosting | Oracle Cloud (VM.Standard.E2.1.Micro) | Always Free |

---

## Project Structure

```
Shellty-Pulse/
├── app.py                    # Application entry point
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── tests/
│   └── test_app.py           # Unit tests (pytest)
├── ansible/
│   └── playbook.yml          # Server provisioning
├── .github/
│   └── workflows/
│       └── ci.yml            # CI/CD pipeline
└── pulse/
    ├── __init__.py            # Flask application factory
    ├── config.py              # Constants and env vars
    ├── state.py               # In-memory application state
    ├── models.py              # Service model and status logic
    ├── checker.py             # Health-check engine
    ├── scheduler.py           # APScheduler lifecycle
    ├── validators.py          # Input validation, SSRF protection
    ├── routes/
    │   ├── api.py             # REST API (/api/*)
    │   └── dashboard.py       # Dashboard route (/)
    ├── static/
    │   └── favicon.svg
    └── templates/
        ├── dashboard.html
        └── includes/
            ├── styles.css.html
            ├── scripts.js.html
            └── logo.svg.html
```

---

## Security

- **SSRF protection** — rejects URLs resolving to localhost, 127.0.0.1, 0.0.0.0, 169.254.169.254, metadata.google.internal
- **Non-root container** — runs as dedicated `pulse` user
- **Read-only filesystem** — container filesystem is read-only (tmpfs for /tmp)
- **No new privileges** — `security_opt: no-new-privileges:true`
- **Input validation** — name and URL length limits on all service endpoints

---

## License

MIT

---

*Built by [Shellty IT](https://shellty-it.github.io)*
