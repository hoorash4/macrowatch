from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import io
import re
from typing import Iterable
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile


XLINK = "{http://www.w3.org/1999/xlink}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
QUARTER_START = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
METRIC_NAMES = {
    "top_line": {"매출", "매출액", "수익", "수익매출액", "매출수익", "순영업이익", "순영업수익", "영업수익"},
    "operating_income": {"영업이익", "영업이익손실", "영업손익", "영업손실"},
    "net_income": {
        "당기순이익", "당기순이익손실", "당기순손익", "당기순손실",
        "분기순이익", "분기순이익손실", "반기순이익", "반기순이익손실",
    },
}


def _local(value: str) -> str:
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _norm(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _concept_aliases(value: str) -> tuple[str, ...]:
    concept = _local(unquote(value.split("#")[-1]))
    aliases = [concept]
    # XBRL schema fragments commonly encode the namespace prefix with an
    # underscore (ifrs-full_Revenue), while instance QNames expose only the
    # local part (Revenue).
    if "_" in concept:
        aliases.append(concept.split("_", 1)[1])
    return tuple(dict.fromkeys(aliases))


def _decimal(value: object, scale: object = None) -> Decimal | None:
    text = str(value or "").replace(",", "").strip()
    if text in {"", "-", "—", "–"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        result = Decimal(text)
        if scale not in (None, ""):
            result *= Decimal(10) ** int(str(scale))
        return result
    except (InvalidOperation, ValueError):
        return None


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class Context:
    start: date | None
    end: date | None
    dimensional: bool


@dataclass(frozen=True)
class Fact:
    concept: str
    label: str
    value: Decimal
    context: Context
    role_rank: int


@dataclass(frozen=True)
class XbrlQuarterMetrics:
    top_line: Decimal | None = None
    operating_income: Decimal | None = None
    net_income: Decimal | None = None


def _safe_xml_members(payload: bytes) -> list[tuple[str, ET.Element]]:
    documents = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            lower = info.filename.lower()
            if info.is_dir():
                continue
            # Facts live in the instance, Korean account names in label
            # linkbases, and statement roles in presentation/schema files.
            # Calculation, definition, reference and English linkbases do not
            # participate in this extraction and can be very large.
            relevant = (
                lower.endswith((".xbrl", ".xsd"))
                or lower.endswith(".xml") and any(token in lower for token in (
                    "_ko", "-ko", "label", "_lab", "-lab", "_pre", "-pre",
                ))
            )
            if not relevant:
                continue
            # The official archive is small, but reject pathological members
            # rather than expanding an unbounded ZIP in memory.
            if info.file_size > 64 * 1024 * 1024:
                continue
            try:
                documents.append((info.filename, ET.fromstring(archive.read(info))))
            except ET.ParseError:
                continue
    return documents


def _labels(documents: Iterable[tuple[str, ET.Element]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for _name, root in documents:
        locations = {}
        resources = {}
        arcs = []
        for node in root.iter():
            kind = _local(node.tag)
            if kind == "loc":
                label = node.attrib.get(f"{XLINK}label", "")
                fragment = node.attrib.get(f"{XLINK}href", "")
                if label and fragment:
                    locations[label] = _concept_aliases(fragment)
            elif kind == "label":
                language = node.attrib.get(XML_LANG, "")
                resource = node.attrib.get(f"{XLINK}label", "")
                text = "".join(node.itertext()).strip()
                if resource and text and (not language or language.lower().startswith("ko")):
                    resources[resource] = text
            elif kind == "labelArc":
                arcs.append((
                    node.attrib.get(f"{XLINK}from", ""),
                    node.attrib.get(f"{XLINK}to", ""),
                ))
        for source, target in arcs:
            concepts = locations.get(source, ())
            label = resources.get(target)
            if concepts and label:
                for concept in concepts:
                    result.setdefault(concept, label)
    return result


def _role_ranks(documents: Iterable[tuple[str, ET.Element]]) -> dict[str, int]:
    definitions = {}
    for _name, root in documents:
        for node in root.iter():
            if _local(node.tag) != "roleType":
                continue
            uri = node.attrib.get("roleURI", "")
            definition = next(("".join(child.itertext()) for child in node if _local(child.tag) == "definition"), "")
            if uri:
                definitions[uri] = _norm(definition)

    ranks: dict[str, int] = {}
    for _name, root in documents:
        for link in root.iter():
            if _local(link.tag) != "presentationLink":
                continue
            role = link.attrib.get(f"{XLINK}role", "")
            definition = definitions.get(role, "")
            is_income = "손익계산서" in definition or "포괄손익계산서" in definition
            is_note = "주석" in definition
            is_separate = "별도" in definition or "개별" in definition
            is_consolidated = "연결" in definition
            if is_income and is_consolidated:
                rank = 0
            elif is_income and not is_separate:
                rank = 1
            elif is_note and is_consolidated:
                rank = 2
            elif is_note:
                rank = 3
            elif is_income:
                rank = 4
            else:
                rank = 5
            for node in link:
                if _local(node.tag) != "loc":
                    continue
                fragment = node.attrib.get(f"{XLINK}href", "")
                if fragment:
                    for concept in _concept_aliases(fragment):
                        ranks[concept] = min(rank, ranks.get(concept, rank))
    return ranks


def _contexts(root: ET.Element) -> dict[str, Context]:
    result = {}
    for node in root:
        if _local(node.tag) != "context":
            continue
        identifier = node.attrib.get("id", "")
        start = end = None
        dimensional = False
        for child in node.iter():
            kind = _local(child.tag)
            if kind == "startDate":
                start = _date(child.text)
            elif kind in {"endDate", "instant"}:
                end = _date(child.text)
            elif kind in {"explicitMember", "typedMember"}:
                dimensional = True
        if identifier:
            result[identifier] = Context(start=start, end=end, dimensional=dimensional)
    return result


def _facts(documents: Iterable[tuple[str, ET.Element]], labels: dict[str, str], roles: dict[str, int]) -> list[Fact]:
    facts = []
    for _name, root in documents:
        if _local(root.tag).lower() != "xbrl":
            continue
        contexts = _contexts(root)
        for node in root:
            context = contexts.get(node.attrib.get("contextRef", ""))
            if context is None:
                continue
            concept = _local(node.tag)
            label = labels.get(concept, concept)
            value = _decimal(node.text, node.attrib.get("scale"))
            if value is not None:
                facts.append(Fact(
                    concept=concept,
                    label=label,
                    value=value,
                    context=context,
                    role_rank=roles.get(concept, 5),
                ))
    return facts


def _matches_metric(metric: str, label: str) -> bool:
    normalized = _norm(label)
    if metric == "top_line":
        return bool(re.fullmatch(r"(?:매출액|매출|수익)+", normalized)) or normalized in {
            "순영업이익", "순영업수익", "영업수익",
        }
    return normalized in METRIC_NAMES[metric]


def _one_value(facts: Iterable[Fact], metric: str, start: date, end: date) -> Decimal | None:
    candidates = [
        fact for fact in facts
        if _matches_metric(metric, fact.label)
        and fact.context.start == start
        and fact.context.end == end
    ]
    if not candidates:
        return None
    # Main non-dimensional consolidated statement values win. If the best
    # context still contains conflicting values, refuse to guess.
    best_rank = min((1 if fact.context.dimensional else 0, fact.role_rank) for fact in candidates)
    best = [
        fact for fact in candidates
        if (1 if fact.context.dimensional else 0, fact.role_rank) == best_rank
    ]
    values = {fact.value for fact in best}
    return next(iter(values)) if len(values) == 1 else None


def _period_metrics(payload: bytes, year: int, quarter: int, *, cumulative: bool) -> XbrlQuarterMetrics:
    documents = _safe_xml_members(payload)
    labels = _labels(documents)
    roles = _role_ranks(documents)
    facts = _facts(documents, labels, roles)
    month, day = QUARTER_END[quarter]
    end = date(year, month, day)
    start_month, start_day = (1, 1) if cumulative else QUARTER_START[quarter]
    start = date(year, start_month, start_day)
    values = {
        metric: _one_value(facts, metric, start, end)
        for metric in METRIC_NAMES
    }
    return XbrlQuarterMetrics(**values)


def extract_single_quarter_metrics(
    payload: bytes,
    year: int,
    quarter: int,
    *,
    prior_quarter_payload: bytes | None = None,
) -> XbrlQuarterMetrics:
    """Extract only the current standalone quarter from an official XBRL ZIP."""
    standalone = _period_metrics(payload, year, quarter, cumulative=False)
    if quarter != 4 or all(value is not None for value in standalone.__dict__.values()):
        return standalone

    annual = _period_metrics(payload, year, quarter, cumulative=True)
    prior = (
        _period_metrics(prior_quarter_payload, year, 3, cumulative=True)
        if prior_quarter_payload is not None else XbrlQuarterMetrics()
    )

    def q4(name: str) -> Decimal | None:
        direct = getattr(standalone, name)
        if direct is not None:
            return direct
        current = getattr(annual, name)
        previous = getattr(prior, name)
        return current - previous if current is not None and previous is not None else None

    return XbrlQuarterMetrics(**{name: q4(name) for name in METRIC_NAMES})
