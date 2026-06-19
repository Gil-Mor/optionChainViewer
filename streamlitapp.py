import streamlit as st
import optionchain
import watchlist
import yfinance as yf
import yfinanceGetOptions as yfi_module
from datetime import datetime, timedelta
import zoneinfo

st.set_page_config(
    page_title="Option Chain Viewer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)
GLOSSARY_MARKDOWN = """
**The basics**

- **Option Chain** — the full list of available Call and Put contracts for a stock at a given expiration date, organized by strike price.
- **The Table** — Calls on the left, Puts on the right, **Strike** in the middle. Each row is one strike, so the call and put at the same strike sit side by side.
- **Strike** — the price at which the contract lets you buy (Call) or sell (Put) the stock.
- **Calls** — contracts that profit if the stock goes *up* past the strike.
- **Puts** — contracts that profit if the stock goes *down* past the strike.
- **Open Interest (OI)** — how many contracts at that strike are currently open. A rough gauge of how much money is parked there.
- **Volume** — how many contracts at that strike traded *today*.
- **IV (Implied Volatility)** — how much price movement the market is pricing in. Higher IV means options are pricier, expecting bigger swings.
- **Change** — how much the contract's price moved today.

**What the Technical Breakdown means by...**

- **MMs (Market Makers)** — the firms on the other side of most trades. They don't bet on direction; they hedge to stay neutral, and that hedging can itself move the stock.
- **Hedging** — MMs and institutions buying or selling shares or other options to offset risk from positions they've already taken, not a directional bet on the stock.
- **Walls** — strikes with unusually large OI. They can act like price magnets or barriers, since MMs hedging that much exposure tend to defend or gravitate toward that level.
"""

# Positioned (not fixed) to the header container's top-right corner instead of flowing
# inline, so it sits next to the title without st.columns stacking it full-width below the
# title on narrow/mobile layouts - but it still scrolls away with the rest of the page.
st.markdown(
    """<style>
    .st-key-header_container {
        position: relative;
    }
    .st-key-help_popover {
        position: absolute;
        top: 0;
        right: 0;
        z-index: 999;
    }
    </style>""",
    unsafe_allow_html=True,
)
with st.container(key="header_container"):
    st.title("Option Chain Viewer")
    st.caption("An option chain viewer with some visual aids to make Calls/Puts positioning easier to read.")
    with st.popover("❓", key="help_popover"):
        st.markdown(GLOSSARY_MARKDOWN)

def is_market_open():
    """Checks if US Markets (NYSE/NASDAQ) are open (9:30 AM - 4:00 PM ET)."""
    tz = zoneinfo.ZoneInfo("America/New_York")
    now = datetime.now(tz)

    # Weekends
    if now.weekday() >= 5:
        return False

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now <= market_close

def format_relative_date(when: datetime) -> str:
    """Formats a date as 'Today'/'Yesterday', a weekday name (within the last week), or dd.mm.yy."""
    target = when.astimezone(zoneinfo.ZoneInfo("America/New_York")).date() if when.tzinfo else when.date()
    today = datetime.now(zoneinfo.ZoneInfo("America/New_York")).date()
    delta_days = (today - target).days

    if delta_days == 0:
        return "Today"
    if delta_days == 1:
        return "Yesterday"
    if 0 < delta_days < 7:
        return target.strftime('%A')
    return target.strftime('%d.%m.%y')

# (display label, underlying column name, default visible)
COLUMN_VISIBILITY_OPTIONS = [
    ("Last Price", "Last Price", False),
    ("Change ($)", "Change", False),
    ("% Change", "% Change", True),
    ("Volume", "Volume", True),
    ("Open Interest", "Open Interest", True),
    ("IV", "IV", True),
    ("Bid", "Bid", False),
    ("Ask", "Ask", False),
]

popular_tickers = (
        'AAPL', 'AMZN', 'GOOGL','META', 'MSFT', 'NVDA', 'TSLA', 'SPY', 'QQQ', 'DOW'
    )
popular_names = (
    'Apple', 'Amazon', 'Google', 'Meta', 'Microsoft', 'Nvidia', 'Tesla', 'S&P 500', 'Nasdaq 100', 'DOW Jones'
)

@st.cache_data
def get_available_dates(ticker_symbol):
    try:
        return yf.Ticker(ticker_symbol).options
    except Exception:
        return []

DEFAULT_TICKER = 'SPY'

qp_ticker = st.query_params.get('ticker')
qp_exp = st.query_params.get('exp')

if 'ticker' not in st.session_state:
    seeded_ticker = DEFAULT_TICKER
    if qp_ticker:
        if yfi_module.search_ticker(qp_ticker.strip()):
            seeded_ticker = qp_ticker.strip()
        else:
            st.warning(f"Ticker from URL not found: {qp_ticker}")
    st.session_state['ticker'] = seeded_ticker
if 'use_ticker_or_name_query' not in st.session_state:
    st.session_state['use_ticker_or_name_query'] = 'ticker_query'
if 'last_ticker' not in st.session_state:
    st.session_state['last_ticker'] = ''
if 'ticker_ready' not in st.session_state:
    st.session_state['ticker_ready'] = True
if 'column_visibility' not in st.session_state:
    st.session_state['column_visibility'] = {col: default for _, col, default in COLUMN_VISIBILITY_OPTIONS}

cookie_controller = watchlist.get_controller()
watchlist.ensure_loaded(cookie_controller)

st.session_state['ticker_query'] = st.session_state['ticker']
st.session_state['name_query'] = yfi_module.get_name_from_ticker(st.session_state['ticker'])
st.session_state['company_name_display'] = st.session_state['name_query']

# Ticker and Name search in the main page area
search_col1, price_chart_col = st.columns([1, 2], gap="medium")

def use_ticker_or_name_query(widget_key: str):
    print(f"using input from: {widget_key}")
    st.session_state['use_ticker_or_name_query'] = widget_key
    if widget_key == 'ticker_query':
        value = st.session_state['ticker_query']
        if not value.strip():
            st.session_state['ticker_ready'] = True
            return
        if yfi_module.search_ticker(value.strip()):
            st.session_state['ticker'] = value
            st.session_state['ticker_ready'] = True
        else:
            st.warning(f"Ticker: {value} not found")
            st.session_state['ticker_ready'] = False
    else:
        value = st.session_state['name_query']
        if not value.strip():
            st.session_state['ticker_ready'] = True
            return
        with st.spinner("Looking up ticker..."):
            ticker_query = yfi_module.get_ticker_from_name(value.strip())
        if ticker_query:
            print(f"Found ticker: {ticker_query} from name: {value}")
            st.session_state['ticker'] = ticker_query
            st.session_state['ticker_ready'] = True
        else:
            # Ticker hasn't changed so no need to rerun.
            st.warning(f"No ticker found for '{value}'.")
            st.session_state['ticker_ready'] = False


def toggle_watchlist_callback():
    current_ticker = st.session_state['ticker']
    current_name = st.session_state.get('company_name_display') or current_ticker
    watchlist.toggle(cookie_controller, current_ticker, current_name)


with search_col1:
    ticker_query = st.text_input(label="Search by Ticker", key='ticker_query', help=f"Ticker ideas: {', '.join(popular_tickers)}", on_change=use_ticker_or_name_query, args=('ticker_query',))

    if st.session_state['company_name_display']:
        st.caption(f"**{st.session_state['company_name_display']}**")

    in_watchlist = watchlist.is_in_watchlist(st.session_state['ticker'])
    st.markdown(
        """<style>
        .st-key-watchlist_star_toggle button[kind="primary"] {
            background-color: #f0c419;
            border-color: #f0c419;
            color: #1a1a1a;
        }
        .st-key-watchlist_star_toggle button[kind="primary"]:hover {
            background-color: #d6ad17;
            border-color: #d6ad17;
            color: #1a1a1a;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    st.button(
        "★ Remove from Watchlist" if in_watchlist else "☆ Add to Watchlist",
        key="watchlist_star_toggle",
        type="primary" if in_watchlist else "secondary",
        on_click=toggle_watchlist_callback,
    )

    name_query = st.text_input("Search by Company / Security Name", key='name_query', on_change=use_ticker_or_name_query, args=('name_query', ))

    # Get expiration dates after we've found the Ticker
    available_dates = get_available_dates(st.session_state['ticker'])
    if not available_dates:
        st.warning(f"No options data found for ticker: {st.session_state['ticker']}")
        st.session_state['ticker_ready'] = False

    if 'exp_date_select' not in st.session_state:
        if qp_exp and qp_exp in available_dates:
            st.session_state['exp_date_select'] = qp_exp
        elif available_dates:
            st.session_state['exp_date_select'] = yfi_module.get_default_expiration(available_dates)

    exp_date = st.selectbox("Expiration Date", options=available_dates, key='exp_date_select', help="Select an expiration date to view its option chain.")

if st.session_state['ticker_ready'] and st.session_state.get('ticker'):
    st.query_params['ticker'] = st.session_state['ticker']
    if exp_date:
        st.query_params['exp'] = exp_date

def jump_to_watchlist_ticker():
    selected = st.session_state.get('watchlist_jump_select')
    if not selected:
        return
    ticker = selected.split(' — ', 1)[0]
    st.session_state['ticker'] = ticker
    st.session_state['ticker_query'] = ticker
    st.session_state['name_query'] = ''
    st.session_state['ticker_ready'] = True
    st.session_state['watchlist_jump_select'] = None


with st.sidebar:
    st.header("⭐ Watchlist")

    watchlist_entries = st.session_state['watchlist']
    watchlist_select_options = [f"{e['ticker']} — {e['name']}" for e in watchlist_entries]
    st.selectbox(
        "Jump to ticker",
        options=watchlist_select_options,
        index=None,
        key='watchlist_jump_select',
        placeholder="🔍 Search watchlist...",
        help="Star a ticker (next to the ticker search box) to add or remove it from this list.",
        on_change=jump_to_watchlist_ticker,
    )

    st.write("---")
    st.header("Chain View Settings")

    display_mode = st.radio(
        "Strike Alignment",
        ["Normal View", "Flip Put Strikes (OTM Puts aligned with OTM Calls)"],
        help="Choose how strike prices are aligned. 'Flip Put Strikes' aligns puts by their distance from the ATM strike, mirroring calls."
    )
    flip_strikes = (display_mode == "Flip Put Strikes (OTM Puts aligned with OTM Calls)")
    trim_around_strike = st.number_input(label="Trim table around strike. 0 to not trim.", min_value=0, value=7)

    st.subheader("Visualization")
    bar_scaling_mode = st.radio(
        "Proportional Bar Scaling",
        ["Relative to OTM/ITM/ATM Groups", "Per Strike (Row)", "Relative to Full Chain", "Per Side (Each side's own peak)"],
        help="Mode 1: Each moneyness group (OTM/ATM/ITM) normalized to its own peak. Mode 2: Calls vs puts relative to row total. Mode 3: Global bell curve — single peak is 100%, all others scale down. Mode 4: Each side (calls/puts) normalized independently to its own peak."
    )

    with st.expander("Column Visibility", expanded=False):
        with st.form("column_visibility_form"):
            selected_visibility = {}
            for label, col_name, _default in COLUMN_VISIBILITY_OPTIONS:
                selected_visibility[col_name] = st.checkbox(
                    label, value=st.session_state['column_visibility'][col_name]
                )
            if st.form_submit_button("Apply"):
                st.session_state['column_visibility'] = selected_visibility

# Columns whose underlying name (or its ".1" put counterpart) should be hidden from the
# rendered chain table. Built from the form's last applied state, not the in-progress form
# widgets, so toggling checkboxes without pressing Apply doesn't change anything yet.
hidden_columns = [
    name
    for col_name, visible in st.session_state['column_visibility'].items()
    if not visible
    for name in (col_name, f"{col_name}.1")
]

@st.cache_data(show_spinner=False, ttl=300)
def get_cached_options_data(ticker_symbol, selected_exp):
    """Fetches raw data and price, cached by ticker and expiration."""
    import yfinanceGetOptions as yfi_module
    df, target_exp, all_exps, retrieval_time = yfi_module.get_options_chain_table(ticker_symbol, selected_exp)

    ticker_obj = yf.Ticker(ticker_symbol)
    info = ticker_obj.info
    name = info.get('longName')
    fast = ticker_obj.fast_info
    price = fast.get('last_price') or info.get('regularMarketPrice')
    prev_close = fast.get('previous_close') or info.get('regularMarketPreviousClose')

    change = None
    percent_change = None
    if price and prev_close:
        change = price - prev_close
        percent_change = (change / prev_close) * 100

    # Trailing realized volatility - substitute for IV Rank/Percentile (see
    # optionchain.OptionContext / yfinanceGetOptions.get_realized_volatility). Fetched here
    # so it's cached alongside the rest of this ticker/expiration's data instead of hitting
    # yfinance again on every Streamlit rerun (e.g. toggling a sidebar control).
    realized_vol = yfi_module.get_realized_volatility(ticker_symbol)

    return df, target_exp, all_exps, price, change, percent_change, name, retrieval_time, realized_vol

res = None
current_price = None
if st.session_state['ticker_ready']:
    with st.spinner(f"Loading {st.session_state['ticker']} data..."):
        raw_df, target_exp, all_exps, current_price, price_change, price_pct_change, company_name, retrieval_time, realized_vol = get_cached_options_data(st.session_state['ticker'], exp_date)
    st.session_state['company_name_display'] = company_name or ''
    st.session_state['last_ticker'] = st.session_state['ticker']

    res = optionchain.main(st.session_state['ticker'],
        df=raw_df,
        expiration_date=target_exp,
        available_expiration_dates=all_exps,
        current_price=current_price,
        flip_strikes=flip_strikes,
        trim_around_strike=trim_around_strike,
        bar_scaling_mode=bar_scaling_mode,
        company_name=company_name,
        retrieval_time=retrieval_time,
        hidden_columns=hidden_columns,
        realized_vol=realized_vol)

    if res is None:
        st.warning(f"Failed to retrieve data for {st.session_state['ticker']}. The symbol might be invalid or the API is currently unavailable.")
    else:
        # Results
        st.write("---")
        display_name = f" - {res['company_name']}" if res.get('company_name') else ""
        st.subheader(f"Ticker: {st.session_state['ticker']}{display_name}")

        price_color = "#4798a5"
        # Dollar signs must be escaped (\$) - st.markdown treats unescaped $...$ as inline
        # LaTeX math, which mangles everything between two unescaped $ in this line.
        price_display = f"Current Price: \\${current_price:.2f}"
        price_change_display = "unch"
        price_change_color = "grey"
        if price_change is not None and price_pct_change is not None:
            price_change_color = "green" if price_change >= 0 else "red"
            sign = "+" if price_change > 0 else "-" if price_change < 0 else ""
            price_change_display = f" {sign}\\${abs(price_change):.2f} ({sign}{abs(price_pct_change):.2f}%)"

        st.markdown(f"<span style='color: {price_color}; font-weight: bold; font-size: 1.2em;'>{price_display}</span><span style='color: {price_change_color}; font-weight: bold; font-size: 1.2em;'> | daily change: {price_change_display}</span>", unsafe_allow_html=True)

        dte = res['context'].dte
        dte_suffix = f" ({dte} day{'s' if dte != 1 else ''} away)" if dte is not None and dte >= 0 else ""
        st.write(f"Expiration Date: {res['expiration_date']}{dte_suffix}")
        if res.get('retrieval_time'):
            retrieval_time = res['retrieval_time']
            retrieval_et = retrieval_time.astimezone(zoneinfo.ZoneInfo("America/New_York")) if retrieval_time.tzinfo else retrieval_time
            when_label = "as of" if is_market_open() else "close"
            st.caption(f"Data from: {format_relative_date(retrieval_time)} {when_label} ({retrieval_et.strftime('%H:%M')} ET)")
        else:
            st.caption("No time data available.")
        if not is_market_open():
            st.info("🌙 US Markets are currently closed. Intraday data may remain from the last close or be missing.")

        df = res['styled_dataframe']

        st.table(df)
        context: optionchain.OptionContext = res['context']

        st.write("---")
        if 'sentiment_summary_styler' in res:
            st.subheader("Market Sentiment: OTM vs ITM")
            st.table(res['sentiment_summary_styler'])
        else:
            st.warning("Sentiment summary data is missing from the results.")

        if 'technical_breakdown' in res:
            st.write("---")
            st.subheader("Technical Breakdown")
            st.table(res['technical_breakdown'])
            st.caption("⚠️ Note: These observations are based on heuristic rules and do not constitute financial advice. E.g. a position could be a directional bet **OR position hedging**")
            st.caption("No LLMs were harmed during this analysis")

PRICE_CHART_PERIODS = {
    "1D": "1d",
    "5D": "5d",
    "M": "1mo",
    "Y": "1y",
    "Max": "max",
}

if 'price_chart_period' not in st.session_state:
    st.session_state['price_chart_period'] = "1D"

@st.cache_data(show_spinner=False, ttl=300)
def get_cached_price_history(ticker_symbol, period):
    return yfi_module.get_price_history(ticker_symbol, period)

with price_chart_col:
    st.subheader("Price Chart")

    # Read the period from session_state (set by the radio below) before the widget call,
    # so the radio can be rendered after the chart while still driving this run's data.
    period_label = st.session_state['price_chart_period']
    hist_df = get_cached_price_history(st.session_state['ticker'], PRICE_CHART_PERIODS[period_label])

    info_line_parts = []

    # Dollar signs must be escaped (\$) - st.markdown treats unescaped $...$ as inline
    # LaTeX math, which mangles everything between two unescaped $ in this line.
    if current_price is not None:
        info_line_parts.append(
            f"<span style='color: #4798a5; font-weight: bold;'>Current Price: \\${current_price:,.2f}</span>"
        )

    period_change = optionchain.get_period_change(hist_df)
    if period_change is not None:
        change, pct_change = period_change
        if change > 0:
            change_color = "green"
        elif change < 0:
            change_color = "red"
        else:
            change_color = "grey"
        sign = "+" if change > 0 else "-" if change < 0 else ""
        change_text = f"{sign}\\${abs(change):,.2f} ({pct_change:+.2f}%)" if change != 0 else "Unchanged"
        info_line_parts.append(
            f"<span style='color: {change_color}; font-weight: bold;'>{period_label} change: {change_text}</span>"
        )

    if period_label == "1D" and not is_market_open() and hist_df is not None and not hist_df.empty:
        last_ts = hist_df.index[-1]
        last_ts_et = last_ts.astimezone(zoneinfo.ZoneInfo("America/New_York")) if last_ts.tzinfo else last_ts
        # The 1D chart's last bar is a 5-minute bucket labeled by its *start* time (e.g.
        # 15:55), not the market's actual close (16:00) - add the bucket width so this
        # reads as the real close time instead of looking "off by 5 minutes".
        close_time_et = last_ts_et + timedelta(minutes=5)
        info_line_parts.append(
            f"<span style='color: grey;'>{format_relative_date(last_ts)} at close ({close_time_et.strftime('%H:%M')} ET)</span>"
        )

    if info_line_parts:
        st.markdown(" | ".join(info_line_parts), unsafe_allow_html=True)

    key_levels = res['context'].get_key_price_levels() if res is not None else {}
    strike_range = res['context'].get_strike_range() if res is not None else None
    chart = optionchain.build_price_chart(hist_df, key_levels, strike_range)

    if chart is not None:
        st.altair_chart(chart, width='stretch')
    else:
        st.info("No price chart data available.")

    _, radio_col = st.columns([1, 12])
    with radio_col:
        st.radio(
            "Range", list(PRICE_CHART_PERIODS.keys()), horizontal=True, key="price_chart_period",
            label_visibility="collapsed"
        )

st.write("---")
st.markdown("[Project Github page](https://github.com/Gil-Mor/optionChainViewer)")
st.caption("🍪 This site uses cookies only for functional purposes, such as remembering your Watchlist. No marketing, analytics, or tracking cookies are used.")
