"""Diagnostic phase accounting for evidence I/O: bytes, not arithmetic.

Step 3 measured a solve and found that a cache hit already skips the MILP.
Step 4 asks the same question of the bytes: how much of a passage build is
reading, compressing, writing, hashing, verifying and publishing evidence.

This module only measures. It is INERT unless a tool installs a collector,
and the installation is context-local and always undone, so nothing here can
leak into a production run, into any identity, or into another profile
running beside it. The module deliberately lives outside
``demand_source_paths`` and outside ``replay_source_sha256`` -- adding a
diagnostic to either inventory would change what every stored archive and
every saved replay claims about the code that produced it.

Two accounting rules earn their own machinery:

* A parent reports wall time MINUS its direct children, so instrumenting a
  nested call can never make the same second count twice.
* A parent whose children ran AT THE SAME TIME reports ``exclusive_s: None``
  and publishes its measured region wall time plus the children's sum and max.
  Descendants remain diagnostic detail and add no second wall contribution.
  Retention compresses up to three files in a thread pool; adding those as if
  they had run in sequence would invent wall time that never elapsed.
"""
from __future__ import annotations

import functools
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Sequence

#: Installed only by a diagnostic tool, only for the duration of one call.
_COLLECTOR: ContextVar[Any] = ContextVar('io_phase_collector', default=None)
#: The open phases of the current context, outermost first.
_STACK: ContextVar[tuple] = ContextVar('io_phase_stack', default=())


class _Frame:
    """One open phase. Shared by reference with any worker thread."""

    __slots__ = ('name', 'started', 'concurrent', 'inside_concurrent',
                 'children_s', 'children_max_s')

    def __init__(self, name: str, concurrent: bool,
                 inside_concurrent: bool = False) -> None:
        self.name = name
        self.concurrent = concurrent
        self.inside_concurrent = inside_concurrent
        self.started = time.perf_counter()
        self.children_s = 0.0
        self.children_max_s = 0.0


class PhaseCollector:
    """Thread-safe accumulator for one measured call.

    Retention runs its children in a pool, so every mutation is guarded. The
    collector is passed explicitly rather than discovered, which is what lets
    two profiles run side by side without meeting.
    """

    def __init__(self, unmeasured_categories: Sequence[str] = ()) -> None:
        self._lock = threading.Lock()
        self._phases: dict[str, dict] = {}
        # Counters answer "how many times", which is the step-5 question:
        # a repeated validation costs the same whether or not it is slow.
        self._counters: dict[str, int] = {}
        self._unique: dict[str, set] = {}
        # Identities of what was produced, so a measured run can be proved
        # byte-identical to an unmeasured one without keeping the artifacts.
        self._digests: dict[str, str] = {}
        self.unmeasured_categories = list(unmeasured_categories)

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(amount)

    def record_digest(self, name: str, value: str) -> None:
        with self._lock:
            self._digests[name] = value

    def count_unique(self, name: str, value) -> None:
        """Record an occurrence AND whether the value was already seen.

        Repetition is the finding: five validations of one path is a very
        different fact from one validation of five paths.
        """
        with self._lock:
            events = f'{name}_events'
            self._counters[events] = self._counters.get(events, 0) + 1
            self._unique.setdefault(name, set()).add(value)

    def _entry(self, name: str) -> dict:
        return self._phases.setdefault(name, {
            'calls': 0, 'inclusive_s': 0.0, 'exclusive_s': 0.0,
            'children_sum_s': 0.0, 'children_max_s': 0.0,
            'wall_contribution_s': 0.0, 'concurrent': False,
            'inside_concurrent': False, 'parents': set(), 'bytes': {},
        })

    def close(self, frame: _Frame, parent: _Frame | None,
              elapsed: float) -> None:
        with self._lock:
            entry = self._entry(frame.name)
            entry['calls'] += 1
            entry['inclusive_s'] += elapsed
            entry['parents'].add(parent.name if parent is not None else None)
            entry['children_sum_s'] += frame.children_s
            entry['children_max_s'] = max(entry['children_max_s'],
                                          frame.children_max_s)
            entry['inside_concurrent'] |= frame.inside_concurrent
            if frame.concurrent:
                entry['concurrent'] = True
                entry['exclusive_s'] = None
            elif entry['exclusive_s'] is not None:
                entry['exclusive_s'] += max(elapsed - frame.children_s, 0.0)
            # A concurrent boundary contributes its measured wall duration.
            # Work below that boundary remains visible as diagnostic thread
            # time, but contributes zero to the global wall-time ranking.
            if not frame.inside_concurrent:
                if frame.concurrent:
                    entry['wall_contribution_s'] += elapsed
                else:
                    entry['wall_contribution_s'] += max(
                        elapsed - frame.children_s, 0.0)
            if parent is not None:
                parent.children_s += elapsed
                parent.children_max_s = max(parent.children_max_s, elapsed)

    def add_bytes(self, name: str, counts: dict[str, int]) -> None:
        with self._lock:
            totals = self._entry(name)['bytes']
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + int(value)

    def report(self, *, root: str | None = None) -> dict:
        """A JSON-safe account. Ranking uses measured wall contribution."""
        with self._lock:
            phases = {}
            for name, entry in self._phases.items():
                parents = sorted(p for p in entry['parents'] if p is not None)
                if not entry['parents'] - {None}:
                    parent: Any = None
                elif len(parents) == 1:
                    parent = parents[0]
                else:
                    parent = parents
                phases[name] = {
                    'calls': entry['calls'],
                    'inclusive_s': round(entry['inclusive_s'], 6),
                    'exclusive_s': (None if entry['exclusive_s'] is None
                                    else round(entry['exclusive_s'], 6)),
                    'concurrent': entry['concurrent'],
                    'children_sum_s': round(entry['children_sum_s'], 6),
                    'children_max_s': round(entry['children_max_s'], 6),
                    'wall_contribution_s': round(
                        entry['wall_contribution_s'], 6),
                    'inside_concurrent': entry['inside_concurrent'],
                    'parent': parent,
                    'bytes': dict(entry['bytes']),
                }
            unmeasured = list(self.unmeasured_categories)

        def contribution(entry: dict) -> float:
            return entry['wall_contribution_s']

        def basis(entry: dict) -> str:
            if entry['inside_concurrent'] and contribution(entry) == 0:
                return 'concurrent_detail_only'
            if entry['inside_concurrent']:
                return 'mixed_exclusive_and_concurrent_detail'
            if entry['concurrent']:
                return 'concurrent_region_wall'
            return 'exclusive'

        total = sum(contribution(entry) for entry in phases.values()) or 1.0
        ranking = [
            {'phase': name,
             'wall_s': round(contribution(entry), 6),
             'share_percent': round(contribution(entry) / total * 100, 2),
             'basis': basis(entry)}
            for name, entry in sorted(
                phases.items(), key=lambda item: -contribution(item[1]))
            if contribution(entry) > 0
        ]
        with self._lock:
            counters = dict(self._counters)
            digests = dict(self._digests)
            unique_counts = {name: len(values)
                             for name, values in self._unique.items()}
        report = {
            'schema_version': 1,
            'phases': phases,
            'ranking': ranking,
            'counters': counters,
            'unique_counts': unique_counts,
            'digests': digests,
            'unmeasured_categories': unmeasured,
        }
        if root is not None:
            report['root'] = root
            root_entry = phases.get(root) or {}
            report['residual_s'] = root_entry.get('exclusive_s')
        return report


def current_collector():
    """The collector installed in this context, or None in production."""
    return _COLLECTOR.get()


@contextmanager
def observe(collector: PhaseCollector):
    """Install a collector for exactly this call, whatever happens."""
    token = _COLLECTOR.set(collector)
    stack_token = _STACK.set(())
    try:
        yield collector
    finally:
        _STACK.reset(stack_token)
        _COLLECTOR.reset(token)


@contextmanager
def phase(name: str, *, concurrent: bool = False):
    """Time one phase. Costs a single ``is None`` check when unobserved."""
    collector = _COLLECTOR.get()
    if collector is None:
        yield
        return
    stack = _STACK.get()
    parent = stack[-1] if stack else None
    inside_concurrent = any(
        ancestor.concurrent or ancestor.inside_concurrent
        for ancestor in stack)
    frame = _Frame(name, concurrent, inside_concurrent)
    token = _STACK.set(stack + (frame,))
    try:
        yield frame
    finally:
        elapsed = time.perf_counter() - frame.started
        _STACK.reset(token)
        collector.close(frame, parent, elapsed)


def add_bytes(*, phase_name: str | None = None, **counts: int) -> None:
    """Attribute moved bytes to the open phase, or to a named one."""
    collector = _COLLECTOR.get()
    if collector is None or not counts:
        return
    if phase_name is None:
        stack = _STACK.get()
        if not stack:
            return
        phase_name = stack[-1].name
    collector.add_bytes(phase_name, counts)


def in_current_context(function: Callable) -> Callable:
    """Wrap a callable so a worker thread sees this context's measurement.

    ``ContextVar`` values do not cross a thread boundary: a pool worker starts
    from the defaults and would silently record nothing. Capturing the two
    values and setting them inside the worker is explicit, needs no reusable
    ``Context`` object (which may not be entered twice), and returns the
    function unchanged when nothing is being measured.
    """
    collector = _COLLECTOR.get()
    if collector is None:
        return function
    stack = _STACK.get()

    @functools.wraps(function)
    def runner(*args, **kwargs):
        collector_token = _COLLECTOR.set(collector)
        stack_token = _STACK.set(stack)
        try:
            return function(*args, **kwargs)
        finally:
            _STACK.reset(stack_token)
            _COLLECTOR.reset(collector_token)

    return runner


class _MeasuredStream:
    """Time and count one direction of a streaming copy.

    ``shutil.copyfileobj`` reads, compresses and writes in one pass, so those
    three costs are not separable by wrapping the CALL. They are separable at
    the stream: timing each ``read`` and each ``write`` leaves compression as
    the remainder, and nothing about the bytes produced changes.
    """

    __slots__ = ('_stream', '_phase', '_key', '_elapsed', '_bytes')

    def __init__(self, stream, phase_name: str, key: str) -> None:
        self._stream = stream
        self._phase = phase_name
        self._key = key
        self._elapsed = 0.0
        self._bytes = 0

    @property
    def elapsed_s(self) -> float:
        return self._elapsed

    def read(self, size=-1):
        started = time.perf_counter()
        chunk = self._stream.read(size)
        self._elapsed += time.perf_counter() - started
        self._bytes += len(chunk)
        return chunk

    def write(self, data):
        started = time.perf_counter()
        written = self._stream.write(data)
        self._elapsed += time.perf_counter() - started
        self._bytes += len(data)
        return written

    def flush(self):
        return self._stream.flush()

    def publish(self) -> None:
        collector = _COLLECTOR.get()
        if collector is None:
            return
        collector.add_bytes(self._phase, {self._key: self._bytes})


def measured_stream(stream, phase_name: str, key: str):
    """Wrap a stream only while measuring; production gets the raw object."""
    if _COLLECTOR.get() is None:
        return stream
    return _MeasuredStream(stream, phase_name, key)


def stream_elapsed(stream) -> float:
    """Seconds this stream itself spent in read/write, 0 when unmeasured."""
    return stream.elapsed_s if isinstance(stream, _MeasuredStream) else 0.0


def stream_bytes(stream) -> int:
    """Bytes this stream moved, 0 when unmeasured."""
    return stream._bytes if isinstance(stream, _MeasuredStream) else 0


def record_derived(name: str, seconds: float, **counts: int) -> None:
    """Record a phase whose duration was measured by instrumentation.

    A streaming copy has no nesting to hang a ``with`` block on: reading,
    compressing and writing happen inside one call. Timing the streams gives
    the two ends; this records them as children of the open phase, so the
    parent's exclusive time becomes exactly the remainder -- the compression
    -- and no second is counted twice.
    """
    collector = _COLLECTOR.get()
    if collector is None:
        return
    stack = _STACK.get()
    parent = stack[-1] if stack else None
    inside_concurrent = bool(
        parent and (parent.concurrent or parent.inside_concurrent))
    collector.close(_Frame(name, False, inside_concurrent), parent,
                    max(seconds, 0.0))
    if counts:
        collector.add_bytes(name, counts)


def count(name: str, amount: int = 1) -> None:
    """Count one diagnostic event; a no-op in production."""
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.count(name, amount)


def count_unique(name: str, value) -> None:
    """Count an event and remember whether its value repeated."""
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.count_unique(name, value)


def record_digest(name: str, value: str) -> None:
    """Record what a measured run produced; a no-op in production."""
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.record_digest(name, value)
