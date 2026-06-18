"""Tests for watchlist.py: pure list-manipulation logic, independent of the
real CookieController (which needs a live browser round-trip and isn't
exercised here)."""

import json

import pytest
import streamlit as st

import watchlist


class FakeCookieController:
    """In-memory stand-in for streamlit_cookies_controller.CookieController."""

    def __init__(self, initial_value=None, raise_on_set=False):
        self._store = {}
        if initial_value is not None:
            self._store[watchlist.COOKIE_NAME] = initial_value
        self.raise_on_set = raise_on_set
        self.set_calls = []

    def get(self, name):
        return self._store.get(name)

    def set(self, name, value, **kwargs):
        if self.raise_on_set:
            raise RuntimeError("cookies blocked")
        self.set_calls.append((name, value))
        self._store[name] = value


@pytest.fixture(autouse=True)
def clear_session_state():
    if "watchlist" in st.session_state:
        del st.session_state["watchlist"]
    yield
    if "watchlist" in st.session_state:
        del st.session_state["watchlist"]


class TestDefaultWatchlist:

    def test_has_fifty_entries(self):
        assert len(watchlist.DEFAULT_WATCHLIST) == 50

    def test_tickers_are_unique(self):
        tickers = [t for t, _ in watchlist.DEFAULT_WATCHLIST]
        assert len(tickers) == len(set(tickers))

    def test_etfs_are_listed_before_individual_stocks(self):
        """SPY (an ETF) must come before AAPL (a stock) per the requested ordering."""
        tickers = [t for t, _ in watchlist.DEFAULT_WATCHLIST]
        assert tickers.index("SPY") < tickers.index("AAPL")

    def test_includes_germany_representation(self):
        tickers = {t for t, _ in watchlist.DEFAULT_WATCHLIST}
        assert "DAX" in tickers
        assert "SAP" in tickers


class TestEnsureLoaded:

    def test_falls_back_to_default_when_no_cookie(self):
        controller = FakeCookieController(initial_value=None)
        watchlist.ensure_loaded(controller)
        assert len(st.session_state["watchlist"]) == 50
        assert st.session_state["watchlist"][0]["ticker"] == "SPY"

    def test_parses_existing_cookie(self):
        saved = json.dumps([{"ticker": "AAPL", "name": "Apple Inc."}])
        controller = FakeCookieController(initial_value=saved)
        watchlist.ensure_loaded(controller)
        assert st.session_state["watchlist"] == [{"ticker": "AAPL", "name": "Apple Inc."}]

    def test_falls_back_to_default_on_malformed_cookie(self):
        controller = FakeCookieController(initial_value="not json")
        watchlist.ensure_loaded(controller)
        assert len(st.session_state["watchlist"]) == 50

    def test_falls_back_to_default_when_cookie_is_not_a_list(self):
        controller = FakeCookieController(initial_value=json.dumps({"oops": True}))
        watchlist.ensure_loaded(controller)
        assert len(st.session_state["watchlist"]) == 50

    def test_skips_malformed_entries_within_an_otherwise_valid_list(self):
        saved = json.dumps([{"ticker": "AAPL", "name": "Apple Inc."}, {"ticker": "BAD"}, "garbage"])
        controller = FakeCookieController(initial_value=saved)
        watchlist.ensure_loaded(controller)
        assert st.session_state["watchlist"] == [{"ticker": "AAPL", "name": "Apple Inc."}]

    def test_is_idempotent_within_a_session(self):
        """Once loaded, a second call must not re-read the cookie (avoids clobbering
        in-session mutations with stale cookie data)."""
        controller = FakeCookieController(initial_value=None)
        watchlist.ensure_loaded(controller)
        st.session_state["watchlist"].append({"ticker": "ZZZZ", "name": "Test Co."})
        watchlist.ensure_loaded(controller)
        assert any(e["ticker"] == "ZZZZ" for e in st.session_state["watchlist"])


class TestIsInWatchlist:

    def test_true_when_present(self):
        st.session_state["watchlist"] = [{"ticker": "AAPL", "name": "Apple Inc."}]
        assert watchlist.is_in_watchlist("AAPL") is True

    def test_false_when_absent(self):
        st.session_state["watchlist"] = [{"ticker": "AAPL", "name": "Apple Inc."}]
        assert watchlist.is_in_watchlist("MSFT") is False

    def test_false_when_watchlist_not_yet_loaded(self):
        assert watchlist.is_in_watchlist("AAPL") is False


class TestAddRemoveToggle:
    """Uses ZZZZ, a ticker that isn't in DEFAULT_WATCHLIST, so these generic
    tests aren't coupled to the default list's contents/ordering. Default-list
    restoration behavior has its own test class below."""

    def test_add_appends_entry(self):
        st.session_state["watchlist"] = []
        controller = FakeCookieController()
        watchlist.add(controller, "ZZZZ", "Test Co.")
        assert st.session_state["watchlist"] == [{"ticker": "ZZZZ", "name": "Test Co."}]

    def test_add_persists_to_cookie(self):
        st.session_state["watchlist"] = []
        controller = FakeCookieController()
        watchlist.add(controller, "ZZZZ", "Test Co.")
        assert len(controller.set_calls) == 1
        name, value = controller.set_calls[0]
        assert name == watchlist.COOKIE_NAME
        assert json.loads(value) == [{"ticker": "ZZZZ", "name": "Test Co."}]

    def test_add_is_idempotent_no_duplicate(self):
        st.session_state["watchlist"] = [{"ticker": "ZZZZ", "name": "Test Co."}]
        controller = FakeCookieController()
        watchlist.add(controller, "ZZZZ", "Test Co.")
        assert st.session_state["watchlist"] == [{"ticker": "ZZZZ", "name": "Test Co."}]
        assert controller.set_calls == []

    def test_add_falls_back_to_ticker_when_name_missing(self):
        st.session_state["watchlist"] = []
        controller = FakeCookieController()
        watchlist.add(controller, "ZZZZ", "")
        assert st.session_state["watchlist"] == [{"ticker": "ZZZZ", "name": "ZZZZ"}]

    def test_remove_deletes_entry(self):
        st.session_state["watchlist"] = [{"ticker": "ZZZZ", "name": "Test Co."}, {"ticker": "YYYY", "name": "Other Co."}]
        controller = FakeCookieController()
        watchlist.remove(controller, "ZZZZ")
        assert st.session_state["watchlist"] == [{"ticker": "YYYY", "name": "Other Co."}]

    def test_remove_nonexistent_is_noop(self):
        st.session_state["watchlist"] = [{"ticker": "ZZZZ", "name": "Test Co."}]
        controller = FakeCookieController()
        watchlist.remove(controller, "WWWW")
        assert st.session_state["watchlist"] == [{"ticker": "ZZZZ", "name": "Test Co."}]

    def test_toggle_adds_when_absent(self):
        st.session_state["watchlist"] = []
        controller = FakeCookieController()
        watchlist.toggle(controller, "ZZZZ", "Test Co.")
        assert watchlist.is_in_watchlist("ZZZZ") is True

    def test_toggle_removes_when_present(self):
        st.session_state["watchlist"] = [{"ticker": "ZZZZ", "name": "Test Co."}]
        controller = FakeCookieController()
        watchlist.toggle(controller, "ZZZZ", "Test Co.")
        assert watchlist.is_in_watchlist("ZZZZ") is False

    def test_persist_swallows_cookie_set_exception(self):
        """A browser blocking cookies must degrade to session-only, not crash."""
        st.session_state["watchlist"] = []
        controller = FakeCookieController(raise_on_set=True)
        watchlist.add(controller, "ZZZZ", "Test Co.")
        assert st.session_state["watchlist"] == [{"ticker": "ZZZZ", "name": "Test Co."}]


class TestDefaultTickerRestoration:
    """Removing then re-adding a default ticker (e.g. via the star toggle) must
    snap it back to its canonical name/icon and original slot, not append it
    at the end with whatever name happened to be passed in."""

    def test_readded_default_ticker_uses_canonical_name_not_passed_in_name(self):
        st.session_state["watchlist"] = []
        controller = FakeCookieController()
        watchlist.add(controller, "AAPL", "some other display name")
        assert st.session_state["watchlist"] == [{"ticker": "AAPL", "name": "🇺🇸 Apple Inc."}]

    def test_readded_default_ticker_restored_to_original_slot(self):
        """Start from the full default list, remove SPY (index 0), then re-add
        it - it must come back before QQQ (index 1), not at the end."""
        st.session_state["watchlist"] = [{"ticker": t, "name": n} for t, n in watchlist.DEFAULT_WATCHLIST]
        controller = FakeCookieController()
        watchlist.remove(controller, "SPY")
        assert st.session_state["watchlist"][0]["ticker"] == "QQQ"
        watchlist.add(controller, "SPY", "irrelevant")
        tickers = [e["ticker"] for e in st.session_state["watchlist"]]
        assert tickers.index("SPY") < tickers.index("QQQ")
        assert tickers == [t for t, _ in watchlist.DEFAULT_WATCHLIST]

    def test_readded_default_ticker_slots_in_among_mixed_entries(self):
        """A default ticker re-added after some custom (non-default) tickers
        were appended must still land by default order, not after the customs."""
        st.session_state["watchlist"] = [
            {"ticker": "QQQ", "name": "🇺🇸 Invesco QQQ Trust (Nasdaq-100)"},
            {"ticker": "ZZZZ", "name": "Custom Co."},
        ]
        controller = FakeCookieController()
        watchlist.add(controller, "SPY", "irrelevant")
        tickers = [e["ticker"] for e in st.session_state["watchlist"]]
        assert tickers == ["SPY", "QQQ", "ZZZZ"]

    def test_custom_ticker_still_appends_at_the_end(self):
        st.session_state["watchlist"] = [{"ticker": "SPY", "name": "🇺🇸 SPDR S&P 500 ETF"}]
        controller = FakeCookieController()
        watchlist.add(controller, "ZZZZ", "Custom Co.")
        assert st.session_state["watchlist"] == [
            {"ticker": "SPY", "name": "🇺🇸 SPDR S&P 500 ETF"},
            {"ticker": "ZZZZ", "name": "Custom Co."},
        ]
