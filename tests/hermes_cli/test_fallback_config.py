"""Tests for hermes_cli/fallback_config.py — fallback entry API-key resolution."""

from agent.secret_scope import reset_secret_scope, set_secret_scope
from hermes_cli.fallback_config import effective_runtime_provider, get_fallback_chain, resolve_entry_api_key
from hermes_cli.models import get_default_model_for_provider


class TestResolveEntryApiKey:
    def test_inline_api_key_wins(self, monkeypatch):
        monkeypatch.setenv("FB_KEY", "env-key")
        entry = {"provider": "custom", "api_key": "inline-key", "key_env": "FB_KEY"}
        assert resolve_entry_api_key(entry) == "inline-key"


    def test_no_key_fields_returns_none(self):
        assert resolve_entry_api_key({"provider": "openrouter", "model": "glm"}) is None


    def test_whitespace_inline_key_falls_through_to_env(self, monkeypatch):
        monkeypatch.setenv("FB_KEY", "env-key")
        entry = {"api_key": "   ", "key_env": "FB_KEY"}
        assert resolve_entry_api_key(entry) == "env-key"

    def test_key_env_resolves_from_active_secret_scope_not_raw_env(self, monkeypatch):
        # Multiplexed gateway: os.environ holds another profile's key, but the
        # active per-turn secret scope holds this profile's key. The scoped
        # value must win — a raw os.getenv() would leak the other profile's
        # credential (issue #74311).
        monkeypatch.setenv("FB_KEY", "fake-other-profile-key")
        token = set_secret_scope({"FB_KEY": "fake-active-profile-key"})
        try:
            assert resolve_entry_api_key({"key_env": "FB_KEY"}) == "fake-active-profile-key"
        finally:
            reset_secret_scope(token)

    def test_key_env_falls_back_to_env_when_no_active_scope(self, monkeypatch):
        # Non-multiplexed / single-profile behavior must be unchanged: with no
        # secret scope installed, resolution still reads os.environ.
        monkeypatch.setenv("FB_KEY", "env-key")
        assert resolve_entry_api_key({"key_env": "FB_KEY"}) == "env-key"


class TestEffectiveRuntimeProvider:
    """Named custom fallback entries must keep their configured identity (#98739)."""

    def test_named_custom_entry_keeps_configured_id(self):
        entry = {"provider": "my-custom-provider", "model": "some-model"}
        runtime = {"provider": "custom", "requested_provider": "my-custom-provider"}
        assert effective_runtime_provider(entry, runtime) == "my-custom-provider"

    def test_requested_provider_missing_falls_back_to_entry(self):
        entry = {"provider": "my-custom-provider", "model": "some-model"}
        runtime = {"provider": "custom"}
        assert effective_runtime_provider(entry, runtime) == "my-custom-provider"

    def test_builtin_provider_untouched(self):
        entry = {"provider": "openrouter", "model": "glm"}
        runtime = {"provider": "openrouter", "requested_provider": "openrouter"}
        assert effective_runtime_provider(entry, runtime) == "openrouter"

    def test_genuinely_bare_custom_stays_custom(self):
        # Ad-hoc endpoint: user literally configured provider: custom.
        entry = {"provider": "custom", "model": "some-model"}
        runtime = {"provider": "custom", "requested_provider": "custom"}
        assert effective_runtime_provider(entry, runtime) == "custom"

    def test_none_inputs_are_safe(self):
        assert effective_runtime_provider(None, None) == ""


class TestBareProviderStrings:
    def test_bare_string_expands_to_default_model(self):
        chain = get_fallback_chain({"fallback_providers": ["deepseek"]})
        assert len(chain) == 1
        assert chain[0]["provider"] == "deepseek"
        assert chain[0]["model"] == get_default_model_for_provider("deepseek")
        assert chain[0]["model"]  # non-empty

    def test_top_level_bare_string(self):
        chain = get_fallback_chain({"fallback_providers": "deepseek"})
        assert len(chain) == 1
        assert chain[0]["provider"] == "deepseek"

    def test_unknown_bare_provider_is_dropped(self):
        chain = get_fallback_chain({"fallback_providers": ["totally-unknown-xyz"]})
        assert chain == []

    def test_empty_string_is_dropped(self):
        chain = get_fallback_chain({"fallback_providers": ["", "  "]})
        assert chain == []


class TestDictEntries:
    def test_full_dict_passes_through(self):
        entry = {"provider": "deepseek", "model": "deepseek-v4-pro"}
        chain = get_fallback_chain({"fallback_providers": [entry]})
        assert chain == [entry]

    def test_dict_missing_model_gets_default(self):
        chain = get_fallback_chain({"fallback_providers": [{"provider": "deepseek"}]})
        assert len(chain) == 1
        assert chain[0]["provider"] == "deepseek"
        assert chain[0]["model"] == get_default_model_for_provider("deepseek")

    def test_dict_missing_provider_is_dropped(self):
        chain = get_fallback_chain({"fallback_providers": [{"model": "deepseek-v4-pro"}]})
        assert chain == []


class TestChainMerge:
    def test_fallback_providers_beat_legacy_fallback_model(self):
        chain = get_fallback_chain({
            "fallback_providers": [{"provider": "deepseek", "model": "deepseek-v4-pro"}],
            "fallback_model": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        })
        assert [e["provider"] for e in chain] == ["deepseek", "openrouter"]

    def test_duplicate_identity_is_deduped(self):
        entry = {"provider": "deepseek", "model": "deepseek-v4-pro"}
        chain = get_fallback_chain({
            "fallback_providers": [entry],
            "fallback_model": dict(entry),
        })
        assert len(chain) == 1

