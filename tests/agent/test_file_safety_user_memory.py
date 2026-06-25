"""Tests for per-tenant memory isolation in agent/file_safety.

Multi-tenant profiles (e.g. the Adelia Telegram persona) serve many end users
through one agent, each with a private memory file under
``<HERMES_HOME>/<profile>_users/<chat_id>.md`` and a roster in
``<HERMES_HOME>/users.yaml``. Without a code-level gate, isolation is purely
prompt-level: a user can talk the agent into reading another user's file.

These tests pin the behaviour of ``get_user_memory_block_error`` /
``get_user_search_block_error`` / ``is_admin_chat``: authority comes from the
authenticated chat_id, the gate is a no-op for single-tenant profiles and for
CLI/cron (no chat_id), and admins bypass.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import agent.file_safety as fs


# Mirrors the real Adelia users.yaml shape: an admin, a primary user with a
# linked secondary account, and two unrelated users.
_REGISTRY = textwrap.dedent(
    """
    profile: adelia
    admin_ids: [321340950]
    primary: 5031992654

    users:
      - id: 321340950
        name: Admin
        role: admin
        memory: false
      - id: 5031992654
        name: Roman
        role: user
        accounts: [7612463108]
        memory: 5031992654.md
      - id: 7612463108
        name: Roman
        role: user
        main_account: 5031992654
        memory: 7612463108.md
      - id: 5718800368
        name: Svitlana
        role: user
        memory: 5718800368.md
      - id: 8198503795
        name: Taisiia
        role: user
        memory: 8198503795.md
    """
).strip()


@pytest.fixture
def adelia_home(tmp_path, monkeypatch):
    """Build a fake Adelia profile home and point file_safety at it."""
    home = tmp_path / "profiles" / "adelia"
    (home / "adelia_users").mkdir(parents=True)
    (home / "users.yaml").write_text(_REGISTRY, encoding="utf-8")
    for uid in ("5031992654", "7612463108", "5718800368", "8198503795"):
        (home / "adelia_users" / f"{uid}.md").write_text(f"# {uid}\n", encoding="utf-8")
    (home / "SOUL.md").write_text("# persona\n", encoding="utf-8")

    monkeypatch.setattr(fs, "_hermes_home_path", lambda: home)
    # Avoid cross-test cache bleed.
    fs._USER_REGISTRY_CACHE.clear()
    # Ensure no ambient session chat_id leaks in from the environment.
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID", raising=False)
    return home


def _f(home: Path, uid: str) -> str:
    return str(home / "adelia_users" / f"{uid}.md")


# ---------------------------------------------------------------------------
# Core read/write isolation
# ---------------------------------------------------------------------------


def test_owner_reads_own_file_allowed(adelia_home):
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5718800368"), chat_id="5718800368")
        is None
    )


def test_non_owner_reads_other_file_denied(adelia_home):
    err = fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="5718800368")
    assert err is not None
    assert "different user" in err


def test_secondary_account_reads_main_file_allowed(adelia_home):
    # 7612463108 is Roman's secondary; main memory lives in 5031992654.md.
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="7612463108")
        is None
    )


def test_main_account_reads_secondary_file_allowed(adelia_home):
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "7612463108"), chat_id="5031992654")
        is None
    )


def test_registry_read_denied_for_non_admin(adelia_home):
    err = fs.get_user_memory_block_error(str(adelia_home / "users.yaml"), chat_id="5718800368")
    assert err is not None


def test_directory_listing_denied(adelia_home):
    err = fs.get_user_memory_block_error(str(adelia_home / "adelia_users"), chat_id="5718800368")
    assert err is not None


def test_traversal_into_other_file_denied(adelia_home):
    # adelia_users/../adelia_users/5031992654.md resolves back inside the dir.
    sneaky = str(adelia_home / "adelia_users" / ".." / "adelia_users" / "5031992654.md")
    err = fs.get_user_memory_block_error(sneaky, chat_id="5718800368")
    assert err is not None


def test_non_memory_path_allowed(adelia_home):
    # SOUL.md is under HERMES_HOME but not a per-user file — not scoped here.
    assert fs.get_user_memory_block_error(str(adelia_home / "SOUL.md"), chat_id="5718800368") is None


# ---------------------------------------------------------------------------
# Admin bypass + unauthenticated / single-tenant no-ops
# ---------------------------------------------------------------------------


def test_admin_reads_any_file_allowed(adelia_home):
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5718800368"), chat_id="321340950")
        is None
    )
    assert fs.get_user_memory_block_error(str(adelia_home / "users.yaml"), chat_id="321340950") is None


def test_empty_chat_id_is_noop(adelia_home):
    # CLI / cron: no authenticated tenant → no scoping at all.
    assert fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="") is None


def test_no_registry_is_noop(tmp_path, monkeypatch):
    # A single-tenant profile (no users.yaml) is completely unaffected.
    home = tmp_path / "profiles" / "coder"
    (home / "adelia_users").mkdir(parents=True)
    target = home / "adelia_users" / "5031992654.md"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: home)
    fs._USER_REGISTRY_CACHE.clear()
    assert fs.get_user_memory_block_error(str(target), chat_id="9999") is None


def test_unknown_user_can_still_reach_own_convention_file(adelia_home):
    # A chat_id not yet in the registry (brand-new user) may reach its own
    # <id>.md (onboarding writes there) but nothing else.
    assert fs.get_user_memory_block_error(_f(adelia_home, "424242"), chat_id="424242") is None
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="424242")
        is not None
    )


# ---------------------------------------------------------------------------
# is_admin_chat
# ---------------------------------------------------------------------------


def test_is_admin_chat(adelia_home):
    assert fs.is_admin_chat("321340950") is True
    assert fs.is_admin_chat("5031992654") is False
    assert fs.is_admin_chat("") is False


def test_is_admin_chat_no_registry(tmp_path, monkeypatch):
    home = tmp_path / "plain"
    home.mkdir()
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: home)
    fs._USER_REGISTRY_CACHE.clear()
    assert fs.is_admin_chat("321340950") is False


# ---------------------------------------------------------------------------
# Session-context resolution (chat_id pulled from HERMES_SESSION_CHAT_ID)
# ---------------------------------------------------------------------------


def test_chat_id_resolved_from_session_env(adelia_home, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "5718800368")
    # No explicit chat_id → resolved from the session env / ContextVar.
    assert fs.get_user_memory_block_error(_f(adelia_home, "5031992654")) is not None
    assert fs.get_user_memory_block_error(_f(adelia_home, "5718800368")) is None


# ---------------------------------------------------------------------------
# Search isolation
# ---------------------------------------------------------------------------


def test_search_at_profile_root_denied_for_scoped_user(adelia_home):
    err = fs.get_user_search_block_error(str(adelia_home), chat_id="5718800368")
    assert err is not None


def test_search_inside_per_user_dir_denied(adelia_home):
    err = fs.get_user_search_block_error(str(adelia_home / "adelia_users"), chat_id="5718800368")
    assert err is not None


def test_search_admin_allowed(adelia_home):
    assert fs.get_user_search_block_error(str(adelia_home), chat_id="321340950") is None


def test_search_cli_allowed(adelia_home):
    assert fs.get_user_search_block_error(str(adelia_home), chat_id="") is None


def test_search_unrelated_dir_allowed(adelia_home, tmp_path):
    other = tmp_path / "some-project"
    other.mkdir()
    assert fs.get_user_search_block_error(str(other), chat_id="5718800368") is None


# ---------------------------------------------------------------------------
# F1 — a PRESENT but broken/mis-shaped registry must fail CLOSED
# ---------------------------------------------------------------------------


def _rewrite_registry(home: Path, text: str) -> None:
    (home / "users.yaml").write_text(text, encoding="utf-8")
    fs._USER_REGISTRY_CACHE.clear()


def test_malformed_yaml_fails_closed(adelia_home):
    _rewrite_registry(adelia_home, "users: [ {id: 1,\n  broken")  # invalid YAML
    # Cross-tenant read must be denied even though the registry won't parse.
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="5718800368")
        is not None
    )


def test_wrong_shape_registry_fails_closed(adelia_home):
    _rewrite_registry(adelia_home, "users: {}\nadmin_ids: [321340950]\n")  # users not a list
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="5718800368")
        is not None
    )


def test_broken_registry_denies_even_admin(adelia_home):
    # A broken registry cannot establish admin authority → no bypass.
    _rewrite_registry(adelia_home, ":\n  not: valid: yaml")
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5718800368"), chat_id="321340950")
        is not None
    )
    assert fs.is_admin_chat("321340950") is False


def test_broken_registry_blocks_search(adelia_home):
    _rewrite_registry(adelia_home, "}{ not yaml")
    assert fs.get_user_search_block_error(str(adelia_home), chat_id="321340950") is not None


def test_broken_registry_still_allows_cli(adelia_home):
    # No authenticated chat_id → still a no-op even when the registry is broken.
    _rewrite_registry(adelia_home, "garbage: [")
    assert fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="") is None


# ---------------------------------------------------------------------------
# F8 — a malicious/relocating `profile:` value must not disable the guard
# ---------------------------------------------------------------------------


def test_malicious_profile_token_does_not_relocate_guard(adelia_home):
    # profile: ../somewhere would, unsanitized, move the guarded dir away from
    # adelia_users. The sanitizer falls back to the home dir name so the real
    # per-user dir stays guarded.
    _rewrite_registry(
        adelia_home,
        "profile: ../escape\nadmin_ids: [321340950]\nusers:\n"
        "  - id: 5718800368\n    memory: 5718800368.md\n"
        "  - id: 5031992654\n    memory: 5031992654.md\n",
    )
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5031992654"), chat_id="5718800368")
        is not None
    )
    # Owner still reaches their own file.
    assert (
        fs.get_user_memory_block_error(_f(adelia_home, "5718800368"), chat_id="5718800368")
        is None
    )
