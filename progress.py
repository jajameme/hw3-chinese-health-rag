"""Atomic JSON files shared by workers and the detached supervisor."""

import json
import os
import time
from datetime import datetime, timezone

from config import ROOT


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.1)


def update(operation, current=0, total=None, detail="", **extra):
    payload = {"operation": operation, "current": current, "total": total,
               "detail": detail, "updated_at": now(), **extra}
    write_json(ROOT / "progress.json", payload)
    print(json.dumps(payload, ensure_ascii=False), flush=True)
