"""Single read-only checkpoint report for a translation batch."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .cjk import CjkScanReport
from .diagnostics import DiagnosticComparison
from .hashes import ManifestComparison
from .mnemonic import MnemonicComparison


@dataclass(frozen=True, slots=True)
class CheckpointReport:
    checkpoint_name: str
    files: ManifestComparison
    mnemonic: MnemonicComparison
    diagnostics: DiagnosticComparison
    remaining_cjk: CjkScanReport

    @property
    def logic_unchanged(self) -> bool:
        return self.mnemonic.identical

    @property
    def passed(self) -> bool:
        return (
            self.logic_unchanged and self.diagnostics.identical and not self.remaining_cjk.has_cjk
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_checkpoint_report(
    checkpoint_name: str,
    files: ManifestComparison,
    mnemonic: MnemonicComparison,
    diagnostics: DiagnosticComparison,
    remaining_cjk: CjkScanReport,
) -> CheckpointReport:
    if not checkpoint_name.strip():
        raise ValueError("A named checkpoint is required")
    return CheckpointReport(checkpoint_name, files, mnemonic, diagnostics, remaining_cjk)
