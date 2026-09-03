import os
import re

import requests

BASE = "https://openapi.koreainvestment.com:9443"
app_key = os.environ["KIS_APP_KEY"]
app_secret = os.environ["KIS_APP_SECRET"]
token_response = requests.post(
    f"{BASE}/oauth2/tokenP",
    json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
    timeout=20,
)
token_response.raise_for_status()
token = token_response.json()["access_token"]

for ticker in ("005930", "032830"):
    continuation = ""
    all_periods = []
    for page in range(1, 11):
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST66430200",
            "custtype": "P",
        }
        if continuation:
            headers["tr_cont"] = continuation
        response = requests.get(
            f"{BASE}/uapi/domestic-stock/v1/finance/income-statement",
            params={
                "FID_DIV_CLS_CODE": "1",
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": ticker,
            },
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("rt_cd", "0")) != "0":
            raise RuntimeError(f"{ticker} rejected: {payload.get('msg_cd', 'unknown')}")
        rows = payload.get("output", [])
        if page == 1 and rows:
            date_keys = sorted(key for key in rows[0] if "yymm" in key.lower() or "date" in key.lower())
            print(f"TICKER={ticker} DATE_KEYS={','.join(date_keys)}", flush=True)
        periods = []
        for row in rows:
            raw = str(row.get("stac_yymm") or "")
            period = re.sub(r"[^0-9]", "", raw)
            if len(period) == 6:
                periods.append(period)
        all_periods.extend(periods)
        next_flag = str(response.headers.get("tr_cont") or "").upper()
        print(f"TICKER={ticker} PAGE={page} ROWS={len(rows)} TR_CONT={next_flag or '-'} PERIODS={','.join(periods)}", flush=True)
        if next_flag != "M":
            break
        continuation = "N"
    unique = list(dict.fromkeys(all_periods))
    print(f"TICKER={ticker} TOTAL_ROWS={len(all_periods)} UNIQUE_PERIODS={len(unique)} PERIODS={','.join(unique)}", flush=True)
