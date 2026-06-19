"""Technical breakdown narrative for an options chain.

Generates the "TA Breakdown" table: a list of rule dicts (Aspect/Status/Logic/
Market Implication) derived from an OptionContext's already-computed chain
data. Kept separate from optionchain.py because this is almost entirely
narrative templates/thresholds rather than chain mechanics - optionchain.py
keeps the underlying data-access and math (calculate_max_pain,
calculate_implied_move, _wall_strike, _oi_missing_reason, etc.), this module
just turns those numbers into rule-by-rule text.

`ctx` is duck-typed as an OptionContext instance rather than imported, so this
module has no dependency on optionchain.py - optionchain.py imports this
module instead, and a two-way import would be circular.
"""

import yfinanceGetOptions as yfi


def _trim_radius_needed_for_strike(ctx, strike: float) -> int:
    """The 'Trim table around strike' radius (row-count from the ATM strike, not a
    $ distance - see trim_rows_symmetric_radius in optionchain.py) a user would need
    to set for `strike` to actually appear in the displayed table/chart.
    """
    original_reset = ctx.original_df.reset_index(drop=True)
    atm_pos = original_reset.index[original_reset["Strike"] == ctx.atm_strike][0]
    strike_pos = original_reset.index[original_reset["Strike"] == strike][0]
    return abs(int(strike_pos) - int(atm_pos))


def get_technical_breakdown(ctx, risk_free_rate: float) -> list[dict]:
    """Generates a technical breakdown based on positioning rules."""
    breakdown = []

    # Rule 1: Total Put/Call Open Interest Ratio (Overall Balance)
    # Computed from original_df (the full, untrimmed chain), not ctx.df - same
    # reasoning as calculate_max_pain()/Rule 7: a user's trim setting narrows what's
    # *displayed*, but shouldn't narrow what this ratio is computed over and silently
    # flip the read sentiment depending on an unrelated display setting.
    total_calls_oi_full = ctx.original_df["Open Interest"].sum()
    total_puts_oi_full = ctx.original_df["Open Interest.1"].sum()
    oi_reason = ctx._oi_missing_reason(
        ("Call", ctx.original_df["Open Interest"], ctx.original_df["Volume"]),
        ("Put", ctx.original_df["Open Interest.1"], ctx.original_df["Volume.1"]),
    )
    # Tracked across rules (None when not computable) so the Cross-Rule Synthesis
    # pass at the bottom can check what each rule actually found, without re-deriving
    # it from rendered status strings (which are free to change wording later).
    pc_ratio = None
    otm_ratio = None
    iv_skew_ratio = None

    if oi_reason is not None:
        breakdown.append({
            "Aspect": "Overall Balance",
            "Status": "N/A ⚠️ Open Interest unreliable",
            "Logic": f"{oi_reason} Cannot compute Put/Call ratio.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable Open Interest data."
        })
    else:
        pc_ratio = total_puts_oi_full / total_calls_oi_full if total_calls_oi_full > 0 else float('inf')

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

    # Implied Move is computed here (hoisted up from Rule 8's original spot) so Rule 2
    # and Rule 3 below can cite its actual $/% boundary instead of generic language -
    # Rule 8 further down reuses this same value rather than recomputing it.
    implied_move = ctx.calculate_implied_move()

    # Rule 2: OTM Distribution (Speculative Skew)
    # Computed from original_df (the full, untrimmed chain) via _get_otm_full_chain -
    # see Rule 1 for why.
    otm_calls_full, otm_puts_full = ctx._get_otm_full_chain()
    otm_call_oi = otm_calls_full["Open Interest"].sum()
    otm_put_oi = otm_puts_full["Open Interest.1"].sum()

    otm_oi_reason = ctx._oi_missing_reason(
        ("Call", otm_calls_full["Open Interest"], otm_calls_full["Volume"]),
        ("Put", otm_puts_full["Open Interest.1"], otm_puts_full["Volume.1"]),
    )
    if otm_oi_reason is not None:
        breakdown.append({
            "Aspect": "OTM Skew (Speculation)",
            "Status": "N/A ⚠️ Open Interest unreliable",
            "Logic": f"{otm_oi_reason} Cannot compute OTM Call/Put ratio.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable Open Interest data."
        })
    else:
        otm_ratio = otm_call_oi / otm_put_oi if otm_put_oi > 0 else float('inf')

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

        # (B) Deep citation: for the two "strong" buckets, name the actual implied-move
        # boundary instead of leaving "betting against extreme moves" as an unanchored
        # claim - ties this rule's read to Rule 8's number instead of reading like a
        # separate, possibly conflicting, magnitude forecast.
        if status in ("Strong OTM Call Skew (Lotto Bias)", "Strong OTM Put Skew (Panic/Hedging)") and implied_move is not None:
            mm_inst += (
                f" The market's own pricing implies a ±{implied_move['move_pct']:.1%} move to "
                f"${implied_move['low']:,.2f}-${implied_move['high']:,.2f} by expiration ({ctx.dte} days) - "
                "positioning this far OTM is a bet on a move beyond that already-priced-in range, not a "
                "forecast that price won't move at all."
            )

        breakdown.append({
            "Aspect": "OTM Skew (Speculation)",
            "Status": status,
            "Logic": f"OTM Call/Put Ratio: {otm_ratio:.2f}",
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })

    # Rule 3: Volume vs Open Interest (Market Urgency)
    # Computed from original_df (the full, untrimmed chain) - see Rule 1 for why.
    total_oi = total_calls_oi_full + total_puts_oi_full
    total_vol = ctx.original_df["Volume"].sum() + ctx.original_df["Volume.1"].sum()

    urgency_oi_reason = ctx._oi_missing_reason(
        ("Call", ctx.original_df["Open Interest"], ctx.original_df["Volume"]),
        ("Put", ctx.original_df["Open Interest.1"], ctx.original_df["Volume.1"]),
    )
    if urgency_oi_reason is not None:
        breakdown.append({
            "Aspect": "Market Urgency (Vol/OI)",
            "Status": "N/A ⚠️ Open Interest unreliable",
            "Logic": f"{urgency_oi_reason} Cannot compute Vol/OI ratio. (Not the same as 'Low Conviction', which means OI exists but Volume is genuinely low against it.)",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable Open Interest data."
        })
    else:
        vol_oi_ratio = total_vol / total_oi

        if vol_oi_ratio > 0.5:
            status = "High Urgency / Fresh Interest"
            mm_inst = "Volume is very high relative to OI. This suggests large-scale 'opening' or 'closing' of positions. Institutions are likely repositioning for a major move or earnings. Retail is often 'chasing' the trend here."
        elif vol_oi_ratio > 0.15:
            status = "Healthy Turnover"
            mm_inst = "Normal market participation. Positions are being rolled or adjusted, but there is no sign of a massive structural shift in sentiment."
        else:
            status = "Low Conviction / Consolidation"
            mm_inst = "Volume is low relative to existing positions. Market participants are standing pat. Expect range-bound price action as the 'status quo' remains unchallenged."

        # (B) Deep citation: temper the "major move/earnings" framing for short-dated
        # chains, where high turnover is structurally normal (weeklies roll/churn fast)
        # rather than necessarily a signal about an upcoming catalyst.
        if status == "High Urgency / Fresh Interest" and ctx.dte is not None and ctx.dte <= 10:
            mm_inst += (
                f" Worth noting this is only a {ctx.dte}-day chain - weeklies structurally show higher "
                "turnover than longer-dated chains regardless of any pending catalyst, so weigh the "
                "'major move' framing above accordingly."
            )

        breakdown.append({
            "Aspect": "Market Urgency (Vol/OI)",
            "Status": status,
            "Logic": f"Vol/OI Ratio: {vol_oi_ratio:.2f}",
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })

    # Rule 4: Key Technical Levels (OI Walls)
    # Find strikes with max Call OI and max Put OI across original_df (the full,
    # untrimmed chain) - same reasoning as Rule 1. Falls back to Volume per-side if
    # that side's OI isn't trustworthy - see _wall_strike / _oi_missing_reason.
    call_wall, call_fallback_reason = ctx._wall_strike(ctx.original_df, "Open Interest", "Volume", ctx.calls_strike_col_name, "Call")
    put_wall, put_fallback_reason = ctx._wall_strike(ctx.original_df, "Open Interest.1", "Volume.1", ctx.puts_strike_col_name, "Put")

    # Distance from current price, shown directly (matching Max Pain's existing "(+X%
    # from current price)" format) rather than left for the reader to eyeball - a row-
    # count like the trim radius below doesn't translate into "is this far/near" on its
    # own since strike spacing isn't uniform across the chain.
    call_wall_pct = (call_wall - ctx.current_price) / ctx.current_price * 100
    put_wall_pct = (put_wall - ctx.current_price) / ctx.current_price * 100
    status_text = f"Resistance: {call_wall} ({call_wall_pct:+.1f}%) | Support: {put_wall} ({put_wall_pct:+.1f}%)"
    logic_text = "Identifying strikes with the highest Open Interest concentration."
    if call_fallback_reason or put_fallback_reason:
        status_text += " ⚠️ Open Interest unreliable - using Volume instead"
        reasons = " ".join(r for r in (call_fallback_reason, put_fallback_reason) if r)
        logic_text = f"{reasons} Falling back to highest Volume strikes instead. Treat these levels as low-confidence."
    else:
        # Concentration sanity check: a wall is an idxmax() pick, so unlike the sum-based
        # rules above (Overall Balance, Market Urgency - where one outlier strike is just
        # diluted into the total), a single strike with implausibly large OI relative to
        # the rest of the chain can single-handedly decide the "wall" outright. Flag it -
        # this can be a real large institutional position, but is also a known failure
        # mode for stale/erroneous OI data from yfinance, so it's worth a second look
        # before treating the level as confirmed.
        concentration_notes = []
        if total_calls_oi_full > 0:
            call_wall_oi = ctx.original_df.loc[ctx.original_df[ctx.calls_strike_col_name] == call_wall, "Open Interest"].iloc[0]
            call_share = call_wall_oi / total_calls_oi_full
            if call_share > 0.15:
                concentration_notes.append(
                    f"the Call Wall strike ({call_wall}) alone holds {call_share:.1%} of total Call Open Interest"
                )
        if total_puts_oi_full > 0:
            put_wall_oi = ctx.original_df.loc[ctx.original_df[ctx.puts_strike_col_name] == put_wall, "Open Interest.1"].iloc[0]
            put_share = put_wall_oi / total_puts_oi_full
            if put_share > 0.15:
                concentration_notes.append(
                    f"the Put Wall strike ({put_wall}) alone holds {put_share:.1%} of total Put Open Interest"
                )
        if concentration_notes:
            logic_text += (
                f" ⚠️ Concentration risk: {' and '.join(concentration_notes)} across the entire chain - "
                "unusually concentrated for a single strike. This can reflect a real large institutional "
                "position, but is also a known failure mode for stale/erroneous OI data from yfinance. "
                "Cross-check this level against another source before treating it as a confirmed wall."
            )

    # A wall computed from the full chain can land on a strike the user has trimmed
    # out of the displayed table/chart - flag that explicitly, with the exact radius
    # needed to bring it into view, rather than leaving someone unable to find
    # "Resistance: 800.0" anywhere in what they're looking at and not knowing how far
    # to widen the trim to fix it.
    displayed_low, displayed_high = ctx.get_strike_range()
    out_of_range = []
    needed_radii = []
    if call_wall < displayed_low or call_wall > displayed_high:
        out_of_range.append(f"Call Wall ({call_wall})")
        needed_radii.append(_trim_radius_needed_for_strike(ctx, call_wall))
    if put_wall < displayed_low or put_wall > displayed_high:
        out_of_range.append(f"Put Wall ({put_wall})")
        needed_radii.append(_trim_radius_needed_for_strike(ctx, put_wall))
    if out_of_range:
        them_it = "them" if len(out_of_range) > 1 else "it"
        logic_text += (
            f" ⚠️ {' and '.join(out_of_range)} outside your displayed range "
            f"({displayed_low:.2f}-{displayed_high:.2f}) - set 'Trim table around strike' to "
            f"{max(needed_radii)} or higher to see {them_it} in the table/chart."
        )

    breakdown.append({
        "Aspect": "Institutional 'Walls'",
        "Status": status_text,
        "Logic": logic_text,
        "Market Implication (MMs/Institutions vs Retail)": (
            f"The Call Wall at {call_wall} acts as a ceiling where MMs are net sellers, creating heavy resistance. "
            f"The Put Wall at {put_wall} acts as a floor where institutions have bought protection. "
            "Price often 'pins' or bounces between these two levels as expiration approaches."
        )
    })

    # Rule 5: Max Pain
    max_pain = ctx.calculate_max_pain()

    if max_pain is None:
        max_pain_reason = ctx._oi_missing_reason(
            ("Call", ctx.original_df["Open Interest"], ctx.original_df["Volume"]),
            ("Put", ctx.original_df["Open Interest.1"], ctx.original_df["Volume.1"]),
        )
        breakdown.append({
            "Aspect": "Max Pain",
            "Status": "N/A ⚠️ Open Interest unreliable",
            "Logic": f"{max_pain_reason} Max Pain has no reliable Open Interest to weigh payouts by.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable Open Interest data."
        })
    else:
        distance_pct = (ctx.current_price - max_pain) / max_pain * 100

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

    # Rule 6: Probability vs Key Levels - risk-neutral odds (Black-Scholes N(d2),
    # flat ATM IV) of price actually breaking the Call/Put Walls and Max Pain by
    # expiration, rather than just naming the levels as in Rules 4-5. Guarded the
    # same way as Implied Move: 'no DTE or IV columns missing entirely' (skip
    # silently) is distinct from 'ATM IV present but unreliable' (show N/A).
    p_above_call_wall = ctx._probability_above_strike(call_wall)
    p_above_put_wall = ctx._probability_above_strike(put_wall)

    if p_above_call_wall is not None and p_above_put_wall is not None:
        p_below_put_wall = 1 - p_above_put_wall

        status_text = (
            f"{p_above_call_wall:.0%} chance above Call Wall (${call_wall}) | "
            f"{p_below_put_wall:.0%} chance below Put Wall (${put_wall})"
        )
        logic_text = (
            f"Black-Scholes N(d2), flat ATM IV ({ctx._get_atm_iv():.1%}), {risk_free_rate:.1%} fixed risk-free rate. "
            "Same flat-vol simplification as Implied Move - ignores the skew tracked separately by the IV Skew rule."
        )
        mm_inst = (
            "A low chance of breaking the Call Wall reinforces it as resistance MMs will defend; a high chance "
            "suggests that level may not hold. Same read in reverse for the Put Wall as support."
        )

        if max_pain is not None:
            p_above_max_pain = ctx._probability_above_strike(max_pain)
            if p_above_max_pain is not None:
                status_text += f" | {p_above_max_pain:.0%} chance above Max Pain (${max_pain:,.2f})"

        breakdown.append({
            "Aspect": "Probability vs Key Levels",
            "Status": status_text,
            "Logic": logic_text,
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })
    elif ctx.dte is not None and ctx.dte <= 0:
        breakdown.append({
            "Aspect": "Probability vs Key Levels",
            "Status": f"N/A - {ctx.dte} DTE",
            "Logic": "This expiration has 0 (or negative) days to expiration - no remaining time premium to derive a risk-neutral probability from.",
            "Market Implication (MMs/Institutions vs Retail)": "Not applicable for an expiration that has already settled or expires today."
        })
    elif ctx.dte is not None and ctx.dte > 0 and 'IV' in ctx.df.columns and 'IV.1' in ctx.df.columns:
        breakdown.append({
            "Aspect": "Probability vs Key Levels",
            "Status": "N/A ⚠️ IV unreliable",
            "Logic": f"{ctx._atm_iv_na_reason()} Cannot estimate probability of breaking key levels.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable IV data."
        })
    # else: no expiration date supplied at all (e.g. CSV-loaded chain) - feature not
    # applicable, skip silently

    # Rule 7: OTM IV Skew (Put vs Call) - the "volatility smirk". Computed from
    # original_df (the full, untrimmed chain), not ctx.df - same reasoning as
    # calculate_max_pain()/Rules 1-4: a user's trim setting narrows what's
    # *displayed*, but shouldn't narrow what this average is computed over and
    # silently make a real skew reading look implausible. Guarded: CSV-loaded
    # chains (filepath= in main()) may not have IV columns.
    if 'IV' in ctx.df.columns and 'IV.1' in ctx.df.columns:
        otm_calls_full, otm_puts_full = ctx._get_otm_full_chain()
        otm_call_iv = otm_calls_full['IV'].mean() if not otm_calls_full.empty else float('nan')
        otm_put_iv = otm_puts_full['IV.1'].mean() if not otm_puts_full.empty else float('nan')

        # Surfaced whenever the displayed chain is narrower than the full chain this
        # rule actually computes from, so a user looking only at the visible rows
        # isn't left wondering why the numbers don't match what they'd eyeball.
        trim_note = (
            f" (Computed from the full {len(ctx.original_df)}-strike chain - "
            f"your current view is trimmed to {len(ctx.df)} strikes.)"
            if len(ctx.df) < len(ctx.original_df) else ""
        )

        if ctx._iv_is_plausible(otm_call_iv) and ctx._iv_is_plausible(otm_put_iv):
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
                "Logic": f"Rule: {rule_ref} (Avg OTM Call IV: {otm_call_iv:.1%}, Avg OTM Put IV: {otm_put_iv:.1%}){trim_note}",
                "Market Implication (MMs/Institutions vs Retail)": mm_inst
            })
        else:
            reasons = []
            if not ctx._iv_is_plausible(otm_call_iv):
                reasons.append(ctx._iv_na_explanation(otm_call_iv, "Avg OTM Call IV"))
            if not ctx._iv_is_plausible(otm_put_iv):
                reasons.append(ctx._iv_na_explanation(otm_put_iv, "Avg OTM Put IV"))
            breakdown.append({
                "Aspect": "IV Skew (OTM Put vs Call)",
                "Status": "N/A ⚠️ IV unreliable",
                "Logic": f"{'; '.join(reasons)} - cannot compute skew.{trim_note}",
                "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable IV data."
            })
    # else: IV columns absent entirely (e.g. CSV-loaded chain) - feature not applicable, skip silently

    # Rule 8: Implied Move - the market's own forecast range for price by expiration.
    # (computed earlier, before Rule 2, so Rules 2-3 above could cite it)
    if implied_move is not None:
        breakdown.append({
            "Aspect": "Implied Move (to Expiration)",
            "Status": (
                f"±${implied_move['move_dollar']:,.2f} ({implied_move['move_pct']:.1%}) "
                f"→ ${implied_move['low']:,.2f} - ${implied_move['high']:,.2f}"
            ),
            "Logic": f"ATM IV ({implied_move['atm_iv']:.1%}) x sqrt(DTE/365), DTE = {ctx.dte} days",
            "Market Implication (MMs/Institutions vs Retail)": (
                "This is roughly the market's own 1-standard-deviation (~68% probability) forecast "
                "for where price lands by expiration, priced in by options buyers and sellers. A move "
                "beyond this range by expiration would be a bigger surprise than current option prices expect."
            )
        })
    elif ctx.dte is not None and ctx.dte <= 0:
        breakdown.append({
            "Aspect": "Implied Move (to Expiration)",
            "Status": f"N/A - {ctx.dte} DTE",
            "Logic": "This expiration has 0 (or negative) days to expiration - no remaining time premium to derive an implied move from.",
            "Market Implication (MMs/Institutions vs Retail)": "Not applicable for an expiration that has already settled or expires today."
        })
    elif ctx.dte is not None and ctx.dte > 0 and 'IV' in ctx.df.columns and 'IV.1' in ctx.df.columns:
        # DTE and IV columns are both available, so implied_move failed only because
        # ATM IV itself was missing, zero, or implausible - a data gap worth flagging,
        # not a feature that's simply inapplicable to this chain.
        breakdown.append({
            "Aspect": "Implied Move (to Expiration)",
            "Status": "N/A ⚠️ IV unreliable",
            "Logic": f"{ctx._atm_iv_na_reason()} Cannot estimate implied move.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable IV data."
        })
    # else: no expiration date supplied at all (e.g. CSV-loaded chain) - feature not
    # applicable, skip silently

    # Rule 9: Probability Range (1-SD / 2-SD) - extends Implied Move's 1-SD (68%)
    # band above with an approximate 2-SD (95%) band, by doubling the 1-SD move.
    probability_cone = ctx.calculate_probability_cone()
    if probability_cone is not None:
        breakdown.append({
            "Aspect": "Probability Range (1σ / 2σ)",
            "Status": (
                f"68%: ${probability_cone['low']:,.2f}-${probability_cone['high']:,.2f} | "
                f"95%: ${probability_cone['low_2sd']:,.2f}-${probability_cone['high_2sd']:,.2f}"
            ),
            "Logic": (
                "1σ band is the Implied Move above; 2σ band doubles it (Empirical Rule "
                "approximation for a roughly lognormal price distribution)."
            ),
            "Market Implication (MMs/Institutions vs Retail)": (
                "Roughly a 32% chance price finishes outside the narrower (1σ) band, and a ~5% chance "
                "outside the wider (2σ) band, by expiration. Useful context for strike selection when "
                "selling premium - strikes outside the 2σ band have an approximate 95% chance of "
                "expiring OTM under this model, though it ignores tail risk and vol skew."
            )
        })
    elif ctx.dte is not None and ctx.dte <= 0:
        breakdown.append({
            "Aspect": "Probability Range (1σ / 2σ)",
            "Status": f"N/A - {ctx.dte} DTE",
            "Logic": "This expiration has 0 (or negative) days to expiration - no remaining time premium to derive a probability range from.",
            "Market Implication (MMs/Institutions vs Retail)": "Not applicable for an expiration that has already settled or expires today."
        })
    elif ctx.dte is not None and ctx.dte > 0 and 'IV' in ctx.df.columns and 'IV.1' in ctx.df.columns:
        # Same data-gap distinction as Implied Move: DTE/IV columns exist, but ATM IV
        # itself came back missing, zero, or implausible.
        breakdown.append({
            "Aspect": "Probability Range (1σ / 2σ)",
            "Status": "N/A ⚠️ IV unreliable",
            "Logic": f"{ctx._atm_iv_na_reason()} Cannot estimate probability range.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without reliable IV data."
        })
    # else: no expiration date supplied at all (e.g. CSV-loaded chain) - feature not
    # applicable, skip silently

    # Rule 10: IV vs Realized Volatility - a substitute for IV Rank/Percentile, which
    # would need daily IV history yfinance doesn't provide (see get_realized_volatility).
    atm_iv = ctx._get_atm_iv()
    if atm_iv is not None and ctx.realized_vol is not None and ctx.realized_vol > 0:
        iv_rv_ratio = atm_iv / ctx.realized_vol

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
                f"ATM IV: {atm_iv:.1%} vs {yfi.REALIZED_VOL_LOOKBACK_DAYS}-Day Realized Vol: {ctx.realized_vol:.1%}. "
                "True IV Rank/Percentile needs daily IV history yfinance doesn't expose; this compares "
                "current IV to the stock's own recent actual volatility instead."
            ),
            "Market Implication (MMs/Institutions vs Retail)": mm_inst
        })
    elif 'IV' in ctx.df.columns and 'IV.1' in ctx.df.columns:
        # IV columns exist structurally, so the guard above failed because ATM IV or
        # Realized Vol came back missing, zero, or implausible - a data gap, not a
        # feature that's simply inapplicable to this chain.
        if atm_iv is None:
            reason = ctx._atm_iv_na_reason()
        else:
            reason = "Realized Volatility could not be calculated (e.g. insufficient price history)"
        breakdown.append({
            "Aspect": "IV vs Realized Vol (IVP Proxy)",
            "Status": "N/A ⚠️ Data unreliable",
            "Logic": f"{reason} - cannot compare IV to realized volatility.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without both reliable values."
        })
    # else: no IV columns at all (e.g. CSV-loaded chain) - feature not applicable, skip silently

    # Rule 11: Liquidity - average bid/ask spread (% of midpoint) across the displayed
    # table. Guarded: CSV-loaded chains (filepath= in main()) may not have Bid/Ask.
    has_bidask_cols = {'Bid', 'Ask', 'Bid.1', 'Ask.1'}.issubset(ctx.df.columns)
    avg_spread_pct = ctx.calculate_avg_spread_pct() if has_bidask_cols else None
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
    elif has_bidask_cols:
        breakdown.append({
            "Aspect": "Liquidity (Bid/Ask Spread)",
            "Status": "N/A ⚠️ Bid/Ask unavailable",
            "Logic": "No usable Bid/Ask quotes (non-zero on both sides) anywhere in the displayed chain - cannot compute spread.",
            "Market Implication (MMs/Institutions vs Retail)": "No conclusion can be drawn without quote data."
        })
    # else: Bid/Ask columns absent entirely (e.g. CSV-loaded chain) - feature not
    # applicable, skip silently

    # (A) Light synthesis: a final pass over the numeric ratios the Overall Balance,
    # OTM Skew, and IV Skew rows already computed above (not their rendered status
    # strings, which are free to reword later) to call out agreement or tension
    # between rules that measure different things (OI positioning vs. OTM positioning
    # vs. option pricing) and can legitimately point different directions without
    # actually disagreeing.
    pc_direction = None
    if pc_ratio is not None:
        pc_direction = "bullish" if pc_ratio < 0.8 else "bearish" if pc_ratio > 1.2 else "neutral"

    otm_direction = None
    if otm_ratio is not None:
        # otm_ratio is calls/puts (inverse of pc_ratio's puts/calls), so a high ratio
        # is the bullish-leaning end here.
        otm_direction = "bullish" if otm_ratio > 1.2 else "bearish" if otm_ratio < 0.8 else "neutral"

    iv_direction = None
    if iv_skew_ratio is not None:
        iv_direction = "put-rich" if iv_skew_ratio > 1.1 else "call-rich" if iv_skew_ratio < 0.9 else "flat"

    synthesis_notes = []

    if pc_direction is not None and otm_direction is not None and pc_direction != "neutral" and otm_direction != "neutral":
        if pc_direction == otm_direction:
            synthesis_notes.append(
                f"Overall Balance and OTM Skew both lean {pc_direction} - broad open interest and far-OTM "
                "positioning reinforce each other."
            )
        else:
            synthesis_notes.append(
                f"Overall Balance leans {pc_direction} while OTM Skew leans {otm_direction} - broad open "
                "interest and far-OTM positioning diverge here; read this as a mixed signal rather than a "
                "single confident direction."
            )

    if pc_direction == "bullish" and iv_direction == "put-rich":
        synthesis_notes.append(
            "Overall Balance shows call-heavy open interest while IV Skew shows puts priced richer than "
            "calls - not a contradiction: positioning (who holds what) and pricing (what tail risk costs) "
            "are different signals. Grinding higher on call interest while paying up for crash insurance is "
            "a common combination, not a sign these rules disagree."
        )

    if synthesis_notes:
        breakdown.append({
            "Aspect": "Cross-Rule Synthesis",
            "Status": f"{len(synthesis_notes)} cross-check note(s)",
            "Logic": "Compares the numeric ratios already computed by the Overall Balance, OTM Skew, and IV Skew rows above for directional agreement/tension.",
            "Market Implication (MMs/Institutions vs Retail)": " ".join(synthesis_notes)
        })

    return breakdown
