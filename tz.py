"""Timezone-aware date helpers — all dates should use the user's local timezone."""

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))


def today() -> date:
    return datetime.now(LOCAL_TZ).date()
