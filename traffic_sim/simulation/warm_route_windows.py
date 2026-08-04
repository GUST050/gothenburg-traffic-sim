"""Bounded route shards for chained SUMO state extension.

Loading a full multi-day route file beside an existing SUMO state makes every
parsed route definition part of the next state. Repeating that at each
checkpoint grows the state and eventually changes execution. This cache parses
the immutable, departure-sorted vehicle file once and materializes only the
half-open departure window needed by an extension: ``[previous, target)``.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


_COPY_BLOCK = 1024 * 1024
_DEFINITION_TAGS = frozenset({
    "vType", "vTypeDistribution", "route", "routeDistribution",
})


class WarmRouteWindowCache:
    """Create exact aligned route windows from one immutable route file."""

    def __init__(self, source: Path, root: Path, *, alignment_s: int = 900) -> None:
        self.source = Path(source)
        self.root = Path(root)
        if self.source.is_symlink() or not self.source.is_file():
            raise ValueError("warm route source must be a regular non-symlink file")
        if isinstance(alignment_s, bool) or not isinstance(alignment_s, int) \
                or alignment_s <= 0:
            raise ValueError("warm route alignment must be a positive integer")
        self.alignment_s = alignment_s
        self._built = False
        self._definitions: tuple[bytes, ...] = ()
        self._buckets: frozenset[int] = frozenset()

    def _fragment(self, bucket: int) -> Path:
        return self.root / "fragments" / f"{bucket:08d}.xmlfrag"

    def _build(self) -> None:
        if self._built:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        fragments = self.root / "fragments"
        fragments.mkdir()
        definitions: list[bytes] = []
        buckets: set[int] = set()
        current_bucket = None
        current_handle = None
        last_depart = -math.inf
        depth = 0
        try:
            for event, element in ET.iterparse(
                self.source, events=("start", "end")
            ):
                if event == "start":
                    depth += 1
                    if depth == 1 and element.tag != "routes":
                        raise ValueError("warm route source root must be <routes>")
                    continue
                if depth == 2:
                    tag = element.tag
                    element.tail = None
                    if tag == "vehicle":
                        raw_depart = element.get("depart")
                        try:
                            depart = float(raw_depart)
                        except (TypeError, ValueError) as exc:
                            raise ValueError(
                                "warm route vehicle depart must be numeric"
                            ) from exc
                        if not math.isfinite(depart) or depart < 0:
                            raise ValueError(
                                "warm route vehicle depart must be finite and non-negative"
                            )
                        if depart < last_depart:
                            raise ValueError(
                                "warm route vehicles must be departure-sorted"
                            )
                        last_depart = depart
                        bucket = int(depart // self.alignment_s)
                        if bucket != current_bucket:
                            if current_handle is not None:
                                current_handle.flush()
                                os.fsync(current_handle.fileno())
                                current_handle.close()
                            current_bucket = bucket
                            buckets.add(bucket)
                            current_handle = self._fragment(bucket).open("wb")
                        current_handle.write(ET.tostring(element, encoding="utf-8"))
                        current_handle.write(b"\n")
                    elif tag in _DEFINITION_TAGS:
                        definitions.append(ET.tostring(element, encoding="utf-8"))
                    else:
                        raise ValueError(
                            f"unsupported top-level warm route element: {tag}"
                        )
                    element.clear()
                depth -= 1
        except BaseException:
            shutil.rmtree(self.root, ignore_errors=True)
            raise
        finally:
            if current_handle is not None and not current_handle.closed:
                current_handle.flush()
                os.fsync(current_handle.fileno())
                current_handle.close()
        self._definitions = tuple(definitions)
        self._buckets = frozenset(buckets)
        self._built = True

    def materialize(self, begin_s: int, end_s: int, destination: Path) -> Path:
        """Write vehicles departing in ``[begin_s, end_s)`` atomically."""
        for name, value in (("begin_s", begin_s), ("end_s", end_s)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"warm route {name} must be a non-negative integer")
            if value % self.alignment_s:
                raise ValueError(f"warm route {name} must align to {self.alignment_s}s")
        if end_s <= begin_s:
            raise ValueError("warm route window must have positive duration")
        self._build()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw = tempfile.mkstemp(
            prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(raw)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(b"<?xml version='1.0' encoding='utf-8'?>\n<routes>\n")
                for definition in self._definitions:
                    output.write(definition)
                    output.write(b"\n")
                first = begin_s // self.alignment_s
                last = end_s // self.alignment_s
                for bucket in range(first, last):
                    if bucket not in self._buckets:
                        continue
                    with self._fragment(bucket).open("rb") as incoming:
                        shutil.copyfileobj(incoming, output, length=_COPY_BLOCK)
                output.write(b"</routes>\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
