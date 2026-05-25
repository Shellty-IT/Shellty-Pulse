"""
Shellty Pulse — application entry point.
"""

from __future__ import annotations

import os

from pulse import create_app
from pulse.config import DISABLE_SCHEDULER

_testing = os.environ.get("TESTING", "").lower() in ("1", "true")

app = create_app(testing=_testing)

if not _testing and not DISABLE_SCHEDULER:
    from pulse.scheduler import start_background_services

    start_background_services()

if __name__ == "__main__":
    import logging

    from pulse.config import PORT

    logging.getLogger("shellty-pulse").info(
        "Development mode — Dashboard: http://0.0.0.0:%d", PORT
    )
    app.run(host="0.0.0.0", port=PORT, debug=False)
