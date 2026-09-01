from __future__ import annotations

import json
import os

from .ecos import EcosFxClient
from .korea_pipeline import KoreaEarningsPipeline
from .kis_financials import KisTopLineClient
from .krx import KrxOpenApiClient
from .open_dart import OpenDartV2Client
from .pilot import build_recent_four_quarter_pilot
from .repository import EarningsV2Store


def main() -> int:
    plan = build_recent_four_quarter_pilot(
        end_year=2026,
        end_quarter=2,
        markets=("kr_largecap", "kr_kosdaq"),
    )
    pipeline = KoreaEarningsPipeline(
        krx=KrxOpenApiClient.from_env(),
        dart=OpenDartV2Client.from_env(),
        fx=EcosFxClient.from_env(),
        store=EarningsV2Store(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            timeout=120,
        ),
        kis_top_lines=KisTopLineClient(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            timeout=120,
        ),
    )
    summary = pipeline.run(
        plan.quarters,
        source="korea_pilot",
        operation="2025q3_2026q2",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
