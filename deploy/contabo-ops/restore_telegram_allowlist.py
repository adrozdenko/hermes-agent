#!/usr/bin/env python3
"""Restore Contabo Telegram DM allowlist for the owner user id."""
from __future__ import annotations

import re
import time
from pathlib import Path

USER_ID = "321340950"
CFG = Path("/opt/data/config.yaml")


def _as_list(val):
    if val is None:
        return []
    if isinstance(val, str):
        return [x.strip() for x in val.replace(";", ",").split(",") if x.strip()]
    if isinstance(val, (list, tuple, set)):
        return [str(x).strip() for x in val]
    return [str(val).strip()]


def ensure(container: dict, key: str) -> bool:
    items = _as_list(container.get(key))
    if USER_ID in items:
        return False
    items.append(USER_ID)
    container[key] = items
    return True


def main() -> int:
    if not CFG.exists():
        raise SystemExit(f"missing {CFG}")
    text = CFG.read_text(encoding="utf-8")
    bak = CFG.with_name(f"config.yaml.bak-allowlist-{int(time.time())}")
    bak.write_text(text, encoding="utf-8")
    print(f"backup={bak}")

    try:
        import yaml
    except ImportError as e:
        raise SystemExit(f"PyYAML required: {e}") from e

    data = yaml.safe_load(text) or {}
    changed = False

    platforms = data.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
        data["platforms"] = platforms

    tg = platforms.get("telegram")
    if not isinstance(tg, dict):
        tg = {}
        platforms["telegram"] = tg

    # Common shapes in hermes config
    targets = [tg]
    for nest in ("extra", "config", "settings"):
        node = tg.get(nest)
        if isinstance(node, dict):
            targets.append(node)
        elif nest == "extra":
            tg["extra"] = {}
            targets.append(tg["extra"])

    for node in targets:
        if ensure(node, "allow_from"):
            changed = True

    # Top-level / env-style mirrors sometimes used
    for key in ("telegram_allowed_users", "TELEGRAM_ALLOWED_USERS"):
        if key in data and ensure(data, key):
            changed = True

    if changed:
        CFG.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        print("patched=yes")
    else:
        print("patched=no (already present)")

    # Always refresh env override file for compose/stage2 consumers
    env_path = Path("/opt/data/secrets/telegram-allow.env")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(f"TELEGRAM_ALLOWED_USERS={USER_ID}\n", encoding="utf-8")
    print(f"env_override={env_path}")

    # Print relevant lines
    out = CFG.read_text(encoding="utf-8")
    for i, line in enumerate(out.splitlines(), 1):
        if re.search(r"telegram|allow_from|321340950|unauthorized", line, re.I):
            print(f"{i}:{line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
