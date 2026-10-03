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

NIFTY50 = ["RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","BHARTIARTL","ITC","SBIN","LT","HINDUNILVR",
    "KOTAKBANK","BAJFINANCE","AXISBANK","ASIANPAINT","MARUTI","SUNPHARMA","TITAN","ULTRACEMCO","NESTLEIND","WIPRO",
    "ADANIENT","ADANIPORTS","ONGC","NTPC","POWERGRID","M&M","TATASTEEL","TATAMOTORS","JSWSTEEL","HCLTECH",
    "BAJAJFINSV","TECHM","INDUSINDBK","GRASIM","DRREDDY","CIPLA","EICHERMOT","BRITANNIA","DIVISLAB","COALINDIA",
    "HEROMOTOCO","BPCL","SBILIFE","HDFCLIFE","APOLLOHOSP","BAJAJ-AUTO","TATACONSUM","UPL","HINDALCO","LTIM"]

NSE_500_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]


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


def fetch_one(yf, symbol, period="4y", retries=3):
    return fetch_ticker(yf, f"{symbol}.NS", period, retries, want_fundamentals=True)


def fetch_ticker(yf, ticker_str, period="4y", retries=3, want_fundamentals=False):
    ticker = yf.Ticker(ticker_str)
    for attempt in range(retries):
        try:
            df = ticker.history(period=period, interval="1d")
            if df.empty:
                return None
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            mcap = None
            fundamentals = {}
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
            }
        except Exception as e:
            if attempt == retries - 1:
                print(f"  x {symbol}: {e}")
                return None
            time.sleep(2 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", help="comma separated custom list")
    parser.add_argument("--csv", help="path to a Symbol-column CSV")
    parser.add_argument("--out", default="research_data.json")
    parser.add_argument("--period", default="4y")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        print("Missing dependency. Run: pip install yfinance pandas")
        sys.exit(1)

    symbols = get_universe(args)
    print(f"Fetching {len(symbols)} symbols at period={args.period} (includes market cap lookup, slower) ...")

    data = {}
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

    print("Fetching NIFTY 50 index (market context) ...", end=" ", flush=True)
    nifty_idx = fetch_ticker(yf, "^NSEI", period=args.period)
    print("ok" if nifty_idx else "failed")

    print("Fetching India VIX (market context) ...", end=" ", flush=True)
    india_vix = fetch_ticker(yf, "^INDIAVIX", period=args.period)
    print("ok" if india_vix else "failed")

    if nifty_idx: data["_NIFTY_INDEX"] = nifty_idx
    if india_vix: data["_INDIA_VIX"] = india_vix

    with open(args.out, "w") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "data": data}, f)

    print(f"\nSaved {len(data)}/{len(symbols)} symbols to {args.out}")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")
    print("Now open research-dashboard.html and use 'Load data file' to select this JSON file.")


if __name__ == "__main__":
    main()
