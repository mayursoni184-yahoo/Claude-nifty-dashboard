"""
fetch_research_data.py
------------------------
Run locally (needs internet): pip install yfinance pandas

    python fetch_research_data.py                       # NIFTY 500 universe (auto-downloaded list)
    python fetch_research_data.py --csv my_list.csv      # custom Symbol-column CSV
    python fetch_research_data.py --symbols RELIANCE,TCS  # explicit small list (fast, for testing)

Fetches 4 years of daily OHLCV plus market cap and core fundamental ratios
(ROE, ROA, debt/equity, margins, growth, P/E, P/B, dividend yield, sector)
for each symbol, then writes research_data.json. Load that file into
research-dashboard.html. (Use --period 8y or similar if you want deeper
history for more monthly/weekly pattern instances -- 4y is the sweet spot
for fetch speed vs. daily/weekly pattern sample size; monthly patterns will
have a noticeably smaller sample at 4y.)

ADDING GOLD & SILVER TO AN EXISTING DATA FILE (no full re-fetch):
    python fetch_research_data.py --metals-only
refreshes just the index/VIX and gold/silver series inside research_data.json in about a minute.

GOLD & SILVER TAB: the script also pulls GOLDBEES, SILVERBEES, COMEX gold/silver/copper
futures, GLD, SLV, the US dollar index, the US 10-year yield and USD/INR (see METALS_CONTEXT
near the top). Use --no-metals to skip them.

NOTE ON "ORDER BOOK": unexecuted order backlog (relevant mainly for
EPC/construction/capital-goods companies) isn't available through Yahoo
Finance or any free API -- it's disclosed in company investor presentations
and exchange filings, not a standard financial ratio. The dashboard flags
this explicitly rather than guessing; check it manually for infra/EPC names.

DAILY REFRESH WITHOUT TYPING A COMMAND EACH MORNING
----------------------------------------------------
The dashboard itself can't reach the internet (browser sandbox), so it can't
truly "auto refresh on open". The practical fix: schedule this script to run
once a day (e.g. after NSE market close, ~4pm IST) so research_data.json is
already fresh by the time you open the dashboard.

  Windows: Task Scheduler -> Create Basic Task -> Daily, 4:15 PM ->
           Action: Start a program -> "python" with arguments
           "C:\\path\\to\\fetch_research_data.py"

  Mac/Linux: crontab -e, then add a line like:
           15 16 * * 1-5 cd /path/to/folder && /usr/bin/python3 fetch_research_data.py

Then each day just open research-dashboard.html and hit "Load data file" --
it'll already be that morning's data.
"""

import argparse
import json
import sys
import time
import urllib.request
import csv
import io
import os
from datetime import datetime, timezone

NIFTY50 = ["RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","BHARTIARTL","ITC","SBIN","LT","HINDUNILVR",
    "KOTAKBANK","BAJFINANCE","AXISBANK","ASIANPAINT","MARUTI","SUNPHARMA","TITAN","ULTRACEMCO","NESTLEIND","WIPRO",
    "ADANIENT","ADANIPORTS","ONGC","NTPC","POWERGRID","M&M","TATASTEEL","TATAMOTORS","JSWSTEEL","HCLTECH",
    "BAJAJFINSV","TECHM","INDUSINDBK","GRASIM","DRREDDY","CIPLA","EICHERMOT","BRITANNIA","DIVISLAB","COALINDIA",
    "HEROMOTOCO","BPCL","SBILIFE","HDFCLIFE","APOLLOHOSP","BAJAJ-AUTO","TATACONSUM","UPL","HINDALCO","LTIM"]

NSE_500_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]


# Gold & silver context for the dashboard's "Gold & Silver" tab. Stored under underscore-prefixed
# keys so they are never mixed into the stock universe. Edit tickers here if you hold different ETFs.
METALS_CONTEXT = {
    "_GOLDBEES":    "GOLDBEES.NS",   # Nippon India ETF Gold BeES (your holding)
    "_SILVERBEES":  "SILVERBEES.NS", # Nippon India Silver ETF (your holding)
    "_GOLD_FUT":    "GC=F",          # COMEX gold futures (global trend)
    "_SILVER_FUT":  "SI=F",          # COMEX silver futures
    "_GLD":         "GLD",           # SPDR Gold Shares (global ETF trading interest)
    "_SLV":         "SLV",           # iShares Silver Trust
    "_COPPER_FUT":  "HG=F",          # copper futures (industrial-demand proxy for silver)
    "_DXY":         "DX-Y.NYB",      # US dollar index
    "_US10Y":       "^TNX",          # US 10-year Treasury yield
    "_USDINR":      "USDINR=X",      # rupee
}


def try_download_nifty500():
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in NSE_500_URLS:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
                reader = csv.DictReader(io.StringIO(text))
                symbols = [row["Symbol"].strip() for row in reader if row.get("Symbol")]
                if symbols:
                    return symbols
        except Exception as e:
            print(f"  (auto-download from {url} failed: {e})")
    return None


def load_symbols_from_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        col = "Symbol" if "Symbol" in reader.fieldnames else reader.fieldnames[2]
        return [row[col].strip() for row in reader if row.get(col)]


def get_universe(args):
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.csv:
        print(f"Loading symbol list from {args.csv} ...")
        return load_symbols_from_csv(args.csv)
    print("Attempting to auto-download NIFTY 500 constituent list from NSE ...")
    symbols = try_download_nifty500()
    if not symbols:
        print("Could not auto-download the list (NSE often blocks scripted requests).")
        print("Falling back to the hardcoded NIFTY 50 list. For the full NIFTY 500,")
        print("download ind_nifty500list.csv manually from niftyindices.com and re-run")
        print("with --csv ind_nifty500list.csv")
        return NIFTY50
    print(f"Loaded {len(symbols)} NIFTY 500 symbols.")
    return symbols


def _iso_date(ts):
    """epoch seconds -> 'YYYY-MM-DD' (UTC), or None"""
    try:
        if ts is None:
            return None
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def extract_events(info):
    """Upcoming results / ex-dividend dates as Yahoo reports them.
    Coverage for Indian stocks is patchy and sometimes estimated or stale, so the
    dashboard treats these as a heads-up, not a guarantee."""
    earnings = sorted({d for d in (
        _iso_date(info.get("earningsTimestamp")),
        _iso_date(info.get("earningsTimestampStart")),
        _iso_date(info.get("earningsTimestampEnd")),
    ) if d})
    return {
        "earnings": earnings,
        "exDividend": _iso_date(info.get("exDividendDate")),
    }


LAST_ERROR = {}   # ticker -> short reason for the most recent failure (recorded in the output file)


def fetch_one(yf, symbol, period="4y", retries=3):
    return fetch_ticker(yf, f"{symbol}.NS", period, retries, want_fundamentals=True)


def fetch_ticker(yf, ticker_str, period="4y", retries=3, want_fundamentals=False, retry_empty=False):
    ticker = yf.Ticker(ticker_str)
    for attempt in range(retries):
        try:
            df = ticker.history(period=period, interval="1d")
            if df.empty:
                LAST_ERROR[ticker_str] = "Yahoo returned no data"
                if retry_empty and attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            mcap = None
            fundamentals = {}
            events = {}
            if want_fundamentals:
                try:
                    info = ticker.get_info()
                    mcap = info.get("marketCap")
                    fundamentals = {
                        "trailingPE": info.get("trailingPE"),
                        "priceToBook": info.get("priceToBook"),
                        "returnOnEquity": info.get("returnOnEquity"),
                        "returnOnAssets": info.get("returnOnAssets"),
                        "debtToEquity": info.get("debtToEquity"),
                        "revenueGrowth": info.get("revenueGrowth"),
                        "earningsGrowth": info.get("earningsGrowth"),
                        "profitMargins": info.get("profitMargins"),
                        "operatingMargins": info.get("operatingMargins"),
                        "dividendYield": info.get("dividendYield"),
                        "currentRatio": info.get("currentRatio"),
                        "sector": info.get("sector"),
                        "industry": info.get("industry"),
                        "beta": info.get("beta"),
                    }
                    events = extract_events(info)
                except Exception:
                    pass
            return {
                "dates": [d.strftime("%Y-%m-%d") for d in df.index],
                "open": [round(float(v), 2) for v in df["Open"]],
                "high": [round(float(v), 2) for v in df["High"]],
                "low": [round(float(v), 2) for v in df["Low"]],
                "close": [round(float(v), 2) for v in df["Close"]],
                "volume": [int(v) for v in df["Volume"]],
                "marketCapCr": round(mcap / 1e7, 1) if mcap else None,  # convert to INR crore
                "fundamentals": fundamentals,
                "events": events,
            }
        except Exception as e:
            LAST_ERROR[ticker_str] = str(e)[:120]
            if attempt == retries - 1:
                print(f"  x {ticker_str}: {e}")
                return None
            time.sleep(2 * (attempt + 1))


def fetch_context(yf, args, data):
    """NIFTY index, India VIX and (unless --no-metals) the gold/silver series.
    Fetched BEFORE the long stock loop so a throttled Yahoo can't silently drop them, with extra
    retries; returns a per-ticker status record that is saved in the output file so the dashboard
    can say exactly what worked."""
    jobs = [("_NIFTY_INDEX", "^NSEI", "market"), ("_INDIA_VIX", "^INDIAVIX", "market")]
    if not args.no_metals:
        jobs += [(k, t, "gold/silver") for k, t in METALS_CONTEXT.items()]
    status = {}
    for key, tkr, kind in jobs:
        print(f"Fetching {tkr} ({kind} context) ...", end=" ", flush=True)
        res = fetch_ticker(yf, tkr, period=args.period, retries=5, retry_empty=True)
        if res:
            data[key] = res
            status[key] = {"ticker": tkr, "ok": True, "bars": len(res["dates"])}
            print(f"ok ({len(res['dates'])} sessions)")
        else:
            status[key] = {"ticker": tkr, "ok": False, "bars": 0, "error": LAST_ERROR.get(tkr, "unknown error")}
            print(f"FAILED ({LAST_ERROR.get(tkr, 'unknown error')})")
        time.sleep(0.3)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", help="comma separated custom list")
    parser.add_argument("--csv", help="path to a Symbol-column CSV")
    parser.add_argument("--out", default="research_data.json")
    parser.add_argument("--period", default="4y")
    parser.add_argument("--no-metals", action="store_true", help="skip the gold/silver context series")
    parser.add_argument("--metals-only", action="store_true",
                        help="refresh ONLY the index/VIX and gold/silver series inside an existing data file (about a minute; no full re-fetch)")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        print("Missing dependency. Run: pip install yfinance pandas")
        sys.exit(1)

    if args.metals_only:
        if not os.path.exists(args.out):
            print(f"{args.out} not found. Run the full fetch once first (python fetch_research_data.py), or run --metals-only from the folder that contains it.")
            sys.exit(1)
        with open(args.out) as f:
            payload = json.load(f)
        data = payload.get("data", payload)
        args.no_metals = False
        status = fetch_context(yf, args, data)
        payload = {"generated": payload.get("generated", time.strftime("%Y-%m-%d %H:%M:%S")), "data": data,
                   "context_status": status, "metals_refreshed": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(args.out, "w") as f:
            json.dump(payload, f)
        bad = [v["ticker"] for v in status.values() if not v["ok"]]
        print(f"\nUpdated {args.out}: {len(status)-len(bad)}/{len(status)} context series OK, stock data left untouched.")
        if bad:
            print("Failed: " + ", ".join(bad) + "  (see the reasons above; rerun in a few minutes if it looks like throttling)")
        return

    symbols = get_universe(args)

    data = {}
    context_status = fetch_context(yf, args, data)

    print(f"Fetching {len(symbols)} symbols at period={args.period} (includes market cap lookup, slower) ...")
    failed = []
    for i, sym in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {sym} ...", end=" ", flush=True)
        result = fetch_one(yf, sym, period=args.period)
        if result:
            data[sym] = result
            mc = result.get("marketCapCr")
            print(f"ok ({len(result['dates'])} sessions, mcap={'₹'+format(mc,',')+' Cr' if mc else 'n/a'})")
        else:
            failed.append(sym)
            print("failed")
        time.sleep(0.3)

    with open(args.out, "w") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "data": data, "context_status": context_status}, f)

    n_stocks = len([k for k in data if not k.startswith("_")])
    print(f"\nSaved {n_stocks}/{len(symbols)} stocks (+ {len(data)-n_stocks} context series) to {args.out}")
    bad_ctx = [v["ticker"] for v in context_status.values() if not v["ok"]]
    if bad_ctx:
        print("Context series that FAILED (the Gold & Silver tab will be incomplete): " + ", ".join(bad_ctx))
        print("  -> run  python fetch_research_data.py --metals-only  in a few minutes to retry just those.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")
    print("Now open research-dashboard.html and use 'Load data file' to select this JSON file.")


if __name__ == "__main__":
    main()
