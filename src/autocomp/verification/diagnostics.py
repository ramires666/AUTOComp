"""Comparison models for editor Check/Compile diagnostics."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: str
    message: str
    code: str | None = None
    source: str | None = None
    line: int | None = None

    def fingerprint(self) -> tuple[str, str, str, str, int | None]:
        return (
            self.severity.strip().casefold(),
            self.code or "",
            self.source or "",
            _SPACE.sub(" ", self.message).strip(),
            self.line,
        )


@dataclass(frozen=True, slots=True)
class DiagnosticComparison:
    added: tuple[Diagnostic, ...]
    removed: tuple[Diagnostic, ...]

    @property
    def identical(self) -> bool:
        return not self.added and not self.removed


def compare_diagnostics(
    baseline: list[Diagnostic] | tuple[Diagnostic, ...],
    candidate: list[Diagnostic] | tuple[Diagnostic, ...],
) -> DiagnosticComparison:
    """Compare diagnostic multisets, preserving duplicate occurrences."""
    before = Counter(item.fingerprint() for item in baseline)
    after = Counter(item.fingerprint() for item in candidate)
    added: list[Diagnostic] = []
    removed: list[Diagnostic] = []
    by_fingerprint_after = {item.fingerprint(): item for item in candidate}
    by_fingerprint_before = {item.fingerprint(): item for item in baseline}
    for fingerprint, count in (after - before).items():
        added.extend([by_fingerprint_after[fingerprint]] * count)
    for fingerprint, count in (before - after).items():
        removed.extend([by_fingerprint_before[fingerprint]] * count)
    return DiagnosticComparison(tuple(added), tuple(removed))
