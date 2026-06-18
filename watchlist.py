import json
from datetime import datetime, timedelta

import streamlit as st
from streamlit_cookies_controller import CookieController

COOKIE_NAME = "occ_watchlist"
COOKIE_EXPIRY_DAYS = 365

# ETFs first (index/region/theme trackers), then individual stocks.
# Every ticker here was verified against yf.Ticker(t).options to actually carry
# an options chain - plain equity indexes (^GSPC, ^GDAXI, ...) and EU-exchange
# listings (.DE, .PA, .AS, .SW, .L) do not, so region ETFs and US-listed shares
# are used instead.
DEFAULT_WATCHLIST = [
    ("SPY", "🇺🇸 SPDR S&P 500 ETF"),
    ("QQQ", "🇺🇸 Invesco QQQ Trust (Nasdaq-100)"),
    ("DIA", "🇺🇸 SPDR Dow Jones Industrial Average ETF"),
    ("IWM", "🇺🇸 iShares Russell 2000 ETF"),
    ("^VIX", "CBOE Volatility Index"),
    ("DAX", "🇩🇪 Global X DAX Germany ETF"),
    ("EWU", "🇬🇧 iShares MSCI United Kingdom ETF"),
    ("EWQ", "🇫🇷 iShares MSCI France ETF"),
    ("EWL", "🇨🇭 iShares MSCI Switzerland ETF"),
    ("VGK", "🇪🇺 Vanguard FTSE Europe ETF"),
    ("EWJ", "🇯🇵 iShares MSCI Japan ETF"),
    ("FXI", "🇨🇳 iShares China Large-Cap ETF"),
    ("GLD", "🥇 SPDR Gold Shares"),
    ("SLV", "🥈 iShares Silver Trust"),
    ("IBIT", "₿ iShares Bitcoin Trust"),
    ("SMH", "💾 VanEck Semiconductor ETF"),
    ("AIQ", "🤖 Global X Artificial Intelligence & Technology ETF"),
    ("XLE", "🇺🇸 Energy Select Sector SPDR Fund"),
    ("IXC", "🌍 iShares Global Energy ETF"),
    ("AAPL", "🇺🇸 Apple Inc."),
    ("MSFT", "🇺🇸 Microsoft Corp."),
    ("GOOGL", "🇺🇸 Alphabet Inc."),
    ("AMZN", "🇺🇸 Amazon.com Inc."),
    ("NVDA", "🇺🇸 NVIDIA Corp."),
    ("META", "🇺🇸 Meta Platforms Inc."),
    ("TSLA", "🇺🇸 Tesla Inc."),
    ("AVGO", "🇺🇸 Broadcom Inc."),
    ("BRK-B", "🇺🇸 Berkshire Hathaway Inc."),
    ("JPM", "🇺🇸 JPMorgan Chase & Co."),
    ("JNJ", "🇺🇸 Johnson & Johnson"),
    ("LLY", "🇺🇸 Eli Lilly and Co."),
    ("V", "🇺🇸 Visa Inc."),
    ("UNH", "🇺🇸 UnitedHealth Group Inc."),
    ("XOM", "🇺🇸 Exxon Mobil Corp."),
    ("WMT", "🇺🇸 Walmart Inc."),
    ("PG", "🇺🇸 Procter & Gamble Co."),
    ("TSM", "🇹🇼 Taiwan Semiconductor Mfg. Co."),
    ("BABA", "🇨🇳 Alibaba Group Holding Ltd."),
    ("ASML", "🇳🇱 ASML Holding N.V."),
    ("SAP", "🇩🇪 SAP SE"),
    ("DB", "🇩🇪 Deutsche Bank AG"),
    ("AZN", "🇬🇧 AstraZeneca PLC"),
    ("SHEL", "🇬🇧 Shell PLC"),
    ("BP", "🇬🇧 BP PLC"),
    ("HSBC", "🇬🇧 HSBC Holdings PLC"),
    ("UL", "🇬🇧 Unilever PLC"),
    ("NVS", "🇨🇭 Novartis AG"),
    ("SNY", "🇫🇷 Sanofi S.A."),
    ("TTE", "🇫🇷 TotalEnergies SE"),
    ("NVO", "🇩🇰 Novo Nordisk A/S"),
]

# Read-only lookups derived from DEFAULT_WATCHLIST (never mutated) so a default
# ticker that's removed and re-added snaps back to its original name/icon and
# position instead of landing at the end with whatever name was passed in.
_DEFAULT_ORDER = {ticker: i for i, (ticker, _) in enumerate(DEFAULT_WATCHLIST)}
_DEFAULT_NAMES = dict(DEFAULT_WATCHLIST)


def get_controller() -> CookieController:
    return CookieController()


def _parse_cookie_value(raw) -> list[dict] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    entries = [
        {"ticker": item["ticker"], "name": item["name"]}
        for item in parsed
        if isinstance(item, dict) and item.get("ticker") and item.get("name")
    ]
    return entries


def ensure_loaded(controller: CookieController):
    """Populate st.session_state['watchlist'] from the cookie, once per session."""
    if "watchlist" in st.session_state:
        return
    entries = _parse_cookie_value(controller.get(COOKIE_NAME))
    if entries is None:
        entries = [{"ticker": t, "name": n} for t, n in DEFAULT_WATCHLIST]
    st.session_state["watchlist"] = entries


def _persist(controller: CookieController):
    expires = datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS)
    value = json.dumps(st.session_state["watchlist"])
    try:
        controller.set(COOKIE_NAME, value, expires=expires)
    except Exception:
        # Cookies blocked/unavailable: keep working session-only.
        pass


def is_in_watchlist(ticker: str) -> bool:
    watchlist = st.session_state.get("watchlist", [])
    return any(entry["ticker"] == ticker for entry in watchlist)


def add(controller: CookieController, ticker: str, name: str):
    if is_in_watchlist(ticker):
        return
    entries = st.session_state["watchlist"]
    if ticker in _DEFAULT_ORDER:
        # Restore a default ticker to its original name/icon and slot rather
        # than appending it with whatever name happened to be passed in.
        entry = {"ticker": ticker, "name": _DEFAULT_NAMES[ticker]}
        target_order = _DEFAULT_ORDER[ticker]
        insert_at = next(
            (i for i, e in enumerate(entries) if _DEFAULT_ORDER.get(e["ticker"], -1) > target_order),
            len(entries),
        )
        entries.insert(insert_at, entry)
    else:
        entries.append({"ticker": ticker, "name": name or ticker})
    _persist(controller)


def remove(controller: CookieController, ticker: str):
    st.session_state["watchlist"] = [
        entry for entry in st.session_state["watchlist"] if entry["ticker"] != ticker
    ]
    _persist(controller)


def toggle(controller: CookieController, ticker: str, name: str):
    if is_in_watchlist(ticker):
        remove(controller, ticker)
    else:
        add(controller, ticker, name)
