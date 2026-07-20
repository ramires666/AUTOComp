from autocomp.verification.cjk import scan_remaining_cjk


def test_scanner_reads_utf8_and_skips_binary_files(tmp_path):
    (tmp_path / "notes.txt").write_text("Pump 启动\n", encoding="utf-8")
    (tmp_path / "project.kvproj").write_bytes(b"\x00\xff\x11")
    report = scan_remaining_cjk(tmp_path)
    assert report.scanned_files == 1
    assert [(item.relative_path, item.line_number, item.text) for item in report.findings] == [
        ("notes.txt", 1, "启动"),
    ]
    assert report.skipped_files == ("project.kvproj",)
