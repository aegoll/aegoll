"""Transaction history and the snapshot the engines read.

The engines never touch the database. They receive a `HistorySnapshot` -- a plain
value object -- which is what makes them pure and replayable (ADR-004). Swapping
SQLite for anything else means reimplementing `snapshot()` and nothing else.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable

from .domain import Verdict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id            TEXT PRIMARY KEY,
    at            TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    vendor_id     TEXT NOT NULL,
    resource      TEXT NOT NULL,
    amount_atomic INTEGER NOT NULL,
    -- What actually settled, when it differed from what was authorised. NULL means "not
    -- reported", which is not the same as "the same as authorised" and not the same as zero:
    -- every window sum falls back to `amount_atomic` for NULL, and a row that genuinely
    -- settled for nothing carries 0.
    settled_amount_atomic INTEGER,
    verdict       TEXT NOT NULL,
    settled       INTEGER NOT NULL DEFAULT 0,
    success       INTEGER NOT NULL DEFAULT 0,
    disputed      INTEGER NOT NULL DEFAULT 0,
    tx_hash       TEXT,
    channel       TEXT NOT NULL DEFAULT 'external'
);
CREATE INDEX IF NOT EXISTS ix_tx_channel ON transactions(channel);
CREATE INDEX IF NOT EXISTS ix_tx_at ON transactions(at);
CREATE INDEX IF NOT EXISTS ix_tx_vendor ON transactions(vendor_id);

CREATE TABLE IF NOT EXISTS vendors (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    first_seen TEXT NOT NULL,
    sanctioned INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class TxRecord:
    id: str
    at: datetime
    agent_id: str
    vendor_id: str
    resource: str
    amount_atomic: int
    verdict: str
    settled: bool
    success: bool
    disputed: bool
    tx_hash: str | None
    channel: str = "external"


@dataclass(frozen=True)
class VendorStats:
    """Everything the trust and risk engines know about one counterparty."""

    vendor_id: str
    first_seen: datetime | None
    settled_count: int
    failed_count: int
    disputed_count: int
    total_atomic: int
    last_seen: datetime | None
    resource_amounts: dict[str, list[int]] = field(default_factory=dict)

    @property
    def attempts(self) -> int:
        return self.settled_count + self.failed_count

    @property
    def success_rate(self) -> float | None:
        return None if self.attempts == 0 else self.settled_count / self.attempts

    @property
    def dispute_rate(self) -> float:
        return 0.0 if self.settled_count == 0 else self.disputed_count / self.settled_count

    def age_days(self, now: datetime) -> float:
        if self.first_seen is None:
            return 0.0
        return max(0.0, (now - self.first_seen).total_seconds() / 86400)

    @property
    def is_new(self) -> bool:
        return self.settled_count == 0


@dataclass(frozen=True)
class HistorySnapshot:
    """An immutable view of history at one instant, for one agent+vendor pair."""

    now: datetime
    agent_id: str
    spent_today_atomic: int
    spent_month_atomic: int
    spent_vendor_30d_atomic: int
    spent_resource_30d_atomic: int
    count_last_60s: int
    count_last_1h: int
    agent_amounts: tuple[int, ...]
    vendor: VendorStats

    # --- derived stats the risk engine uses -------------------------------
    @property
    def agent_mean_atomic(self) -> float:
        return mean(self.agent_amounts) if self.agent_amounts else 0.0

    @property
    def agent_stdev_atomic(self) -> float:
        return pstdev(self.agent_amounts) if len(self.agent_amounts) > 1 else 0.0

    def amount_zscore(self, amount_atomic: int) -> float | None:
        """How unusual this amount is for this agent. None when there's no baseline."""
        if len(self.agent_amounts) < 3:
            return None
        sd = self.agent_stdev_atomic
        if sd <= 0:
            return 0.0 if amount_atomic == self.agent_mean_atomic else 6.0
        return (amount_atomic - self.agent_mean_atomic) / sd

    def vendor_resource_median(self, resource: str) -> int | None:
        """The vendor's own historical price for this resource.

        Used to catch a seller silently repricing -- the same endpoint suddenly
        costing 10x is a signal no amount-threshold rule would notice.
        """
        seen = self.vendor.resource_amounts.get(resource) or []
        if not seen:
            return None
        ordered = sorted(seen)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) // 2


class Store:
    """SQLite-backed history. `:memory:` for tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(transactions)")}
        if "channel" not in cols:
            self._conn.execute(
                "ALTER TABLE transactions ADD COLUMN channel TEXT NOT NULL DEFAULT 'external'"
            )
        if "settled_amount_atomic" not in cols:
            # Nullable, and the fallback is `amount_atomic`. Rows written before settlement
            # amounts were tracked did not settle for zero; nothing was reported about them.
            self._conn.execute(
                "ALTER TABLE transactions ADD COLUMN settled_amount_atomic INTEGER"
            )
        if "intent_id" not in cols:
            # Nullable on purpose: rows written before intent existed were not
            # ungoverned-by-intent-and-checked, they were taken when the concept
            # did not exist. NULL says that; '' would claim they had no intent.
            self._conn.execute("ALTER TABLE transactions ADD COLUMN intent_id TEXT")

    def close(self) -> None:
        self._conn.close()

    # --- writes ------------------------------------------------------------
    def register_vendor(
        self, vendor_id: str, name: str = "", first_seen: datetime | None = None,
        sanctioned: bool = False,
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO vendors (id, name, first_seen, sanctioned) VALUES (?,?,?,?)",
            (
                vendor_id,
                name,
                (first_seen or datetime.now(timezone.utc)).isoformat(),
                int(sanctioned),
            ),
        )
        self._conn.commit()

    def record(
        self,
        *,
        tx_id: str,
        at: datetime,
        agent_id: str,
        vendor_id: str,
        resource: str,
        amount_atomic: int,
        verdict: Verdict | str,
        settled: bool = False,
        success: bool = False,
        disputed: bool = False,
        tx_hash: str | None = None,
        channel: str = "external",
        intent_id: str | None = None,
    ) -> None:
        self.register_vendor(vendor_id, first_seen=at)
        self._conn.execute(
            """INSERT OR REPLACE INTO transactions
               (id, at, agent_id, vendor_id, resource, amount_atomic, verdict,
                settled, success, disputed, tx_hash, channel, intent_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tx_id,
                at.isoformat(),
                agent_id,
                vendor_id,
                resource,
                int(amount_atomic),
                verdict.value if isinstance(verdict, Verdict) else str(verdict),
                int(settled),
                int(success),
                int(disputed),
                tx_hash,
                channel,
                intent_id,
            ),
        )
        self._conn.commit()

    def already_settled(self, tx_id: str) -> bool:
        """Has this request id already settled?

        The ledger writes with `INSERT OR REPLACE`, which is right for the
        authorize-then-settle lifecycle of *one* request and wrong for a second
        request arriving under an id that has already completed: the settled row
        would be silently overwritten, and the evidence of the first payment would
        be gone. Found by RT-EVID-004.
        """
        rows = self._rows(
            "SELECT 1 FROM transactions WHERE id=? AND settled=1 LIMIT 1", (tx_id,)
        )
        return bool(rows)

    def spent_under_intent(self, intent_id: str) -> int:
        """What has actually been spent under one intent, in atomic units.

        Settled and successful only, matching every other envelope: an authorised
        payment that never settled did not consume budget, and counting it would
        make an intent exhaust itself on failures.
        """
        row = self._rows(
            "SELECT COALESCE(SUM(COALESCE(settled_amount_atomic, amount_atomic)),0) AS s FROM transactions "
            "WHERE intent_id=? AND settled=1 AND success=1",
            (intent_id,),
        )[0]
        return int(row["s"])

    def mark_settled(
        self,
        tx_id: str,
        tx_hash: str | None,
        success: bool = True,
        amount_atomic: int | None = None,
    ) -> None:
        """Close a transaction, recording what actually settled if it differed.

        `amount_atomic` is what moved. It is stored separately from the authorised amount and
        is what every window sum then counts -- see `_SCHEMA`. Passing it was previously
        impossible, so a settlement for more than was authorised consumed the authorised figure
        and a cumulative envelope could be walked through by overspending at settlement.
        """
        if amount_atomic is not None:
            if amount_atomic < 0:
                raise ValueError(
                    f"a settlement cannot be for a negative amount ({amount_atomic}). "
                    "A refund is its own event, not a payment with a minus sign -- see "
                    "AEGS-0.1-ARITH-4."
                )
            self._conn.execute(
                "UPDATE transactions SET settled=1, success=?, tx_hash=?, "
                "settled_amount_atomic=? WHERE id=?",
                (int(success), tx_hash, amount_atomic, tx_id),
            )
        else:
            self._conn.execute(
                "UPDATE transactions SET settled=1, success=?, tx_hash=? WHERE id=?",
                (int(success), tx_hash, tx_id),
            )
        self._conn.commit()

    def mark_disputed(self, tx_id: str) -> None:
        self._conn.execute("UPDATE transactions SET disputed=1 WHERE id=?", (tx_id,))
        self._conn.commit()

    def seed(self, records: Iterable[dict[str, Any]]) -> None:
        """Bulk-load synthetic history for scenarios and tests."""
        for r in records:
            self.record(**r)

    # --- reads -------------------------------------------------------------
    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self._conn.execute(sql, params))

    def all_transactions(self, limit: int = 500) -> list[TxRecord]:
        rows = self._rows(
            "SELECT * FROM transactions ORDER BY at DESC LIMIT ?", (limit,)
        )
        return [self._to_record(r) for r in rows]

    @staticmethod
    def _to_record(r: sqlite3.Row) -> TxRecord:
        return TxRecord(
            id=r["id"],
            at=datetime.fromisoformat(r["at"]),
            agent_id=r["agent_id"],
            vendor_id=r["vendor_id"],
            resource=r["resource"],
            amount_atomic=int(r["amount_atomic"]),
            verdict=r["verdict"],
            settled=bool(r["settled"]),
            success=bool(r["success"]),
            disputed=bool(r["disputed"]),
            tx_hash=r["tx_hash"],
            channel=(r["channel"] if "channel" in r.keys() else "external"),
        )

    def vendor_stats(self, vendor_id: str, now: datetime) -> VendorStats:
        vrow = self._rows("SELECT * FROM vendors WHERE id=?", (vendor_id,))
        first_seen = datetime.fromisoformat(vrow[0]["first_seen"]) if vrow else None

        rows = self._rows(
            "SELECT * FROM transactions WHERE vendor_id=? ORDER BY at", (vendor_id,)
        )
        settled = failed = disputed = 0
        total = 0
        last_seen: datetime | None = None
        per_resource: dict[str, list[int]] = {}

        for r in rows:
            rec = self._to_record(r)
            last_seen = rec.at
            if rec.settled and rec.success:
                settled += 1
                total += rec.amount_atomic
                per_resource.setdefault(rec.resource, []).append(rec.amount_atomic)
            elif rec.verdict == Verdict.APPROVE.value and not rec.success:
                # Approved but never settled: a vendor-side or rail-side failure.
                failed += 1
            if rec.disputed:
                disputed += 1

        return VendorStats(
            vendor_id=vendor_id,
            first_seen=first_seen,
            settled_count=settled,
            failed_count=failed,
            disputed_count=disputed,
            total_atomic=total,
            last_seen=last_seen,
            resource_amounts=per_resource,
        )

    def snapshot(
        self,
        *,
        agent_id: str,
        vendor_id: str,
        resource: str,
        now: datetime,
        channel: str = "external",
    ) -> HistorySnapshot:
        """Everything the engines need, computed once, at one instant.

        Every aggregate is scoped to `channel`. Internal (token) spend and
        external (USDC) spend must never appear in each other's envelopes -- they
        are different currencies paid to different counterparties.
        """
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        d30 = now - timedelta(days=30)
        m60s = now - timedelta(seconds=60)
        h1 = now - timedelta(hours=1)

        def spent(where: str, params: tuple[Any, ...]) -> int:
            sql = (
                "SELECT COALESCE(SUM(COALESCE(settled_amount_atomic, amount_atomic)),0) AS s FROM transactions "
                f"WHERE settled=1 AND success=1 AND channel=? AND {where}"
            )
            return int(self._rows(sql, (channel, *params))[0]["s"])

        def count(where: str, params: tuple[Any, ...]) -> int:
            sql = f"SELECT COUNT(*) AS c FROM transactions WHERE channel=? AND {where}"
            return int(self._rows(sql, (channel, *params))[0]["c"])

        amounts = [
            int(r["amount_atomic"])
            for r in self._rows(
                "SELECT amount_atomic FROM transactions "
                "WHERE agent_id=? AND channel=? AND settled=1 AND success=1 "
                "ORDER BY at DESC LIMIT 200",
                (agent_id, channel),
            )
        ]

        return HistorySnapshot(
            now=now,
            agent_id=agent_id,
            spent_today_atomic=spent("agent_id=? AND at>=?", (agent_id, day_start.isoformat())),
            spent_month_atomic=spent(
                "agent_id=? AND at>=?", (agent_id, month_start.isoformat())
            ),
            spent_vendor_30d_atomic=spent(
                "agent_id=? AND vendor_id=? AND at>=?",
                (agent_id, vendor_id, d30.isoformat()),
            ),
            spent_resource_30d_atomic=spent(
                "agent_id=? AND resource=? AND at>=?",
                (agent_id, resource, d30.isoformat()),
            ),
            count_last_60s=count("agent_id=? AND at>=?", (agent_id, m60s.isoformat())),
            count_last_1h=count("agent_id=? AND at>=?", (agent_id, h1.isoformat())),
            agent_amounts=tuple(amounts),
            vendor=self.vendor_stats(vendor_id, now),
        )
