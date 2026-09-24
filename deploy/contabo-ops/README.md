# Contabo Hermes ops (skybot-prod)

Standing runbook for the shared Hermes gateway on Contabo (`root@5.189.159.195`).
Volume data lives at `/opt/hermes/data` (never wipe on image update).

## Hard pins (do not regress)

1. **Container CMD must be `sleep infinity`** so **s6** owns `gateway-default` /
   `claude-proxy`. Image CMD `gateway run` fights s6 and restart-loops (~20s),
   which flaps `:11435` and trips fleet-watchdog.
   - Overlay file: [`docker-compose.contabo-cmd.yml`](../../docker-compose.contabo-cmd.yml)
   - Host copies: `/opt/hermes/docker-compose.contabo-cmd.yml` and
     `/opt/hermes/app/docker-compose.contabo-cmd.yml`
   - Deploy layers that overlay before `docker compose up -d`
   - Install without rebuild: workflow **Install Contabo compose overlay**

2. **Profile tombstones** — dirs under `profiles/` whose names start with `.` or
   `_` must not abort boot reconcile. Upstream rejects dotted profile names;
   a leftover `.jam-migrated-20260713` (has `SOUL.md`) caused
   `02-reconcile-profiles` `ValueError` and kept the gateway dead.
   - Code: soft-skip in `hermes_cli/container_boot.py`
   - Live volume: quarantine under `profiles/_quarantine/` (do not delete
     without operator OK)

3. **Allowlist survives seed** — Contabo `git reset --hard` + boot-seed
   `config.yaml` can clear Telegram DM auth. Owner Telegram user id
   `321340950` must remain in `platforms.telegram.allow_from` and
   `TELEGRAM_ALLOWED_USERS`. Workflow **Fix Contabo Telegram allowlist**
   patches without rebuild.

## Incident 2026-09-24 (summary)

| Symptom | Cause | Fix |
|---|---|---|
| fleet-watchdog `:11435` FAIL; Telegram shutdown notices | Profile tombstone aborted reconcile; Docker CMD fought s6 | Quarantine `.jam-migrated-*`; recreate with CMD sleep infinity; pin overlay |
| Telegram DM unread / no reply after recover | Mid-window `Blocked unauthorized user 321340950` while auth settled; dual telegram adapters `--replace` handoffs | Confirm allowlist; kick/restart `gateway-default`; operator re-ping |
| hermes-dashboard flap | Same image `/init` + claude-proxy seed on shared volume | Leave dashboard stopped (`restart=no`) until role-gate is clean |

Volume `/opt/hermes/data` was **not** wiped.

## Manual workflows (Actions → workflow_dispatch)

| Workflow | Purpose |
|---|---|
| Diagnose Contabo (read-only) | Containers, proxy health, s6 services — no mutate |
| Recover Contabo (restart loop) | Quarantine tombstone + recreate gateway CMD sleep infinity |
| Kick Contabo Telegram gateways | Restart s6 gateways; stop flapping dashboard |
| Fix Contabo Telegram allowlist | Restore owner `allow_from` / env |
| Probe Contabo Telegram auth | Allowlist + pairing + recent connect/block lines |
| Install Contabo compose overlay | scp CMD overlay to host without image rebuild |

## Deploy to Contabo

- Trigger: push to `main` (path-filtered) or manual dispatch.
- **Disable** this workflow during an active outage so ops-only pushes do not
  rebuild (16+ min + UID chown). Re-enable after Telegram reply confirmed.
- Docs / contabo-ops / diagnose-recover-kick-probe workflows are
  `paths-ignore` so they do not start a Contabo rebuild.

## Operator checklist (bots down)

1. Run **Diagnose Contabo** — is `hermes` healthy? Is `:11435` OK inside hermes?
2. If restart loop / bad CMD → **Recover Contabo**.
3. If healthy but Telegram silent → **Probe** then **Kick**; if unauthorized
   block for owner → **Fix allowlist**.
4. Confirm with a fresh Telegram ping (old messages during the outage window
   may stay unread forever).
5. Keep dashboard stopped if it flaps the gateway.
