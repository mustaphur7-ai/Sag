# Gold GEX Levels Terminal

Static site that shows gold (XAUUSD) options-derived levels (gamma flip, call/put walls, top GEX strikes, IV skew) with a hold/violate lean per level.

**Levels are computed once per day** by a GitHub Action at Asian open + 1 hour (default 01:00 UTC, Mon-Fri), saved to `site/data/levels.json`, and stay frozen until the next run. The browser only reads that file and updates the live price line.

## Setup
1. Create a GitHub repo and push these files to `main`.
2. Settings > Pages > Source: **GitHub Actions**.
3. Actions tab > "Daily levels and deploy" > **Run workflow** (first run builds real levels).
4. Open the Pages URL on your phone and add it to the home screen.

## Change the run time
Edit the cron in `.github/workflows/daily.yml` (UTC) and `asian_open_utc` / `analysis_delay_minutes` in `config.json` (used for the countdown). Tokyo open = 00:00 UTC, Sydney open = 22:00 UTC.

## Data (all free, no keys)
- Options: CBOE delayed quotes for GLD (strikes, OI, IV, delta, gamma).
- Candles and spot: Yahoo Finance chart API (XAUUSD=X, GLD).
- Live price in the browser: Binance public PAXGUSDT, shifted by the frozen offset to XAUUSD.

## Limits
- GLD options are a proxy for gold options; strikes are converted with the GLD/XAUUSD ratio taken at the same moment (GLD last candle).
- Dealer sign is assumed (calls long gamma, puts short gamma) from open interest, not measured from the tape.
- The hold/violate lean is rule-based (gamma regime, IV skew, GEX size, recent reactions at the level). See `judge()` in `scripts/build_levels.py`. It is not a guarantee.
- Yahoo and CBOE are unofficial endpoints and can change. On failure the script keeps the previous levels and shows a warning.
