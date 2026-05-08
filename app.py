"""
Shellty Pulse — application entry point.
"""
from __future__ import annotations

import os

# ← DODAJ TO NA POCZĄTKU
try:
    from dotenv import load_dotenv
    load_dotenv()  # Ładuje zmienne z .env
except ImportError:
    pass  # python-dotenv nie zainstalowany (OK na production/Render)

from pulse import create_app
from pulse.scheduler import start_background_services

_testing = os.environ.get("TESTING", "").lower() in ("1", "true")

app = create_app(testing=_testing)

if not _testing:
    start_background_services()

# ── Development server ───────────────────────────────────────────────────────
if __name__ == "__main__":
    from pulse.config import PORT
    import logging
    logging.getLogger("shellty-pulse").info(
        "Development mode — Dashboard: http://0.0.0.0:%d", PORT
    )
    app.run(host="0.0.0.0", port=PORT, debug=False)
