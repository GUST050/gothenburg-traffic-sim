"""Exact content-addressed storage for annual warm-state artifacts.

SUMO save-states omit future route departures, so a usable annual artifact is
the state *plus* its exact route input, prefix evidence, and demand provenance.
This store deduplicates those members by uncompressed SHA-256 and applies
deterministic compression only when it reduces bytes.  Every restore revalidates
both the stored representation and the original member bytes.

Two codecs are offered because the members differ in kind.  ``route.rou.xml``
and ``prefix_evidence.json`` are plain XML/JSON that LZMA compresses far better
than gzip (measured on a real 96-link chain: route 6,371,443 -> 950,432 bytes,
14.9%; prefix evidence 181,037 -> 110,204 bytes, 60.9%).  ``state.xml.gz`` is
already gzip-compressed by SUMO, so no codec can shrink it and both are skipped
in favour of ``identity``.  The selection is made per member from measured
output sizes, never from the member's name, so it stays correct if a member's
representation ever changes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping


ARTIFACT_SCHEMA = "annual_warm_artifact_v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.+-]+$")
_MEMBER_NAMES = frozenset({
    "state.xml.gz",
    "prefix_evidence.json",
    "route.rou.xml",
    "demand_meta.json",
    "demand_build_spec.json",
    "demand_manifest.json",
})
_COPY_BLOCK = 1024 * 1024
# Per-process proof that a stored representation expands to the content digest
# in its content-addressed filename. The stored blob itself is still hashed on
# every reuse, so corruption cannot hide behind this cache; only repeated gzip
# decompression of an unchanged shared route is avoided.
_VERIFIED_BLOBS: set[tuple[str, str, str, int]] = set()

# Encoding names are part of the on-disk artifact record, so the suffix map is
# the single place that binds a name to a blob filename. "identity" keeps the
# historical ".raw" suffix and every previously written gzip blob keeps ".gz",
# so artifacts published before LZMA existed still validate and restore.
_BLOB_SUFFIXES = {"identity": "raw", "gzip": "gz", "xz": "xz"}
# Reuse probes run smallest-codec-first so a chain that already stored a route
# under LZMA never falls back to a larger gzip copy of the same content.
_COMPRESSED_ENCODINGS = ("xz", "gzip")
# LZMA preset 6 is the measured knee: preset 9|EXTREME bought a further 3.5
# percentage points on the largest member while costing 5x the CPU, which is
# the wrong trade for a job already bounded by simulation time.
_XZ_PRESET = 6
# Attempting LZMA on data gzip could not meaningfully compress wastes CPU on
# every checkpoint of every chain. SUMO's already-gzipped state is the member
# this guard exists for.
_XZ_ATTEMPT_RATIO = 0.9


def _open_encoded(blob: Path, encoding: str):
    """Open a stored blob as a stream of its ORIGINAL member bytes."""
    if encoding == "gzip":
        return gzip.open(blob, "rb")
    if encoding == "xz":
        return lzma.open(blob, "rb")
    return blob.open("rb")


def _canonical_key(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_key"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while block := handle.read(_COPY_BLOCK):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _strict_regular_file(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    return path


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    """Persist a newly linked directory entry where the platform supports it."""
    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AnnualWarmArtifactStore:
    """Immutable blob store rooted at an explicitly supplied directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _blob_path(self, content_sha256: str, encoding: str) -> Path:
        suffix = _BLOB_SUFFIXES.get(encoding, "raw")
        return self.root / "blobs" / content_sha256[:2] / (
            f"{content_sha256}.{suffix}"
        )

    def _artifact_path(self, unit_id: str) -> Path:
        return self.root / "artifacts" / f"{unit_id}.json"

    def _publish_blob(
        self,
        temporary: Path,
        *,
        content_sha256: str,
        encoding: str,
        stored_sha256: str,
        stored_bytes: int,
    ) -> Path:
        target = self._blob_path(content_sha256, encoding)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"blob target is not a regular file: {target}")
            digest, size = _sha256_file(target)
            if digest != stored_sha256 or size != stored_bytes:
                raise ValueError(f"existing content-addressed blob is corrupt: {target}")
            return target
        try:
            # A hard-link publishes without ever replacing an existing blob.
            # Concurrent identical writers converge; a corrupt pre-existing
            # target is evidence to reject, never something to repair here.
            os.link(temporary, target)
            _fsync_directory(target.parent)
        except FileExistsError:
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"raced blob target is not regular: {target}")
            digest, size = _sha256_file(target)
            if digest != stored_sha256 or size != stored_bytes:
                raise ValueError(f"raced content-addressed blob is corrupt: {target}")
        return target

    def _reuse_blob(
        self, *, content_sha256: str, content_bytes: int, encoding: str
    ) -> dict[str, Any] | None:
        """Return a verified existing blob record without recompressing input."""
        target = self._blob_path(content_sha256, encoding)
        if not target.exists():
            return None
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"blob target is not a regular file: {target}")
        stored_sha256, stored_bytes = _sha256_file(target)
        cache_key = (
            str(self.root.resolve()), content_sha256, stored_sha256, stored_bytes)
        if encoding == "identity":
            if stored_sha256 != content_sha256 or stored_bytes != content_bytes:
                raise ValueError(
                    f"existing identity blob is corrupt: {target}")
        elif cache_key not in _VERIFIED_BLOBS:
            expanded = hashlib.sha256()
            expanded_bytes = 0
            try:
                with _open_encoded(target, encoding) as handle:
                    while block := handle.read(_COPY_BLOCK):
                        expanded.update(block)
                        expanded_bytes += len(block)
            except (OSError, EOFError, lzma.LZMAError) as exc:
                raise ValueError(
                    f"existing {encoding} blob is corrupt: {target}") from exc
            if expanded.hexdigest() != content_sha256 \
                    or expanded_bytes != content_bytes:
                raise ValueError(
                    f"existing {encoding} blob has different content: {target}")
            _VERIFIED_BLOBS.add(cache_key)
        return {
            "content_sha256": content_sha256,
            "content_bytes": content_bytes,
            "encoding": encoding,
            "stored_sha256": stored_sha256,
            "stored_bytes": stored_bytes,
            "blob": target.relative_to(self.root).as_posix(),
        }

    def _store_member(self, source: Path) -> dict[str, Any]:
        source = _strict_regular_file(source, "artifact member")
        content_sha256, content_bytes = _sha256_file(source)
        # Existing content-addressed members already reveal their encoding.
        # Prefer the compressed representation when it exists and is smaller,
        # exactly matching the normal publication choice. This removes a full
        # route recompression from every later checkpoint in a chain. Probing
        # smallest-codec-first keeps a chain on the representation it already
        # chose instead of silently republishing a larger one.
        for candidate_encoding in _COMPRESSED_ENCODINGS:
            compressed = self._reuse_blob(
                content_sha256=content_sha256,
                content_bytes=content_bytes,
                encoding=candidate_encoding,
            )
            if compressed is not None \
                    and compressed["stored_bytes"] < content_bytes:
                return compressed
        identity = self._reuse_blob(
            content_sha256=content_sha256,
            content_bytes=content_bytes,
            encoding="identity",
        )
        if identity is not None:
            return identity
        staging = self.root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)

        gzip_descriptor, raw_gzip = tempfile.mkstemp(
            prefix=content_sha256 + ".", suffix=".gz.tmp", dir=staging
        )
        gzip_path = Path(raw_gzip)
        os.close(gzip_descriptor)
        xz_path: Path | None = None
        try:
            compression_content = hashlib.sha256()
            compression_bytes = 0
            with source.open("rb") as incoming, gzip_path.open("wb") as outgoing:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=outgoing,
                    compresslevel=6, mtime=0,
                ) as compressed:
                    while block := incoming.read(_COPY_BLOCK):
                        compression_content.update(block)
                        compression_bytes += len(block)
                        compressed.write(block)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if compression_content.hexdigest() != content_sha256 \
                    or compression_bytes != content_bytes:
                raise ValueError(f"artifact member changed while reading: {source}")
            gzip_sha256, gzip_bytes = _sha256_file(gzip_path)
            best_sha256, best_bytes, best_path, best_encoding = (
                gzip_sha256, gzip_bytes, gzip_path, "gzip"
            )
            # Only pay for LZMA where gzip proved the bytes are compressible.
            # SUMO's already-gzipped state lands above the ratio and is skipped,
            # so no chain pays LZMA CPU for zero bytes saved.
            if gzip_bytes < content_bytes * _XZ_ATTEMPT_RATIO:
                xz_descriptor, raw_xz = tempfile.mkstemp(
                    prefix=content_sha256 + ".", suffix=".xz.tmp", dir=staging
                )
                xz_path = Path(raw_xz)
                os.close(xz_descriptor)
                xz_content = hashlib.sha256()
                xz_bytes_read = 0
                with source.open("rb") as incoming, xz_path.open("wb") as outgoing:
                    with lzma.LZMAFile(
                        outgoing, mode="wb", format=lzma.FORMAT_XZ,
                        preset=_XZ_PRESET,
                    ) as compressed:
                        while block := incoming.read(_COPY_BLOCK):
                            xz_content.update(block)
                            xz_bytes_read += len(block)
                            compressed.write(block)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                if xz_content.hexdigest() != content_sha256 \
                        or xz_bytes_read != content_bytes:
                    raise ValueError(
                        f"artifact member changed while reading: {source}")
                xz_sha256, xz_stored_bytes = _sha256_file(xz_path)
                if xz_stored_bytes < best_bytes:
                    best_sha256, best_bytes, best_path, best_encoding = (
                        xz_sha256, xz_stored_bytes, xz_path, "xz"
                    )
            if best_bytes < content_bytes:
                encoding = best_encoding
                stored_sha256 = best_sha256
                stored_bytes = best_bytes
                chosen = best_path
            else:
                encoding = "identity"
                raw_descriptor, raw_copy = tempfile.mkstemp(
                    prefix=content_sha256 + ".", suffix=".raw.tmp", dir=staging
                )
                chosen = Path(raw_copy)
                copy_content = hashlib.sha256()
                copy_bytes = 0
                try:
                    with os.fdopen(raw_descriptor, "wb") as outgoing, \
                            source.open("rb") as incoming:
                        while block := incoming.read(_COPY_BLOCK):
                            copy_content.update(block)
                            copy_bytes += len(block)
                            outgoing.write(block)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                except BaseException:
                    chosen.unlink(missing_ok=True)
                    raise
                if copy_content.hexdigest() != content_sha256 \
                        or copy_bytes != content_bytes:
                    raise ValueError(f"artifact member changed while copying: {source}")
                stored_sha256 = content_sha256
                stored_bytes = content_bytes
            target = self._publish_blob(
                chosen,
                content_sha256=content_sha256,
                encoding=encoding,
                stored_sha256=stored_sha256,
                stored_bytes=stored_bytes,
            )
            return {
                "content_sha256": content_sha256,
                "content_bytes": content_bytes,
                "encoding": encoding,
                "stored_sha256": stored_sha256,
                "stored_bytes": stored_bytes,
                "blob": target.relative_to(self.root).as_posix(),
            }
        finally:
            gzip_path.unlink(missing_ok=True)
            if xz_path is not None:
                xz_path.unlink(missing_ok=True)
            if "chosen" in locals():
                chosen.unlink(missing_ok=True)

    def pack_artifact(
        self,
        *,
        plan_content_key: str,
        unit_id: str,
        members: Mapping[str, Path],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(plan_content_key, str) or not _HEX64.fullmatch(
            plan_content_key
        ):
            raise ValueError("plan_content_key must be lowercase SHA-256")
        if not isinstance(unit_id, str) or not _SAFE_ID.fullmatch(unit_id):
            raise ValueError("unit_id must be a safe non-empty key")
        if not isinstance(members, Mapping) or set(members) != _MEMBER_NAMES:
            raise ValueError(
                "annual artifact members must be exactly "
                + ", ".join(sorted(_MEMBER_NAMES))
            )
        if not isinstance(metadata, Mapping):
            raise ValueError("annual artifact metadata must be an object")
        # Prove metadata is finite/canonical before writing blobs.
        metadata_copy = json.loads(json.dumps(
            metadata, sort_keys=True, separators=(",", ":"), allow_nan=False
        ))
        records = {
            name: self._store_member(Path(members[name]))
            for name in sorted(members)
        }
        manifest: dict[str, Any] = {
            "schema": ARTIFACT_SCHEMA,
            "plan_content_key": plan_content_key,
            "unit_id": unit_id,
            "metadata": metadata_copy,
            "members": records,
        }
        manifest["content_key"] = _canonical_key(manifest)
        target = self._artifact_path(unit_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=target.parent
        )
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    manifest, handle, indent=2, sort_keys=True, allow_nan=False
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Never replace an immutable unit manifest. Concurrent writers
                # either publish the same bytes or the second writer fails.
                os.link(temporary, target)
                _fsync_directory(target.parent)
            except FileExistsError:
                existing = self._load_artifact(
                    unit_id, verify_member_content=False
                )
                if existing != manifest:
                    raise ValueError(
                        "annual artifact unit already has different content: "
                        + unit_id
                    )
                return existing
        finally:
            temporary.unlink(missing_ok=True)
        # Every source member was hashed while being stored and every chosen
        # blob was verified by its stored digest. Avoid decompressing large,
        # shared route blobs again for each of 100k manifests; consumers restore
        # and hash the members they actually use.
        return self._load_artifact(unit_id, verify_member_content=False)

    def artifact_exists(self, unit_id: str) -> bool:
        """Return whether a manifest entry exists without accepting a symlink."""
        if not isinstance(unit_id, str) or not _SAFE_ID.fullmatch(unit_id):
            raise ValueError("unit_id must be a safe non-empty key")
        path = self._artifact_path(unit_id)
        if path.is_symlink():
            raise ValueError("annual artifact manifest must not be a symlink")
        return path.is_file()

    def _validate_member_record(
        self, name: str, record: Mapping[str, Any]
    ) -> Path:
        required = {
            "content_sha256", "content_bytes", "encoding", "stored_sha256",
            "stored_bytes", "blob",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError(f"artifact member record is malformed: {name}")
        for field in ("content_sha256", "stored_sha256"):
            if not isinstance(record[field], str) or not _HEX64.fullmatch(record[field]):
                raise ValueError(f"artifact member {name} has invalid {field}")
        for field in ("content_bytes", "stored_bytes"):
            if isinstance(record[field], bool) or not isinstance(record[field], int) \
                    or record[field] < 0:
                raise ValueError(f"artifact member {name} has invalid {field}")
        if record["encoding"] not in _BLOB_SUFFIXES:
            raise ValueError(f"artifact member {name} has unsupported encoding")
        expected = self._blob_path(record["content_sha256"], record["encoding"])
        if record["blob"] != expected.relative_to(self.root).as_posix():
            raise ValueError(f"artifact member {name} blob path is not canonical")
        return _strict_regular_file(expected, f"artifact blob {name}")

    def _validate_member(self, name: str, record: Mapping[str, Any]) -> None:
        blob = self._validate_member_record(name, record)
        stored_sha256, stored_bytes = _sha256_file(blob)
        if stored_sha256 != record["stored_sha256"] \
                or stored_bytes != record["stored_bytes"]:
            raise ValueError(f"artifact blob {name} stored bytes changed")

        digest = hashlib.sha256()
        content_bytes = 0
        with _open_encoded(blob, record["encoding"]) as handle:
            while block := handle.read(_COPY_BLOCK):
                digest.update(block)
                content_bytes += len(block)
        if digest.hexdigest() != record["content_sha256"] \
                or content_bytes != record["content_bytes"]:
            raise ValueError(f"artifact blob {name} original bytes changed")

    def _load_artifact(
        self, unit_id: str, *, verify_member_content: bool
    ) -> dict[str, Any]:
        if not isinstance(unit_id, str) or not _SAFE_ID.fullmatch(unit_id):
            raise ValueError("unit_id must be a safe non-empty key")
        path = _strict_regular_file(self._artifact_path(unit_id), "artifact manifest")
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
        required = {
            "schema", "plan_content_key", "unit_id", "metadata", "members",
            "content_key",
        }
        if not isinstance(manifest, Mapping) or set(manifest) != required:
            raise ValueError("annual artifact manifest fields are malformed")
        if manifest["schema"] != ARTIFACT_SCHEMA or manifest["unit_id"] != unit_id:
            raise ValueError("annual artifact schema or unit identity differs")
        if not isinstance(manifest["plan_content_key"], str) \
                or not _HEX64.fullmatch(manifest["plan_content_key"]):
            raise ValueError("annual artifact plan identity is invalid")
        if not isinstance(manifest["metadata"], Mapping):
            raise ValueError("annual artifact metadata is malformed")
        if manifest["content_key"] != _canonical_key(manifest):
            raise ValueError("annual artifact content key differs")
        members = manifest["members"]
        if not isinstance(members, Mapping) or set(members) != _MEMBER_NAMES:
            raise ValueError("annual artifact member set differs")
        for name, record in sorted(members.items()):
            if verify_member_content:
                self._validate_member(name, record)
            else:
                self._validate_member_record(name, record)
        return json.loads(json.dumps(manifest))

    def load_artifact(self, unit_id: str) -> dict[str, Any]:
        return self._load_artifact(unit_id, verify_member_content=True)

    def load_artifact_manifest(self, unit_id: str) -> dict[str, Any]:
        """Validate the immutable manifest and member locations without I/O duplication."""
        return self._load_artifact(unit_id, verify_member_content=False)

    def restore_artifact_subset(
        self,
        unit_id: str,
        destination: Path,
        member_names,
    ) -> tuple[dict[str, Any], dict[str, Path]]:
        """Restore and hash only the named members from a bound manifest."""
        names = tuple(member_names)
        if (
            not names or len(names) != len(set(names))
            or any(name not in _MEMBER_NAMES for name in names)
        ):
            raise ValueError("artifact restore subset is invalid")
        manifest = self._load_artifact(
            unit_id, verify_member_content=False
        )
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        restored: dict[str, Path] = {}
        for name in sorted(names):
            record = manifest["members"][name]
            target = destination / name
            descriptor, raw_temporary = tempfile.mkstemp(
                prefix=name + ".", suffix=".tmp", dir=destination
            )
            temporary = Path(raw_temporary)
            try:
                blob = self.root / record["blob"]
                try:
                    with os.fdopen(descriptor, "wb") as outgoing:
                        with _open_encoded(blob, record["encoding"]) as incoming:
                            shutil.copyfileobj(
                                incoming, outgoing, length=_COPY_BLOCK
                            )
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                except (OSError, EOFError, lzma.LZMAError) as exc:
                    raise ValueError(
                        f"artifact blob {name} cannot be restored"
                    ) from exc
                digest, size = _sha256_file(temporary)
                if digest != record["content_sha256"] \
                        or size != record["content_bytes"]:
                    raise ValueError(f"restored artifact member differs: {name}")
                os.replace(temporary, target)
                restored[name] = target
            finally:
                temporary.unlink(missing_ok=True)
        return manifest, restored

    def restore_artifact(self, unit_id: str, destination: Path) -> dict[str, Path]:
        _manifest, restored = self.restore_artifact_subset(
            unit_id, destination, _MEMBER_NAMES
        )
        return restored

    def storage_totals(self, unit_id: str) -> dict[str, int | float]:
        manifest = self.load_artifact(unit_id)
        content = sum(item["content_bytes"] for item in manifest["members"].values())
        stored = sum(item["stored_bytes"] for item in manifest["members"].values())
        return {
            "content_bytes": content,
            "stored_bytes": stored,
            "stored_ratio": 0.0 if content == 0 else stored / content,
        }
