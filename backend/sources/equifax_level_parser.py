"""Strict parser for Equifax small-business delinquency/default monthly levels."""
from __future__ import annotations

import re


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))


def _metric_token(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label.strip().upper())
    if normalized.startswith("SBDFI"):
        return "default"
    if re.search(r"SBDI\s*31\s*-\s*90", normalized):
        return "short"
    if re.search(r"SBDI\s*91\s*-\s*180", normalized):
        return "severe"
    raise ValueError(label)


def _table_layout_levels(clean: str) -> tuple[float, float, float] | None:
    """Parse modern PDF tables without assuming the metric-column order.

    Equifax layouts may emit all three column headers first and then the three level
    values. Read the header order, then map the subsequent `(Level)` percentages in
    that same order. This prevents M/M or Y/Y percentages from being mistaken for
    current levels and survives header-order changes such as SBDFI-first layouts.
    """
    header_pattern = re.compile(
        r"(?P<label>SBDFI\b|SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?|SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?)",
        re.I,
    )
    matches = list(header_pattern.finditer(clean))
    for i in range(len(matches) - 2):
        trio = matches[i:i + 3]
        tokens = [_metric_token(m.group("label")) for m in trio]
        if set(tokens) != {"short", "severe", "default"}:
            continue
        if trio[-1].end() - trio[0].start() > 260:
            continue
        tail = clean[trio[-1].end():trio[-1].end() + 700]
        levels = [float(v) for v in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)", tail, re.I)[:3]]
        if len(levels) != 3 or any(not (0 <= v < 10) for v in levels):
            continue
        mapped = dict(zip(tokens, levels))
        return mapped["short"], mapped["severe"], mapped["default"]
    return None


def _local_metric_level(clean: str, label_pattern: str) -> float | None:
    """Parse layouts where each heading and its level are emitted together."""
    label = re.search(label_pattern, clean, re.I)
    if not label:
        return None
    next_metric = re.search(
        r"SBDFI\b|SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?|SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?|SBLI\b",
        clean[label.end():],
        re.I,
    )
    end = label.end() + (next_metric.start() if next_metric else 220)
    body = clean[label.end():min(len(clean), end)]
    level = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*\(Level\)", body, re.I)
    if level:
        value = float(level.group(1))
        return value if 0 <= value < 10 else None
    percentages = [float(v) for v in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", body)]
    if len(percentages) == 1 and 0 <= percentages[0] < 10:
        return percentages[0]
    return None


def extract_equifax_levels(text: str) -> tuple[float, float, float] | None:
    clean = _clean(text)

    modern = _table_layout_levels(clean)
    if modern is not None:
        return modern

    short = _local_metric_level(clean, r"SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?")
    severe = _local_metric_level(clean, r"SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?")
    default = _local_metric_level(clean, r"SBDFI\b")
    if short is not None and severe is not None and default is not None:
        return short, severe, default

    short_match = re.search(
        r"SBDI\)?\s*31\s*-\s*90\s*Days\s*Past\s*Due[^.]{0,180}?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        clean,
        re.I,
    )
    severe_match = re.search(
        r"SBDI\s*91\s*-\s*180\s*Days\s*Past\s*Due[^.]{0,180}?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        clean,
        re.I,
    )
    default_match = re.search(
        r"Defaults?\b[^.]{0,160}?(?:to|at)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        clean,
        re.I,
    )
    if short_match and severe_match and default_match:
        return float(short_match.group(1)), float(severe_match.group(1)), float(default_match.group(1))
    return None
