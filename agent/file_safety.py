"""Shared file safety rules used by both tools and ACP shims."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _hermes_home_path() -> Path:
    """Resolve the active HERMES_HOME (profile-aware) without circular imports."""
    try:
        from hermes_constants import get_hermes_home  # local import to avoid cycles
        return get_hermes_home()
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def _hermes_root_path() -> Path:
    """Resolve the Hermes root dir (always the parent of any profile, never per-profile)."""
    try:
        from hermes_constants import get_default_hermes_root  # local import to avoid cycles
        return get_default_hermes_root()
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def build_write_denied_paths(home: str) -> set[str]:
    """Return exact sensitive paths that must never be written."""
    hermes_home = _hermes_home_path()
    hermes_root = _hermes_root_path()
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".ssh", "authorized_keys"),
            os.path.join(home, ".ssh", "id_rsa"),
            os.path.join(home, ".ssh", "id_ed25519"),
            os.path.join(home, ".ssh", "config"),
            # Active profile .env (or top-level .env when not in profile mode).
            str(hermes_home / ".env"),
            # Top-level .env, even when running under a profile — overwriting it
            # leaks credentials across every profile that inherits from root (#15981).
            str(hermes_root / ".env"),
            # Active profile Anthropic PKCE credential store.
            str(hermes_home / ".anthropic_oauth.json"),
            # Top-level Anthropic PKCE credential store remains sensitive even
            # when a profile is active; default/non-profile sessions still read it.
            str(hermes_root / ".anthropic_oauth.json"),
            os.path.join(home, ".bashrc"),
            os.path.join(home, ".zshrc"),
            os.path.join(home, ".profile"),
            os.path.join(home, ".bash_profile"),
            os.path.join(home, ".zprofile"),
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            os.path.join(home, ".git-credentials"),
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }


def build_write_denied_prefixes(home: str) -> list[str]:
    """Return sensitive directory prefixes that must never be written."""
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            "/etc/sudoers.d",
            "/etc/systemd",
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
            os.path.join(home, ".config", "gcloud"),
        ]
    ]


def get_safe_write_root() -> Optional[str]:
    """Return the resolved HERMES_WRITE_SAFE_ROOT path, or None if unset."""
    root = os.getenv("HERMES_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def is_write_denied(path: str) -> bool:
    """Return True if path is blocked by the write denylist or safe root."""
    home = os.path.realpath(os.path.expanduser("~"))
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    if resolved in build_write_denied_paths(home):
        return True
    for prefix in build_write_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    # Hermes control-plane files: block both the ACTIVE profile's view
    # (hermes_home) AND the global root view. Without the root pass, a
    # profile-mode session leaves <root>/auth.json + <root>/config.yaml
    # writable — letting a prompt-injected write_file overwrite the global
    # files that every profile inherits from (same shape as #15981).
    control_file_names = ("auth.json", "config.yaml", "webhook_subscriptions.json")
    mcp_tokens_dir_name = "mcp-tokens"

    hermes_dirs = []
    for base in (_hermes_home_path(), _hermes_root_path()):
        try:
            real = os.path.realpath(base)
            if real not in hermes_dirs:
                hermes_dirs.append(real)
        except Exception:
            continue

    for base_real in hermes_dirs:
        for name in control_file_names:
            try:
                if resolved == os.path.realpath(os.path.join(base_real, name)):
                    return True
            except Exception:
                continue
        try:
            mcp_real = os.path.realpath(os.path.join(base_real, mcp_tokens_dir_name))
            if resolved == mcp_real or resolved.startswith(mcp_real + os.sep):
                return True
        except Exception:
            pass
        try:
            pairing_real = os.path.realpath(os.path.join(base_real, "pairing"))
            if resolved == pairing_real or resolved.startswith(pairing_real + os.sep):
                return True
        except Exception:
            pass

    safe_root = get_safe_write_root()
    if safe_root and not (resolved == safe_root or resolved.startswith(safe_root + os.sep)):
        return True

    return False


# Common secret-bearing project-local environment file basenames.
# These are blocked because .env files routinely contain API keys,
# database passwords, and other credentials.
_BLOCKED_PROJECT_ENV_BASENAMES: set[str] = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".envrc",
}


def get_read_block_error(path: str) -> Optional[str]:
    """Return an error message when a read targets a denied Hermes path.

    Three categories are blocked:

      * Internal Hermes cache files under ``HERMES_HOME/skills/.hub`` —
        readable metadata that an attacker could use as a prompt-injection
        carrier.
      * Credential / secret stores under HERMES_HOME and the global Hermes
        root: ``auth.json``, ``auth.lock``, ``.anthropic_oauth.json``,
        ``.env``, ``webhook_subscriptions.json``, ``auth/google_oauth.json``,
        and anything under ``mcp-tokens/``. These hold plaintext provider keys,
        OAuth tokens, and HMAC secrets that the agent never needs to read
        directly — provider tools / gateway adapters consume them through
        internal channels.
      * Project-local environment files anywhere on disk: ``.env``,
        ``.env.local``, ``.env.development``, ``.env.production``,
        ``.env.test``, ``.env.staging``, ``.envrc``. These routinely hold
        API keys, database passwords, and other credentials for the user's
        own projects. The agent helping debug a project shouldn't normally
        need to read these — ``.env.example`` is the documented-shape
        substitute.

    **This is NOT a security boundary.** The terminal tool runs as the
    same OS user with shell access; the agent can still ``cat auth.json``
    or ``cat ~/.hermes/.env`` and exfiltrate the file. The read-deny exists
    as defense-in-depth that:

      * Returns a clear error to models that respect tool denials, which
        empirically prompts most modern models to stop rather than reach
        for the shell.
      * Surfaces a visible audit trail when something tries to read
        credentials — easier to spot in logs than a generic ``cat``.

    Treat any user-visible framing around this as "may help" rather than
    "stops attackers." A determined model or malicious instruction can
    always shell out.

    Callers that resolve relative paths against a non-process cwd
    (e.g. ``TERMINAL_CWD`` in ``tools/file_tools.py``) MUST pre-resolve
    and pass the absolute path string.  This function's own ``resolve()``
    is anchored at the Python process cwd, so a relative input like
    ``"auth.json"`` would otherwise miss the denylist when the task's
    terminal cwd differs from the process cwd.
    """
    resolved = Path(path).expanduser().resolve()

    # Resolve BOTH the active HERMES_HOME (profile-aware) AND the global
    # Hermes root so credential stores at <root>/auth.json etc. are also
    # blocked when running under a profile (HERMES_HOME points at
    # <root>/profiles/<name> in profile mode). Same shape as the write
    # deny widening (#15981, #14157).
    hermes_dirs: list[Path] = []
    for base in (_hermes_home_path(), _hermes_root_path()):
        try:
            real = base.resolve()
            if real not in hermes_dirs:
                hermes_dirs.append(real)
        except Exception:
            continue

    # Skills .hub: prompt-injection carriers.
    for hd in hermes_dirs:
        blocked_dirs = [
            hd / "skills" / ".hub" / "index-cache",
            hd / "skills" / ".hub",
        ]
        for blocked in blocked_dirs:
            try:
                resolved.relative_to(blocked)
            except ValueError:
                continue
            return (
                f"Access denied: {path} is an internal Hermes cache file "
                "and cannot be read directly to prevent prompt injection. "
                "Use the skills_list or skill_view tools instead."
            )

    # Credential / secret stores. Exact-file matches under either
    # HERMES_HOME or <root>.
    credential_file_names = (
        "auth.json",
        "auth.lock",
        ".anthropic_oauth.json",
        ".env",
        "webhook_subscriptions.json",
        os.path.join("auth", "google_oauth.json"),
        # Bitwarden Secrets Manager disk cache: stores plaintext secret values
        # to avoid re-fetching across back-to-back CLI invocations. The file
        # was introduced by #31968 but not added to this guard.
        os.path.join("cache", "bws_cache.json"),
    )
    for hd in hermes_dirs:
        for name in credential_file_names:
            try:
                blocked = (hd / name).resolve()
            except Exception:
                continue
            if resolved == blocked:
                return (
                    f"Access denied: {path} is a Hermes credential store "
                    "and cannot be read directly. Provider tools consume "
                    "these credentials through internal channels. "
                    "(Defense-in-depth — not a security boundary; the "
                    "terminal tool can still bypass.)"
                )

    # mcp-tokens/: directory prefix match — anything inside is OAuth
    # token material.
    for hd in hermes_dirs:
        try:
            mcp_tokens = (hd / "mcp-tokens").resolve()
        except Exception:
            continue
        if resolved == mcp_tokens:
            return (
                f"Access denied: {path} is the Hermes MCP token directory "
                "and cannot be read directly. (Defense-in-depth — not a "
                "security boundary; the terminal tool can still bypass.)"
            )
        try:
            resolved.relative_to(mcp_tokens)
        except ValueError:
            continue
        return (
            f"Access denied: {path} is a Hermes MCP token file "
            "and cannot be read directly. (Defense-in-depth — not a "
            "security boundary; the terminal tool can still bypass.)"
        )

    # Block common secret-bearing project-local .env files anywhere on disk.
    # The agent helping a user with their project rarely needs to read raw
    # .env contents — .env.example is the documented-shape substitute. The
    # terminal tool can still ``cat .env``; this is defense-in-depth, not a
    # boundary (see module docstring).
    if resolved.name in _BLOCKED_PROJECT_ENV_BASENAMES:
        return (
            f"Access denied: {path} is a secret-bearing environment file "
            "and cannot be read to prevent credential leakage. "
            "If you need to check the file structure, read .env.example instead. "
            "(Defense-in-depth — not a security boundary; the terminal tool can still bypass.)"
        )

    return None


# ---------------------------------------------------------------------------
# Per-tenant memory isolation (multi-tenant profiles)
#
# Some Hermes profiles serve MANY end users through a single agent (e.g. the
# Adelia Telegram persona). Each user has a private memory file under
# ``<HERMES_HOME>/<profile>_users/<chat_id>.md`` and a roster lives in
# ``<HERMES_HOME>/users.yaml``. Without a code-level gate, isolation between
# users is purely prompt-level: a user can talk the agent into
# ``read_file <profile>_users/<someone-else>.md`` (or grep the whole profile)
# and exfiltrate another user's data. Prompt rules ride the same channel the
# attacker controls, so they are advisory, not a boundary.
#
# The gate below makes authority come from the transport-authenticated
# ``HERMES_SESSION_CHAT_ID`` (a concurrency-safe ContextVar set by the
# gateway), NEVER from text in the conversation. A session may only touch its
# own user's memory file(s); reading another user's file, listing the per-user
# directory, or reading ``users.yaml`` is denied.
#
# Scope / safety:
#   * No-op when there is no authenticated chat_id (CLI, cron, tests).
#   * No-op for any profile WITHOUT a ``users.yaml`` registry — so every
#     single-tenant profile is completely unaffected (presence-driven).
#   * Admins (registry ``admin_ids``) bypass: they maintain the roster and do
#     technical support. This is the concrete consumer of ``is_admin_chat``.
#   * Unlike the read-deny above this CAN be a real boundary for a channel
#     with no shell/terminal tool (Adelia-over-Telegram is restricted to
#     browser/file/memory/send_message/skills/todo/vision). A channel that
#     grants a shell could still ``cat`` the file — there it is only
#     defense-in-depth, like ``get_read_block_error``.
# ---------------------------------------------------------------------------

# Sentinel: ``users.yaml`` exists on disk but does not parse / validate.
# Distinct from ``None`` (file absent → legitimately single-tenant). A broken
# registry must fail CLOSED — a security boundary that silently disappears on
# a YAML typo is exactly the kind of regression that goes unnoticed in prod.
_REGISTRY_BROKEN: Any = object()

# Cache the parsed users.yaml registry keyed by path, invalidated on mtime
# change. The registry is tiny but this guard runs on every file read/write.
_USER_REGISTRY_CACHE: dict[str, tuple[float, Any]] = {}


def _load_user_registry(hermes_home: Path) -> Any:
    """Parse ``<hermes_home>/users.yaml``.

    Returns one of three states (cached by path + mtime so the common
    per-read call is cheap):

      * a ``dict`` — a valid ``{users: [...]}`` registry,
      * ``_REGISTRY_BROKEN`` — the file EXISTS but is unparseable / wrong
        shape (callers must fail closed), or
      * ``None`` — the file is ABSENT (legitimately single-tenant; no-op).
    """
    try:
        registry_path = (hermes_home / "users.yaml").resolve()
    except Exception:
        return None
    key = str(registry_path)
    try:
        mtime = registry_path.stat().st_mtime
    except OSError:
        _USER_REGISTRY_CACHE.pop(key, None)
        return None  # absent
    cached = _USER_REGISTRY_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    # The file is present; anything short of a valid parse is BROKEN, not absent.
    data: Any = _REGISTRY_BROKEN
    try:
        import yaml  # local import: file_safety must stay import-light

        with open(registry_path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        if isinstance(loaded, dict) and isinstance(loaded.get("users"), list):
            data = loaded
    except Exception:
        data = _REGISTRY_BROKEN
    _USER_REGISTRY_CACHE[key] = (mtime, data)
    return data


def _safe_profile_token(value: object, fallback: str) -> str:
    """Return *value* only if it is a single safe path segment.

    The registry ``profile:`` field is admin-controlled but still interpolated
    into a directory name; reject anything with separators / ``..`` / NUL so a
    stray value cannot relocate the guarded directory (F8).
    """
    token = str(value) if value else ""
    if (
        not token
        or token in (".", "..")
        or "/" in token
        or "\\" in token
        or "\x00" in token
    ):
        return fallback
    return token


def _per_user_memory_dir(hermes_home: Path, registry: object) -> Path:
    """Directory holding per-user memory files, e.g. ``<home>/adelia_users``.

    Derived from the registry ``profile:`` field (``<profile>_users``),
    falling back to the HERMES_HOME directory name — both resolve to
    ``adelia_users`` for the Adelia profile. A broken registry falls back to
    the directory name.
    """
    raw = registry.get("profile") if isinstance(registry, dict) else None
    profile = _safe_profile_token(raw, hermes_home.name)
    return hermes_home / f"{profile}_users"


def is_admin_chat(
    chat_id: str,
    registry: Optional[dict] = None,
    hermes_home: Optional[Path] = None,
) -> bool:
    """Return True when *chat_id* is an authenticated admin for the profile.

    Authority is the registry ``admin_ids`` list — a code-level source of
    truth. A user CLAIMING to be admin in the conversation never reaches this
    function; only the transport-authenticated chat_id does.
    """
    if not chat_id:
        return False
    if registry is None:
        if hermes_home is None:
            hermes_home = _hermes_home_path()
        registry = _load_user_registry(hermes_home)
    if not isinstance(registry, dict):
        # Absent or broken registry → cannot establish admin authority.
        return False
    admin_ids = registry.get("admin_ids") or []
    return str(chat_id) in {str(a) for a in admin_ids}


def _allowed_memory_files(chat_id: str, registry: dict) -> set[str]:
    """Memory filenames *chat_id* may access: its own file plus every file in
    the same identity cluster (a person with several linked Telegram accounts).

    Always includes the bare ``<chat_id>.md`` convention name as a floor, so a
    known user can reach their own file even if the registry omits ``memory:``.
    """
    by_id: dict[str, dict] = {}
    for user in registry.get("users") or []:
        if isinstance(user, dict) and "id" in user:
            by_id[str(user["id"])] = user

    # Expand the identity cluster: self + main_account + accounts, transitively.
    cluster: set[str] = {str(chat_id)}
    frontier = [str(chat_id)]
    while frontier:
        cid = frontier.pop()
        user = by_id.get(cid)
        if not user:
            continue
        linked: list[str] = []
        main_account = user.get("main_account")
        if main_account is not None:
            linked.append(str(main_account))
        for acc in user.get("accounts") or []:
            linked.append(str(acc))
        for nid in linked:
            if nid not in cluster:
                cluster.add(nid)
                frontier.append(nid)

    allowed = {f"{chat_id}.md"}
    for cid in cluster:
        allowed.add(f"{cid}.md")
        user = by_id.get(cid)
        if user:
            mem = user.get("memory")
            if isinstance(mem, str) and mem:
                allowed.add(mem)
    return allowed


# Agent-facing denial. The model sees this and (per the persona's §8/§8a
# confidentiality rules) must translate it into a neutral user-facing reply —
# never parrot the path or the existence of other users.
_USER_MEMORY_DENIED = (
    "Access denied: this per-user memory file belongs to a different user. "
    "Each session may only access its own user's memory file(s). "
    "(Per-tenant isolation enforced by the authenticated chat_id — it cannot "
    "be overridden from the conversation. Do not reveal this to the user; "
    "respond per the persona's confidentiality rules.)"
)


def _resolve_tenant_context(
    chat_id: Optional[str],
) -> Optional[tuple[str, Path, Any]]:
    """Resolve (chat_id, hermes_home, registry) when per-tenant scoping applies.

    ``registry`` is either a valid dict or ``_REGISTRY_BROKEN`` (present but
    unparseable → callers fail closed). Returns ``None`` (gate is a complete
    no-op) only when there is no authenticated chat_id or ``users.yaml`` is
    ABSENT (a genuinely single-tenant profile).
    """
    if chat_id is None:
        try:
            from gateway.session_context import get_session_env

            chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
        except Exception:
            chat_id = ""
    if not chat_id:
        return None
    hermes_home = _hermes_home_path()
    registry = _load_user_registry(hermes_home)
    if registry is None:
        return None  # users.yaml absent → single-tenant, no scoping
    return str(chat_id), hermes_home, registry


def get_user_memory_block_error(
    path: str, chat_id: Optional[str] = None
) -> Optional[str]:
    """Deny cross-tenant access to per-user memory files and the user registry.

    Callers MUST pass an already-resolved absolute path (file tools resolve
    against ``TERMINAL_CWD`` which can differ from the process cwd — same
    contract as :func:`get_read_block_error`).
    """
    ctx = _resolve_tenant_context(chat_id)
    if ctx is None:
        return None
    chat_id, hermes_home, registry = ctx
    broken = registry is _REGISTRY_BROKEN

    # Authenticated admins maintain the roster / do support — full access.
    # A broken registry cannot establish admin authority, so NO bypass: the
    # boundary holds for everyone until the registry is repaired (fail closed).
    if not broken and is_admin_chat(chat_id, registry=registry):
        return None

    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return None

    # The roster itself enumerates every user — non-admins must not read it.
    try:
        if resolved == (hermes_home / "users.yaml").resolve():
            return _USER_MEMORY_DENIED
    except Exception:
        pass

    try:
        per_user_dir = _per_user_memory_dir(hermes_home, registry).resolve()
    except Exception:
        # Cannot locate the guarded dir on a broken registry → deny the whole
        # per-user area conservatively is impossible without it, so fall back
        # to the home-derived default which _per_user_memory_dir already uses.
        return _USER_MEMORY_DENIED if broken else None

    # The directory itself (a listing would enumerate other users).
    if resolved == per_user_dir:
        return _USER_MEMORY_DENIED
    # Outside the per-user directory → not our concern.
    try:
        resolved.relative_to(per_user_dir)
    except ValueError:
        return None
    # Inside but nested below the flat dir → no legitimate files there.
    if resolved.parent != per_user_dir:
        return _USER_MEMORY_DENIED

    # Broken registry: cannot resolve the caller's allowed set → deny all
    # per-user files (fail closed) until the roster is repaired.
    if broken:
        return _USER_MEMORY_DENIED

    if resolved.name in _allowed_memory_files(chat_id, registry):
        return None
    return _USER_MEMORY_DENIED


def get_user_search_block_error(
    search_root: str, chat_id: Optional[str] = None
) -> Optional[str]:
    """Deny tree searches by a scoped caller that could scan other users'
    memory files or the registry.

    A content/file search walks ``search_root`` recursively. If that subtree
    contains the per-user memory directory or the registry (e.g. a search
    rooted at the profile home), a scoped non-admin caller could enumerate
    other tenants — so the search is refused. Searches unrelated to the
    per-user area are unaffected; admin / CLI / cron are unaffected.
    """
    ctx = _resolve_tenant_context(chat_id)
    if ctx is None:
        return None
    chat_id, hermes_home, registry = ctx
    broken = registry is _REGISTRY_BROKEN
    if not broken and is_admin_chat(chat_id, registry=registry):
        return None

    try:
        root = Path(search_root).expanduser().resolve()
    except Exception:
        return None
    try:
        per_user_dir = _per_user_memory_dir(hermes_home, registry).resolve()
        registry_file = (hermes_home / "users.yaml").resolve()
    except Exception:
        return None

    def _within(inner: Path, outer: Path) -> bool:
        if inner == outer:
            return True
        try:
            inner.relative_to(outer)
            return True
        except ValueError:
            return False

    # Deny when the search would descend into the per-user dir (root is an
    # ancestor of, or equal to, it), when the search is rooted inside it, or
    # when it would reach the registry file.
    if (
        _within(per_user_dir, root)
        or _within(root, per_user_dir)
        or _within(registry_file, root)
    ):
        return _USER_MEMORY_DENIED
    return None


# ---------------------------------------------------------------------------
# Cross-profile write guard (#TBD)
#
# Hermes profiles are separate HERMES_HOME dirs under
# ``<root>/profiles/<name>/``. Each profile has its own skills/, plugins/,
# cron/, memories/. When an agent runs under one profile, writing into
# ANOTHER profile's directories is almost always wrong — those skills /
# plugins / cron jobs / memories affect a different session the user runs
# from a different shell.
#
# Soft guard, NOT a security boundary: the agent runs as the same OS user
# and has unrestricted terminal access, so this returns a warning the model
# can choose to honor or override with ``cross_profile=True``. Same shape
# as the dangerous-command approval flow — the agent is told the boundary
# exists, and explicit user direction is required to cross it.
#
# Reference: May 2026 incident where a hermes-security profile session
# edited skills under both ``~/.hermes/profiles/hermes-security/skills/``
# AND ``~/.hermes/skills/`` (the default profile's skills) without realizing
# the second path belonged to a different profile.
# ---------------------------------------------------------------------------

# Profile-scoped directories under HERMES_HOME / <root> / <root>/profiles/<X>/
# that should be guarded. Adding a new area here extends the guard with no
# other code change.
PROFILE_SCOPED_AREAS = ("skills", "plugins", "cron", "memories")


def _resolve_active_profile_name() -> str:
    """Return the active profile name derived from HERMES_HOME.

    ``~/.hermes``              -> ``"default"``
    ``~/.hermes/profiles/X``  -> ``"X"``

    Falls back to ``"default"`` on any resolution failure so the guard
    never raises into the tool path.
    """
    try:
        home_real = _hermes_home_path().resolve()
        root_real = _hermes_root_path().resolve()
    except (OSError, RuntimeError):
        return "default"
    profiles_dir = root_real / "profiles"
    try:
        rel = home_real.relative_to(profiles_dir)
        parts = rel.parts
        if len(parts) >= 1:
            return parts[0]
    except ValueError:
        pass
    return "default"


def classify_cross_profile_target(path: str) -> Optional[dict]:
    """Classify a write target as cross-profile if it lands in another
    profile's scoped area (skills/plugins/cron/memories).

    Returns ``None`` when the target is outside Hermes scope, or is inside
    the ACTIVE profile, or doesn't hit a profile-scoped area. Otherwise
    returns a dict with:

      * ``active_profile``: name of the profile the agent is running as
      * ``target_profile``: name of the profile the path belongs to
      * ``area``: which scoped area (``"skills"``, ``"plugins"``, etc.)
      * ``target_path``: the resolved path string

    The caller decides what to do with the result — surface a warning to
    the model, prompt the user, or (with explicit consent /
    ``cross_profile=True``) proceed anyway.
    """
    try:
        target = Path(os.path.expanduser(str(path))).resolve()
        root_real = _hermes_root_path().resolve()
    except (OSError, RuntimeError):
        return None

    target_profile: Optional[str] = None
    area: Optional[str] = None

    try:
        rel = target.relative_to(root_real)
    except ValueError:
        return None

    parts = rel.parts
    if not parts:
        return None

    if parts[0] in PROFILE_SCOPED_AREAS:
        # ``<root>/<area>/...`` → default profile.
        target_profile = "default"
        area = parts[0]
    elif (
        parts[0] == "profiles"
        and len(parts) >= 3
        and parts[2] in PROFILE_SCOPED_AREAS
    ):
        # ``<root>/profiles/<name>/<area>/...`` → named profile.
        target_profile = parts[1]
        area = parts[2]
    else:
        return None

    active_profile = _resolve_active_profile_name()
    if target_profile == active_profile:
        # In-profile write — not a cross-profile event.
        return None

    return {
        "active_profile": active_profile,
        "target_profile": target_profile,
        "area": area,
        "target_path": str(target),
    }


def get_cross_profile_warning(path: str) -> Optional[str]:
    """Return a model-facing warning string when ``path`` is cross-profile.

    Returns ``None`` when the write is in-scope (same profile) or outside
    Hermes entirely. Caller is expected to surface the warning to the
    agent as a tool-result error, NOT to silently allow the write — the
    agent must either get explicit user direction to proceed, or pass
    ``cross_profile=True`` to its write tool.

    This is defense-in-depth: the terminal tool runs as the same OS user
    and can write any of these paths without going through this guard.
    Treat the guard as a confusion-reducer, not a security boundary.
    """
    info = classify_cross_profile_target(path)
    if info is None:
        return None
    return (
        f"Cross-profile write blocked by soft guard: {info['target_path']} "
        f"belongs to Hermes profile {info['target_profile']!r}, but the "
        f"agent is running under profile {info['active_profile']!r}. "
        f"Editing another profile's {info['area']}/ will affect that "
        f"profile's future sessions, not the one you are currently in. "
        f"Confirm with the user before proceeding. To bypass this guard "
        f"after explicit user direction, retry the call with "
        f"``cross_profile=True``. (Defense-in-depth — not a security "
        f"boundary; the terminal tool can still bypass.)"
    )
