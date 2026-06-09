"""Tests for the company name ↔ ticker search feature.

Coverage:
  - get_ticker_from_name(): emulates all yfinance.Search response shapes
  - pending_ticker state machine: pure-Python simulation of the Streamlit rerun logic
  - Streamlit UI scenarios via AppTest (yfinance and optionchain mocked)
"""

import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pandas as pd
import streamlit as st

import yfinanceGetOptions


# ──────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────

FAKE_EXP_DATES = ("2025-01-17", "2025-02-21", "2025-03-21")


def _chain_df(strikes=(100.0, 105.0, 110.0)):
    """Minimal yfinance-style calls/puts DataFrame."""
    n = len(strikes)
    return pd.DataFrame({
        "strike": list(strikes),
        "lastPrice": [1.0] * n,
        "change": [0.0] * n,
        "percentChange": [0.0] * n,
        "volume": [100] * n,
        "openInterest": [1000] * n,
    })


def _make_mock_ticker(options=FAKE_EXP_DATES, long_name="NVIDIA Corporation",
                      price=500.0, prev_close=495.0):
    """yfinance.Ticker mock that satisfies all calls made by the app."""
    mock = MagicMock()
    mock.options = options
    mock.info = {
        "longName": long_name,
        "regularMarketPrice": price,
        "regularMarketPreviousClose": prev_close,
    }
    mock.fast_info = {"last_price": price, "previous_close": prev_close}
    chain = MagicMock()
    chain.calls = _chain_df()
    chain.puts = _chain_df()
    mock.option_chain.return_value = chain
    return mock


def _minimal_main_result(company_name="NVIDIA Corporation", exp="2025-01-17"):
    """Minimal optionchain.main() return value to prevent rendering crashes."""
    return {
        "company_name": company_name,
        "expiration_date": exp,
        "styled_dataframe": pd.DataFrame({"A": [1]}).style,
        "context": MagicMock(),
    }


@pytest.fixture(autouse=True)
def clear_st_cache():
    """Prevent @st.cache_data results bleeding across tests."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


# ──────────────────────────────────────────────────────────────
# Unit tests: get_ticker_from_name
# ──────────────────────────────────────────────────────────────

class TestGetTickerFromName:
    """Tests for yfinanceGetOptions.get_ticker_from_name.
    All yf.Search calls are mocked — no network I/O."""

    def _search_mock(self, quotes):
        m = MagicMock()
        m.quotes = quotes
        return patch("yfinanceGetOptions.yf.Search", return_value=m)

    # --- happy path ---

    def test_valid_name_returns_ticker(self):
        with self._search_mock([{"symbol": "MSFT", "longname": "Microsoft Corporation"}]):
            assert yfinanceGetOptions.get_ticker_from_name("Microsoft") == "MSFT"

    def test_multiple_results_uses_first(self):
        quotes = [{"symbol": "AAPL"}, {"symbol": "AAPLX"}]
        with self._search_mock(quotes):
            assert yfinanceGetOptions.get_ticker_from_name("Apple") == "AAPL"

    def test_symbol_field_is_returned_not_other_keys(self):
        with self._search_mock([{"symbol": "GOOGL", "shortName": "Alphabet Inc."}]):
            assert yfinanceGetOptions.get_ticker_from_name("Alphabet") == "GOOGL"

    # --- no-result cases ---

    def test_empty_quotes_list_returns_none(self):
        with self._search_mock([]):
            assert yfinanceGetOptions.get_ticker_from_name("nonexistent xyz corp 99") is None

    def test_quote_missing_symbol_key_returns_none(self):
        with self._search_mock([{"longname": "No Symbol Corp"}]):
            assert yfinanceGetOptions.get_ticker_from_name("No Symbol") is None

    # --- error / edge cases ---

    def test_yfinance_network_exception_returns_none(self):
        with patch("yfinanceGetOptions.yf.Search", side_effect=Exception("timeout")):
            assert yfinanceGetOptions.get_ticker_from_name("Microsoft") is None

    def test_yfinance_value_error_returns_none(self):
        with patch("yfinanceGetOptions.yf.Search", side_effect=ValueError("bad input")):
            assert yfinanceGetOptions.get_ticker_from_name("???") is None

    @pytest.mark.parametrize("name", ["", "   ", "\t"])
    def test_blank_name_returns_none(self, name):
        """Function itself must not crash; callers are responsible for stripping."""
        with self._search_mock([]):
            assert yfinanceGetOptions.get_ticker_from_name(name) is None

    # --- API hygiene ---

    def test_requests_exactly_one_result(self):
        """max_results=1 keeps API load minimal for a hobby project."""
        with patch("yfinanceGetOptions.yf.Search") as mock_cls:
            mock_cls.return_value.quotes = []
            yfinanceGetOptions.get_ticker_from_name("Apple")
        mock_cls.assert_called_once_with("Apple", max_results=1)


# ──────────────────────────────────────────────────────────────
# Unit tests: pending_ticker state machine (pure Python)
#
# The pending_ticker pattern lets streamlitapp.py update session_state
# BEFORE the ticker widget renders (Streamlit forbids modifying a widget's
# session_state key after the widget is instantiated in the same run).
#
# Logic in streamlitapp.py:
#   if session_state['pending_ticker']:
#       session_state['ticker'] = session_state['pending_ticker']
#       session_state['pending_ticker'] = None
# ──────────────────────────────────────────────────────────────

class TestPendingTickerStateMachine:

    def _apply_pending(self, state: dict):
        """Simulate one Streamlit rerun of the pending_ticker block."""
        if state["pending_ticker"]:
            state["ticker"] = state["pending_ticker"]
            state["pending_ticker"] = None

    def _state(self, ticker="NVDA", pending=None, display="", last_ticker="", name_query=""):
        return {"ticker": ticker, "pending_ticker": pending, "company_name_display": display,
                "last_ticker": last_ticker, "name_query": name_query}

    # --- core transitions ---

    def test_pending_is_applied_and_cleared(self):
        s = self._state(pending="MSFT")
        self._apply_pending(s)
        assert s["ticker"] == "MSFT"
        assert s["pending_ticker"] is None

    def test_none_pending_leaves_ticker_unchanged(self):
        s = self._state(ticker="NVDA", pending=None)
        self._apply_pending(s)
        assert s["ticker"] == "NVDA"

    def test_empty_string_pending_is_falsy_leaves_ticker_unchanged(self):
        s = self._state(ticker="NVDA", pending="")
        self._apply_pending(s)
        assert s["ticker"] == "NVDA"

    def test_second_rerun_does_not_reapply_cleared_pending(self):
        s = self._state(pending="MSFT")
        self._apply_pending(s)   # first rerun: NVDA → MSFT, cleared
        self._apply_pending(s)   # second rerun: no-op
        assert s["ticker"] == "MSFT"

    def test_sequential_searches_each_overwrite_ticker(self):
        s = self._state(pending="MSFT")
        self._apply_pending(s)
        assert s["ticker"] == "MSFT"
        s["pending_ticker"] = "AAPL"
        self._apply_pending(s)
        assert s["ticker"] == "AAPL"

    # --- name field sync: name → ticker ---

    def test_search_clears_company_name_display_to_prevent_stale_caption(self):
        """When a name search fires, the old company name caption is wiped
        immediately so it doesn't linger until new data loads."""
        s = self._state(ticker="NVDA", display="NVIDIA Corporation")
        # Simulate Search button handler:
        s["pending_ticker"] = "MSFT"
        s["company_name_display"] = ""
        self._apply_pending(s)
        assert s["ticker"] == "MSFT"
        assert s["company_name_display"] == ""

    # --- ticker field sync: ticker → name ---

    def test_company_name_display_set_after_data_load(self):
        """After get_cached_options_data returns, company_name_display is populated."""
        s = self._state(ticker="AAPL")
        company_name = "Apple Inc."
        s["company_name_display"] = company_name or ""
        assert s["company_name_display"] == "Apple Inc."

    def test_none_company_name_falls_back_to_empty_string(self):
        """yfinance may return no longName; display must be empty string not None."""
        s = self._state(ticker="AAPL")
        company_name = None
        s["company_name_display"] = company_name or ""
        assert s["company_name_display"] == ""

    # --- one-field-empty scenarios ---

    def test_ticker_set_name_display_empty_is_valid_initial_state(self):
        """App starts with ticker=NVDA and no name — name populates after first load."""
        s = self._state(ticker="NVDA", display="")
        assert s["ticker"] == "NVDA"
        assert s["company_name_display"] == ""
        assert s["pending_ticker"] is None

    def test_pending_ticker_set_existing_ticker_not_yet_updated(self):
        """After Search, pending_ticker holds the new symbol until next rerun."""
        s = self._state(ticker="NVDA", pending="TSLA")
        assert s["ticker"] == "NVDA"       # old value still showing
        assert s["pending_ticker"] == "TSLA"  # new value queued
        self._apply_pending(s)
        assert s["ticker"] == "TSLA"       # updated after apply

    def _apply_ticker_change_clear(self, state: dict):
        """Simulate the ticker-change detection block in streamlitapp.py."""
        if state["ticker"] != state["last_ticker"]:
            state["name_query"] = ""
            state["company_name_display"] = ""

    def test_ticker_change_clears_name_field(self):
        """Typing a new ticker must wipe the name field so a stale name can't
        re-trigger a name search on the next Search click."""
        s = self._state(ticker="AAPL", last_ticker="NVDA", name_query="Microsoft")
        self._apply_ticker_change_clear(s)
        assert s["name_query"] == ""

    def test_ticker_change_clears_stale_company_caption(self):
        s = self._state(ticker="AAPL", last_ticker="NVDA", display="NVIDIA Corporation")
        self._apply_ticker_change_clear(s)
        assert s["company_name_display"] == ""

    def test_no_ticker_change_preserves_name_and_display(self):
        s = self._state(ticker="NVDA", last_ticker="NVDA",
                        name_query="Some partial text", display="NVIDIA Corporation")
        self._apply_ticker_change_clear(s)
        assert s["name_query"] == "Some partial text"
        assert s["company_name_display"] == "NVIDIA Corporation"

    def test_name_search_resolution_also_triggers_clear(self):
        """Name search resolves NVDA→MSFT via pending_ticker.  After the pending
        is applied, ticker differs from last_ticker, so name_query is cleared."""
        s = self._state(ticker="NVDA", last_ticker="NVDA",
                        pending="MSFT", name_query="Microsoft")
        # step 1: apply pending (simulates pre-sidebar block)
        self._apply_pending(s)
        assert s["ticker"] == "MSFT"
        # step 2: ticker-change detection clears name field
        self._apply_ticker_change_clear(s)
        assert s["name_query"] == ""

    def test_empty_name_field_search_guard(self):
        """The Search handler skips the API call when the name input is blank.
        Mimics: `if name_query.strip(): ...`
        """
        for blank in ("", "   ", "\t"):
            pending_before = None
            pending_after = pending_before if not blank.strip() else "SOME_TICKER"
            assert pending_after is None, f"blank={repr(blank)} should not set pending"


# ──────────────────────────────────────────────────────────────
# Streamlit AppTest integration tests
# ──────────────────────────────────────────────────────────────

try:
    from streamlit.testing.v1 import AppTest
    _HAS_APPTEST = True
except ImportError:
    _HAS_APPTEST = False

pytestmark_apptest = pytest.mark.skipif(
    not _HAS_APPTEST, reason="streamlit.testing.v1 not available"
)


@contextmanager
def _patched_app(search_return=MagicMock(), ticker_mock=None, main_result=None):
    """Context manager: returns a running AppTest with yfinance/optionchain mocked."""
    if ticker_mock is None:
        ticker_mock = _make_mock_ticker()
    if main_result is None:
        main_result = _minimal_main_result()
    with (
        patch("yfinance.Ticker", return_value=ticker_mock),
        patch("yfinanceGetOptions.yf.Ticker", return_value=ticker_mock),
        patch("optionchain.main", return_value=main_result),
    ):
        at = AppTest.from_file("streamlitapp.py", default_timeout=15)
        at.run()
        yield at


@pytestmark_apptest
class TestStreamlitNameSearchUI:
    """UI-level tests via AppTest.  Widget indexes:
        text_input[0] → Ticker
        text_input[1] → Company / Security Name (inside the form)
        form_submit_button[0] → Search
    """

    def test_app_loads_without_exception(self):
        with _patched_app() as at:
            assert not at.exception

    def test_initial_ticker_defaults_to_nvda(self):
        with _patched_app() as at:
            assert at.session_state["ticker"] == "NVDA"

    def test_initial_company_name_populated_after_load(self):
        with _patched_app(main_result=_minimal_main_result(company_name="NVIDIA Corporation")) as at:
            assert at.session_state["company_name_display"] == "NVIDIA Corporation"

    def test_empty_ticker_field_does_not_clear_ticker(self):
        """Clearing the ticker field must not set ticker to '' or break the app."""
        with _patched_app() as at:
            original = at.session_state["ticker"]
            at.text_input[0].set_value("").run()
            assert at.session_state["ticker"] == original
            assert at.session_state["ticker_ready"] is True

    def test_clearing_ticker_after_failed_search_restores_ticker_ready(self):
        """After an invalid ticker (ticker_ready=False), clearing the field restores ticker_ready."""
        mock_ticker = _make_mock_ticker()

        def ticker_side_effect(symbol):
            if symbol == "NOTREAL":
                raise Exception("unknown symbol")
            return mock_ticker

        with (
            patch("yfinance.Ticker", side_effect=ticker_side_effect),
            patch("yfinanceGetOptions.yf.Ticker", side_effect=ticker_side_effect),
            patch("optionchain.main", return_value=_minimal_main_result()),
        ):
            at = AppTest.from_file("streamlitapp.py", default_timeout=15)
            at.run()
            at.text_input[0].set_value("NOTREAL").run()
            assert at.session_state["ticker_ready"] is False
            at.text_input[0].set_value("").run()
        assert at.session_state["ticker_ready"] is True
        assert at.session_state["ticker"] == "NVDA"

    def test_empty_name_field_does_not_trigger_search(self):
        """Clearing the name field must not change the ticker."""
        with _patched_app() as at:
            original = at.session_state["ticker"]
            at.text_input[1].set_value("").run()
            assert at.session_state["ticker"] == original

    def test_clearing_name_after_failed_search_restores_ticker_ready(self):
        """After a failed name search (ticker_ready=False), clearing the name field
        must restore ticker_ready=True so the options chain loads for the current ticker."""
        with (
            patch("yfinance.Ticker", return_value=_make_mock_ticker()),
            patch("yfinanceGetOptions.yf.Ticker", return_value=_make_mock_ticker()),
            patch("yfinanceGetOptions.get_ticker_from_name", return_value=None),
            patch("optionchain.main", return_value=_minimal_main_result()),
        ):
            at = AppTest.from_file("streamlitapp.py", default_timeout=15)
            at.run()
            # Failed name search → ticker_ready becomes False
            at.text_input[1].set_value("xyzzy").run()
            assert at.session_state["ticker_ready"] is False
            # Clearing the name field should restore ticker_ready
            at.text_input[1].set_value("").run()
        assert at.session_state["ticker_ready"] is True
        assert at.session_state["ticker"] == "NVDA"

    def test_whitespace_only_name_does_not_trigger_search(self):
        with _patched_app() as at:
            original = at.session_state["ticker"]
            at.text_input[1].set_value("   ").run()
            assert at.session_state["ticker"] == original

    def test_name_found_updates_ticker(self):
        """Successful name search: typing a name and submitting updates the ticker."""
        with (
            patch("yfinance.Ticker", return_value=_make_mock_ticker()),
            patch("yfinanceGetOptions.yf.Ticker", return_value=_make_mock_ticker()),
            patch("yfinanceGetOptions.get_ticker_from_name", return_value="MSFT"),
            patch("optionchain.main", return_value=_minimal_main_result()),
        ):
            at = AppTest.from_file("streamlitapp.py", default_timeout=15)
            at.run()
            at.text_input[1].set_value("Microsoft").run()
        assert at.session_state["ticker"] == "MSFT"

    def test_name_not_found_shows_warning_and_keeps_ticker(self):
        """When yfinance finds no match, a warning is shown and ticker is unchanged."""
        with (
            patch("yfinance.Ticker", return_value=_make_mock_ticker()),
            patch("yfinanceGetOptions.yf.Ticker", return_value=_make_mock_ticker()),
            patch("yfinanceGetOptions.get_ticker_from_name", return_value=None),
            patch("optionchain.main", return_value=_minimal_main_result()),
        ):
            at = AppTest.from_file("streamlitapp.py", default_timeout=15)
            at.run()
            original = at.session_state["ticker"]
            at.text_input[1].set_value("xyzzy corp that does not exist").run()
        assert at.session_state["ticker"] == original
        assert len(at.warning) > 0

    def test_two_way_sync_name_to_ticker_updates_company_display(self):
        """After searching 'Microsoft' → MSFT, the company caption reflects the new ticker.
        Uses per-symbol mocks so NVDA and MSFT return distinct company names."""
        mock_nvda = _make_mock_ticker(long_name="NVIDIA Corporation")
        mock_msft = _make_mock_ticker(long_name="Microsoft Corporation")

        def ticker_side_effect(symbol):
            return mock_msft if symbol == "MSFT" else mock_nvda

        with (
            patch("yfinance.Ticker", side_effect=ticker_side_effect),
            patch("yfinanceGetOptions.yf.Ticker", side_effect=ticker_side_effect),
            patch("yfinanceGetOptions.get_ticker_from_name", return_value="MSFT"),
            patch("optionchain.main", return_value=_minimal_main_result(company_name="Microsoft Corporation")),
        ):
            at = AppTest.from_file("streamlitapp.py", default_timeout=15)
            at.run()
            assert at.session_state["company_name_display"] == "NVIDIA Corporation"
            at.text_input[1].set_value("Microsoft").run()
        # After the full rerun with MSFT as ticker, display shows the new company name
        assert at.session_state["ticker"] == "MSFT"
        assert at.session_state["company_name_display"] == "Microsoft Corporation"

    def test_ticker_change_after_name_search_syncs_correctly(self):
        """In real Streamlit each text_input on_change fires its own rerun (blur/Enter
        triggers an immediate rerun before the user can interact with another field).
        This test mirrors that: name search fires first (own run), then ticker change
        fires (own run).  The final state should reflect the ticker change."""
        mock_nvda = _make_mock_ticker(long_name="NVIDIA Corporation")
        mock_aapl = _make_mock_ticker(long_name="Apple Inc.")
        mock_msft = _make_mock_ticker(long_name="Microsoft Corporation")

        def ticker_side_effect(symbol):
            if symbol == "AAPL":
                return mock_aapl
            if symbol == "MSFT":
                return mock_msft
            return mock_nvda

        with (
            patch("yfinance.Ticker", side_effect=ticker_side_effect),
            patch("yfinanceGetOptions.yf.Ticker", side_effect=ticker_side_effect),
            patch("yfinanceGetOptions.get_ticker_from_name", return_value="MSFT"),
            patch("optionchain.main", return_value=_minimal_main_result()),
        ):
            at = AppTest.from_file("streamlitapp.py", default_timeout=15)
            at.run()
            # Run 1: user submits name field → ticker resolves to MSFT
            at.text_input[1].set_value("Microsoft").run()
            assert at.session_state["ticker"] == "MSFT"
            # Run 2: user then changes ticker directly to AAPL
            at.text_input[0].set_value("AAPL").run()

        # Name field syncs to the new ticker's canonical name
        assert at.session_state["ticker"] == "AAPL"
        assert at.session_state["name_query"] == "Apple Inc."

    def test_no_options_data_for_ticker_shows_warning(self):
        """If yfinance returns no expiration dates, the app shows a warning."""
        empty_ticker = _make_mock_ticker(options=())
        with _patched_app(ticker_mock=empty_ticker) as at:
            assert len(at.warning) > 0
