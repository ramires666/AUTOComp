"""Project-local glossary and exact-match translation memory."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Glossary:
    terms: dict[str, str] = field(default_factory=dict)

    def lookup(self, source: str) -> str | None:
        return self.terms.get(source)

    def add(self, source: str, target: str) -> None:
        if not source.strip() or not target.strip():
            raise ValueError("glossary terms must not be blank")
        self.terms[source] = target


@dataclass(slots=True)
class TranslationMemory:
    """Exact source-text cache; context intentionally does not change stable wording."""

    entries: dict[str, str] = field(default_factory=dict)

    def lookup(self, source: str) -> str | None:
        return self.entries.get(source)

    def remember(self, source: str, target: str) -> None:
        if source and target:
            self.entries[source] = target
