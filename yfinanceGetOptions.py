import math
import yfinance as yf
import pandas as pd
from datetime import datetime

def search_ticker(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
    except:
        return None
    return ticker

def get_name_from_ticker(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        return ticker.info.get('longName')
    except:
        return None

def get_options_chain_table(symbol: str,
    expiration_date=None,
    keep_only_common_strikes: bool = True) -> tuple[pd.DataFrame, str, list, datetime]:
    """
    Get options chain in a formatted table with calls on left, puts on right

    Args:
        symbol (str): Stock symbol (e.g., 'AAPL')
        expiration_date (str, optional): Expiration date in format 'YYYY-MM-DD'
                                       If None, uses nearest expiration

    Returns:
        tuple: (pandas.DataFrame, target_exp, exp_dates, retrieval_time)
    """

    try:
        # Create ticker object
        ticker = yf.Ticker(symbol)

        # Get available expiration dates
        exp_dates = ticker.options
        if not exp_dates:
            print(f"No options data available for {symbol}")
            return pd.DataFrame(), "", [], None

        # Use specified date or nearest expiration
        if expiration_date:
            if expiration_date not in exp_dates:
                print(f"Expiration date {expiration_date} not available.")
                print(f"Available dates: {list(exp_dates)}")
                return pd.DataFrame(), "", [], None
            target_exp = expiration_date
        else:
            target_exp = exp_dates[0]  # Nearest expiration

        print(f"Getting options chain for {symbol} expiring on {target_exp}")

        # Get options chain
        options_chain = ticker.option_chain(target_exp)
        calls = options_chain.calls
        puts = options_chain.puts

        # Extract retrieval time from metadata if available, else use None
        retrieval_time = None
        underlying_info = getattr(options_chain, 'underlying', {})
        if underlying_info and 'regularMarketTime' in underlying_info:
            retrieval_time = datetime.fromtimestamp(underlying_info['regularMarketTime'])

        # yfinance already provides change and percentChange columns
        # Just clean up any NaN values
        calls = calls.fillna(0)
        puts = puts.fillna(0)

        # Aggregate by strike to handle cases where multiple contracts exist for the same strike.
        # This prevents Cartesian product inflation during the merge.
        agg_map = {'lastPrice': 'mean', 'change': 'mean', 'percentChange': 'mean', 'volume': 'sum', 'openInterest': 'sum', 'impliedVolatility': 'mean', 'bid': 'mean', 'ask': 'mean'}
        calls = calls.groupby('strike').agg(agg_map).reset_index()
        puts = puts.groupby('strike').agg(agg_map).reset_index()

        # Prepare calls data - use the actual change and percentChange columns
        calls_formatted = calls[['lastPrice', 'change', 'percentChange', 'volume', 'openInterest', 'impliedVolatility', 'bid', 'ask', 'strike']].copy()
        calls_formatted.columns = ['Call_LastPrice', 'Call_Change', 'Call_ChangePct', 'Call_Volume', 'Call_OpenInterest', 'Call_IV', 'Call_Bid', 'Call_Ask', 'Strike']

        # Prepare puts data - use the actual change and percentChange columns
        puts_formatted = puts[['lastPrice', 'change', 'percentChange', 'volume', 'openInterest', 'impliedVolatility', 'bid', 'ask', 'strike']].rename(
            columns={'lastPrice': 'Put_LastPrice', 'change': 'Put_Change', 'percentChange': 'Put_ChangePct', 'volume': 'Put_Volume', 'openInterest': 'Put_OpenInterest', 'impliedVolatility': 'Put_IV', 'bid': 'Put_Bid', 'ask': 'Put_Ask', 'strike': 'Put_Strike'})

        # Merge on strike price
        if keep_only_common_strikes:
            # merge on strike prices which exist in both chains.
            # This leads to easier handling later.
            # E.g. there was a problem with trimming around the ATM strike
            merged = pd.merge(calls_formatted, puts_formatted,
                    left_on='Strike', right_on='Put_Strike', how='inner')
        else:
            # Keep all strikes - even ones which are only in one chain.
            merged = pd.merge(calls_formatted, puts_formatted,
                            left_on='Strike', right_on='Put_Strike', how='outer')

        # Sort by strike price
        merged = merged.sort_values('Strike')

        # Reorder columns to match desired format
        final_columns = [
            'Call_LastPrice', 'Call_Change', 'Call_ChangePct', 'Call_Volume', 'Call_OpenInterest', 'Call_IV', 'Call_Bid', 'Call_Ask',
            'Strike',
            'Put_LastPrice', 'Put_Change', 'Put_ChangePct', 'Put_Volume', 'Put_OpenInterest', 'Put_IV', 'Put_Bid', 'Put_Ask'
        ]

        # Select and reorder columns
        result = merged[final_columns].copy()

        # Clean column names for display
        result.columns = [
            'Last Price', 'Change', '% Change', 'Volume', 'Open Interest', 'IV', 'Bid', 'Ask',  # Calls
            'Strike',
            'Last Price.1', 'Change.1', '% Change.1', 'Volume.1', 'Open Interest.1', 'IV.1', 'Bid.1', 'Ask.1'   # Puts
        ]

        # Fill NaN values with 0 and format numbers
        result = result.fillna(0)

        # Spread % (liquidity flag): NaN (not 0) when there's no real bid/ask quote at all,
        # so it renders as "-" instead of a misleading "0.0%" that would look like a perfect
        # market. Breakeven is just strike +/- premium, the standard definition.
        calls_mid = (result['Ask'] + result['Bid']) / 2
        result['Spread %'] = ((result['Ask'] - result['Bid']) / calls_mid * 100).where(calls_mid > 0)
        result['Breakeven'] = result['Strike'] + result['Last Price']

        puts_mid = (result['Ask.1'] + result['Bid.1']) / 2
        result['Spread %.1'] = ((result['Ask.1'] - result['Bid.1']) / puts_mid * 100).where(puts_mid > 0)
        result['Breakeven.1'] = result['Strike'] - result['Last Price.1']

        # Re-center on Strike: calls-only columns to its left, puts-only to its right.
        # optionchain.py's flip/split logic depends on this symmetry.
        result = result[[
            'Last Price', 'Change', '% Change', 'Volume', 'Open Interest', 'IV', 'Bid', 'Ask', 'Spread %', 'Breakeven',
            'Strike',
            'Last Price.1', 'Change.1', '% Change.1', 'Volume.1', 'Open Interest.1', 'IV.1', 'Bid.1', 'Ask.1', 'Spread %.1', 'Breakeven.1',
        ]]

        return result, target_exp, exp_dates, retrieval_time

    except Exception as e:
        print(f"Error getting options data: {e}")
        return pd.DataFrame(), "", [], None

def get_price_history(symbol: str, period: str = "1mo") -> pd.DataFrame:
    """Fetch historical price data for charting.

    Args:
        symbol (str): Stock symbol (e.g. 'AAPL').
        period (str): One of '1d', '5d', '1mo', '1y', 'max'.

    Returns:
        pandas.DataFrame: Indexed by datetime with OHLCV columns. Empty DataFrame on failure.
    """
    # This is a rough-picture sparkline, not a trading chart, so bias toward fewer points:
    # dense intraday bars only for the short ranges where they stay cheap (~70-130 rows),
    # then step down sharply for longer ranges to keep weight low (1y ~53 rows, max ~a
    # few hundred rows even for decades-old tickers) instead of fetching daily bars
    # all the way out.
    interval_map = {
        "1d": "5m",
        "5d": "15m",
        "1mo": "1d",
        "1y": "1wk",
        "max": "1mo",
    }
    interval = interval_map.get(period, "1d")
    try:
        history = yf.Ticker(symbol).history(period=period, interval=interval)
        return history if history is not None else pd.DataFrame()
    except Exception as e:
        print(f"Error getting price history for '{symbol}': {e}")
        return pd.DataFrame()


REALIZED_VOL_LOOKBACK_DAYS = 20


def get_realized_volatility(symbol: str, lookback_days: int = REALIZED_VOL_LOOKBACK_DAYS) -> float | None:
    """Annualized realized volatility from trailing daily closes.

    Standard substitute for IV Rank/Percentile: yfinance has no historical-options
    endpoint, so there's no way to compare today's IV to its own past daily values.
    Comparing IV to recent *realized* volatility (computed here from actual price
    history, which yfinance does provide) is the closest equivalent signal available.

    Returns None on any failure (network error, too little history) rather than
    raising, since this is a supplementary metric - the rest of the chain should
    still render without it.
    """
    try:
        # Fetch comfortably more calendar days than lookback_days trading days need,
        # to absorb weekends/holidays.
        history = yf.Ticker(symbol).history(period="2mo", interval="1d")
        if history is None or history.empty or "Close" not in history.columns:
            return None
        closes = history["Close"].tail(lookback_days + 1)
        if len(closes) < 2:
            return None
        log_returns = closes.apply(math.log).diff().dropna()
        if log_returns.empty:
            return None
        return float(log_returns.std() * math.sqrt(252))
    except Exception as e:
        print(f"Error computing realized volatility for '{symbol}': {e}")
        return None


def get_ticker_from_name(name: str) -> str | None:
    """Return the best-match ticker symbol for a company/security name."""
    try:
        results = yf.Search(name, max_results=1).quotes
        if results:
            return results[0].get('symbol')
    except Exception as e:
        print(f"Error searching for '{name}': {e}")
    return None


if __name__ == "__main__":
    symbol = "AAPL"
    df, target_exp, exp_dates, retrieval_time = get_options_chain_table(symbol)
    print(df.head())