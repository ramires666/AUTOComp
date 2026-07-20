"""Read-only scan for CJK text left in safe export formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_TEXT_SUFFIXES = frozenset(
    {".txt", ".csv", ".json", ".jsonl", ".md", ".log", ".xml", ".ini", ".cfg"}
)
_ENCODINGS = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "big5")


@dataclass(frozen=True, slots=True)
class CjkFinding:
    relative_path: str
    line_number: int
    column_number: int
    text: str


@dataclass(frozen=True, slots=True)
class CjkScanReport:
    root: str
    scanned_files: int
    skipped_files: tuple[str, ...]
    findings: tuple[CjkFinding, ...]

    @property
    def has_cjk(self) -> bool:
        return bool(self.findings)


def decode_text_export(path: Path | str) -> str | None:
    """Decode a known text export without guessing at proprietary binary data."""

    path = Path(path)
    raw = path.read_bytes()
    if b"\x00" in raw[:4096] and not (raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff")):
        return None
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def scan_remaining_cjk(
    directory: Path | str, *, extensions: frozenset[str] = _TEXT_SUFFIXES
) -> CjkScanReport:
    """Find Chinese/Japanese/Korean ideographs in textual exports only.

    Proprietary project binaries are intentionally skipped; this scanner never writes.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError(f"Directory must exist: {root}")
    scanned = 0
    skipped: list[str] = []
    findings: list[CjkFinding] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.suffix.casefold() not in extensions:
            skipped.append(relative)
            continue
        text = decode_text_export(path)
        if text is None:
            skipped.append(relative)
            continue
        scanned += 1
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in _CJK.finditer(line):
                findings.append(
                    CjkFinding(relative, line_number, match.start() + 1, match.group(0))
                )
    return CjkScanReport(str(root), scanned, tuple(skipped), tuple(findings))
