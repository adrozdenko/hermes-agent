#!/usr/bin/env python3
"""Append standing Contabo capability awareness into agent memory files.

Idempotent: a unique marker header prevents duplicate notes on re-runs.
Does NOT create cron jobs or enable webhook routes — memory-only so the
agent can offer those features when Andrii has a recurring or external-
trigger need.

DATA_ROOT defaults to /opt/data (inside hermes container). On the host
volume, set DATA_ROOT=/opt/hermes/data.
"""
from __future__ import annotations

import os
from pathlib import Path

MARKER = "<!-- contabo-capability-awareness:v1 -->"

NOTE = """<!-- contabo-capability-awareness:v1 -->
## Contabo capabilities (offer when useful, do not nag)

Telegram cold-boot keeps offline messages (`drop_pending_on_cold_boot=false`).
Cron can schedule digests/jobs to Telegram. Webhook ingress can accept external
HTTPS events and deliver into chat with session mirror. Only propose cron/webhook
when Andrii has a recurring or external-trigger need; he uses Grok-only Telegram
here.
"""

PREFERRED_PROFILES = ("default", "alfred")


def _candidates(root: Path) -> list[Path]:
    ordered: list[Path] = [
        root / "MEMORY.md",
        root / "memory" / "MEMORY.md",
        root / "SOUL.md",
    ]
    profiles = root / "profiles"
    if profiles.is_dir():
        for name in PREFERRED_PROFILES:
            ordered.append(profiles / name / "MEMORY.md")
            ordered.append(profiles / name / "SOUL.md")
        # Any other profile MEMORY.md (stable sort) after preferred
        extras = sorted(
            p
            for p in profiles.glob("*/MEMORY.md")
            if p.parent.name not in PREFERRED_PROFILES
            and not p.parent.name.startswith((".", "_"))
        )
        ordered.extend(extras)
    return ordered


def _append_if_needed(path: Path) -> str:
    if not path.is_file():
        return "skip-missing"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return "skip-present"
    # Ensure a blank line before the note when file is non-empty
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + NOTE.rstrip() + "\n", encoding="utf-8")
    return "appended"


def main() -> int:
    root = Path(os.environ.get("DATA_ROOT", "/opt/data")).resolve()
    if not root.is_dir():
        raise SystemExit(f"DATA_ROOT missing or not a dir: {root}")

    print(f"data_root={root}")
    candidates = _candidates(root)
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        print("no memory/soul files found; nothing written")
        print("looked:")
        for p in candidates:
            print(f"  - {p}")
        return 0

    touched = 0
    for path in existing:
        # Prefer writing into the first present preferred path, then also
        # stamp profile MEMORY files that exist so default+alfred both know.
        status = _append_if_needed(path)
        print(f"{status}\t{path}")
        if status == "appended":
            touched += 1

    print(f"touched={touched}")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
