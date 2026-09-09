from datetime import timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:
    BEIJING = timezone(timedelta(hours=8))
