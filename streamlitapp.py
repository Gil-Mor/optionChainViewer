import streamlit as st
import optionchain
import yfinance as yf
from datetime import datetime
import zoneinfo

st.set_page_config(
    page_title="Option Chain Viewer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
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

popular_tickers = set([
        'AAPL', 'AMZN', 'GOOGL','META', 'MSFT', 'NVDA', 'TSLA'
    ])

@st.cache_data
def get_available_dates(ticker_symbol):
    try:
        return yf.Ticker(ticker_symbol).options
    except Exception:
        return []

with st.sidebar:
    st.header("Settings")
    st.write(f"Ticker ideas: {', '.join(popular_tickers)}")
    ticker = st.text_input(label="Ticker", value="NVDA", placeholder="NVDA")

    available_dates = get_available_dates(ticker)
    if not available_dates:
        st.error(f"No options data found for ticker: {ticker}")
        st.stop()

    exp_date = st.selectbox("Expiration Date", options=available_dates, index=0, help="Select an expiration date to view its option chain.")

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
        ["Relative to OTM/ATM Total", "Per Strike (Row)", "Relative to Full Chain"],
        help="Mode 1: Relative to OTM/ATM sums. Mode 2: Per Strike Price. Mode 3: Relative to total chain sums."
    )

@st.cache_data(show_spinner=False)
def get_cached_options_data(ticker_symbol, selected_exp):
    """Fetches raw data and price, cached by ticker and expiration."""
    import yfinanceGetOptions as yfi_module
    df, target_exp, all_exps = yfi_module.get_options_chain_table(ticker_symbol, selected_exp)

    ticker_obj = yf.Ticker(ticker_symbol)
    fast = ticker_obj.fast_info
    price = fast.get('last_price') or ticker_obj.info.get('regularMarketPrice')
    prev_close = fast.get('previous_close') or ticker_obj.info.get('regularMarketPreviousClose')

    change = None
    percent_change = None
    if price and prev_close:
        change = price - prev_close
        percent_change = (change / prev_close) * 100

    return df, target_exp, all_exps, price, change, percent_change

with st.spinner(f"Loading {ticker} data..."):
    raw_df, target_exp, all_exps, current_price, price_change, price_pct_change = get_cached_options_data(ticker, exp_date)

res = optionchain.main(ticker,
    df=raw_df,
    expiration_date=target_exp,
    available_expiration_dates=all_exps,
    current_price=current_price,
    flip_strikes=flip_strikes,
    trim_around_strike=trim_around_strike,
    bar_scaling_mode=bar_scaling_mode)

if res is None:
    st.error(f"Failed to retrieve data for {ticker}. The symbol might be invalid or the API is currently unavailable.")
    st.stop()

# Results
st.write("---")
st.subheader(f"Ticker: {ticker}")

price_color = "#4798a5"
price_display = f"Current Price: {current_price:.2f}"
price_change_display = "unch"
price_change_color = "grey"
if price_change is not None and price_pct_change is not None:
    price_change_color = "green" if price_change >= 0 else "red"
    sign = "+" if price_change > 0 else ""
    price_change_display = f" {sign}{price_change:.2f} ({sign}{price_pct_change:.2f}%)"

st.markdown(f"<span style='color: {price_color}; font-weight: bold; font-size: 1.2em;'>{price_display}</span><span style='color: {price_change_color}; font-weight: bold; font-size: 1.2em;'> | daily change: {price_change_display}</span>", unsafe_allow_html=True)

# st.markdown(f"<span style='color: {price_change_color}; font-weight: bold; font-size: 1.2em;'>{price_change_display}</span>", unsafe_allow_html=True)

st.write(f"Expiration Date: {res['expiration_date']}")
if not is_market_open():
    st.info("🌙 US Markets are currently closed. Open Interest are not available.")

df = res['styled_dataframe']
st.table(df)
context: optionchain.OptionContext = res['context']

st.write("---")
if 'sentiment_summary_styler' in res:
    st.subheader("Market Sentiment Summary")
    st.table(res['sentiment_summary_styler'])
else:
    st.warning("Sentiment summary data is missing from the results.")

if 'technical_breakdown' in res:
    st.write("---")
    st.subheader("Technical Breakdown")
    st.table(res['technical_breakdown'])
    st.caption("Disclaimer!!!: These observations are based on heuristic rules and do not constitute financial advice.")