from autocomp.verification.checkpoint import build_checkpoint_report
from autocomp.verification.cjk import CjkScanReport
from autocomp.verification.diagnostics import Diagnostic, compare_diagnostics
from autocomp.verification.hashes import ManifestComparison
from autocomp.verification.mnemonic import compare_mnemonic_exports


def test_diagnostic_comparison_normalizes_whitespace_and_detects_new_error():
    old = [Diagnostic("Error", "Invalid  operand", code="E1", line=2)]
    same = [Diagnostic("error", "Invalid operand", code="E1", line=2)]
    assert compare_diagnostics(old, same).identical
    comparison = compare_diagnostics(old, same + [Diagnostic("warning", "new")])
    assert comparison.added == (Diagnostic("warning", "new"),)


def test_checkpoint_requires_clean_logic_diagnostics_and_cjk(tmp_path):
    report = build_checkpoint_report(
        "03_comments",
        ManifestComparison((), (), ("comments.csv",)),
        compare_mnemonic_exports("LD M0", "LD M0"),
        compare_diagnostics([], []),
        CjkScanReport(str(tmp_path), 1, (), ()),
    )
    assert report.passed
    assert report.files.changed == ("comments.csv",)
