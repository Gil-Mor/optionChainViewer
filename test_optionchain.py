import pytest
import pandas as pd
import numpy as np
from optionchain import (
    OptionContext,
    calls_puts_side_by_side_distance_from_strike,
    get_atm_strike_from_current_price,
    readcsv,
)

# --- Helpers ---

CHAIN_COLS = [
    "Last Price", "Change", "% Change", "Volume", "Open Interest",
    "Strike",
    "Last Price.1", "Change.1", "% Change.1", "Volume.1", "Open Interest.1",
]

def make_chain(strikes, call_oi, put_oi, call_vol=None, put_vol=None):
    """Build a minimal synthetic option-chain DataFrame matching yfinance structure."""
    n = len(strikes)
    if call_vol is None:
        call_vol = [float(i * 10) for i in range(1, n + 1)]
    if put_vol is None:
        put_vol = [float(i * 5) for i in range(1, n + 1)]
    data = {
        "Last Price": [1.0] * n,
        "Change": [0.0] * n,
        "% Change": [0.0] * n,
        "Volume": list(map(float, call_vol)),
        "Open Interest": list(map(float, call_oi)),
        "Strike": list(map(float, strikes)),
        "Last Price.1": [1.0] * n,
        "Change.1": [0.0] * n,
        "% Change.1": [0.0] * n,
        "Volume.1": list(map(float, put_vol)),
        "Open Interest.1": list(map(float, put_oi)),
    }
    return pd.DataFrame(data, columns=CHAIN_COLS)


def make_context(strikes, call_oi, put_oi, current_price, call_vol=None, put_vol=None):
    df = make_chain(strikes, call_oi, put_oi, call_vol, put_vol)
    ctx = OptionContext(df, "TEST", current_price)
    ctx.get_total_stats()
    return ctx


# --- ATM detection ---

class TestAtmDetection:
    def test_exact_match(self):
        df = make_chain([90, 95, 100, 105, 110], [0]*5, [0]*5)
        assert get_atm_strike_from_current_price(df, 100.0) == 100.0

    def test_closest_below(self):
        df = make_chain([90, 95, 100, 105, 110], [0]*5, [0]*5)
        assert get_atm_strike_from_current_price(df, 102.0) == 100.0

    def test_closest_above(self):
        df = make_chain([90, 95, 100, 105, 110], [0]*5, [0]*5)
        # 97 is closer to 95 (dist 2) than to 100 (dist 3)
        assert get_atm_strike_from_current_price(df, 97.0) == 95.0

    def test_empty_df(self):
        assert get_atm_strike_from_current_price(pd.DataFrame(), 100.0) == 0.0

    def test_none_price(self):
        df = make_chain([100], [0], [0])
        assert get_atm_strike_from_current_price(df, None) == 0.0


# --- Partition invariant: ITM + ATM + OTM == Total ---

class TestPartitionInvariant:
    """The three buckets must cover all strikes with no overlap and no gap."""

    def _assert_partition(self, ctx):
        assert ctx.itm_calls_open_interest_sum + ctx.atm_calls_open_interest_sum + ctx.otm_calls_open_interest_sum == ctx.total_calls_open_interest_sum
        assert ctx.itm_puts_open_interest_sum + ctx.atm_puts_open_interest_sum + ctx.otm_puts_open_interest_sum == ctx.total_puts_open_interest_sum
        assert ctx.itm_calls_volume_sum + ctx.atm_calls_volume_sum + ctx.otm_calls_volume_sum == ctx.total_calls_volume_sum
        assert ctx.itm_puts_volume_sum + ctx.atm_puts_volume_sum + ctx.otm_puts_volume_sum == ctx.total_puts_volume_sum

    def test_atm_below_price(self):
        # current_price=102, ATM=100 (closest below)
        ctx = make_context(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[1000, 2000, 3000, 4000, 5000],
            put_oi=[500, 1000, 1500, 2000, 2500],
            current_price=102,
        )
        self._assert_partition(ctx)

    def test_atm_above_price(self):
        # current_price=93, ATM=95 (closest above)
        ctx = make_context(
            strikes=[90, 95, 100],
            call_oi=[1000, 2000, 3000],
            put_oi=[500, 1000, 1500],
            current_price=93,
        )
        self._assert_partition(ctx)

    def test_atm_exact_match(self):
        # current_price=100, ATM=100 (exact)
        ctx = make_context(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[1000, 2000, 3000, 4000, 5000],
            put_oi=[500, 1000, 1500, 2000, 2500],
            current_price=100,
        )
        self._assert_partition(ctx)

    def test_single_strike_is_atm(self):
        ctx = make_context(
            strikes=[100],
            call_oi=[5000],
            put_oi=[3000],
            current_price=99,
        )
        self._assert_partition(ctx)
        assert ctx.itm_calls_open_interest_sum == 0
        assert ctx.otm_calls_open_interest_sum == 0
        assert ctx.atm_calls_open_interest_sum == 5000

    def test_after_trim(self):
        # 10 strikes, trim ±2 → 5 visible strikes
        ctx = OptionContext(
            make_chain(
                strikes=list(range(80, 131, 5)),   # 80,85,...,130 (11 strikes)
                call_oi=[100 * i for i in range(1, 12)],
                put_oi=[50 * i for i in range(1, 12)],
            ),
            "TEST", 102.0,
        )
        ctx = calls_puts_side_by_side_distance_from_strike(ctx, trim_around_strike=2)
        ctx.get_total_stats()
        self._assert_partition(ctx)


# --- Correct bucket membership ---

class TestCorrectBuckets:
    """Spot-check that specific strikes land in the expected bucket."""

    def setup_method(self):
        # strikes=[90,95,100,105,110], current_price=102, ATM=100
        self.ctx = make_context(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[1000, 2000, 3000, 4000, 5000],
            put_oi=[500, 1000, 1500, 2000, 2500],
            current_price=102,
        )

    def test_low_strike_is_itm_call(self):
        assert 90.0 in self.ctx.itm_calls["Strike"].values
        assert 95.0 in self.ctx.itm_calls["Strike"].values

    def test_low_strike_is_otm_put(self):
        assert 90.0 in self.ctx.otm_puts["Strike"].values
        assert 95.0 in self.ctx.otm_puts["Strike"].values

    def test_high_strike_is_otm_call(self):
        assert 105.0 in self.ctx.otm_calls["Strike"].values
        assert 110.0 in self.ctx.otm_calls["Strike"].values

    def test_high_strike_is_itm_put(self):
        assert 105.0 in self.ctx.itm_puts["Strike"].values
        assert 110.0 in self.ctx.itm_puts["Strike"].values

    def test_atm_strike_excluded_from_itm_and_otm(self):
        atm = self.ctx.atm_strike  # 100.0
        assert atm not in self.ctx.itm_calls["Strike"].values
        assert atm not in self.ctx.otm_calls["Strike"].values
        assert atm not in self.ctx.itm_puts["Strike"].values
        assert atm not in self.ctx.otm_puts["Strike"].values


# --- Exact sum values ---

class TestExactSums:
    """Verify the computed sums match hand-calculated expected values."""

    def test_atm_below_price_known_values(self):
        # current_price=102, ATM=100
        # Calls: ITM={90→1000, 95→2000}=3000, ATM={100→3000}=3000, OTM={105→4000,110→5000}=9000
        # Puts:  OTM={90→500,  95→1000}=1500, ATM={100→1500}=1500, ITM={105→2000,110→2500}=4500
        ctx = make_context(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[1000, 2000, 3000, 4000, 5000],
            put_oi=[500, 1000, 1500, 2000, 2500],
            current_price=102,
        )
        assert ctx.itm_calls_open_interest_sum == 3000
        assert ctx.atm_calls_open_interest_sum == 3000
        assert ctx.otm_calls_open_interest_sum == 9000
        assert ctx.total_calls_open_interest_sum == 15000

        assert ctx.otm_puts_open_interest_sum == 1500
        assert ctx.atm_puts_open_interest_sum == 1500
        assert ctx.itm_puts_open_interest_sum == 4500
        assert ctx.total_puts_open_interest_sum == 7500

    def test_atm_above_price_known_values(self):
        # current_price=93, ATM=95 (dist 2 vs dist 3 from 90)
        # Calls: ITM={90→1000}=1000, ATM={95→2000}=2000, OTM={100→3000}=3000
        # Puts:  ITM={100→1500}=1500, ATM={95→1000}=1000, OTM={90→500}=500
        ctx = make_context(
            strikes=[90, 95, 100],
            call_oi=[1000, 2000, 3000],
            put_oi=[500, 1000, 1500],
            current_price=93,
        )
        assert ctx.itm_calls_open_interest_sum == 1000
        assert ctx.atm_calls_open_interest_sum == 2000
        assert ctx.otm_calls_open_interest_sum == 3000

        assert ctx.itm_puts_open_interest_sum == 1500
        assert ctx.atm_puts_open_interest_sum == 1000
        assert ctx.otm_puts_open_interest_sum == 500

    def test_atm_excluded_from_otm_when_atm_above_price(self):
        # ATM (95) is technically OTM for calls (95 > 93) but must be in ATM bucket only
        ctx = make_context(
            strikes=[90, 95, 100],
            call_oi=[1000, 2000, 3000],
            put_oi=[500, 1000, 1500],
            current_price=93,
        )
        # ATM OI (2000) must NOT be included in OTM calls
        assert ctx.otm_calls_open_interest_sum == 3000  # only strike 100

    def test_atm_excluded_from_itm_when_atm_below_price(self):
        # current_price=102, ATM=100 (ATM technically ITM for calls)
        ctx = make_context(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[1000, 2000, 3000, 4000, 5000],
            put_oi=[500, 1000, 1500, 2000, 2500],
            current_price=102,
        )
        # ATM OI (3000) must NOT be in ITM calls — only strikes 90,95
        assert ctx.itm_calls_open_interest_sum == 3000  # 1000+2000 only


# --- Trim behavior ---

class TestTrimBehavior:
    """Stats are computed on the TRIMMED (visible) window, not the full chain."""

    def test_totals_reflect_trimmed_window(self):
        # 11 strikes: 80,85,...,130. current_price=102 → ATM=100 (index 4)
        # Full chain call OI: 100*i for i=1..11 → total = 100*(1+2+...+11) = 6600
        # Trim ±2 → 5 visible strikes: 90,95,100,105,110 → OI = 300+400+500+600+700 = 2500
        ctx = OptionContext(
            make_chain(
                strikes=list(range(80, 131, 5)),
                call_oi=[100 * i for i in range(1, 12)],
                put_oi=[50 * i for i in range(1, 12)],
            ),
            "TEST", 102.0,
        )
        ctx = calls_puts_side_by_side_distance_from_strike(ctx, trim_around_strike=2)
        ctx.get_total_stats()

        # Only the 5 visible strikes count toward total
        assert ctx.total_calls_open_interest_sum == 2500.0
        # Full-chain total would be 6600 — verify we are NOT using it
        assert ctx.total_calls_open_interest_sum != 6600.0

    def test_no_trim_uses_full_chain(self):
        ctx = OptionContext(
            make_chain(
                strikes=list(range(80, 131, 5)),
                call_oi=[100 * i for i in range(1, 12)],
                put_oi=[50 * i for i in range(1, 12)],
            ),
            "TEST", 102.0,
        )
        ctx = calls_puts_side_by_side_distance_from_strike(ctx, trim_around_strike=0)
        ctx.get_total_stats()
        assert ctx.total_calls_open_interest_sum == 6600.0


# --- flip_strikes=True mode ---

class TestFlipStrikesMode:
    """Row-flipping the puts side must not change OI/Volume sums."""

    def _expected_sums(self, strikes, call_oi, put_oi, current_price):
        return make_context(strikes, call_oi, put_oi, current_price)

    def test_partition_holds_after_flip(self):
        ctx = OptionContext(
            make_chain(
                strikes=[90, 95, 100, 105, 110],
                call_oi=[1000, 2000, 3000, 4000, 5000],
                put_oi=[500, 1000, 1500, 2000, 2500],
            ),
            "TEST", 102.0,
        )
        ctx = calls_puts_side_by_side_distance_from_strike(ctx, flip_strikes=True)
        ctx.get_total_stats()

        assert ctx.itm_calls_open_interest_sum + ctx.atm_calls_open_interest_sum + ctx.otm_calls_open_interest_sum == ctx.total_calls_open_interest_sum
        assert ctx.itm_puts_open_interest_sum + ctx.atm_puts_open_interest_sum + ctx.otm_puts_open_interest_sum == ctx.total_puts_open_interest_sum

    def test_sums_identical_to_no_flip(self):
        kwargs = dict(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[1000, 2000, 3000, 4000, 5000],
            put_oi=[500, 1000, 1500, 2000, 2500],
        )
        ref = make_context(**kwargs, current_price=102)

        ctx = OptionContext(make_chain(**kwargs), "TEST", 102.0)
        ctx = calls_puts_side_by_side_distance_from_strike(ctx, flip_strikes=True)
        ctx.get_total_stats()

        assert ctx.otm_calls_open_interest_sum == ref.otm_calls_open_interest_sum
        assert ctx.atm_calls_open_interest_sum == ref.atm_calls_open_interest_sum
        assert ctx.itm_calls_open_interest_sum == ref.itm_calls_open_interest_sum
        assert ctx.otm_puts_open_interest_sum == ref.otm_puts_open_interest_sum
        assert ctx.atm_puts_open_interest_sum == ref.atm_puts_open_interest_sum
        assert ctx.itm_puts_open_interest_sum == ref.itm_puts_open_interest_sum


# --- CSV comma-formatted number bug ---

class TestCsvNumericTypes:
    """Comma-formatted Open Interest values (e.g. "3,341") in TSV input
    cause .sum() to concatenate strings instead of summing numbers.
    The fix is to clean numeric columns during CSV ingestion."""

    def _make_string_oi_df(self):
        """DataFrame with OI as comma-formatted strings, like a raw TSV import."""
        return pd.DataFrame({
            "Last Price": [1.0, 1.0],
            "Change": [0.0, 0.0],
            "% Change": [0.0, 0.0],
            "Volume": [100.0, 200.0],
            "Open Interest": ["1,000", "2,000"],  # string dtype — the bug
            "Strike": [95.0, 100.0],
            "Last Price.1": [1.0, 1.0],
            "Change.1": [0.0, 0.0],
            "% Change.1": [0.0, 0.0],
            "Volume.1": [50.0, 100.0],
            "Open Interest.1": ["500", "1,500"],  # string dtype — the bug
        })

    def test_string_oi_crashes_get_total_stats(self):
        # String OI/Volume causes get_total_stats to crash with TypeError
        # when it tries to compare strings to ints in max().
        df = self._make_string_oi_df()
        ctx = OptionContext(df, "TEST", 98.0)
        with pytest.raises((TypeError, ValueError)):
            ctx.get_total_stats()

    def test_numeric_conversion_fixes_sum(self):
        df = self._make_string_oi_df()
        for col in ["Open Interest", "Open Interest.1", "Volume", "Volume.1"]:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
        ctx = OptionContext(df, "TEST", 98.0)
        ctx.get_total_stats()
        assert ctx.total_calls_open_interest_sum == 3000.0
        assert ctx.total_puts_open_interest_sum == 2000.0

    def test_readcsv_handles_thousands_separator(self, tmp_path):
        tsv = tmp_path / "chain.tsv"
        tsv.write_text(
            "Last Price\tChange\t% Change\tVolume\tOpen Interest\tStrike\t"
            "Last Price\tChange\t% Change\tVolume\tOpen Interest\n"
            "1.0\t0.0\t0.0\t100\t1,000\t95.0\t1.0\t0.0\t0.0\t50\t500\n"
            "1.0\t0.0\t0.0\t200\t2,000\t100.0\t1.0\t0.0\t0.0\t100\t1,500\n"
        )
        df = readcsv(str(tsv))
        # After the fix in readcsv, OI columns must be numeric
        assert pd.api.types.is_numeric_dtype(df["Open Interest"]), (
            "Open Interest column is not numeric after readcsv — "
            "comma-formatted numbers are not being cleaned up."
        )
        assert df["Open Interest"].sum() == 3000.0


# --- Bar scaling mode smoke tests ---

class TestBarScalingModes:
    """Smoke tests: each mode renders without crashing and distinct modes differ."""

    STRIKES = [90, 95, 100, 105, 110]
    # Asymmetric OI so modes with different denominators produce visually distinct output
    CALL_OI = [100, 500, 3000, 800, 200]
    PUT_OI  = [150, 400, 2500, 900, 100]

    def _ctx(self, mode):
        ctx = OptionContext(
            make_chain(self.STRIKES, self.CALL_OI, self.PUT_OI),
            "TEST", 102.0,
        )
        return calls_puts_side_by_side_distance_from_strike(ctx, bar_scaling_mode=mode)

    def test_mode1_groups_does_not_crash(self):
        # Previously crashed with AttributeError — get_total_stats() was called too late
        ctx = self._ctx("Relative to OTM/ITM/ATM Groups")
        assert "background" in ctx.styled_df.to_html()

    def test_mode2_per_strike_does_not_crash(self):
        ctx = self._ctx("Per Strike (Row)")
        assert ctx.styled_df is not None

    def test_mode3_full_chain_does_not_crash(self):
        ctx = self._ctx("Relative to Full Chain")
        assert "background" in ctx.styled_df.to_html()

    def test_mode4_per_side_does_not_crash(self):
        ctx = self._ctx("Per Side (Each side's own peak)")
        assert "background" in ctx.styled_df.to_html()

    def test_mode1_and_mode3_produce_different_output(self):
        # Different denominators → different bar widths → different HTML
        html1 = self._ctx("Relative to OTM/ITM/ATM Groups").styled_df.to_html()
        html3 = self._ctx("Relative to Full Chain").styled_df.to_html()
        assert html1 != html3

    def test_mode3_and_mode4_differ_when_call_put_peaks_differ(self):
        # Call peak (3000) >> put peak (2500): Mode 3 uses 3000 for both sides,
        # Mode 4 uses 3000 for calls and 2500 for puts → puts bars differ
        html3 = self._ctx("Relative to Full Chain").styled_df.to_html()
        html4 = self._ctx("Per Side (Each side's own peak)").styled_df.to_html()
        assert html3 != html4
