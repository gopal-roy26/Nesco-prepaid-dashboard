"""
NESCO প্রিপেইড ড্যাশবোর্ড — ব্যাকএন্ড (FastAPI)
"""

import os  # <-- নতুন ইম্পোর্ট করা হয়েছে
import re
import subprocess
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NESCO Prepaid Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_TTL_SECONDS = 120
_cache: dict[str, tuple[float, dict]] = {}

BALANCE_DELAY_NOTE = (
    "এই ব্যালেন্স NESCO সার্ভারের সর্বশেষ সিঙ্কের সময়কার হিসাব — "
    "মিটারের গায়ে দেখানো তাৎক্ষণিক রিডিং থেকে সামান্য ভিন্ন হতে পারে।"
)


def run_nesco_cli(command: str, customer_number: str) -> str:
    """nesco-cli কে subprocess হিসেবে চালিয়ে raw টেক্সট আউটপুট ফেরত দেয়।"""
    try:
        # উইন্ডোজে ইমোজি সাপোর্ট করানোর জন্য এনভায়রনমেন্ট সেট করা হলো
        my_env = os.environ.copy()
        my_env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            ["nesco-cli", command, "-c", customer_number],
            capture_output=True,
            text=True,
            encoding="utf-8", 
            env=my_env,  # <-- এটি যুক্ত করা হয়েছে
            timeout=25,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="nesco-cli পাওয়া যায়নি। 'pip install nesco' করে আবার চেষ্টা করুন।",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="NESCO সার্ভার থেকে সময়মতো উত্তর আসেনি।")

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "অজানা সমস্যা").strip()
        raise HTTPException(status_code=502, detail=f"nesco-cli ব্যর্থ হয়েছে: {message}")

    return result.stdout.strip()


def extract_balance(raw: str) -> Optional[float]:
    if "no balance data" in raw.lower():
        return None

    match = re.search(r"balance[:\s]*([\d,]+\.?\d*)", raw, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", ""))

    try:
        return float(raw.strip().replace(",", ""))
    except ValueError:
        return None


def parse_tabulate(output: str) -> list[dict]:
    lines = [line for line in output.split("\n") if line.strip()]

    sep_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and set(stripped) <= {"-", " "}:
            sep_idx = i
            break

    if sep_idx is None or sep_idx == 0:
        return []

    header_line, sep_line = lines[sep_idx - 1], lines[sep_idx]
    spans = [(m.start(), m.end()) for m in re.finditer(r"-+", sep_line)]
    if not spans:
        return []

    def slice_row(line: str) -> list[str]:
        values = []
        for i, (start, _end) in enumerate(spans):
            stop = spans[i + 1][0] if i + 1 < len(spans) else len(line)
            values.append(line[start:stop].strip())
        return values

    headers = slice_row(header_line)
    rows = []
    for line in lines[sep_idx + 1:]:
        rows.append(dict(zip(headers, slice_row(line))))
    return rows


def get_cached_or_fetch(customer_number: str) -> dict:
    now = time.time()
    cached = _cache.get(customer_number)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    balance_raw = run_nesco_cli("get-balance", customer_number)
    balance = extract_balance(balance_raw)

    customer_info_rows = parse_tabulate(run_nesco_cli("get-customer-info", customer_number))
    customer_info = customer_info_rows[0] if customer_info_rows else {}

    recharge_history = parse_tabulate(run_nesco_cli("get-recharge-history", customer_number))
    monthly_consumption = parse_tabulate(run_nesco_cli("get-monthly-consumption", customer_number))

    data = {
        "customer_number": customer_number,
        "balance": balance,
        "note": BALANCE_DELAY_NOTE if balance is not None else "কোনো ব্যালেন্স ডেটা পাওয়া যায়নি।",
        "customer_info": customer_info,
        "recharge_history": recharge_history,
        "monthly_consumption": monthly_consumption,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache[customer_number] = (now, data)
    return data


@app.get("/api/dashboard/{customer_number}")
def get_dashboard(customer_number: str):
    if not customer_number.isdigit():
        raise HTTPException(status_code=400, detail="কনজিউমার/মিটার নম্বর শুধু সংখ্যা হতে হবে।")
    return get_cached_or_fetch(customer_number)


@app.get("/api/balance/{customer_number}")
def get_balance_only(customer_number: str):
    if not customer_number.isdigit():
        raise HTTPException(status_code=400, detail="কনজিউমার/মিটার নম্বর শুধু সংখ্যা হতে হবে।")
    balance_raw = run_nesco_cli("get-balance", customer_number)
    balance = extract_balance(balance_raw)
    return {"customer_number": customer_number, "balance": balance, "note": BALANCE_DELAY_NOTE}


@app.get("/health")
def health():
    return {"status": "ok"}