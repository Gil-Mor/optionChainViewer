import streamlit as st
import optionchain

st.set_page_config(layout="wide")
st.title("Option Chain")

popular_tickers = set([
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'NFLX',
        'AMD', 'INTC', 'CRM', 'PYPL', 'ADBE', 'UBER', 'ZOOM', 'SHOP',
        'SQ', 'ROKU', 'TWLO', 'ZM', 'PELOTON', 'SNOW', 'PLTR', 'GME', 'GOOG',
        'AMC', 'BB', 'NOK', 'SPCE', 'NIO', 'XPEV', 'AMAT', 'BBAI', 'WDAY', 'WMT', 'TGT', 'NVO'
    ])

# Input
st.write(f"Ticker ideas: {', '.join(popular_tickers)}")
ticker = st.text_input(label="Ticker", value="NVDA", placeholder="NVDA")
exp_date = st.text_input(label="Expiration Date (Optional). Use nearest if None",  placeholder="YYYY-MM-DD or leave empty for nearest date")
flip_strikes = st.checkbox("Flip Put Strikes to see prices with same distance from strike on the same row.")
trim_around_strike = st.number_input(label="Trim table around strike. 0 to not trim.", min_value=0, value=10)

# main
res = optionchain.main(ticker,
    expiration_date=exp_date,
    flip_strikes=flip_strikes,
    trim_around_strike=trim_around_strike)

# Results
st.write("="*80)
st.write(f"Current Price: {res['current_price']}")
st.write(f"Expiration Date: {res['expiration_date']}")
available_expiration_dates = res['available_expiration_dates']
st.write(f"More Available Expiration Dates: {available_expiration_dates[:min(10, len(available_expiration_dates))]} ...")
df = res['styled_dataframe']
st.table(df)
context: optionchain.OptionContext = res['context']
st.write(f"Calls Total OTM Open Interest: {context.otm_calls_open_interest_sum}")
st.write(f"Puts Total OTM Open Interest: {context.otm_puts_open_interest_sum}")
st.write(f"Calls Total OTM Volume: {context.otm_calls_volume_sum}")
st.write(f"Puts Total OTM Volume: {context.otm_puts_volume_sum}")