"""Content-addressed artifact directory."""

from __future__ import annotations

import hashlib
from pathlib import Path

from testbed_contracts.ports import BlobRef


class LocalArtifactStore:
    """Files named by their own sha256, so identical payloads are stored once."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        bare = digest.split(":", 1)[1]
        return self.root / bare[:2] / bare

    def put(self, data: bytes, *, media_type: str = "application/octet-stream") -> BlobRef:
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return BlobRef(hash=digest, media_type=media_type, size_bytes=len(data))

    def get(self, ref: BlobRef) -> bytes:
        path = self._path(ref.hash)
        if not path.exists():
            raise FileNotFoundError(f"artifact {ref.hash} is not in {self.root}")
        return path.read_bytes()

    def has(self, ref: BlobRef) -> bool:
        return self._path(ref.hash).exists()
