import math
import pandas as pd
from pandas.io.formats.style import Styler
from datetime import datetime
import yfinanceGetOptions as yfi
import yfinance

def readcsv(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path, delimiter="\t", thousands=",")
    return df


class OptionContext:
    def __init__(self,
        df: pd.DataFrame,
        ticker: str,
        current_price: float,
        expiration_date: str = None,
        realized_vol: float = None,
    ):
        self.df: pd.DataFrame = df
        # Snapshot of the full, untrimmed/unflipped chain. Trimming drops far strikes and
        # flipping breaks the call/put strike alignment per row, so anything that needs
        # every strike's true Call/Put pairing (e.g. Max Pain) must read from this instead
        # of self.df.
        self.original_df: pd.DataFrame = df.copy()
        self.styled_df: Styler = df.style
        self.current_price = current_price
        self.ticker = ticker
        self.calls_strike_col_name = "Strike"
        self.puts_strike_col_name = "Strike"
        self.atm_strike = get_atm_strike_from_current_price(df, current_price)
        self.atm_strike_row = df[df['Strike'] == self.atm_strike].index[0]

        # Days to expiration, used by calculate_implied_move(). None if expiration_date
        # wasn't supplied (e.g. a CSV-loaded chain via main(filepath=...)) or fails to parse.
        self.dte: int | None = None
        if expiration_date:
            try:
                exp_date = datetime.strptime(expiration_date, '%Y-%m-%d').date()
                self.dte = (exp_date - datetime.now().date()).days
            except (ValueError, TypeError):
                self.dte = None

        # Trailing annualized realized volatility, used as an IV Rank/Percentile substitute
        # (see yfinanceGetOptions.get_realized_volatility - true IVP needs daily IV history
        # yfinance doesn't expose). None if not supplied or unavailable.
        self.realized_vol: float | None = realized_vol

    def update_strike_row(self) -> None:
        self.atm_strike_row = self.df[self.df[self.calls_strike_col_name] == self.atm_strike].index[0]
        if self.calls_strike_col_name != self.puts_strike_col_name:
            puts_atm_strike_row = self.df[self.df[self.puts_strike_col_name] == self.atm_strike].index[0]
            assert self.atm_strike_row == puts_atm_strike_row, f"{self.atm_strike_row} != {puts_atm_strike_row}"

    def get_calls_strike_col_index(self) -> int:
        return self.df.columns.get_loc(self.calls_strike_col_name)

    def get_puts_strike_col_index(self) -> int:
        return self.df.columns.get_loc(self.puts_strike_col_name)

    def get_key_price_levels(self) -> dict[str, float]:
        """Returns key option-derived price levels (OI walls) for charting.

        Just Resistance/Support: ATM Strike sits right on top of the price line itself, and
        Max Pain often lands on the same strike as ATM (see calculate_max_pain) - both made
        the chart's line labels overlap without adding information beyond the walls.

        Computed from self.df (the displayed/trimmed chain) to match the "Institutional
        Walls" rule in get_technical_breakdown() - both must agree on the same Resistance/
        Support numbers. Deliberately NOT original_df: a deep-OTM strike with old, large
        OI can win the full-chain max but isn't a meaningful near-term level, and showing
        a different wall on the chart than in the TA table is just confusing.
        """
        max_call_idx = self.df["Open Interest"].idxmax()
        max_put_idx = self.df["Open Interest.1"].idxmax()

        return {
            "Resistance (Call Wall)": float(self.df.loc[max_call_idx, self.calls_strike_col_name]),
            "Support (Put Wall)": float(self.df.loc[max_put_idx, self.puts_strike_col_name]),
        }

    def get_strike_range(self) -> tuple[float, float]:
        """Returns (min, max) strike currently displayed (after trim/flip).

        Used to bound the price chart's axis to the strikes the user is actually looking
        at, rather than letting far-OTM walls from the full chain (see get_key_price_levels)
        stretch it out.
        """
        strikes = pd.concat([self.df[self.calls_strike_col_name], self.df[self.puts_strike_col_name]])
        return float(strikes.min()), float(strikes.max())

    def calculate_max_pain(self) -> float:
        """Finds the strike at which total option holder payout is minimized.

        Computed from original_df (the full, unflipped chain) since trimming would
        ignore OI from dropped strikes, and flipping pairs calls/puts by distance
        from ATM rather than by actual strike.
        """
        strikes = self.original_df["Strike"].tolist()
        call_oi = self.original_df["Open Interest"].tolist()
        put_oi = self.original_df["Open Interest.1"].tolist()

        def total_payout(price: float) -> float:
            call_payout = sum(max(0.0, price - k) * oi for k, oi in zip(strikes, call_oi))
            put_payout = sum(max(0.0, k - price) * oi for k, oi in zip(strikes, put_oi))
            return call_payout + put_payout

        payouts = [total_payout(p) for p in strikes]
        return float(strikes[payouts.index(min(payouts))])

    def _get_atm_iv(self) -> float | None:
        """Average of call/put IV at the ATM strike, or whichever side is available.

        Reads from self.df (the displayed table), consistent with how get_total_stats()
        and the IV skew rule already scale against the displayed chain rather than
        original_df. Returns None if IV columns are absent or the ATM row's IV is unusable.
        """
        if 'IV' not in self.df.columns or 'IV.1' not in self.df.columns:
            return None

        atm_row = self.df[self.df[self.calls_strike_col_name] == self.atm_strike]
        if atm_row.empty:
            return None

        call_iv = atm_row['IV'].iloc[0]
        put_iv = atm_row['IV.1'].iloc[0]
        call_valid = pd.notna(call_iv) and math.isfinite(call_iv) and call_iv > 0
        put_valid = pd.notna(put_iv) and math.isfinite(put_iv) and put_iv > 0

        if call_valid and put_valid:
            return (call_iv + put_iv) / 2
        if call_valid:
            return float(call_iv)
        if put_valid:
            return float(put_iv)
        return None

    def calculate_implied_move(self) -> dict | None:
        """Market-implied price range by expiration, from ATM IV and days to expiration.

        Standard approximation: expected_move_pct = ATM IV * sqrt(DTE / 365). Returns None
        if DTE or ATM IV aren't available (e.g. CSV-loaded chain, or missing IV columns).
        """
        if self.dte is None or self.dte <= 0:
            return None

        atm_iv = self._get_atm_iv()
        if atm_iv is None:
            return None

        move_pct = atm_iv * math.sqrt(self.dte / 365)
        move_dollar = self.current_price * move_pct

        return {
            "atm_iv": atm_iv,
            "move_pct": move_pct,
            "move_dollar": move_dollar,
            "low": self.current_price - move_dollar,
            "high": self.current_price + move_dollar,
        }

    def calculate_avg_spread_pct(self) -> float | None:
        """Average bid/ask spread, as % of midpoint, across the displayed table.

        A chain-wide liquidity read computed fresh from Bid/Ask (no separate stored
        column) - rows with no real quote at all (bid and ask both 0) are excluded
        rather than counted as 0% (which would misread as a perfectly tight market).
        Returns None if Bid/Ask columns aren't present (e.g. CSV-loaded chain) or there's
        no usable quote anywhere in the displayed table.
        """
        if not {'Bid', 'Ask', 'Bid.1', 'Ask.1'}.issubset(self.df.columns):
            return None

        calls_mid = (self.df['Ask'] + self.df['Bid']) / 2
        calls_spread = ((self.df['Ask'] - self.df['Bid']) / calls_mid * 100).where(calls_mid > 0)

        puts_mid = (self.df['Ask.1'] + self.df['Bid.1']) / 2
        puts_spread = ((self.df['Ask.1'] - self.df['Bid.1']) / puts_mid * 100).where(puts_mid > 0)

        combined = pd.concat([calls_spread, puts_spread]).dropna()
        if combined.empty:
            return None
        return float(combined.mean())

    def get_total_stats(self) -> None:
        """Calculates OTM, ATM, and Total metrics for Calls and Puts."""
        self.otm_calls = self.df[(self.df[self.calls_strike_col_name] > self.current_price) & (self.df[self.calls_strike_col_name] != self.atm_strike)]
        self.otm_calls_open_interest_sum = self.otm_calls["Open Interest"].sum()
        self.otm_calls_volume_sum = self.otm_calls["Volume"].sum()
        self.otm_puts = self.df[(self.df[self.puts_strike_col_name] < self.current_price) & (self.df[self.puts_strike_col_name] != self.atm_strike)]
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

        # Max values for normalization in Scaling Modes
        # Standard UI practice: scale relative to the Max strike in the group so bars are visible
        self.otm_max_oi = max(self.otm_calls["Open Interest"].max() if not self.otm_calls.empty else 0,
                              self.otm_puts["Open Interest.1"].max() if not self.otm_puts.empty else 0, 1)
        self.otm_max_vol = max(self.otm_calls["Volume"].max() if not self.otm_calls.empty else 0,
                               self.otm_puts["Volume.1"].max() if not self.otm_puts.empty else 0, 1)

        self.itm_calls = self.df[(self.df[self.calls_strike_col_name] < self.current_price) & (self.df[self.calls_strike_col_name] != self.atm_strike)]
        self.itm_puts = self.df[(self.df[self.puts_strike_col_name] > self.current_price) & (self.df[self.puts_strike_col_name] != self.atm_strike)]
        self.itm_calls_open_interest_sum = self.itm_calls["Open Interest"].sum()
        self.itm_calls_volume_sum = self.itm_calls["Volume"].sum()
        self.itm_puts_open_interest_sum = self.itm_puts["Open Interest.1"].sum()
        self.itm_puts_volume_sum = self.itm_puts["Volume.1"].sum()
        self.itm_max_oi = max(self.itm_calls["Open Interest"].max() if not self.itm_calls.empty else 0,
                              self.itm_puts["Open Interest.1"].max() if not self.itm_puts.empty else 0, 1)
        self.itm_max_vol = max(self.itm_calls["Volume"].max() if not self.itm_calls.empty else 0,
                               self.itm_puts["Volume.1"].max() if not self.itm_puts.empty else 0, 1)

        atm_df = self.df[self.df[self.calls_strike_col_name] == self.atm_strike]
        self.atm_max_oi = max(atm_df["Open Interest"].max() if not atm_df.empty else 0,
                              atm_df["Open Interest.1"].max() if not atm_df.empty else 0, 1)
        self.atm_max_vol = max(atm_df["Volume"].max() if not atm_df.empty else 0,
                               atm_df["Volume.1"].max() if not atm_df.empty else 0, 1)

        self.total_max_oi = max(self.df["Open Interest"].max(), self.df["Open Interest.1"].max(), 1)
        self.total_max_vol = max(self.df["Volume"].max(), self.df["Volume.1"].max(), 1)

    def get_sentiment_summary_styler(self) -> Styler:
        """Creates a styled summary table for OTM, ATM, ITM, and Total sentiment using proportional bars."""
        data = {
            'Metric': [
                'ITM Open Interest', 'ATM Open Interest', 'OTM Open Interest', 'Total Open Interest',
                'ITM Volume', 'ATM Volume', 'OTM Volume', 'Total Volume'
            ],
            'Calls': [
                self.itm_calls_open_interest_sum, self.atm_calls_open_interest_sum, self.otm_calls_open_interest_sum, self.total_calls_open_interest_sum,
                self.itm_calls_volume_sum, self.atm_calls_volume_sum, self.otm_calls_volume_sum, self.total_calls_volume_sum
            ],
            'Puts': [
                self.itm_puts_open_interest_sum, self.atm_puts_open_interest_sum, self.otm_puts_open_interest_sum, self.total_puts_open_interest_sum,
                self.itm_puts_volume_sum, self.atm_puts_volume_sum, self.otm_puts_volume_sum, self.total_puts_volume_sum
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
            left_color="#198754",
            right_color="#dc3545",
            text_color="white"
        )
        styler = styler.format({'Calls': '{:,.0f}', 'Puts': '{:,.0f}', 'P/C Ratio': '{:.2f}'})
        styler = styler.set_properties(**{'text-align': 'center'})
        return styler

    def get_technical_breakdown(self) -> list[dict]:
        """Generates a technical breakdown based on positioning rules."""
        breakdown = []

        # Rule 1: Total Put/Call Open Interest Ratio (Overall Balance)
        pc_ratio = self.total_puts_open_interest_sum / self.total_calls_open_interest_sum if self.total_calls_open_interest_sum > 0 else 1.0

        if pc_ratio < 0.5:
            status = "Strong Bullish Skew"
            rule_ref = "P/C Ratio < 0.5"
            mm_inst = "Market Makers (MMs) are likely net short calls. If price rallies, MMs must buy shares to hedge, potentially fueling a 'gamma squeeze'. Retail is heavily long calls."
        elif pc_ratio < 0.8:
            status = "Moderate Bullish Skew"
            rule_ref = "0.5 <= P/C Ratio < 0.8"
            mm_inst = "Positive sentiment. Institutions may be selling calls for income. Retail sentiment is optimistic."
        elif pc_ratio <= 1.2:
            status = "Balanced Market"
            rule_ref = "0.8 <= P/C Ratio <= 1.2"
            mm_inst = "Market is in equilibrium. No clear dominance. MMs are neutral, collecting spreads. Retail and institutions are not showing directional consensus."
        elif pc_ratio <= 2.0:
            status = "Moderate Bearish Skew"
            rule_ref = "1.2 < P/C Ratio <= 2.0"
            mm_inst = "Hedging is dominant. Institutions are buying puts for protection. MMs are providing liquidity at higher premiums."
        else:
            status = "Strong Bearish Skew"
            rule_ref = "P/C Ratio > 2.0"
            mm_inst = "Extreme fear or heavy hedging. MMs are net short puts and may sell underlying aggressively if price drops to stay delta-neutral (Gamma acceleration)."

        breakdown.append({
            "Aspect": "Overall Balance",
            "Status": status,
            "Logic": f"Rule: {rule_ref} (Actual: {pc_ratio:.2f})",
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })

        # Rule 2: OTM Distribution (Speculative Skew)
        otm_call_oi = self.otm_calls_open_interest_sum
        otm_put_oi = self.otm_puts_open_interest_sum
        otm_ratio = otm_call_oi / otm_put_oi if otm_put_oi > 0 else 1.0

        if otm_ratio > 2.0:
            status = "Strong OTM Call Skew (Lotto Bias)"
            mm_inst = "Retail is buying cheap 'lottery ticket' calls. Institutions are likely the sellers (smart money), betting against extreme moves."
        elif otm_ratio > 1.2:
            status = "Moderate OTM Call Skew"
            mm_inst = "Speculative upside interest outweighs downside hedging. Market participants are positioning for a breakout."
        elif 0.8 <= otm_ratio <= 1.2:
            status = "Balanced OTM Distribution"
            mm_inst = "Symmetric positioning. Market expects standard volatility in either direction. No extreme greed or fear."
        elif otm_ratio >= 0.5:
            status = "Moderate OTM Put Skew"
            mm_inst = "Elevated fear. Protective puts are being accumulated by institutions to hedge portfolios."
        else:
            status = "Strong OTM Put Skew (Panic/Hedging)"
            mm_inst = "Institutions are loading up on crash protection. MMs are charging high premiums due to expansion in implied volatility."

        breakdown.append({
            "Aspect": "OTM Skew (Speculation)",
            "Status": status,
            "Logic": f"OTM Call/Put Ratio: {otm_ratio:.2f}",
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })

        # Rule 3: Volume vs Open Interest (Market Urgency)
        total_oi = self.total_calls_open_interest_sum + self.total_puts_open_interest_sum
        total_vol = self.total_calls_volume_sum + self.total_puts_volume_sum
        vol_oi_ratio = total_vol / total_oi if total_oi > 0 else 0

        if vol_oi_ratio > 0.5:
            status = "High Urgency / Fresh Interest"
            mm_inst = "Volume is very high relative to OI. This suggests large-scale 'opening' or 'closing' of positions. Institutions are likely repositioning for a major move or earnings. Retail is often 'chasing' the trend here."
        elif vol_oi_ratio > 0.15:
            status = "Healthy Turnover"
            mm_inst = "Normal market participation. Positions are being rolled or adjusted, but there is no sign of a massive structural shift in sentiment."
        else:
            status = "Low Conviction / Consolidation"
            mm_inst = "Volume is low relative to existing positions. Market participants are standing pat. Expect range-bound price action as the 'status quo' remains unchallenged."

        breakdown.append({
            "Aspect": "Market Urgency (Vol/OI)",
            "Status": status,
            "Logic": f"Vol/OI Ratio: {vol_oi_ratio:.2f}",
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })

        # Rule 4: Key Technical Levels (OI Walls)
        # Find strikes with max Call OI and max Put OI
        max_call_idx = self.df["Open Interest"].idxmax()
        max_put_idx = self.df["Open Interest.1"].idxmax()

        call_wall = self.df.loc[max_call_idx, self.calls_strike_col_name]
        put_wall = self.df.loc[max_put_idx, self.puts_strike_col_name]

        status_text = f"Resistance: {call_wall} | Support: {put_wall}"

        breakdown.append({
            "Aspect": "Institutional 'Walls'",
            "Status": status_text,
            "Logic": "Identifying strikes with the highest Open Interest concentration.",
            "Market Implication (MMs/Institutions vs Retail)": (
                f"The Call Wall at {call_wall} acts as a ceiling where MMs are net sellers, creating heavy resistance. "
                f"The Put Wall at {put_wall} acts as a floor where institutions have bought protection. "
                "Price often 'pins' or bounces between these two levels as expiration approaches."
            )
        })

        # Rule 5: Max Pain
        max_pain = self.calculate_max_pain()
        distance_pct = ((self.current_price - max_pain) / max_pain * 100) if max_pain else 0.0

        breakdown.append({
            "Aspect": "Max Pain",
            "Status": f"${max_pain:,.2f} ({distance_pct:+.1f}% from current price)",
            "Logic": "Strike price minimizing total option payout across all calls and puts in the chain.",
            "Market Implication (MMs/Institutions vs Retail)": (
                f"MMs (typically net option sellers) benefit if price settles near ${max_pain:,.2f} at expiration, "
                "since that minimizes what they owe option holders. Price often gravitates toward Max Pain as "
                "expiration nears, though this effect weakens further out in time."
            )
        })

        # Rule 6: OTM IV Skew (Put vs Call) - the "volatility smirk".
        # Guarded: CSV-loaded chains (filepath= in main()) may not have IV columns.
        if 'IV' in self.df.columns and 'IV.1' in self.df.columns:
            otm_call_iv = self.otm_calls['IV'].mean() if not self.otm_calls.empty else float('nan')
            otm_put_iv = self.otm_puts['IV.1'].mean() if not self.otm_puts.empty else float('nan')

            if pd.notna(otm_call_iv) and pd.notna(otm_put_iv) and otm_call_iv > 0:
                iv_skew_ratio = otm_put_iv / otm_call_iv

                if iv_skew_ratio > 1.3:
                    status = "Steep Put Skew (Crash Hedging)"
                    rule_ref = "OTM Put IV / OTM Call IV > 1.3"
                    mm_inst = "OTM puts are priced far richer than OTM calls. Institutions are paying a steep premium for downside protection; MMs are charging accordingly for tail risk."
                elif iv_skew_ratio > 1.1:
                    status = "Moderate Put Skew (Normal Equity Skew)"
                    rule_ref = "1.1 < OTM Put IV / OTM Call IV <= 1.3"
                    mm_inst = "Typical equity options skew - downside protection costs more than upside speculation. No unusual stress."
                elif iv_skew_ratio >= 0.9:
                    status = "Flat Skew (Symmetric Risk Pricing)"
                    rule_ref = "0.9 <= OTM Put IV / OTM Call IV <= 1.1"
                    mm_inst = "Calls and puts are priced almost identically. Market is pricing similar odds of a large move in either direction - often seen ahead of binary catalysts like earnings."
                elif iv_skew_ratio >= 0.7:
                    status = "Mild Call Skew (Unusual)"
                    rule_ref = "0.7 <= OTM Put IV / OTM Call IV < 0.9"
                    mm_inst = "OTM calls are pricier than OTM puts, which is unusual for equities. Suggests speculative upside demand (e.g. squeeze potential) outweighing hedging demand."
                else:
                    status = "Inverted Call Skew (Strong Melt-Up Bias)"
                    rule_ref = "OTM Put IV / OTM Call IV < 0.7"
                    mm_inst = "Heavily inverted skew. Aggressive call buying (often retail-driven) is bidding up OTM call IV well above puts - a classic 'lotto ticket'/gamma-squeeze setup."

                breakdown.append({
                    "Aspect": "IV Skew (OTM Put vs Call)",
                    "Status": f"{status} (Ratio: {iv_skew_ratio:.2f})",
                    "Logic": f"Rule: {rule_ref} (Avg OTM Call IV: {otm_call_iv:.1%}, Avg OTM Put IV: {otm_put_iv:.1%})",
                    "Market Implication (MMs/Institutions vs Retail)": mm_inst
                })

        # Rule 7: Implied Move - the market's own forecast range for price by expiration.
        implied_move = self.calculate_implied_move()
        if implied_move is not None:
            breakdown.append({
                "Aspect": "Implied Move (to Expiration)",
                "Status": (
                    f"±${implied_move['move_dollar']:,.2f} ({implied_move['move_pct']:.1%}) "
                    f"→ ${implied_move['low']:,.2f} - ${implied_move['high']:,.2f}"
                ),
                "Logic": f"ATM IV ({implied_move['atm_iv']:.1%}) x sqrt(DTE/365), DTE = {self.dte} days",
                "Market Implication (MMs/Institutions vs Retail)": (
                    "This is roughly the market's own 1-standard-deviation (~68% probability) forecast "
                    "for where price lands by expiration, priced in by options buyers and sellers. A move "
                    "beyond this range by expiration would be a bigger surprise than current option prices expect."
                )
            })

        # Rule 8: IV vs Realized Volatility - a substitute for IV Rank/Percentile, which
        # would need daily IV history yfinance doesn't provide (see get_realized_volatility).
        atm_iv = self._get_atm_iv()
        if atm_iv is not None and self.realized_vol is not None and self.realized_vol > 0:
            iv_rv_ratio = atm_iv / self.realized_vol

            if iv_rv_ratio > 1.5:
                status = "Richly Priced (Elevated IV Premium)"
                mm_inst = "Options are pricing in much more movement than the stock has actually shown recently. Common ahead of known catalysts (earnings, FDA decisions) or amid speculative option buying - selling premium here is statistically favored for option writers, all else equal."
            elif iv_rv_ratio > 1.15:
                status = "Moderate IV Premium (Normal)"
                mm_inst = "IV sits modestly above realized volatility - the normal/expected state, since options carry a built-in risk premium for sellers. Nothing unusual."
            elif iv_rv_ratio >= 0.85:
                status = "Fairly Priced"
                mm_inst = "Implied and realized volatility are closely aligned. Options are priced about in line with the stock's recent actual movement."
            else:
                status = "Cheap Relative to Realized Vol"
                mm_inst = "Unusual: the market is pricing in LESS movement than the stock has actually shown recently. Can happen right after a vol-crushing event (e.g. post-earnings) or in persistently low-IV names."

            breakdown.append({
                "Aspect": "IV vs Realized Vol (IVP Proxy)",
                "Status": f"{status} (Ratio: {iv_rv_ratio:.2f})",
                "Logic": (
                    f"ATM IV: {atm_iv:.1%} vs {yfi.REALIZED_VOL_LOOKBACK_DAYS}-Day Realized Vol: {self.realized_vol:.1%}. "
                    "True IV Rank/Percentile needs daily IV history yfinance doesn't expose; this compares "
                    "current IV to the stock's own recent actual volatility instead."
                ),
                "Market Implication (MMs/Institutions vs Retail)": mm_inst
            })

        # Rule 9: Liquidity - average bid/ask spread (% of midpoint) across the displayed
        # table. Guarded: CSV-loaded chains (filepath= in main()) may not have Bid/Ask.
        avg_spread_pct = self.calculate_avg_spread_pct()
        if avg_spread_pct is not None:
            if avg_spread_pct < 5:
                status = "Tight Spreads (Liquid)"
                mm_inst = "Market makers are competing tightly here - low cost to enter/exit positions. Typical of high-volume, popular names and near-term expirations."
            elif avg_spread_pct < 15:
                status = "Normal Liquidity"
                mm_inst = "Reasonable cost to trade. Use limit orders near the midpoint rather than market orders to avoid overpaying the spread."
            elif avg_spread_pct < 30:
                status = "Wide Spreads (Reduced Liquidity)"
                mm_inst = "MMs are demanding more compensation for taking the other side, likely due to low volume/open interest or distance from the front-month/ATM strikes. Expect meaningful slippage versus the midpoint - always use limit orders."
            else:
                status = "Very Wide (Illiquid)"
                mm_inst = "Extremely thin two-sided interest. Entering or exiting a position here can cost a large share of the premium in spread alone - a strong signal these contracts are effectively untradeable at any size."

            breakdown.append({
                "Aspect": "Liquidity (Bid/Ask Spread)",
                "Status": f"{status} (Avg: {avg_spread_pct:.1f}% of mid)",
                "Logic": "Average (Ask - Bid) / Midpoint across all displayed calls and puts.",
                "Market Implication (MMs/Institutions vs Retail)": mm_inst
            })

        return breakdown

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
                return f'background-color: rgba(0, 200, 0, {alpha})'
            return f'background-color: rgba(255, 0, 0, {alpha})'
        change_cols = [c for c in ('% Change', '% Change.1') if c in self.styled_df.data.columns]
        if change_cols:
            self.styled_df = self.styled_df.map(color_gradient, subset=change_cols)

    def color_iv_values(self) -> None:
        """Heatmap the IV / IV.1 columns by relative dispersion within the displayed table."""
        iv_cols = [c for c in ('IV', 'IV.1') if c in self.styled_df.data.columns]
        if not iv_cols:
            return

        combined = pd.concat([self.df[c] for c in iv_cols])
        combined = combined[combined.apply(lambda v: pd.notna(v) and math.isfinite(v) and v > 0)]

        if len(combined.unique()) < 2:
            return  # No dispersion to show (empty, all-NaN, single value, all-identical).

        lo = combined.quantile(0.05)
        hi = combined.quantile(0.95)
        if hi <= lo:
            return

        alpha_min, alpha_max = 0.10, 0.55

        def color_gradient(val):
            if pd.isna(val) or not math.isfinite(val) or val <= 0:
                return None
            clipped = min(max(val, lo), hi)
            normalized = (clipped - lo) / (hi - lo)
            alpha = normalized * (alpha_max - alpha_min) + alpha_min
            return f'background-color: rgba(255, 165, 0, {alpha:.3f})'

        self.styled_df = self.styled_df.map(color_gradient, subset=iv_cols)


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

def highlight_cell(styler: Styler, col_name: str, val: float, color: str = "#3D7192") -> Styler:
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

    # Determine a global reference for comparison across rows.
    # We normalize against the sum of the largest Put and largest Call (as suggested).
    # This creates a stable "field" where distribution curves become visible.
    if 'Metric' in df.columns:
        # Logic for Sentiment Summary table
        global_ref = max(df[left_col].max(), df[right_col].max(), 1)
        calls_ref = global_ref
        puts_ref = global_ref
    else:
        # Logic for main options chain table
        max_c = context.df["Open Interest"].max() if is_oi else context.df["Volume"].max()
        max_p = context.df["Open Interest.1"].max() if is_oi else context.df["Volume.1"].max()
        global_ref = max(max_c, max_p, 1)
        calls_ref = max(max_c, 1)
        puts_ref = max(max_p, 1)

    # Row-local sum (used for Mode 1)
    # We pre-calculate sums if needed, but row-local is handled inside the loop.

    def get_dynamic_color(is_left, idx):
        # Determine if this row is OI or Volume.
        # Fallback to column-based detection for the main chain.
        current_is_oi = is_oi
        if 'Metric' in df.columns:
            current_is_oi = "Open Interest" in str(df.loc[idx, 'Metric'])

        # Use strike price to determine color if we're in the main options chain table
        if context.calls_strike_col_name in df.columns:
            strike_col = context.calls_strike_col_name if is_left else context.puts_strike_col_name
            strike = df.loc[idx, strike_col]

            if is_left: # Calls
                if strike == context.atm_strike:
                    return "#005E98"

                # Calls: OTM if Strike > Price, ITM if Strike < Price
                is_otm = (strike > context.current_price)
                if current_is_oi:
                    return "#157347" if is_otm else "#0a3622" # Darker Green / Deep Green
                else:
                    return "#198754" if is_otm else "#0f5132" # Std Green / Forest Green
            else: # Puts
                if strike == context.atm_strike:
                    return "#005E98"

                # Puts: OTM if Strike < Price, ITM if Strike > Price
                is_otm = (strike < context.current_price)
                if current_is_oi:
                    return "#bb2d3b" if is_otm else "#58151c" # Strong Red / Deep Red
                else:
                    return "#dc3545" if is_otm else "#842029" # Std Red / Wine Red

        # Handle sentiment summary table which has a 'Metric' column but no strike data
        if 'Metric' in df.columns:
            metric_text = str(df.loc[idx, 'Metric'])
            if "ATM" in metric_text:
                return "#005E98"

            # Everything else in the summary uses the vibrant shade.
            if is_left: # Calls
                return "#157347" if current_is_oi else "#198754"
            else: # Puts
                return "#bb2d3b" if current_is_oi else "#dc3545"

        return left_color if is_left else right_color

    def apply_bar_styling(s):
        styles = [None] * len(s)

        # Only apply styling to the specified columns
        if s.name == left_col:
            # Left column: bars from left to right
            for idx in s.index:
                current_color = get_dynamic_color(True, idx)
                try:
                    val = convert_comma_number(df.loc[idx, left_col])
                    if pd.isna(val) or val <= 0: continue

                    if mode == "Per Strike (Row)":
                        right_val = convert_comma_number(df.loc[idx, right_col])
                        denom = val + (right_val if not pd.isna(right_val) else 0)
                    elif mode == "Relative to OTM/ITM/ATM Groups" and 'Metric' not in df.columns:
                        # Identify strike position to use localized Max-normalization
                        strike = df.loc[idx, context.calls_strike_col_name]

                        if strike == context.atm_strike: # ATM
                            denom = context.atm_max_oi if is_oi else context.atm_max_vol
                        elif strike > context.current_price: # OTM Call
                            denom = context.otm_max_oi if is_oi else context.otm_max_vol
                        else: # ITM Call
                            denom = context.itm_max_oi if is_oi else context.itm_max_vol
                    elif mode == "Per Side (Each side's own peak)":
                        denom = calls_ref
                    else:
                        # Full Chain: Normalize against the single largest peak in the entire table
                        denom = global_ref

                    if denom > 0:
                        # Use a Power Transform (x^0.7) which is milder than Square Root (x^0.5).
                        # This preserves the "curve" and differentiation between high-value strikes
                        # while still boosting visibility for smaller values.
                        percentage = math.pow(val / denom, 0.7) * 100
                        percentage = min(percentage, 98.0)

                        styles[idx] = f'''
                            background: linear-gradient(
                                to right,
                                {current_color} 0%,
                                {current_color} {percentage:.1f}%,
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
                current_color = get_dynamic_color(False, idx)
                try:
                    val = convert_comma_number(df.loc[idx, right_col])
                    if pd.isna(val) or val <= 0: continue

                    if mode == "Per Strike (Row)":
                        left_val = convert_comma_number(df.loc[idx, left_col])
                        denom = (left_val if not pd.isna(left_val) else 0) + val
                    elif mode == "Relative to OTM/ITM/ATM Groups" and 'Metric' not in df.columns:
                        # Identify strike position for Puts
                        strike = df.loc[idx, context.puts_strike_col_name]

                        if strike == context.atm_strike: # ATM
                            denom = context.atm_max_oi if is_oi else context.atm_max_vol
                        elif strike < context.current_price: # OTM Put
                            denom = context.otm_max_oi if is_oi else context.otm_max_vol
                        else: # ITM Put
                            denom = context.itm_max_oi if is_oi else context.itm_max_vol
                    elif mode == "Per Side (Each side's own peak)":
                        denom = puts_ref
                    else:
                        denom = global_ref

                    if denom > 0:
                        # Apply the same 0.7 power transform for consistency
                        percentage = math.pow(val / denom, 0.7) * 100
                        percentage = min(percentage, 98.0)

                        styles[idx] = f'''
                            background: linear-gradient(
                                to left,
                                {current_color} 0%,
                                {current_color} {percentage:.1f}%,
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

    if rows_to_trim == 0:
        return df # No trimming

    if pivot_row < 0 or pivot_row >= len(df):
        raise ValueError(f"Pivot row {pivot_row} is out of bounds for DataFrame with {len(df)} rows")

    # Calculate how many rows we can include symmetrically around pivot
    rows_before_pivot = pivot_row
    rows_after_pivot = len(df) - pivot_row - 1
    symmetric_radius = min(rows_before_pivot, rows_after_pivot)
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
    bar_scaling_mode: str = 'Per Strike (Row)',
    hidden_columns: list[str] = None,
) -> OptionContext:

    df_context.df = trim_rows_symmetric_radius(df_context.df, pivot_row=df_context.atm_strike_row, rows_to_trim=trim_around_strike)
    df_context.update_strike_row() # Rows indexes might have changed.

    if flip_strikes:
        df_context.df = duplicate_and_rename_strike(df_context.df)
        df_context.calls_strike_col_name = "Calls Strike"
        df_context.puts_strike_col_name = "Puts Strike"
        df_context.df = flip_rows_around_strike(df_context.df)
        # Rows indexes changed. Get ATM strike row again
        df_context.update_strike_row()

    df_context.df = flip_right_half_columns(df_context.df, start=df_context.get_puts_strike_col_index() + 1)

    # Compute stats on the final (trimmed, flipped) DataFrame before styling.
    # Mode "Relative to OTM/ITM/ATM Groups" reads atm_max_oi / otm_max_oi / itm_max_oi
    # from the context, which are only available after get_total_stats() runs.
    df_context.get_total_stats()

    # The displayed table only ever drops purely cosmetic columns here - df_context.df
    # itself is left untouched so Max Pain, the sentiment summary, the technical
    # breakdown, and the price chart's key levels (all of which read df_context.df
    # directly, not styled_df) keep working regardless of what's hidden from view.
    display_df = df_context.df
    if hidden_columns:
        cols_to_drop = [c for c in hidden_columns if c in display_df.columns]
        if cols_to_drop:
            display_df = display_df.drop(columns=cols_to_drop)

    df_context.styled_df = display_df.style
    df_context.styled_df = style_proportional_bars(display_df, df_context.styled_df, 'Open Interest', 'Open Interest.1', df_context, bar_scaling_mode)
    df_context.styled_df = style_proportional_bars(display_df, df_context.styled_df, 'Volume', 'Volume.1', df_context, bar_scaling_mode)
    df_context.styled_df = format_style(df_context.styled_df)
    # Volume and Open Interest are share/contract counts; display as integers, no decimals.
    int_cols = [c for c in ('Volume', 'Volume.1', 'Open Interest', 'Open Interest.1') if c in display_df.columns]
    if int_cols:
        df_context.styled_df = df_context.styled_df.format({c: '{:,.0f}' for c in int_cols}, subset=int_cols)
    # IV is a fraction (e.g. 0.35); display as a percentage. Guarded since CSV-loaded
    # chains (the filepath= path in main()) may not have IV columns at all.
    iv_cols = [c for c in ('IV', 'IV.1') if c in display_df.columns]
    if iv_cols:
        df_context.styled_df = df_context.styled_df.format({c: '{:.1%}' for c in iv_cols}, subset=iv_cols)
    df_context.styled_df = highlight_cell(df_context.styled_df, df_context.calls_strike_col_name, df_context.atm_strike)
    df_context.styled_df = highlight_cell(df_context.styled_df, df_context.puts_strike_col_name, df_context.atm_strike)
    return df_context


_KEY_LEVEL_COLORS = {
    # Bright, high-contrast against the dark chart background, and a different hue family
    # (amber/cyan) than the green/red price line so the two don't get confused.
    "Resistance (Call Wall)": "#ffa726",
    "Support (Put Wall)": "#29b6f6",
}

_PRICE_UP_COLOR = "#198754"
_PRICE_DOWN_COLOR = "#dc3545"


def build_price_chart(
    history_df: pd.DataFrame,
    key_levels: dict[str, float] | None = None,
    strike_range: tuple[float, float] | None = None,
):
    """Builds an Altair chart of historical close price with horizontal reference lines.

    Either input may be missing: history_df can be empty (no price data) and/or
    key_levels can be empty (no option data). Returns None if there's nothing to plot.

    The y-axis domain always covers the price series' own min/max for the selected period
    (yfinance's OHLC history gives us the real trading range) PLUS every key_levels value,
    so Resistance/Support are always visible rather than getting clipped off a tightly-zoomed
    short-period chart. This is safe to do unconditionally because key_levels now comes from
    the same trimmed/displayed chain as the "Institutional Walls" TA rule (see
    get_key_price_levels), not the full chain - so it can no longer drag in a meaningless
    far-OTM wall. strike_range is only a last-resort fallback when there's neither price data
    nor key_levels to size the axis from.
    """
    import altair as alt

    key_levels = {label: price for label, price in (key_levels or {}).items() if price}

    layers = []
    domain_candidates = []

    price_df = None
    if history_df is not None and not history_df.empty and "Close" in history_df.columns:
        price_df = history_df.reset_index()
        date_col = price_df.columns[0]  # 'Date' or 'Datetime' depending on the interval used
        price_df = price_df.rename(columns={date_col: "Date"})
        domain_candidates += [price_df["Close"].min(), price_df["Close"].max()]

    if key_levels:
        domain_candidates += list(key_levels.values())

    if not domain_candidates and strike_range:
        domain_candidates += [strike_range[0], strike_range[1]]

    domain = None
    if domain_candidates:
        lo, hi = min(domain_candidates), max(domain_candidates)
        pad = (hi - lo) * 0.08 or (hi * 0.05 if hi else 1.0)
        domain = [max(lo - pad, 0), hi + pad]

    if price_df is not None:
        is_up = price_df["Close"].iloc[-1] >= price_df["Close"].iloc[0]
        # A line mark draws nothing for a single point (e.g. "1 Day" right after the
        # market opens, when only one 5-minute bar exists yet) - add point markers so
        # there's still something visible. Skipped for denser series to avoid clutter.
        show_points = len(price_df) <= 3
        layers.append(
            alt.Chart(price_df).mark_line(
                color=_PRICE_UP_COLOR if is_up else _PRICE_DOWN_COLOR, clip=True, point=show_points
            ).encode(
                x=alt.X("Date:T", title=None),
                y=alt.Y("Close:Q", title="Price", scale=alt.Scale(domain=domain, zero=False)),
                tooltip=[alt.Tooltip("Date:T"), alt.Tooltip("Close:Q", format=".2f", title="Close")],
            )
        )

    if key_levels:
        labels = list(key_levels.keys())
        plot_prices = list(key_levels.values())

        # Labels sit right next to their line, so when Resistance and Support are equal or
        # nearly so they need real vertical room for the TEXT (not just a hairline gap
        # between two dashed lines) or the two labels overlap each other. Nudge them apart
        # by a chunk of the visible range; the tooltip/label text still reports the real,
        # un-nudged price via a separate field.
        if domain and len(plot_prices) == 2:
            min_gap = (domain[1] - domain[0]) * 0.08
            if abs(plot_prices[0] - plot_prices[1]) < min_gap:
                mid = (plot_prices[0] + plot_prices[1]) / 2
                direction = 1 if plot_prices[0] >= plot_prices[1] else -1
                plot_prices[0] = mid + direction * (min_gap / 2)
                plot_prices[1] = mid - direction * (min_gap / 2)

        levels_df = pd.DataFrame({"Label": labels, "Price": plot_prices, "ActualPrice": list(key_levels.values())})
        levels_df["Text"] = [f"{label}: ${price:,.2f}" for label, price in zip(labels, levels_df["ActualPrice"])]
        color = alt.Color(
            "Label:N",
            scale=alt.Scale(domain=list(_KEY_LEVEL_COLORS.keys()), range=list(_KEY_LEVEL_COLORS.values())),
            legend=None,
        )
        y = alt.Y("Price:Q", scale=alt.Scale(domain=domain, zero=False))

        layers.append(
            alt.Chart(levels_df).mark_rule(strokeDash=[4, 4], clip=True).encode(
                y=y,
                color=color,
                tooltip=[alt.Tooltip("Label:N"), alt.Tooltip("ActualPrice:Q", format=".2f", title="Price")],
            )
        )

        if price_df is not None:
            # Anchor to the last date in the price series so the label sits right at the
            # line's right end, hugging the chart's right edge, instead of a fixed pixel
            # offset that wouldn't track the actual plot width.
            levels_df = levels_df.assign(_AnchorDate=price_df["Date"].max())
            text_x = alt.X("_AnchorDate:T")
            text_align, text_dx = "right", -4
        else:
            text_x = alt.value(2)
            text_align, text_dx = "left", 4

        layers.append(
            alt.Chart(levels_df).mark_text(
                align=text_align, baseline="bottom", dx=text_dx, dy=-2, fontWeight="bold", clip=False
            ).encode(
                x=text_x,
                y=y,
                text="Text:N",
                color=color,
            )
        )

    if not layers:
        return None

    chart = layers[0] if len(layers) == 1 else alt.layer(*layers)
    return chart.properties(height=350).interactive()


def get_period_change(history_df: pd.DataFrame) -> tuple[float, float] | None:
    """Returns (change, pct_change) between the first and last Close in history_df.

    Compares the same two points build_price_chart uses to color the line, so the
    displayed change figure and the line's red/green color never disagree. None if
    there's no price data to compare.
    """
    if history_df is None or history_df.empty or "Close" not in history_df.columns:
        return None
    first_close = float(history_df["Close"].iloc[0])
    last_close = float(history_df["Close"].iloc[-1])
    if first_close == 0:
        return None
    change = last_close - first_close
    return change, (change / first_close) * 100


def main(
    ticker: str,
    df: pd.DataFrame = None,
    filepath: str = None,
    expiration_date: str = None,
    available_expiration_dates: list = None,
    current_price: float = None,
    flip_strikes: bool = False,
    trim_around_strike: int = 0,
    bar_scaling_mode: str = 'Per Strike (Row)',
    company_name: str = None,
    retrieval_time: datetime = None,
    hidden_columns: list[str] = None,
    realized_vol: float = None,
):
    if df is not None:
        pass
    elif filepath:
        df = readcsv(filepath)
    else:
        df, expiration_date, available_expiration_dates, retrieval_time = yfi.get_options_chain_table(ticker, expiration_date)

    if df is None or df.empty:
        return None

    if not current_price:
        ticker_obj = yfinance.Ticker(ticker)
        info = ticker_obj.info
        current_price = info.get('regularMarketPrice')
        current_price = float(current_price)
        print(f"Current price is: {current_price}")
        if not company_name:
            company_name = info.get('longName')

    if realized_vol is None:
        realized_vol = yfi.get_realized_volatility(ticker)

    df_context = OptionContext(df, ticker, current_price, expiration_date=expiration_date, realized_vol=realized_vol)

    df_context = calls_puts_side_by_side_distance_from_strike(
        df_context,
        flip_strikes,
        trim_around_strike,
        bar_scaling_mode,
        hidden_columns
    )
    # get_total_stats() is called inside calls_puts_side_by_side_distance_from_strike

    df_context.color_change_values()
    df_context.color_iv_values()

    return {
        "styled_dataframe": df_context.styled_df,
        "current_price": current_price,
        "expiration_date": expiration_date,
        "available_expiration_dates": available_expiration_dates,
        "context": df_context,
        "sentiment_summary_styler": df_context.get_sentiment_summary_styler(),
        "technical_breakdown": df_context.get_technical_breakdown(),
        "company_name": company_name,
        "retrieval_time": retrieval_time
    }
