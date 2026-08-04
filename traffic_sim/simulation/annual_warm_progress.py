"""Transactional progress registry for the 100k+ annual warm work units."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.simulation.annual_warm_plan import (
    iter_work_units,
    validate_annual_warm_plan,
)


SCHEMA = "annual_warm_progress_v2"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AnnualWarmProgressStore:
    """Small O(1) lifecycle updates with SQLite durability and exact identity."""

    def __init__(self, path: Path, plan: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self.plan = validate_annual_warm_plan(plan)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        existed = self.path.exists() or self.path.is_symlink()
        if existed and (self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("annual progress path is not a regular file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_units (
                    ordinal INTEGER PRIMARY KEY,
                    unit_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    demand_build_key TEXT NOT NULL,
                    checkpoint_s INTEGER NOT NULL,
                    seed INTEGER NOT NULL,
                    variant TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending', 'running', 'succeeded', 'failed')
                    ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    artifact_content_key TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS work_units_state_ordinal
                    ON work_units(state, ordinal);
                CREATE INDEX IF NOT EXISTS work_units_demand_state
                    ON work_units(demand_build_key, state, ordinal);
            """)
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            count = db.execute("SELECT COUNT(*) FROM work_units").fetchone()[0]
            if metadata:
                if metadata != {
                    "schema": SCHEMA,
                    "plan_content_key": self.plan["content_key"],
                    "unit_count": str(self.plan["coverage"]["state_requests"]),
                }:
                    raise ValueError("annual progress database belongs to another plan")
                if count != self.plan["coverage"]["state_requests"]:
                    raise ValueError("annual progress database unit count differs")
                self._verify_rows(db)
                return
            if existed:
                raise ValueError(
                    "existing annual progress database lacks bound metadata"
                )
            if count:
                raise ValueError("annual progress database has units without metadata")
            db.executemany(
                """INSERT INTO work_units (
                    ordinal, unit_id, request_id, demand_build_key,
                    checkpoint_s, seed, variant, state, attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0)""",
                (
                    (
                        ordinal,
                        unit["unit_id"],
                        unit["request_id"],
                        unit["demand_build_key"],
                        unit["checkpoint_s"],
                        unit["seed"],
                        unit["variant"],
                    )
                    for ordinal, unit in enumerate(iter_work_units(self.plan))
                ),
            )
            db.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("schema", SCHEMA),
                    ("plan_content_key", self.plan["content_key"]),
                    ("unit_count", str(self.plan["coverage"]["state_requests"])),
                ),
            )

    def _verify_rows(self, db: sqlite3.Connection) -> None:
        """Recompute every immutable row and validate lifecycle consistency."""
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"annual progress database integrity failed: {integrity}")
        fields = (
            "ordinal", "unit_id", "request_id", "demand_build_key",
            "checkpoint_s", "seed", "variant", "state", "attempts",
            "artifact_content_key", "error",
        )
        rows = db.execute(
            "SELECT " + ", ".join(fields) + " FROM work_units ORDER BY ordinal"
        )
        expected_units = iter(iter_work_units(self.plan))
        count = 0
        for count, row in enumerate(rows, start=1):
            try:
                expected = next(expected_units)
            except StopIteration as exc:
                raise ValueError("annual progress database has extra units") from exc
            ordinal = count - 1
            immutable = {
                "ordinal": ordinal,
                "unit_id": expected["unit_id"],
                "request_id": expected["request_id"],
                "demand_build_key": expected["demand_build_key"],
                "checkpoint_s": expected["checkpoint_s"],
                "seed": expected["seed"],
                "variant": expected["variant"],
            }
            if any(row[field] != value for field, value in immutable.items()):
                raise ValueError(
                    f"annual progress unit identity differs at ordinal {ordinal}"
                )
            state = row["state"]
            attempts = row["attempts"]
            artifact = row["artifact_content_key"]
            error = row["error"]
            if state not in {"pending", "running", "succeeded", "failed"}:
                raise ValueError(
                    f"annual progress state is invalid at ordinal {ordinal}"
                )
            if isinstance(attempts, bool) or not isinstance(attempts, int) \
                    or attempts < 0:
                raise ValueError(
                    f"annual progress attempts are invalid at ordinal {ordinal}"
                )
            if state == "pending" and (
                attempts != 0 or artifact is not None or error is not None
            ):
                raise ValueError("pending annual progress unit is inconsistent")
            if state == "running" and (
                attempts < 1 or artifact is not None or error is not None
            ):
                raise ValueError("running annual progress unit is inconsistent")
            if state == "succeeded" and (
                attempts < 1 or not isinstance(artifact, str)
                or not _HEX64.fullmatch(artifact) or error is not None
            ):
                raise ValueError("succeeded annual progress unit is inconsistent")
            if state == "failed" and (
                attempts < 1 or artifact is not None
                or not isinstance(error, str) or not error.strip()
            ):
                raise ValueError("failed annual progress unit is inconsistent")
        try:
            next(expected_units)
        except StopIteration:
            pass
        else:
            raise ValueError("annual progress database is missing units")

    def verify(self) -> None:
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("annual progress database is missing or not regular")
        self.initialize()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def selectable(
        self,
        *,
        limit: int | None = None,
        demand_build_key: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("population limit must be positive or None")
        where = "state != 'succeeded'"
        parameters: list[Any] = []
        if demand_build_key is not None:
            where += " AND demand_build_key = ?"
            parameters.append(demand_build_key)
        query = "SELECT * FROM work_units WHERE " + where + " ORDER BY ordinal"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as db:
            return tuple(self._row(row) for row in db.execute(query, parameters))

    def mark_running(self, unit_id: str) -> dict[str, Any]:
        return self.mark_running_many((unit_id,))[unit_id]

    def mark_running_many(
        self, unit_ids: tuple[str, ...] | list[str]
    ) -> dict[str, dict[str, Any]]:
        unit_ids = tuple(unit_ids)
        if not unit_ids or len(unit_ids) != len(set(unit_ids)):
            raise ValueError("annual running batch must contain unique units")
        with self._connect() as db:
            for unit_id in unit_ids:
                cursor = db.execute(
                    """UPDATE work_units
                       SET state='running', attempts=attempts+1,
                           artifact_content_key=NULL, error=NULL
                       WHERE unit_id=?
                         AND state IN ('pending','running','failed')""",
                    (unit_id,),
                )
                if cursor.rowcount != 1:
                    raise ValueError("annual unit cannot enter running state")
            placeholders = ",".join("?" for _ in unit_ids)
            rows = db.execute(
                "SELECT * FROM work_units WHERE unit_id IN ("
                + placeholders + ")",
                unit_ids,
            )
            return {
                row["unit_id"]: self._row(row) for row in rows
            }

    def mark_succeeded(self, unit_id: str, artifact_content_key: str) -> None:
        if not isinstance(artifact_content_key, str) or not _HEX64.fullmatch(
            artifact_content_key
        ):
            raise ValueError("annual success requires an exact artifact key")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE work_units
                   SET state='succeeded', artifact_content_key=?, error=NULL
                   WHERE unit_id=? AND state='running'""",
                (artifact_content_key, unit_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("only a running annual unit may succeed")

    def mark_failed(self, unit_id: str, error: str) -> None:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("annual failure requires a non-empty error")
        stored_error = error.strip()[:1000]
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE work_units
                   SET state='failed', artifact_content_key=NULL, error=?
                   WHERE unit_id=? AND state='running'""",
                (stored_error, unit_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("only a running annual unit may fail")

    def finish_batch(
        self,
        *,
        succeeded: Mapping[str, str],
        failed: Mapping[str, str],
    ) -> None:
        """Durably finish one dependency-ready batch in one transaction."""
        succeeded = dict(succeeded)
        failed = dict(failed)
        if not succeeded and not failed:
            raise ValueError("annual finished batch must not be empty")
        if set(succeeded) & set(failed):
            raise ValueError("annual finished batch outcomes overlap")
        for artifact in succeeded.values():
            if not isinstance(artifact, str) or not _HEX64.fullmatch(artifact):
                raise ValueError("annual success requires an exact artifact key")
        for error in failed.values():
            if not isinstance(error, str) or not error.strip():
                raise ValueError("annual failure requires a non-empty error")
        with self._connect() as db:
            for unit_id, artifact in succeeded.items():
                cursor = db.execute(
                    """UPDATE work_units
                       SET state='succeeded', artifact_content_key=?, error=NULL
                       WHERE unit_id=? AND state='running'""",
                    (artifact, unit_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("only a running annual unit may succeed")
            for unit_id, error in failed.items():
                cursor = db.execute(
                    """UPDATE work_units
                       SET state='failed', artifact_content_key=NULL, error=?
                       WHERE unit_id=? AND state='running'""",
                    (error.strip()[:1000], unit_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("only a running annual unit may fail")

    def summary(self) -> dict[str, Any]:
        with self._connect() as db:
            counts = {
                row["state"]: row["count"]
                for row in db.execute(
                    "SELECT state, COUNT(*) AS count FROM work_units GROUP BY state"
                )
            }
            attempts = db.execute(
                "SELECT COALESCE(SUM(attempts), 0) FROM work_units"
            ).fetchone()[0]
        total = self.plan["coverage"]["state_requests"]
        return {
            "plan_content_key": self.plan["content_key"],
            "total": total,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "attempts": attempts,
            "complete": counts.get("succeeded", 0) == total,
        }
