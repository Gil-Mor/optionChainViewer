import yfinance as yf
import pandas as pd


def get_options_chain_table(symbol: str,
    expiration_date=None,
    keep_only_common_strikes: bool = True) -> tuple[pd.DataFrame, str]:
    """
    Get options chain in a formatted table with calls on left, puts on right

    Args:
        symbol (str): Stock symbol (e.g., 'AAPL')
        expiration_date (str, optional): Expiration date in format 'YYYY-MM-DD'
                                       If None, uses nearest expiration

    Returns:
        pandas.DataFrame: Formatted options chain table
    """

    try:
        # Create ticker object
        ticker = yf.Ticker(symbol)

        # Get available expiration dates
        exp_dates = ticker.options
        if not exp_dates:
            print(f"No options data available for {symbol}")
            return pd.DataFrame(), "", []

        # Use specified date or nearest expiration
        if expiration_date:
            if expiration_date not in exp_dates:
                print(f"Expiration date {expiration_date} not available.")
                print(f"Available dates: {list(exp_dates)}")
                return pd.DataFrame(), "", []
            target_exp = expiration_date
        else:
            target_exp = exp_dates[0]  # Nearest expiration

        print(f"Getting options chain for {symbol} expiring on {target_exp}")

        # Get options chain
        options_chain = ticker.option_chain(target_exp)
        calls = options_chain.calls
        puts = options_chain.puts

        # yfinance already provides change and percentChange columns
        # Just clean up any NaN values
        calls = calls.fillna(0)
        puts = puts.fillna(0)

        # Prepare calls data - use the actual change and percentChange columns
        calls_formatted = calls[['lastPrice', 'change', 'percentChange', 'volume', 'openInterest', 'strike']].copy()
        calls_formatted.columns = ['Call_LastPrice', 'Call_Change', 'Call_ChangePct', 'Call_Volume', 'Call_OpenInterest', 'Strike']

        # Prepare puts data - use the actual change and percentChange columns
        puts_formatted = puts[['lastPrice', 'change', 'percentChange', 'volume', 'openInterest', 'strike']].copy()
        puts_formatted.columns = ['Put_LastPrice', 'Put_Change', 'Put_ChangePct', 'Put_Volume', 'Put_OpenInterest', 'Put_Strike']

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
            'Call_LastPrice', 'Call_Change', 'Call_ChangePct', 'Call_Volume', 'Call_OpenInterest',
            'Strike',
            'Put_LastPrice', 'Put_Change', 'Put_ChangePct', 'Put_Volume', 'Put_OpenInterest'
        ]

        # Select and reorder columns
        result = merged[final_columns].copy()

        # Clean column names for display
        result.columns = [
            'Last Price', 'Change', '% Change', 'Volume', 'Open Interest',  # Calls
            'Strike',
            'Last Price.1', 'Change.1', '% Change.1', 'Volume.1', 'Open Interest.1'   # Puts
        ]

        # Fill NaN values with 0 and format numbers
        result = result.fillna(0)

        return result, target_exp, exp_dates

    except Exception as e:
        print(f"Error getting options data: {e}")
        return pd.DataFrame(), "", []

if __name__ == "__main__":
    symbol = "AAPL"
    df, target_exp, exp_dates = get_options_chain_table(symbol)
    print(df.head())