import pandas as pd
from pandas.io.formats.style import Styler
import yfinanceGetOptions as yfi
import yfinance

def readcsv(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path, delimiter="\t")
    return df


class OptionContext:
    def __init__(self,
        df: pd.DataFrame,
        ticker: str,
        current_price: float,
    ):
        self.df: pd.DataFrame = df
        self.styled_df: Styler = df.style
        self.current_price = current_price
        self.ticker = ticker
        self.calls_strike_col_name = "Strike"
        self.puts_strike_col_name = "Strike"
        self.atm_strike = get_atm_strike_from_current_price(df, current_price)
        self.atm_strike_row = df[df['Strike'] == self.atm_strike].index[0]

    def update_strike_row(self) -> None:
        self.atm_strike_row = self.df[self.df[self.calls_strike_col_name] == self.atm_strike].index[0]
        if self.calls_strike_col_name != self.puts_strike_col_name:
            puts_atm_strike_row = self.df[self.df[self.puts_strike_col_name] == self.atm_strike].index[0]
            assert self.atm_strike_row == puts_atm_strike_row, f"{self.atm_strike_row} != {puts_atm_strike_row}"

    def get_calls_strike_col_index(self) -> int:
        return self.df.columns.get_loc(self.calls_strike_col_name)

    def get_puts_strike_col_index(self) -> int:
        return self.df.columns.get_loc(self.puts_strike_col_name)

    def get_total_stats(self) -> None:
        """Calculates OTM, ATM, and Total metrics for Calls and Puts."""
        self.otm_calls = self.df[self.df[self.calls_strike_col_name] > self.current_price]
        self.otm_calls_open_interest_sum = self.otm_calls["Open Interest"].sum()
        self.otm_calls_volume_sum = self.otm_calls["Volume"].sum()
        self.otm_puts = self.df[self.df[self.puts_strike_col_name] < self.current_price]
        self.otm_puts_open_interest_sum = self.otm_puts["Open Interest.1"].sum()
        self.otm_puts_volume_sum = self.otm_puts["Volume.1"].sum()

        # ATM stats (specifically at the ATM Strike)
        atm_calls = self.df[self.df[self.calls_strike_col_name] == self.atm_strike]
        self.atm_calls_open_interest_sum = atm_calls["Open Interest"].sum()
        self.atm_calls_volume_sum = atm_calls["Volume"].sum()

        atm_puts = self.df[self.df[self.puts_strike_col_name] == self.atm_strike]
        self.atm_puts_open_interest_sum = atm_puts["Open Interest.1"].sum()
        self.atm_puts_volume_sum = atm_puts["Volume.1"].sum()

        # Total stats for the entire chain
        self.total_calls_open_interest_sum = self.df["Open Interest"].sum()
        self.total_calls_volume_sum = self.df["Volume"].sum()
        self.total_puts_open_interest_sum = self.df["Open Interest.1"].sum()
        self.total_puts_volume_sum = self.df["Volume.1"].sum()

        # Max values for normalization in Modes 2 and 3
        # Standard UI practice: scale relative to the Max strike in the group so bars are visible
        self.otm_max_oi = max(self.otm_calls["Open Interest"].max() if not self.otm_calls.empty else 0,
                              self.otm_puts["Open Interest.1"].max() if not self.otm_puts.empty else 0, 1)
        self.otm_max_vol = max(self.otm_calls["Volume"].max() if not self.otm_calls.empty else 0,
                               self.otm_puts["Volume.1"].max() if not self.otm_puts.empty else 0, 1)

        atm_df = self.df[self.df[self.calls_strike_col_name] == self.atm_strike]
        self.atm_max_oi = max(atm_df["Open Interest"].max() if not atm_df.empty else 0,
                              atm_df["Open Interest.1"].max() if not atm_df.empty else 0, 1)
        self.atm_max_vol = max(atm_df["Volume"].max() if not atm_df.empty else 0,
                               atm_df["Volume.1"].max() if not atm_df.empty else 0, 1)

        self.total_max_oi = max(self.df["Open Interest"].max(), self.df["Open Interest.1"].max(), 1)
        self.total_max_vol = max(self.df["Volume"].max(), self.df["Volume.1"].max(), 1)

    def get_sentiment_summary_styler(self) -> Styler:
        """Creates a styled summary table for OTM, ATM, and Total sentiment using proportional bars."""
        data = {
            'Metric': [
                'OTM Open Interest', 'ATM Open Interest', 'Total Open Interest',
                'OTM Volume', 'ATM Volume', 'Total Volume'
            ],
            'Calls': [
                self.otm_calls_open_interest_sum, self.atm_calls_open_interest_sum, self.total_calls_open_interest_sum,
                self.otm_calls_volume_sum, self.atm_calls_volume_sum, self.total_calls_volume_sum
            ],
            'Puts': [
                self.otm_puts_open_interest_sum, self.atm_puts_open_interest_sum, self.total_puts_open_interest_sum,
                self.otm_puts_volume_sum, self.atm_puts_volume_sum, self.total_puts_volume_sum
            ],
        }
        sentiment_df = pd.DataFrame(data)
        sentiment_df['P/C Ratio'] = sentiment_df.apply(
            lambda x: x['Puts'] / x['Calls'] if x['Calls'] > 0 else 0, axis=1
        )

        styler = sentiment_df.style.hide(axis='index')
        styler = style_proportional_bars(
            sentiment_df,
            styler,
            'Calls',
            'Puts',
            self,
            left_color="#66C76673",
            right_color="#B9696384",
            text_color="white"
        )
        styler = styler.format({'Calls': '{:,.0f}', 'Puts': '{:,.0f}', 'P/C Ratio': '{:.2f}'})
        styler = styler.set_properties(**{'text-align': 'center'})
        return styler

    def color_change_values(self) -> None:
        def color_gradient(val):
            if pd.isna(val) or val == 0:
                return None

            # Cap the scaling at 100% so that outliers (e.g., 2000%)
            # don't result in blindingly opaque colors.
            max_val = 80
            capped_val = min(abs(val), max_val)

            # Scale alpha between 0.1 and 0.7.
            # 0.7 is bright enough to show significance without being blinding.
            alpha = (capped_val / max_val) * 0.6 + 0.1

            if val > 0:
                return f'background-color: rgba(0, 255, 0, {alpha})'
            return f'background-color: rgba(255, 0, 0, {alpha})'
        self.styled_df = self.styled_df.map(color_gradient, subset=['% Change', '% Change.1'])


def flip_rows_around_strike(df, split_point=None):
    """
    Split a DataFrame and flip the right portion rows vertically, optionally around a pivot.

    Parameters:
    df: pandas DataFrame
    split_point: int, column index where to split (if None, splits in middle)
    pivot_row: int, row index to use as pivot (if None, flips all rows)

    Returns:
    pandas DataFrame with left portion + right portion with flipped rows
    """

    # Determine split point (middle of columns if not specified)
    if split_point is None:
        split_point = len(df.columns) // 2

    # Split the DataFrame
    left_portion = df.iloc[:, :split_point]
    right_portion = df.iloc[:, split_point:]

    # Flip right portion:
    # Only flip rows vertically (reverse row order)
    # Keep columns in original order

    # Original behavior - flip all rows
    right_flipped = right_portion.iloc[::-1, :]

    left_to_use = left_portion
    right_to_use = right_flipped

    # Combine left portion with flipped right portion
    # Reset index to ensure proper concatenation
    left_portion_reset = left_to_use.reset_index(drop=True)
    right_flipped_reset = right_to_use.reset_index(drop=True)

    # Concatenate horizontally
    result_df = pd.concat([left_portion_reset, right_flipped_reset], axis=1)

    return result_df


def flip_right_half_columns(df: pd.DataFrame, start: int = None) -> pd.DataFrame:
    if start is None:
        start = len(df.columns) // 2
    left = list(df.columns[:start])

    # Reverse columns to the right.
    right = list(df.columns[start:])[::-1]

    return df[left + right]


def duplicate_and_rename_strike(df: pd.DataFrame) -> pd.DataFrame:
    strike_idx = df.columns.get_loc("Strike")

    left = list(df.columns[:strike_idx])
    middle = ['Calls Strike', 'Puts Strike']  # Renamed columns
    right = list(df.columns[strike_idx+1:])

    new_columns = left + middle + right
    df_result = df[left + [df.columns[strike_idx], df.columns[strike_idx]] + right].copy()
    df_result.columns = new_columns

    return df_result

def highlight_cell(styler: Styler, col_name: str, val: float, color: str = "#4798a5") -> Styler:
    def style_atm_strike(s, target_val):
        return [f'background-color: {color}; font-weight: bold' if val == target_val else None for val in s]

    styler = styler.apply(style_atm_strike, subset=[col_name], target_val=val)

    return styler


def format_style(styler: Styler):
    styler = styler.format(precision=2)
    styler = styler.set_properties(**{'text-align': 'center !important'})
    styler = styler.set_table_styles([
        {'selector': 'td', 'props': [('text-align', 'center !important')]},
        {'selector': 'th', 'props': [('text-align', 'center !important')]}
    ])
    return styler


def convert_comma_number(value) -> float:
    """Convert comma-separated numbers to float."""
    try:
        if pd.isna(value):
            return float('nan')
        # Convert to string and remove commas
        str_val = str(value).replace(',', '')
        return float(str_val)
    except (ValueError, TypeError):
        return float('nan')

def style_proportional_bars(df: pd.DataFrame, styler: Styler, left_col, right_col, context: OptionContext, mode='Per Strike (Row)', left_color='green', right_color='red', text_color='white'):
    """
    Style two columns with proportional horizontal bars.
    Supports three scaling modes. Modes 2 and 3 use Max-normalization for better visibility.
    """
    is_oi = "Open Interest" in left_col

    # For Modes 2 and 3, we scale relative to the Maximum value in that category
    # rather than the Sum. This is the UI standard for in-cell data bars.
    ref_all = context.total_max_oi if is_oi else context.total_max_vol
    ref_otm = context.otm_max_oi if is_oi else context.otm_max_vol
    ref_atm = context.atm_max_oi if is_oi else context.atm_max_vol

    # Row-local sum (used for Mode 1)
    # We pre-calculate sums if needed, but row-local is handled inside the loop.

    def apply_bar_styling(s):
        styles = [None] * len(s)

        # Only apply styling to the specified columns
        if s.name == left_col:
            # Left column: bars from left to right
            for idx in s.index:
                try:
                    left_val = convert_comma_number(df.loc[idx, left_col])
                    right_val = convert_comma_number(df.loc[idx, right_col])

                    if pd.isna(left_val): continue

                    if mode == "Relative to Full Chain":
                        denominator = ref_all
                    elif mode == "Relative to OTM/ATM Total":
                        strike = df.loc[idx, context.calls_strike_col_name]
                        if strike == context.atm_strike:
                            denominator = ref_atm
                        elif strike > context.current_price: # OTM Call
                            denominator = ref_otm
                        else: # ITM Call - fall back to total or row-local
                            denominator = ref_all
                    else: # Default: Per Strike (Row)
                        denominator = left_val + (right_val if not pd.isna(right_val) else 0)

                    if denominator > 0:
                        percentage = (left_val / denominator) * 100
                        percentage = min(percentage, 100)
                        styles[idx] = f'''
                            background: linear-gradient(
                                to right,
                                {left_color} 0%,
                                {left_color} {percentage:.1f}%,
                                transparent {percentage:.1f}%,
                                transparent 100%
                            );
                            color: {text_color};
                            font-weight: bold;
                            text-align: center;
                        '''
                except (ValueError, TypeError, KeyError):
                    # Skip styling if there's any error
                    continue

        elif s.name == right_col:
            # Right column: bars from right to left
            for idx in s.index:
                try:
                    left_val = convert_comma_number(df.loc[idx, left_col])
                    right_val = convert_comma_number(df.loc[idx, right_col])

                    if pd.isna(right_val): continue

                    if mode == "Relative to Full Chain":
                        denominator = ref_all
                    elif mode == "Relative to OTM/ATM Total":
                        strike = df.loc[idx, context.puts_strike_col_name]
                        if strike == context.atm_strike:
                            denominator = ref_atm
                        elif strike < context.current_price: # OTM Put
                            denominator = ref_otm
                        else: # ITM Put
                            denominator = ref_all
                    else: # Default: Per Strike (Row)
                        denominator = (left_val if not pd.isna(left_val) else 0) + right_val

                    if denominator > 0:
                        percentage = (right_val / denominator) * 100
                        percentage = min(percentage, 100)
                        styles[idx] = f'''
                            background: linear-gradient(
                                to left,
                                {right_color} 0%,
                                {right_color} {percentage:.1f}%,
                                transparent {percentage:.1f}%,
                                transparent 100%
                            );
                            color: {text_color};
                            font-weight: bold;
                            text-align: center;
                        '''
                except (ValueError, TypeError, KeyError):
                    # Skip styling if there's any error
                    continue

        return styles

    return styler.apply(apply_bar_styling, axis=0)

def get_atm_strike_from_current_price(
    df: pd.DataFrame,
    current_price: float
) -> float:
    if df.empty or current_price is None:
        return 0.0
    # Efficiently find the strike price closest to the current price
    closest_strike = df["Strike"].iloc[(df["Strike"] - current_price).abs().idxmin()]
    return float(closest_strike)


def trim_rows_symmetric_radius(
    df: pd.DataFrame,
    pivot_row: int,
    rows_to_trim: int
) -> pd.DataFrame:
    """
    Trim a DataFrame symmetrically around a given pivot row.

    Parameters:
    df: pandas DataFrame to trim
    pivot_row: int, row index to use as pivot center
    rows_to_trim: Optional fixed number of rows to trim around ATM strike.
    if 0 - do the normal symmetric trim around the ATM (ATM in the middle of table)

    Returns:
    pandas DataFrame trimmed symmetrically around pivot_row
    """

    if pivot_row < 0 or pivot_row >= len(df):
        raise ValueError(f"Pivot row {pivot_row} is out of bounds for DataFrame with {len(df)} rows")

    # Calculate how many rows we can include symmetrically around pivot
    rows_before_pivot = pivot_row
    rows_after_pivot = len(df) - pivot_row - 1
    symmetric_radius = min(rows_before_pivot, rows_after_pivot)
    if rows_to_trim:
        symmetric_radius = min(rows_before_pivot, rows_after_pivot, rows_to_trim)


    # Define the symmetric range around pivot
    start_row = pivot_row - symmetric_radius
    end_row = pivot_row + symmetric_radius + 1  # +1 because end is exclusive

    # Return the symmetrically trimmed DataFrame
    trimmed_df = df.iloc[start_row:end_row].reset_index(drop=True)

    return trimmed_df


def calls_puts_side_by_side_distance_from_strike(
    df_context: OptionContext,
    flip_strikes: bool = False,
    trim_around_strike: int = 0,
    bar_scaling_mode: str = 'Per Strike (Row)'
) -> OptionContext:

    if trim_around_strike:
        df_context.df = trim_rows_symmetric_radius(df_context.df, pivot_row=df_context.atm_strike_row, rows_to_trim=trim_around_strike)
        # Rows indexes changed. Get ATM strike row again
        df_context.update_strike_row()

    if flip_strikes:
        df_context.df = trim_rows_symmetric_radius(df_context.df, pivot_row=df_context.atm_strike_row, rows_to_trim=trim_around_strike)
        df_context.df = duplicate_and_rename_strike(df_context.df)
        df_context.calls_strike_col_name = "Calls Strike"
        df_context.puts_strike_col_name = "Puts Strike"
        df_context.df = flip_rows_around_strike(df_context.df)
        # Rows indexes changed. Get ATM strike row again
        df_context.update_strike_row()

    df_context.df = flip_right_half_columns(df_context.df, start=df_context.get_puts_strike_col_index() + 1)


    df_context.styled_df = df_context.df.style
    df_context.styled_df = style_proportional_bars(df_context.df, df_context.styled_df, 'Open Interest', 'Open Interest.1', df_context, bar_scaling_mode, "#64a375c6", "#ad6368c7")
    df_context.styled_df = style_proportional_bars(df_context.df, df_context.styled_df, 'Volume', 'Volume.1', df_context, bar_scaling_mode, "#8dc170b4", "#e6957ea6")
    df_context.styled_df = format_style(df_context.styled_df)
    df_context.styled_df = highlight_cell(df_context.styled_df, df_context.calls_strike_col_name, df_context.atm_strike)
    df_context.styled_df = highlight_cell(df_context.styled_df, df_context.puts_strike_col_name, df_context.atm_strike)
    return df_context


def main(
    ticker: str,
    df: pd.DataFrame = None,
    filepath: str = None,
    expiration_date: str = None,
    available_expiration_dates: list = None,
    current_price: float = None,
    flip_strikes: bool = False,
    trim_around_strike: int = 0,
    bar_scaling_mode: str = 'Per Strike (Row)'
):
    if df is not None:
        pass
    elif filepath:
        df = readcsv(filepath)
    else:
        df, expiration_date, available_expiration_dates = yfi.get_options_chain_table(ticker, expiration_date)

    if df is None or df.empty:
        return None

    if not current_price:
        current_price = yfinance.Ticker(ticker).info['regularMarketPrice']
        current_price = float(current_price)
        print(f"Current price is: {current_price}")

    df_context = OptionContext(df, ticker, current_price)

    # Calculate stats on the full dataframe before trimming/flipping
    df_context.get_total_stats()

    df_context = calls_puts_side_by_side_distance_from_strike(
        df_context,
        flip_strikes,
        trim_around_strike,
        bar_scaling_mode
    )

    df_context.color_change_values()

    return {
        "styled_dataframe": df_context.styled_df,
        "current_price": current_price,
        "expiration_date": expiration_date,
        "available_expiration_dates": available_expiration_dates,
        "context": df_context,
        "sentiment_summary_styler": df_context.get_sentiment_summary_styler()
    }
