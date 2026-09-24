#!/usr/bin/env python3
"""Set Contabo Telegram cold-boot to keep offline messages.

Writes platforms.telegram.extra.drop_pending_on_cold_boot = false so a
gateway cold start delivers messages sent while Contabo was offline
(default upstream is true / drop). Does not create cron jobs or webhooks.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

CFG = Path("/opt/data/config.yaml")
KEY = "drop_pending_on_cold_boot"
WANT = False


def main() -> int:
    if not CFG.exists():
        raise SystemExit(f"missing {CFG}")
    text = CFG.read_text(encoding="utf-8")
    bak = CFG.with_name(f"config.yaml.bak-coldboot-{int(time.time())}")
    bak.write_text(text, encoding="utf-8")
    print(f"backup={bak}")

    try:
        import yaml
    except ImportError as e:
        raise SystemExit(f"PyYAML required: {e}") from e

    print("================ BEFORE (relevant) ================")
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"telegram|drop_pending|cold_boot|extra:", line, re.I):
            print(f"{i}:{line}")

    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"config root must be a mapping, got {type(data).__name__}")

    platforms = data.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
        data["platforms"] = platforms

    tg = platforms.get("telegram")
    if not isinstance(tg, dict):
        tg = {}
        platforms["telegram"] = tg

    extra = tg.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        tg["extra"] = extra

    before = extra.get(KEY, "<unset>")
    print(f"before={KEY}={before!r}")

    if extra.get(KEY) is WANT:
        print("patched=no (already set)")
    else:
        extra[KEY] = WANT
        CFG.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        print("patched=yes")

    out = CFG.read_text(encoding="utf-8")
    print("================ AFTER (relevant) ================")
    for i, line in enumerate(out.splitlines(), 1):
        if re.search(r"telegram|drop_pending|cold_boot|extra:", line, re.I):
            print(f"{i}:{line}")

    # Re-parse to confirm nested value
    after = yaml.safe_load(out) or {}
    got = (
        after.get("platforms", {})
        .get("telegram", {})
        .get("extra", {})
        .get(KEY, "<missing>")
    )
    print(f"after={KEY}={got!r}")
    if got is not WANT:
        raise SystemExit(f"verify failed: want {WANT!r} got {got!r}")
    print("verify=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
