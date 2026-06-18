import streamlit as st
import optionchain
import yfinance as yf
import yfinanceGetOptions as yfi_module
from datetime import datetime
import zoneinfo

st.set_page_config(
    page_title="Option Chain Viewer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)
st.title("Option Chain Viewer")
st.markdown("[Project Github page](https://github.com/Gil-Mor/optionChainViewer)")

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

DEFAULT_TICKER = 'NVDA'

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


with search_col1:
    ticker_query = st.text_input(label="Search by Ticker", key='ticker_query', help=f"Ticker ideas: {', '.join(popular_tickers)}", on_change=use_ticker_or_name_query, args=('ticker_query',))
    if st.session_state['company_name_display']:
        st.caption(f"**{st.session_state['company_name_display']}**")

    name_query = st.text_input("Search by Company / Security Name", key='name_query', on_change=use_ticker_or_name_query, args=('name_query', ))

    # Get expiration dates after we've found the Ticker
    available_dates = get_available_dates(st.session_state['ticker'])
    if not available_dates:
        st.warning(f"No options data found for ticker: {st.session_state['ticker']}")
        st.session_state['ticker_ready'] = False

    if 'exp_date_select' not in st.session_state and qp_exp and qp_exp in available_dates:
        st.session_state['exp_date_select'] = qp_exp

    exp_date = st.selectbox("Expiration Date", options=available_dates, key='exp_date_select', help="Select an expiration date to view its option chain.")

if st.session_state['ticker_ready'] and st.session_state.get('ticker'):
    st.query_params['ticker'] = st.session_state['ticker']
    if exp_date:
        st.query_params['exp'] = exp_date

with st.sidebar:
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

@st.cache_data(show_spinner=False)
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
        price_display = f"Current Price: {current_price:.2f}"
        price_change_display = "unch"
        price_change_color = "grey"
        if price_change is not None and price_pct_change is not None:
            price_change_color = "green" if price_change >= 0 else "red"
            sign = "+" if price_change > 0 else ""
            price_change_display = f" {sign}{price_change:.2f} ({sign}{price_pct_change:.2f}%)"

        st.markdown(f"<span style='color: {price_color}; font-weight: bold; font-size: 1.2em;'>{price_display}</span><span style='color: {price_change_color}; font-weight: bold; font-size: 1.2em;'> | daily change: {price_change_display}</span>", unsafe_allow_html=True)

        dte = res['context'].dte
        dte_suffix = f" ({dte} day{'s' if dte != 1 else ''} away)" if dte is not None and dte >= 0 else ""
        st.write(f"Expiration Date: {res['expiration_date']}{dte_suffix}")
        if res.get('retrieval_time'):
            st.caption(f"Data from: {res['retrieval_time'].strftime('%Y-%m-%d %H:%M:%S')}")
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
        info_line_parts.append(
            f"<span style='color: grey;'>Data from: {format_relative_date(hist_df.index[-1])}</span>"
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
