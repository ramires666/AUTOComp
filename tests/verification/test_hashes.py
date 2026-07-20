from autocomp.verification.hashes import build_hash_manifest, compare_manifests


def test_manifest_detects_changed_added_and_removed(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "same.txt").write_text("same", encoding="utf-8")
    (before / "changed.txt").write_text("old", encoding="utf-8")
    (before / "removed.txt").write_text("gone", encoding="utf-8")
    (after / "same.txt").write_text("same", encoding="utf-8")
    (after / "changed.txt").write_text("new", encoding="utf-8")
    (after / "added.txt").write_text("new", encoding="utf-8")
    result = compare_manifests(build_hash_manifest(before), build_hash_manifest(after))
    assert result.added == ("added.txt",)
    assert result.removed == ("removed.txt",)
    assert result.changed == ("changed.txt",)
