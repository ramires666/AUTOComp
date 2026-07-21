"""Inventory helpers: detection, risk classification, and reporting."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import InventoryRecord, RiskLevel, TextKind

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\|(?<![*/])/(?![*/]))")
_PROTOCOL_RE = re.compile(r"\b(?:https?|ftp|mqtt|modbus|tcp|udp)\b", re.I)


def contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def assess_risk(kind: TextKind, text: str) -> tuple[RiskLevel, bool, str]:
    """Classify candidates conservatively; high-risk items are never auto-applied."""
    if kind in {TextKind.PROGRAM_NAME, TextKind.IDENTIFIER}:
        return RiskLevel.HIGH, True, "names and identifiers require manual review"
    if kind == TextKind.STRING_LITERAL:
        return RiskLevel.HIGH, True, "string literal may affect equipment behavior"
    if _PATH_RE.search(text) or _PROTOCOL_RE.search(text):
        return RiskLevel.HIGH, True, "contains path or protocol token"
    if kind in {TextKind.UI_LABEL, TextKind.OTHER}:
        return RiskLevel.MEDIUM, False, "context should be checked"
    return RiskLevel.LOW, False, "commentary text"


def with_assessed_risk(record: InventoryRecord) -> InventoryRecord:
    risk, review, _ = assess_risk(record.kind, record.source_text)
    return InventoryRecord(
        record_id=record.record_id,
        source_text=record.source_text,
        kind=record.kind,
        hierarchy=record.hierarchy,
        context=record.context,
        location=record.location,
        risk=risk,
        requires_review=review,
    )


def remaining_cjk(records: Iterable[InventoryRecord]) -> list[InventoryRecord]:
    return [record for record in records if contains_cjk(record.source_text)]
