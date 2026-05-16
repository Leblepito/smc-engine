# Prompt: Liquidity Sweep / SFP (equal high-low + reclaim + alert)

> Bu prompt'u Claude Code'a yapistir. Onkosul: `tradingview-mcp` kurulu, TradingView Desktop debug-modda (`--remote-debugging-port=9222`) ve `tv_health_check` cdp_connected=true donmus olmali.

---

Build me a Pine Script v6 indicator called "SMC Liquidity Sweep (EQH/EQL + SFP)".

It should:

1. Detect equal highs (EQH) and equal lows (EQL): two or more recent swing extremes within `equal_level_tolerance` (default `0.001` = 0.1%) of each other.
2. Detect a **sweep / SFP (Swing Failure Pattern)**: price wicks above an EQH (or below an EQL) **and then closes back inside** the prior range — i.e. the wick takes out liquidity but the candle closes as a rejection.
3. Detect **reclaim**: the next bar's close confirming the rejection direction.
4. Mark each sweep with an arrow at the wick + a label "SWEEP" or "SFP".
5. Highlight the swept liquidity level with a horizontal dashed line that fades after the sweep.
6. Fire an alert via `alert()` (use M15 chart as default) whenever a sweep+reclaim is confirmed — message must include direction (BUY-side / SELL-side liquidity), level price, and timestamp.
7. Expose inputs: `equal_level_tolerance` (float, default 0.001), swing lookback, alert toggle.

Use Pine Script v6 syntax only. Use `ta.*`, not bare names. Use `indicator()`, not `study()`. Use the `alert()` function (not `alertcondition()`). Ensure it compiles with zero errors.
