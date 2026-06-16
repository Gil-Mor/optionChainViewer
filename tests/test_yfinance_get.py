import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import pandas as pd
import yfinanceGetOptions

# --- Unit Test Helpers ---

def _make_chain_df(strikes=[100.0]):
    """Helper to create minimal valid dataframes for get_options_chain_table."""
    return pd.DataFrame({
        'strike': strikes,
        'lastPrice': [1.0] * len(strikes),
        'change': [0.0] * len(strikes),
        'percentChange': [0.0] * len(strikes),
        'volume': [10] * len(strikes),
        'openInterest': [100] * len(strikes),
        'impliedVolatility': [0.3] * len(strikes)
    })

# --- Unit Tests ---

class TestGetOptionsChainRetrievalTime:
    """Focuses on the timestamp extraction logic in yfinanceGetOptions."""

    @patch("yfinanceGetOptions.yf.Ticker")
    def test_yes_time_data(self, mock_ticker_cls):
        """Verify datetime is extracted when regularMarketTime exists in metadata."""
        mock_ticker = MagicMock()
        mock_ticker.options = ["2025-01-17"]
        mock_ticker_cls.return_value = mock_ticker

        mock_chain = MagicMock()
        mock_chain.calls = _make_chain_df()
        mock_chain.puts = _make_chain_df()

        # Fixed timestamp: 2025-01-10 21:00:00 UTC
        ts = 1736544000
        mock_chain.underlying = {'regularMarketTime': ts}
        mock_ticker.option_chain.return_value = mock_chain

        _, _, _, retrieval_time = yfinanceGetOptions.get_options_chain_table("AAPL")

        assert retrieval_time is not None
        assert isinstance(retrieval_time, datetime)
        # Match the behavior of datetime.fromtimestamp() used in the source
        assert retrieval_time == datetime.fromtimestamp(ts)

    @patch("yfinanceGetOptions.yf.Ticker")
    def test_no_time_data_missing_key(self, mock_ticker_cls):
        """Verify None is returned when metadata exists but the time key is missing."""
        mock_ticker = MagicMock()
        mock_ticker.options = ["2025-01-17"]
        mock_ticker_cls.return_value = mock_ticker

        mock_chain = MagicMock()
        mock_chain.calls = _make_chain_df()
        mock_chain.puts = _make_chain_df()
        mock_chain.underlying = {}
        mock_ticker.option_chain.return_value = mock_chain

        _, _, _, retrieval_time = yfinanceGetOptions.get_options_chain_table("AAPL")
        assert retrieval_time is None

# --- Integration / UI Tests (AppTest) ---

try:
    from streamlit.testing.v1 import AppTest
    _HAS_APPTEST = True
except ImportError:
    _HAS_APPTEST = False

@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1 not available")
class TestRetrievalTimeUI:
    """Verifies that the Streamlit app correctly renders the time caption."""

    def _setup_app_mocks(self, mock_yfi_get, mock_yf_ticker, mock_opt_main, ret_time=None):
        """Common mock setup for AppTests."""
        mock_yfi_get.return_value = (pd.DataFrame(), "2025-01-17", ["2025-01-17"], ret_time)
        mock_yf_ticker.return_value.options = ["2025-01-17"]
        mock_yf_ticker.return_value.info = {"longName": "Test Corp", "regularMarketPrice": 100.0}
        mock_yf_ticker.return_value.fast_info = {"last_price": 100.0, "previous_close": 99.0}

        mock_opt_main.return_value = {
            "company_name": "Test Corp",
            "expiration_date": "2025-01-17",
            "styled_dataframe": pd.DataFrame().style,
            "retrieval_time": ret_time,
            "context": MagicMock()
        }

    @patch("optionchain.main")
    @patch("yfinance.Ticker")
    @patch("yfinanceGetOptions.get_options_chain_table")
    def test_ui_displays_formatted_time(self, mock_get, mock_ticker, mock_main):
        dt = datetime(2025, 1, 10, 16, 0, 0)
        self._setup_app_mocks(mock_get, mock_ticker, mock_main, ret_time=dt)
        at = AppTest.from_file("streamlitapp.py").run()
        assert any(dt.strftime('%Y-%m-%d %H:%M:%S') in c.value for c in at.caption)

    @patch("optionchain.main")
    @patch("yfinance.Ticker")
    @patch("yfinanceGetOptions.get_options_chain_table")
    def test_ui_displays_fallback_message(self, mock_get, mock_ticker, mock_main):
        self._setup_app_mocks(mock_get, mock_ticker, mock_main, ret_time=None)
        at = AppTest.from_file("streamlitapp.py").run()
        assert any("No time data available." in c.value for c in at.caption)