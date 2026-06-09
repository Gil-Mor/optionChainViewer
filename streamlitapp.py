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

if 'ticker' not in st.session_state:
    st.session_state['ticker'] = DEFAULT_TICKER
if 'use_ticker_or_name_query' not in st.session_state:
    st.session_state['use_ticker_or_name_query'] = 'ticker_query'
if 'last_ticker' not in st.session_state:
    st.session_state['last_ticker'] = ''
if 'ticker_ready' not in st.session_state:
    st.session_state['ticker_ready'] = True

st.session_state['ticker_query'] = st.session_state['ticker']
st.session_state['name_query'] = yfi_module.get_name_from_ticker(st.session_state['ticker'])
st.session_state['company_name_display'] = st.session_state['name_query']

# Ticker and Name search in the main page area
search_col1, search_col2 = st.columns([1, 2])

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

    exp_date = st.selectbox("Expiration Date", options=available_dates, index=0, help="Select an expiration date to view its option chain.")

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

    return df, target_exp, all_exps, price, change, percent_change, name, retrieval_time

if st.session_state['ticker_ready']:
    with st.spinner(f"Loading {st.session_state['ticker']} data..."):
        raw_df, target_exp, all_exps, current_price, price_change, price_pct_change, company_name, retrieval_time = get_cached_options_data(st.session_state['ticker'], exp_date)
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
        retrieval_time=retrieval_time)

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

        st.write(f"Expiration Date: {res['expiration_date']}")
        if res.get('retrieval_time'):
            st.caption(f"Data from: {res['retrieval_time'].strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            st.caption("No time data available.")
        if not is_market_open():
            st.info("🌙 US Markets are currently closed. Intraday data may remain from the last close or be missing. Open Interest should be available.")

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
