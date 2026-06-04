import streamlit as st
import optionchain
import yfinance as yf
from datetime import datetime
import zoneinfo

st.set_page_config(layout="wide")
st.title("Option Chain")

popular_tickers = set([
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'NFLX',
        'AMD', 'INTC', 'CRM', 'PYPL', 'ADBE', 'UBER', 'ZOOM', 'SHOP',
        'SQ', 'ROKU', 'TWLO', 'ZM', 'PELOTON', 'SNOW', 'PLTR', 'GME', 'GOOG',
        'AMC', 'BB', 'NOK', 'SPCE', 'NIO', 'XPEV', 'AMAT', 'BBAI', 'WDAY', 'WMT', 'TGT', 'NVO'
    ])

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


# Input
st.write(f"Ticker ideas: {', '.join(popular_tickers)}")
ticker = st.text_input(label="Ticker", value="NVDA", placeholder="NVDA")

@st.cache_data
def get_available_dates(ticker_symbol):
    try:
        return yf.Ticker(ticker_symbol).options
    except Exception:
        return []

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
trim_around_strike = st.number_input(label="Trim table around strike. 0 to not trim.", min_value=0, value=10)

if not is_market_open():
    st.info("🌙 US Markets are currently closed. Open Interest are not available.")

with st.spinner("Fetching option chain data..."):
    # main
    res = optionchain.main(ticker,
        expiration_date=exp_date,
        flip_strikes=flip_strikes,
        trim_around_strike=trim_around_strike)

if res is None:
    st.error(f"Failed to retrieve data for {ticker}. The symbol might be invalid or the API is currently unavailable.")
    st.stop()

# Results
st.write("---")
st.write(f"Current Price: {res['current_price']}")
st.write(f"Expiration Date: {res['expiration_date']}")
df = res['styled_dataframe']
st.table(df)
context: optionchain.OptionContext = res['context']
st.write(f"Calls Total OTM Open Interest: {context.otm_calls_open_interest_sum}")
st.write(f"Puts Total OTM Open Interest: {context.otm_puts_open_interest_sum}")
st.write(f"Calls Total OTM Volume: {context.otm_calls_volume_sum}")
st.write(f"Puts Total OTM Volume: {context.otm_puts_volume_sum}")