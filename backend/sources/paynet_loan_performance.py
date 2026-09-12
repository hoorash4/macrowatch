"""Direct PayNet Risk Insight Suite collector for national 31-180 delinquency and default rates.

This module intentionally does not derive 31-180 from the 31-90 and 91-180 buckets.
It logs into the free PayNet Risk Insight Suite, selects the published 31-180 SBDI
and SBDFI series directly, and reads the chart's own data points.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import os
import re
from typing import Any

SERIES_DELINQUENCY = "US_SBDI_31_180"
SERIES_DEFAULT = "US_SBDFI"
LOGIN_URL = "https://sbinsights.paynetonline.com/loan-performance/"


def _row(code: str, observed: date, value: float, source: str) -> dict:
    return {
        "series_code": code,
        "observation_date": observed.isoformat(),
        "value": float(value),
        "frequency": "M",
        "source": source,
    }


def _month_start_from_ms(value: int | float) -> date:
    dt = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    return date(dt.year, dt.month, 1)


def rows_from_highcharts_payload(payload: list[dict[str, Any]], start: date, end: date) -> dict[str, list[dict]]:
    """Convert direct PayNet Highcharts series into the two stored MacroWatch series."""
    first = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    result = {SERIES_DELINQUENCY: [], SERIES_DEFAULT: []}

    for series in payload:
        name = re.sub(r"\s+", " ", str(series.get("name") or "")).strip()
        normalized = name.lower().replace("–", "-").replace("—", "-")
        code = None
        if "31-180" in normalized and ("sbdi" in normalized or "delin" in normalized):
            code = SERIES_DELINQUENCY
        elif "sbdfi" in normalized or "default" in normalized:
            code = SERIES_DEFAULT
        if code is None:
            continue

        for point in series.get("data") or []:
            if isinstance(point, dict):
                x, y = point.get("x"), point.get("y")
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                x, y = point[0], point[1]
            else:
                continue
            try:
                observed = _month_start_from_ms(x)
                value = float(y)
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            if not (first <= observed <= last) or not (0 <= value < 20):
                continue
            result[code].append(_row(code, observed, value, f"PayNet-RIS:{name}"))

    for code in result:
        by_date: dict[str, dict] = {}
        for row in result[code]:
            observed = row["observation_date"]
            previous = by_date.get(observed)
            if previous is not None and abs(float(previous["value"]) - float(row["value"])) > 1e-12:
                raise RuntimeError(f"{code}: conflicting PayNet values for {observed}")
            by_date[observed] = row
        result[code] = [by_date[key] for key in sorted(by_date, reverse=True)]
    return result


def _credentials() -> tuple[str, str]:
    username = os.getenv("PAYNET_USERNAME", "").strip()
    password = os.getenv("PAYNET_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("PAYNET_USERNAME/PAYNET_PASSWORD are required for direct PayNet historical data")
    return username, password


def fetch_paynet_rows(start: date, end: date) -> dict[str, list[dict]]:
    """Fetch national 31-180 SBDI and SBDFI directly from PayNet's authenticated chart."""
    username, password = _credentials()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("playwright is required for PayNet collection") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        response = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"PayNet page returned HTTP {response.status}")

        # The site presents login controls on the loan-performance page itself.
        username_box = page.locator('input[type="text"], input[type="email"]').first
        password_box = page.locator('input[type="password"]').first
        username_box.fill(username)
        password_box.fill(password)
        page.get_by_role("button", name=re.compile(r"^login$", re.I)).first.click()
        page.wait_for_load_state("networkidle", timeout=120_000)

        body_text = page.locator("body").inner_text()
        if re.search(r"please login or register to view details", body_text, re.I):
            raise RuntimeError("PayNet login did not unlock historical loan-performance data")

        # Select the published 31-180 delinquency measure directly. Never sum split buckets.
        clicked = False
        for locator in (
            page.get_by_text("31-180 days", exact=True),
            page.locator("label").filter(has_text=re.compile(r"31\s*-\s*180\s*days", re.I)),
        ):
            try:
                if locator.count():
                    locator.first.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("PayNet 31-180 Days selector not found")

        # Enable the published default index on the same chart.
        default_clicked = False
        for locator in (
            page.get_by_text("Annualized Default Index", exact=True),
            page.locator("label").filter(has_text=re.compile(r"Annualized Default Index", re.I)),
        ):
            try:
                if locator.count():
                    locator.first.click()
                    default_clicked = True
                    break
            except Exception:
                continue
        if not default_clicked:
            raise RuntimeError("PayNet SBDFI selector not found")

        page.get_by_role("button", name=re.compile(r"^apply$", re.I)).first.click()
        page.wait_for_load_state("networkidle", timeout=120_000)
        page.wait_for_timeout(1500)

        payload = page.evaluate(
            """
            () => {
              if (!window.Highcharts || !Array.isArray(window.Highcharts.charts)) return [];
              const charts = window.Highcharts.charts.filter(Boolean);
              const out = [];
              for (const chart of charts) {
                for (const s of (chart.series || [])) {
                  if (!s || s.visible === false) continue;
                  out.push({
                    name: String(s.name || ''),
                    data: (s.points || []).map(p => [p.x, p.y]),
                  });
                }
              }
              return out;
            }
            """
        )
        browser.close()

    result = rows_from_highcharts_payload(payload, start, end)
    if not result[SERIES_DELINQUENCY] or not result[SERIES_DEFAULT]:
        names = [str(item.get("name") or "") for item in payload]
        raise RuntimeError(f"PayNet chart did not expose both required direct series; found={names}")
    return result
