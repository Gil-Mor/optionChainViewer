import math
import pandas as pd
from pandas.io.formats.style import Styler
from datetime import datetime
import yfinanceGetOptions as yfi
import yfinance

def readcsv(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path, delimiter="\t", thousands=",")
    return df


# Minimum number of distinct strikes that must carry nonzero Open Interest before an
# OI-driven computation (P/C ratio, Max Pain, OI walls, ...) is trusted. A sum > 0 isn't
# enough: a handful of stray nonzero values - e.g. stale OI surviving on one or two deep
# ITM/OTM contracts from an old position, far from where any real trading is happening -
# makes the sum nonzero while the result still collapses onto wherever that handful sits.
# That's the same degenerate failure as an all-zero column, just disguised.
_MIN_STRIKES_WITH_OI = 3

# A pure strike-count check can still be fooled by stray OI that's real-looking in count
# but nowhere near the money (e.g. 3+ deep OTM strikes with leftover OI from old hedges,
# while every strike that actually matters is empty). Real OI for a liquid underlying
# concentrates near the current price, so also require that most of the strikes closest
# to current_price carry nonzero OI. Always measured against original_df (the full,
# untrimmed chain with a stable Strike column) rather than the displayed self.df - using
# a user-adjustable trim/flip view as the source of the window would make the check only
# as strict as whatever the user happened to be looking at.
_NEAR_ATM_WINDOW = 10
_NEAR_ATM_MIN_COVERAGE = 0.8

# No real tradable option prices at single-digit-basis-point IV - a reading below this
# is a placeholder/broken value from yfinance (common on illiquid/far-dated chains,
# alongside the OI gap above), not a genuinely "ultra calm" market.
_MIN_PLAUSIBLE_IV = 0.01

# IV this far below the underlying's own trailing Realized Volatility is implausible:
# forward-looking implied vol almost always carries some risk premium over backward-
# looking realized vol, even right after a vol-crushing event. Falling below this
# fraction of Realized Vol is a stronger sign of broken IV data than of a genuinely
# "cheap" market - deliberately well under the 0.85 threshold where Rule 8 (IV vs
# Realized Vol) starts calling things "Cheap Relative to Realized Vol", so real (if
# unusual) cheap-vol readings between this floor and 0.85 still come through normally.
_MIN_IV_TO_REALIZED_VOL_RATIO = 0.25

# Fixed approximation of the risk-free rate (~short-term T-bill yield). Not fetched
# live - its effect on these probability estimates is small relative to ATM IV over
# typical option DTE windows, same simplification tradeoff as using a single flat
# ATM IV instead of per-strike IV.
_RISK_FREE_RATE = 0.045


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

    def _oi_strike_coverage(self, *oi_series: pd.Series) -> int:
        """Count of distinct strikes carrying nonzero Open Interest in any of the given
        Series (calls, puts, or both - pass one or several, aligned by position).
        """
        nonzero = oi_series[0] > 0
        for series in oi_series[1:]:
            nonzero = nonzero | (series > 0)
        return int(nonzero.sum())

    def _near_atm_oi_coverage_ok(self) -> bool:
        """True when at least _NEAR_ATM_MIN_COVERAGE of the _NEAR_ATM_WINDOW strikes
        closest to current_price carry nonzero Open Interest (either side). See
        _NEAR_ATM_WINDOW for why this is checked against original_df.
        """
        nearest = self.original_df.assign(
            _dist_from_price=(self.original_df["Strike"] - self.current_price).abs()
        ).nsmallest(_NEAR_ATM_WINDOW, "_dist_from_price")

        window = len(nearest)
        if window == 0:
            return False

        covered = self._oi_strike_coverage(nearest["Open Interest"], nearest["Open Interest.1"])
        return covered / window >= _NEAR_ATM_MIN_COVERAGE

    def _oi_missing_reason(self, *oi_vol_pairs: tuple[str, pd.Series, pd.Series]) -> str | None:
        """Returns why Open Interest should be distrusted for this scope, or None if it
        looks reliable. Each pair is (label, oi_series, vol_series) for one side, e.g.
        ("Call", self.df["Open Interest"], self.df["Volume"]) - pass one side or both.

        Checks three independent signals, any of which is disqualifying:

        1. Volume traded but OI reads 0 on that side. This is the clearest possible
           sign of a yfinance data gap - real trades leave OI behind (even a day
           stale), so Volume > 0 with OI == 0 isn't a real lack of positions. Checked
           per side so one broken side (e.g. Put OI dead, Call OI fine) isn't hidden
           by averaging with a side that's working.
        2. Too few strikes (summed across all given sides) carry any nonzero OI at
           all - a handful of stray values (e.g. leftover OI on one old deep ITM/OTM
           contract) isn't enough to anchor a chain-wide computation. See
           _MIN_STRIKES_WITH_OI.
        3. The strikes nearest current_price are mostly empty of OI - real OI for a
           liquid underlying concentrates near the money, so OI that only exists far
           from it is more likely stale than a real signal. See _NEAR_ATM_WINDOW.
        """
        broken_sides = [label for label, oi, vol in oi_vol_pairs if oi.sum() == 0 and vol.sum() > 0]
        if broken_sides:
            sides = " and ".join(broken_sides)
            return f"{sides} Open Interest reads 0 despite real Volume traded - a yfinance data gap, not a real lack of positions."

        oi_series_list = [oi for _, oi, _ in oi_vol_pairs]
        if self._oi_strike_coverage(*oi_series_list) < _MIN_STRIKES_WITH_OI:
            return "Open Interest is missing/zero across this chain - too few strikes carry any to compute from."

        if not self._near_atm_oi_coverage_ok():
            return "What little Open Interest exists is concentrated far from the current price, not near the money - likely stale rather than a real signal."

        return None

    def _get_otm_full_chain(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Returns (otm_calls, otm_puts) filtered from original_df (the full, untrimmed
        chain), not self.df - shared by Rule 2 (OTM Skew) and Rule 7 (IV Skew) so a
        user's trim setting narrows what's *displayed* without narrowing what either
        rule computes over.
        """
        otm_calls_full = self.original_df[
            (self.original_df["Strike"] > self.current_price) & (self.original_df["Strike"] != self.atm_strike)
        ]
        otm_puts_full = self.original_df[
            (self.original_df["Strike"] < self.current_price) & (self.original_df["Strike"] != self.atm_strike)
        ]
        return otm_calls_full, otm_puts_full

    def _wall_strike(self, df: pd.DataFrame, oi_col: str, vol_col: str, strike_col: str, label: str) -> tuple[float, str | None]:
        """Strike with the highest value in oi_col within df, falling back to vol_col
        when oi_col isn't trustworthy (see _oi_missing_reason). Below that bar,
        idxmax()'s tie-break (or a result pinned to a stray/broken value) produces a
        "wall" that doesn't reflect any real positioning.

        df is original_df (the full, untrimmed chain) for both the TA breakdown's
        Institutional Walls rule and the price chart's overlay - see those callers for
        why, and get_strike_range()/the trim-disclaimer at the Walls rule's call site
        for how a wall outside the user's displayed range is flagged.

        Returns (strike, fallback_reason). fallback_reason is None if Open Interest was
        used directly; otherwise it's why Volume was used instead.
        """
        reason = self._oi_missing_reason((label, df[oi_col], df[vol_col]))
        if reason is not None:
            idx = df[vol_col].idxmax()
            return float(df.loc[idx, strike_col]), reason
        idx = df[oi_col].idxmax()
        return float(df.loc[idx, strike_col]), None

    def get_key_price_levels(self) -> dict[str, float]:
        """Returns key option-derived price levels (OI walls) for charting.

        Just Resistance/Support: ATM Strike sits right on top of the price line itself, and
        Max Pain often lands on the same strike as ATM (see calculate_max_pain) - both made
        the chart's line labels overlap without adding information beyond the walls.

        Computed from original_df (the full, untrimmed chain) to match the "Institutional
        Walls" rule in get_technical_breakdown() - both must agree on the same Resistance/
        Support numbers. A wall can fall outside the chart's plotted strike range (see
        get_strike_range) when it sits on a strike the user has trimmed out of view; the
        Walls rule's text carries a disclaimer for that case, the chart simply won't show
        a line for it.
        """
        call_wall, _ = self._wall_strike(self.original_df, "Open Interest", "Volume", self.calls_strike_col_name, "Call")
        put_wall, _ = self._wall_strike(self.original_df, "Open Interest.1", "Volume.1", self.puts_strike_col_name, "Put")

        return {
            "Resistance (Call Wall)": call_wall,
            "Support (Put Wall)": put_wall,
        }

    def get_strike_range(self) -> tuple[float, float]:
        """Returns (min, max) strike currently displayed (after trim/flip).

        Used to bound the price chart's axis to the strikes the user is actually looking
        at, rather than letting far-OTM walls from the full chain (see get_key_price_levels)
        stretch it out.
        """
        strikes = pd.concat([self.df[self.calls_strike_col_name], self.df[self.puts_strike_col_name]])
        return float(strikes.min()), float(strikes.max())

    def calculate_max_pain(self) -> float | None:
        """Finds the strike at which total option holder payout is minimized.

        Computed from original_df (the full, unflipped chain) since trimming would
        ignore OI from dropped strikes, and flipping pairs calls/puts by distance
        from ATM rather than by actual strike.

        Returns None if Open Interest across the full chain isn't trustworthy (see
        _oi_missing_reason) - otherwise the payout minimization can degenerate onto
        whichever handful of strikes (even just one, e.g. stale leftover OI on a single
        deep ITM/OTM contract) happens to hold the only nonzero/believable values,
        producing a strike nowhere near a meaningful Max Pain level.
        """
        oi_reason = self._oi_missing_reason(
            ("Call", self.original_df["Open Interest"], self.original_df["Volume"]),
            ("Put", self.original_df["Open Interest.1"], self.original_df["Volume.1"]),
        )
        if oi_reason is not None:
            return None

        strikes = self.original_df["Strike"].tolist()
        call_oi = self.original_df["Open Interest"].tolist()
        put_oi = self.original_df["Open Interest.1"].tolist()

        def total_payout(price: float) -> float:
            call_payout = sum(max(0.0, price - k) * oi for k, oi in zip(strikes, call_oi))
            put_payout = sum(max(0.0, k - price) * oi for k, oi in zip(strikes, put_oi))
            return call_payout + put_payout

        payouts = [total_payout(p) for p in strikes]
        return float(strikes[payouts.index(min(payouts))])

    def _iv_is_plausible(self, iv: float) -> bool:
        """True when iv looks like a real market quote rather than placeholder/broken
        data - see _MIN_PLAUSIBLE_IV and _MIN_IV_TO_REALIZED_VOL_RATIO. The Realized
        Vol cross-check is skipped if realized_vol isn't available (e.g. yfinance
        history fetch failed), falling back to the absolute floor alone.
        """
        if pd.isna(iv) or not math.isfinite(iv) or iv <= 0:
            return False
        if iv < _MIN_PLAUSIBLE_IV:
            return False
        if self.realized_vol is not None and self.realized_vol > 0:
            if iv / self.realized_vol < _MIN_IV_TO_REALIZED_VOL_RATIO:
                return False
        return True

    def _iv_implausibility_threshold_text(self, iv: float) -> str:
        """Names which plausibility threshold an already-failing (>0) iv value falls
        short of, with the concrete numbers - used to explain N/A IV reasons precisely
        instead of restating the general "missing, zero, or implausibly low" policy.
        Only meaningful for iv that _iv_is_plausible() has already rejected.
        """
        if iv < _MIN_PLAUSIBLE_IV:
            return f"below the {_MIN_PLAUSIBLE_IV:.0%} floor for a believable real quote"
        relative_floor = self.realized_vol * _MIN_IV_TO_REALIZED_VOL_RATIO
        return (
            f"below {_MIN_IV_TO_REALIZED_VOL_RATIO:.0%} of this stock's {self.realized_vol:.1%} "
            f"trailing Realized Vol ({relative_floor:.1%})"
        )

    def _iv_na_explanation(self, iv: float, label: str) -> str:
        """Full descriptive clause for why a given (label, iv) pair fails plausibility,
        e.g. 'Avg OTM Call IV (0.4%) is below the 1% floor for a believable real quote'.
        """
        if pd.isna(iv) or not math.isfinite(iv) or iv <= 0:
            return f"{label} is missing or reads as zero"
        return f"{label} ({iv:.1%}) is {self._iv_implausibility_threshold_text(iv)}"

    def _atm_iv_na_reason(self) -> str:
        """Explains exactly why _get_atm_iv() returned None - the concrete threshold
        and the closest candidate actually found, instead of a generic "missing or
        implausible". Searches the same near-ATM window _get_atm_iv() itself does.
        """
        if 'IV' not in self.df.columns or 'IV.1' not in self.df.columns:
            return "IV columns aren't present in this chain."

        nearest = self.df.assign(
            _dist_from_price=(self.df[self.calls_strike_col_name] - self.current_price).abs()
        ).nsmallest(_NEAR_ATM_WINDOW, "_dist_from_price")
        window_n = len(nearest)

        candidates = [
            v for col in ('IV', 'IV.1') for v in nearest[col]
            if pd.notna(v) and math.isfinite(v) and v > 0
        ]
        if not candidates:
            return (
                f"No strike within the {window_n} nearest the current price has any nonzero "
                "IV quote at all, on either side - likely all locked/placeholder data."
            )

        best = max(candidates)
        return (
            f"The closest usable IV within the {window_n} nearest strikes to the current price "
            f"was {best:.1%}, which is {self._iv_implausibility_threshold_text(best)} - treated "
            "as an unreliable/placeholder quote rather than real (if unusually cheap) volatility."
        )

    def _get_atm_iv(self) -> float | None:
        """Average of call/put IV at the ATM strike, or whichever side is available.

        Reads from self.df (the displayed table), consistent with how get_total_stats()
        and the IV skew rule already scale against the displayed chain rather than
        original_df. Falls back to the nearest strike (within _NEAR_ATM_WINDOW of
        current_price) with plausible IV on at least one side if the exact ATM strike's
        IV is missing/implausible on both sides - a single locked/crossed quote at that
        one strike (common for 0DTE or thinly-traded names) shouldn't blank out every
        IV-driven rule when neighboring strikes are fine. Returns None if IV columns are
        absent or no strike in that window has usable IV.
        """
        if 'IV' not in self.df.columns or 'IV.1' not in self.df.columns:
            return None

        nearest = self.df.assign(
            _dist_from_price=(self.df[self.calls_strike_col_name] - self.current_price).abs()
        ).nsmallest(_NEAR_ATM_WINDOW, "_dist_from_price")

        for _, row in nearest.iterrows():
            call_iv = row['IV']
            put_iv = row['IV.1']
            call_valid = self._iv_is_plausible(call_iv)
            put_valid = self._iv_is_plausible(put_iv)

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

    def calculate_probability_cone(self) -> dict | None:
        """Implied Move's 1-SD (68%) band plus an approximate 2-SD (95%) band, by
        doubling the 1-SD move - the same Empirical Rule approximation already
        implicit in treating the IV-implied move as ~normal.
        """
        implied_move = self.calculate_implied_move()
        if implied_move is None:
            return None

        move_dollar = implied_move["move_dollar"]
        return {
            **implied_move,
            "low_2sd": self.current_price - 2 * move_dollar,
            "high_2sd": self.current_price + 2 * move_dollar,
        }

    def _probability_above_strike(self, strike: float) -> float | None:
        """Risk-neutral probability the stock finishes above `strike` at expiration,
        via Black-Scholes N(d2). Uses flat ATM IV (same simplification as
        calculate_implied_move) rather than per-strike IV, so it ignores the vol skew
        tracked separately by the IV Skew rule.
        """
        if self.dte is None or self.dte <= 0 or strike <= 0 or self.current_price <= 0:
            return None

        atm_iv = self._get_atm_iv()
        if atm_iv is None:
            return None

        t = self.dte / 365
        d2 = (
            math.log(self.current_price / strike) + (_RISK_FREE_RATE - 0.5 * atm_iv ** 2) * t
        ) / (atm_iv * math.sqrt(t))
        return 0.5 * (1 + math.erf(d2 / math.sqrt(2)))

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
        """Generates a technical breakdown based on positioning rules.

        Narrative generation lives in ta_breakdown.py (kept separate since it's mostly
        text templates rather than chain mechanics) - this just delegates to it, passing
        self for the chain-derived data/math methods it calls back into (calculate_max_pain,
        _wall_strike, _oi_missing_reason, etc., all of which stay here since some are also
        used outside the TA breakdown, e.g. by get_key_price_levels for the price chart).
        """
        import ta_breakdown
        return ta_breakdown.get_technical_breakdown(self, _RISK_FREE_RATE)

    def get_technical_breakdown_styler(self) -> Styler:
        """Styled get_technical_breakdown() for display: rows flagged with a "⚠️" in
        their Status (e.g. the OI-walls rule falling back to Volume because Open
        Interest is missing) are highlighted in orange so the caveat isn't missed.
        Orange (not red) deliberately - these flag missing/unreliable data, not a
        bearish or alarming market signal.
        """
        breakdown_df = pd.DataFrame(self.get_technical_breakdown())
        breakdown_df = breakdown_df[["Aspect", "Status", "Market Implication (MMs/Institutions vs Retail)", "Logic"]]

        def highlight_warnings(row: pd.Series) -> list[str]:
            if "⚠️" in str(row["Status"]):
                return ['color: #fd7e14; font-weight: bold'] * len(row)
            return [''] * len(row)

        return breakdown_df.style.hide(axis='index').apply(highlight_warnings, axis=1)

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

def highlight_cell(styler: Styler, col_name: str, val: float, color: str = "#3D7192", column_tint: str = "rgba(61, 113, 146, 0.18)") -> Styler:
    def style_atm_strike(s, target_val):
        # ATM cell gets the strong highlight; the rest of the Strike column gets a
        # gentle tint (lighter than the ATM color) so the column reads as a distinct
        # axis at a glance, not just a single highlighted cell.
        return [f'background-color: {color}; font-weight: bold' if v == target_val else f'background-color: {column_tint}' for v in s]

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
    # IV is a fraction (e.g. 0.35); display as a percentage. 2 decimal places (not 1) so
    # genuinely small-but-real IV readings (e.g. 0.3%) aren't visually indistinguishable
    # from the 0.0% placeholder/broken values _iv_is_plausible() filters out. Guarded
    # since CSV-loaded chains (the filepath= path in main()) may not have IV columns at all.
    iv_cols = [c for c in ('IV', 'IV.1') if c in display_df.columns]
    if iv_cols:
        df_context.styled_df = df_context.styled_df.format({c: '{:.2%}' for c in iv_cols}, subset=iv_cols)
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
    import json

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

        # yfinance timestamps are tz-aware in the exchange's local time (America/New_York).
        # Vega-Lite renders temporal values in the *browser's* local timezone by default, so
        # without this every viewer outside US Eastern would see different clock times for
        # the same bar. Convert to ET wall-clock digits, then relabel (not convert) those
        # digits as UTC: this makes the embedded instant equal "ET digits read as UTC", so
        # formatting with the UTC-based Vega functions below reproduces the original ET
        # digits identically for every viewer, regardless of their own timezone.
        price_df["Date"] = price_df["Date"].dt.tz_convert("America/New_York").dt.tz_localize(None).dt.tz_localize("UTC")

        # Bars that start a new calendar day (the first bar of each trading session) get
        # a date tick label instead of a time-of-day one. For daily-or-coarser granularity
        # (1mo/1y/max) every bar starts a new day, so every tick is dated, same as before.
        # Listed as absolute epoch-ms instants (timezone-proof) since the ordinal x-axis
        # below only ticks at real data points - unlike a continuous time scale, it can't
        # rely on Vega auto-placing a tick at literal local midnight to detect day changes.
        is_new_day = price_df["Date"].dt.normalize() != price_df["Date"].dt.normalize().shift(1)
        day_start_epoch_ms = json.dumps([int(ts.timestamp() * 1000) for ts in price_df["Date"][is_new_day]])
        has_intraday_ticks = bool((~is_new_day).any())

        # Precomputed ET-labeled tooltip text, rather than letting Vega-Lite format the
        # temporal field itself, since that formatting also defaults to the browser's local
        # timezone (the "relabel as UTC" trick above only covers the axis, which we format
        # with explicit UTC-based Vega expressions).
        price_df["DateLabel"] = price_df["Date"].dt.strftime("%b %d, %Y, %H:%M") + " ET"

        layers.append(
            alt.Chart(price_df).mark_line(
                color=_PRICE_UP_COLOR if is_up else _PRICE_DOWN_COLOR, clip=True, point=show_points
            ).encode(
                x=alt.X(
                    # Ordinal, not temporal: a continuous time scale draws the gap between
                    # each day's last bar and the next day's first bar (after-hours overnight,
                    # or a whole weekend) as real elapsed time, which looks like a long flat/
                    # diagonal "fake" move connecting the two points. Ordinal spaces every bar
                    # evenly regardless of the real time gap, so only actual trading-session
                    # data shapes the line.
                    "Date:O",
                    title="Time (ET)" if has_intraday_ticks else None,
                    # Default tick labels use 12-hour AM/PM; force 24-hour time for
                    # intraday ticks while leaving the first bar of each day showing as a
                    # plain date. utcFormat (not timeFormat) reads the relabeled-as-UTC
                    # field above as plain ET digits instead of re-shifting to the browser's
                    # own timezone.
                    axis=alt.Axis(
                        labelExpr=(
                            f"indexof({day_start_epoch_ms}, time(datum.value)) >= 0 "
                            "? utcFormat(datum.value, '%b %d') "
                            ": utcFormat(datum.value, '%H:%M')"
                        )
                    ),
                ),
                y=alt.Y("Close:Q", title="Price", scale=alt.Scale(domain=domain, zero=False)),
                tooltip=[
                    alt.Tooltip("DateLabel:N", title="Date"),
                    alt.Tooltip("Close:Q", format=".2f", title="Close"),
                ],
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
            text_x = alt.X("_AnchorDate:O")
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
        "technical_breakdown": df_context.get_technical_breakdown_styler(),
        "company_name": company_name,
        "retrieval_time": retrieval_time
    }
