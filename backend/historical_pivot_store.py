"""Persistence adapter for finalized Historical Insight pivot results.

This module is intentionally outside the Stage 1~6 generation pipeline.
It observes already-produced stage results, serializes their final structure and
selection evidence, and atomically replaces one case/index/series snapshot in
Supabase.  It must not alter or re-run pivot selection rules.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any, Iterable, Sequence

from common import SupabaseRest
from historical_pivot_shared import PivotPoint, SimplifiedLineResult


def _key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, float(point.value), point.pivot_type


def _keys(points: Iterable[PivotPoint]) -> set[tuple[date, float, str]]:
    return {_key(point) for point in points}


def _stage_markers(stage: Any) -> tuple[PivotPoint, ...]:
    markers = getattr(stage, "markers", None)
    if markers is not None:
        return tuple(markers)
    display = getattr(stage, "display_markers", None)
    if display is not None:
        return tuple(display)
    highs = tuple(getattr(stage, "high_pivots", ()) or ())
    lows = tuple(getattr(stage, "low_pivots", ()) or ())
    return tuple((*highs, *lows))


def _technical_reason_text(
    point: PivotPoint,
    *,
    base_rdp: bool,
    spike_peak: bool,
    spike_entry: bool,
    marker_only: bool,
    sideways_boundaries: Sequence[dict[str, Any]],
    stage3_vertex: bool,
    stage4_survivor: bool,
    standalone: bool,
) -> str:
    suffix = " Stage 5·6 삭제 조건을 통과해 최종 유지되었습니다." if stage4_survivor else " 최종 결과에 유지되었습니다."

    if marker_only:
        return (
            "Stage 2에서 spike peak로 확정되었고 반대편 sideways 내부의 marker-only spike로 보호되어 "
            "선 연결 없이 최종 유지되었습니다."
        )
    if spike_peak:
        return "Stage 2에서 spike peak로 확정되어 보호 구조의 꼭짓점으로 유지되었고," + suffix
    if spike_entry:
        return "Stage 2에서 spike entry로 확정되어 spike 보호 구조의 진입점으로 유지되었고," + suffix
    if sideways_boundaries:
        sides = "/".join(sorted({str(item["boundary"]) for item in sideways_boundaries}))
        return f"Stage 2에서 sideways 보호 구간의 {sides} 경계점으로 보호되었고," + suffix
    if base_rdp and stage3_vertex:
        return (
            f"Stage 1 RDP {point.pivot_type}로 선정된 뒤 Stage 3 단일 wave line의 꼭짓점으로 채택되었고,"
            + suffix
        )
    if base_rdp:
        return f"Stage 1 RDP {point.pivot_type}로 선정되었고," + suffix
    if stage3_vertex:
        return "Stage 3 단일 wave line의 꼭짓점으로 채택되었고," + suffix
    if standalone:
        return "최종 선 구조에 연결되지 않는 독립 보호 마커로 유지되었습니다."
    return "기존 Stage 결과에서 최종 선 구조의 꼭짓점으로 남아 Stage 6까지 유지되었습니다."


def _previous_same_side(
    ordered: Sequence[PivotPoint],
    index: int,
) -> PivotPoint | None:
    point = ordered[index]
    for candidate in reversed(ordered[:index]):
        if candidate.pivot_type == point.pivot_type:
            return candidate
    return None


def _next_same_side(
    ordered: Sequence[PivotPoint],
    index: int,
) -> PivotPoint | None:
    point = ordered[index]
    for candidate in ordered[index + 1:]:
        if candidate.pivot_type == point.pivot_type:
            return candidate
    return None


def _direction(left: PivotPoint | None, right: PivotPoint | None) -> int:
    if left is None or right is None:
        return 0
    if right.value > left.value:
        return 1
    if right.value < left.value:
        return -1
    return 0


def _format_value(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text != "-0" else "0"


def _interpretive_reason(
    ordered: Sequence[PivotPoint],
    index: int,
    *,
    spike: dict[str, Any] | None,
    sideways_boundaries: Sequence[dict[str, Any]],
    standalone: bool,
) -> tuple[str, str]:
    """Describe only the already-selected final structure in plain language.

    This function never changes pivot selection. It interprets final Stage-6
    vertices and existing protection metadata using value/direction relations
    that are already present in the stored result.
    """
    point = ordered[index]
    previous = ordered[index - 1] if index > 0 else None
    following = ordered[index + 1] if index + 1 < len(ordered) else None
    previous_same = _previous_same_side(ordered, index)
    next_same = _next_same_side(ordered, index)
    incoming = _direction(previous, point)
    outgoing = _direction(point, following)
    value = _format_value(point.value)

    if spike and spike.get("marker_only"):
        return (
            "isolated_spike",
            f"{value}까지 짧고 급격한 변동이 나타났지만 주 추세선과는 분리된 특이 피봇으로 남았습니다.",
        )

    if spike and spike.get("role") == "peak":
        direction = str(spike.get("direction") or "")
        word = "상방" if direction == "up" else "하방" if direction == "down" else ""
        return (
            "protected_spike_peak",
            f"{word}으로 급격한 움직임이 집중되며 주변 흐름과 뚜렷이 구별되는 변곡점이 형성됐습니다.",
        )

    if sideways_boundaries:
        boundaries = {str(item.get("boundary") or "") for item in sideways_boundaries}
        prior_trends = {str(item.get("prior_trend") or "") for item in sideways_boundaries}
        if "start" in boundaries and "end" not in boundaries:
            prior = "상승" if "up" in prior_trends else "하락" if "down" in prior_trends else "기존"
            return (
                "sideways_entry",
                f"{prior} 흐름의 극값 갱신이 둔화되고 기준선이 평탄해지면서 횡보 구간으로 진입한 지점입니다.",
            )
        if "end" in boundaries and "start" not in boundaries:
            if following is not None:
                next_word = "상승" if outgoing > 0 else "하락" if outgoing < 0 else "새로운"
                return (
                    "sideways_exit",
                    f"횡보 구간이 끝난 뒤 {next_word} 방향의 움직임이 다시 시작되는 경계점입니다.",
                )
            return (
                "sideways_boundary_end",
                "방향성이 약했던 횡보 구간의 마지막 경계점으로 남았습니다.",
            )
        return (
            "sideways_boundary",
            "비슷한 수준의 극값이 반복되며 방향성이 약해진 횡보 구조의 핵심 경계점입니다.",
        )

    if standalone:
        return (
            "standalone_protected_pivot",
            "주 추세선과 직접 연결되지는 않지만 구조적으로 독립적인 변동점으로 보호되어 남았습니다.",
        )

    if previous is None and following is not None:
        word = "상승" if outgoing > 0 else "하락" if outgoing < 0 else "다음"
        return (
            "structure_start",
            f"최종 피봇 구조가 시작되는 기준점으로, 이후 {word} 흐름의 출발점이 됐습니다.",
        )

    if following is None:
        if previous_same is not None:
            if point.pivot_type == "low" and point.value < previous_same.value:
                return (
                    "final_lower_low",
                    f"직전 주요 저점 {_format_value(previous_same.value)}보다 더 낮은 {value}의 저점을 만들며 하락 흐름의 최종 극값을 형성했습니다.",
                )
            if point.pivot_type == "low" and point.value > previous_same.value:
                return (
                    "final_higher_low",
                    f"직전 주요 저점 {_format_value(previous_same.value)}보다 높은 {value}에서 저점이 형성돼 하락 압력이 약해진 상태로 끝났습니다.",
                )
            if point.pivot_type == "high" and point.value > previous_same.value:
                return (
                    "final_higher_high",
                    f"직전 주요 고점 {_format_value(previous_same.value)}을 넘어 {value}의 새 고점을 만들며 상승 흐름의 최종 극값을 형성했습니다.",
                )
            if point.pivot_type == "high" and point.value < previous_same.value:
                return (
                    "final_lower_high",
                    f"직전 주요 고점 {_format_value(previous_same.value)}을 넘지 못하고 {value}에서 고점이 형성돼 상승 탄력이 약해진 상태로 끝났습니다.",
                )
        word = "고점" if point.pivot_type == "high" else "저점"
        return (
            "final_extreme",
            f"이 구간에서 마지막으로 확인된 주요 {word}으로 최종 피봇 구조의 끝점입니다.",
        )

    if incoming and outgoing and incoming != outgoing:
        if point.pivot_type == "high":
            if previous_same is not None and point.value > previous_same.value:
                if next_same is not None and next_same.value < point.value:
                    return (
                        "uptrend_peak_reversal",
                        f"직전 주요 고점 {_format_value(previous_same.value)}을 넘어 {value}까지 상승한 뒤 방향이 꺾여 하락으로 전환된 변곡점입니다.",
                    )
                return (
                    "higher_high_turn",
                    f"직전 주요 고점을 넘어 {value}의 새 고점을 만든 뒤 상승 흐름이 멈추고 방향이 전환된 지점입니다.",
                )
            if previous is not None and following is not None and following.pivot_type == "low":
                prior_low = previous if previous.pivot_type == "low" else None
                if prior_low is not None and following.value < prior_low.value:
                    return (
                        "failed_rebound_in_downtrend",
                        f"하락 중 {value}까지 반등했지만 이후 저점이 다시 더 낮아져 기존 하락 구조가 유지된 반등 변곡점입니다.",
                    )
            return (
                "local_high_turn",
                f"{value}에서 반등이 멈추고 이후 하락 방향으로 전환된 주요 고점입니다.",
            )

        if point.pivot_type == "low":
            if previous_same is not None and point.value < previous_same.value:
                if next_same is not None and next_same.value > point.value:
                    return (
                        "downtrend_low_reversal",
                        f"직전 주요 저점 {_format_value(previous_same.value)}을 밑돌아 {value}까지 하락한 뒤 방향이 꺾여 상승으로 전환된 변곡점입니다.",
                    )
                return (
                    "lower_low_turn",
                    f"직전 주요 저점보다 낮은 {value}의 새 저점을 만든 뒤 하락 흐름이 멈추고 방향이 전환된 지점입니다.",
                )
            if previous is not None and following is not None and following.pivot_type == "high":
                prior_high = previous if previous.pivot_type == "high" else None
                if prior_high is not None and following.value > prior_high.value:
                    return (
                        "failed_pullback_in_uptrend",
                        f"상승 중 {value}까지 조정받았지만 이후 고점이 다시 더 높아져 기존 상승 구조가 유지된 조정 변곡점입니다.",
                    )
            return (
                "local_low_turn",
                f"{value}에서 하락이 멈추고 이후 상승 방향으로 전환된 주요 저점입니다.",
            )

    if point.pivot_type == "high" and previous_same is not None:
        if point.value > previous_same.value:
            return (
                "higher_high_continuation",
                f"직전 주요 고점 {_format_value(previous_same.value)}을 넘어 {value}의 새 고점을 만들며 상승 구조가 이어졌습니다.",
            )
        if point.value < previous_same.value:
            return (
                "lower_high_weakness",
                f"반등이 {value}에서 멈추며 직전 주요 고점을 회복하지 못해 상승 탄력이 약해진 구조입니다.",
            )

    if point.pivot_type == "low" and previous_same is not None:
        if point.value < previous_same.value:
            return (
                "lower_low_continuation",
                f"직전 주요 저점 {_format_value(previous_same.value)}을 밑돌아 {value}의 새 저점을 만들며 하락 구조가 이어졌습니다.",
            )
        if point.value > previous_same.value:
            return (
                "higher_low_weakening_downtrend",
                f"저점이 직전 주요 저점보다 높은 {value}에서 형성돼 하락 압력이 약해진 구조입니다.",
            )

    word = "고점" if point.pivot_type == "high" else "저점"
    return (
        "final_structure_pivot",
        f"주변의 작은 변동을 제거한 뒤에도 최종 구조에 남은 주요 {word}입니다.",
    )

def build_storage_rows(
    *,
    base: Any,
    stage1: Any,
    stage2: Any,
    stage3: Any,
    stage4: Any,
    stage5: Any,
    final: SimplifiedLineResult,
) -> list[dict[str, Any]]:
    """Serialize one completed Stage 1~6 result without changing generation state."""
    ordered = sorted(final.markers, key=lambda item: (item.day, item.pivot_type))
    order_by_key = {_key(point): index for index, point in enumerate(ordered)}

    segment_by_start: dict[tuple[date, float, str], tuple[PivotPoint, str]] = {}
    endpoint_keys: set[tuple[date, float, str]] = set()
    for segment in final.segments:
        start_key = _key(segment.start)
        end_key = _key(segment.end)
        endpoint_keys.update((start_key, end_key))
        previous = segment_by_start.get(start_key)
        if previous is not None and _key(previous[0]) != end_key:
            raise RuntimeError("final pivot result has multiple outgoing segments from one marker")
        segment_by_start[start_key] = (segment.end, segment.kind)

    base_rdp_keys = _keys((*getattr(base, "high_pivots", ()), *getattr(base, "low_pivots", ())))
    stage1_keys = _keys(_stage_markers(stage1))
    stage2_keys = _keys(_stage_markers(stage2))
    stage3_keys = _keys(_stage_markers(stage3))
    stage4_keys = _keys(_stage_markers(stage4))
    stage5_keys = _keys(_stage_markers(stage5))

    spike_meta: dict[tuple[date, float, str], dict[str, Any]] = {}
    for spike in tuple(getattr(stage2, "spike_peaks", ()) or ()):
        peak_key = _key(spike.point)
        spike_meta.setdefault(peak_key, {}).update({
            "role": "peak",
            "direction": spike.direction,
            "angle_deg": float(spike.angle_deg),
            "marker_only": bool(spike.marker_only),
        })
        if spike.entry is not None:
            entry_key = _key(spike.entry)
            spike_meta.setdefault(entry_key, {}).update({
                "role": "entry",
                "direction": spike.direction,
                "peak_date": spike.point.day.isoformat(),
                "peak_value": float(spike.point.value),
                "marker_only": False,
            })

    sideways_meta: dict[tuple[date, float, str], list[dict[str, Any]]] = {}
    for group_name in ("high_sideways_segments", "low_sideways_segments"):
        for segment in tuple(getattr(stage2, group_name, ()) or ()):
            for point, boundary in ((segment.start, "start"), (segment.end, "end")):
                sideways_meta.setdefault(_key(point), []).append({
                    "boundary": boundary,
                    "prior_trend": segment.prior_trend,
                    "reference_side": segment.reference_side,
                    "angle_deg": float(segment.angle_deg),
                    "start_date": segment.start.day.isoformat(),
                    "end_date": segment.end.day.isoformat(),
                })

    rows: list[dict[str, Any]] = []
    for pivot_order, point in enumerate(ordered):
        point_key = _key(point)
        outgoing = segment_by_start.get(point_key)
        next_order = order_by_key.get(_key(outgoing[0])) if outgoing is not None else None
        segment_kind = outgoing[1] if outgoing is not None else None
        spike = spike_meta.get(point_key)
        sideways = sideways_meta.get(point_key, [])
        standalone = point_key not in endpoint_keys

        reason_codes: list[str] = []
        base_rdp = point_key in base_rdp_keys
        if base_rdp:
            reason_codes.append("stage1_rdp")
        if spike and spike.get("role") == "peak":
            reason_codes.append("spike_peak")
            if spike.get("marker_only"):
                reason_codes.append("spike_marker_only")
        if spike and spike.get("role") == "entry":
            reason_codes.append("spike_entry")
        if sideways:
            reason_codes.append("sideways_boundary")
        if point_key in stage3_keys:
            reason_codes.append("stage3_wave_vertex")
        if point_key in stage4_keys:
            reason_codes.append("stage4_rapid_survivor")
        if point_key in stage5_keys:
            reason_codes.append("stage5_survivor")
        reason_codes.append("stage6_survivor")
        reason_codes.append("standalone_marker" if standalone else "final_line_vertex")

        reason_type, selection_reason = _interpretive_reason(
            ordered,
            pivot_order,
            spike=spike,
            sideways_boundaries=sideways,
            standalone=standalone,
        )
        technical_reason = _technical_reason_text(
            point,
            base_rdp=base_rdp,
            spike_peak=bool(spike and spike.get("role") == "peak"),
            spike_entry=bool(spike and spike.get("role") == "entry"),
            marker_only=bool(spike and spike.get("marker_only")),
            sideways_boundaries=sideways,
            stage3_vertex=point_key in stage3_keys,
            stage4_survivor=point_key in stage4_keys,
            standalone=standalone,
        )

        rows.append({
            "pivot_order": pivot_order,
            "pivot_date": point.day.isoformat(),
            "pivot_value": float(point.value),
            "pivot_type": point.pivot_type,
            "next_pivot_order": next_order,
            "segment_to_next": segment_kind,
            "selection_reason_codes": reason_codes,
            "selection_reason": selection_reason,
            "selection_meta": {
                "stage_membership": {
                    "stage1": point_key in stage1_keys,
                    "stage2": point_key in stage2_keys,
                    "stage3": point_key in stage3_keys,
                    "stage4": point_key in stage4_keys,
                    "stage5": point_key in stage5_keys,
                    "stage6": True,
                },
                "base_rdp": base_rdp,
                "spike": spike,
                "sideways_boundaries": sideways,
                "reason_type": reason_type,
                "technical_reason": technical_reason,
                "final_role": {
                    "standalone": standalone,
                    "next_pivot_order": next_order,
                    "segment_to_next": segment_kind,
                },
            },
        })
    return rows


def source_input_sha256(source_rows: Sequence[dict[str, Any]]) -> str:
    """Hash the exact persisted pivot input in a stable, representation-independent form."""
    canonical = [
        {
            "observation_date": str(row.get("observation_date") or "")[:10],
            "value": format(float(row["value"]), ".17g"),
            "frequency": str(row.get("frequency") or ""),
        }
        for row in source_rows
    ]
    canonical.sort(key=lambda item: (item["observation_date"], item["frequency"], item["value"]))
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def replace_stored_pivots(
    db: SupabaseRest,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
    algorithm_version: str,
    frequency: str,
    buffer_start: date,
    buffer_end: date,
    source_point_count: int,
    input_sha256: str,
    rows: Sequence[dict[str, Any]],
) -> int:
    """Atomically replace one stored pivot snapshot through a DB RPC."""
    if not case_code.strip() or not index_code.strip() or not series_code.strip():
        raise ValueError("case_code, index_code and series_code are required")
    if not algorithm_version.strip():
        raise ValueError("algorithm_version is required")
    if not frequency.strip():
        raise ValueError("frequency is required")
    if buffer_end < buffer_start:
        raise ValueError("buffer_end must not precede buffer_start")
    if source_point_count < 0:
        raise ValueError("source_point_count must be non-negative")
    if len(input_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in input_sha256):
        raise ValueError("input_sha256 must be a lowercase SHA-256")

    result = db.request(
        "POST",
        "rpc/replace_historical_indicator_pivots",
        body={
            "p_case_code": case_code,
            "p_index_code": index_code,
            "p_series_code": series_code,
            "p_algorithm_version": algorithm_version,
            "p_frequency": frequency,
            "p_buffer_start": buffer_start.isoformat(),
            "p_buffer_end": buffer_end.isoformat(),
            "p_source_point_count": source_point_count,
            "p_input_sha256": input_sha256,
            "p_rows": list(rows),
        },
        retry_safe=False,
    )
    return int(result or 0)


def store_pipeline_result(
    db: SupabaseRest,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
    algorithm_version: str,
    source_rows: Sequence[dict[str, Any]],
    frequency: str,
    buffer_start: date,
    buffer_end: date,
    base: Any,
    stage1: Any,
    stage2: Any,
    stage3: Any,
    stage4: Any,
    stage5: Any,
    final: SimplifiedLineResult,
) -> int:
    """Build and atomically persist the already-computed Stage 1~6 result."""
    rows = build_storage_rows(
        base=base,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        final=final,
    )
    return replace_stored_pivots(
        db,
        case_code=case_code,
        index_code=index_code,
        series_code=series_code,
        algorithm_version=algorithm_version,
        frequency=frequency,
        buffer_start=buffer_start,
        buffer_end=buffer_end,
        source_point_count=len(source_rows),
        input_sha256=source_input_sha256(source_rows),
        rows=rows,
    )
