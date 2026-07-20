"""Content hash manifests for verifying project copies without editing them."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileHash:
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class HashManifest:
    root: str
    files: tuple[FileHash, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "files": [
                {"path": item.relative_path, "size_bytes": item.size_bytes, "sha256": item.sha256}
                for item in self.files
            ],
        }


@dataclass(frozen=True, slots=True)
class ManifestComparison:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def identical(self) -> bool:
        return not (self.added or self.removed or self.changed)


def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_hash_manifest(project_copy: Path | str) -> HashManifest:
    """Hash every regular file beneath an existing project-copy directory.

    This function is deliberately read-only and is safe for proprietary binary files:
    it treats their contents as opaque bytes.
    """
    root = Path(project_copy).resolve()
    if not root.is_dir():
        raise ValueError(f"Project copy must be an existing directory: {root}")
    files = tuple(
        FileHash(
            relative_path=path.relative_to(root).as_posix(),
            size_bytes=path.stat().st_size,
            sha256=_hash_file(path),
        )
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold())
        if path.is_file()
    )
    return HashManifest(root=str(root), files=files)


def compare_manifests(baseline: HashManifest, candidate: HashManifest) -> ManifestComparison:
    """Compare two manifests by relative path and byte-level content hash."""
    before = {item.relative_path: item for item in baseline.files}
    after = {item.relative_path: item for item in candidate.files}
    return ManifestComparison(
        added=tuple(sorted(after.keys() - before.keys())),
        removed=tuple(sorted(before.keys() - after.keys())),
        changed=tuple(
            sorted(
                path
                for path in before.keys() & after.keys()
                if before[path].sha256 != after[path].sha256
            )
        ),
    )
