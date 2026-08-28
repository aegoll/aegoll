"""Engine 8 -- the audit journal.

Append-only JSONL where every record carries the hash of its predecessor, so the
sequence is tamper-evident: editing or deleting any past record breaks the chain
from that point onward and `verify()` reports exactly where.

This is deliberately not a database. An append-only file is easy to ship, easy to
diff, and easy for a third party to verify without our code.
"""

from __future__ import annotations

from ...hashing import HASH_HEX, digest
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ...domain import Decision, PaymentRequest, atomic_to_usd

#: What the first entry commits to. AEGS-0.1-EVID-2 requires a *declared*
#: genesis: without one the first entry has nothing to commit to, and a whole
#: journal could be dropped and replaced with a plausible chain of one.
GENESIS = "0" * HASH_HEX


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    at: str
    prev_hash: str
    entry_hash: str
    payload: dict[str, Any]

    @property
    def verdict(self) -> str:
        return str(self.payload.get("decision", {}).get("verdict", "?"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            **self.payload,
        }


def _hash_entry(seq: int, at: str, prev_hash: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(
        {"seq": seq, "at": at, "prev_hash": prev_hash, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return digest(blob)


def _vendor_payload(vendor: Any) -> dict[str, Any]:
    """The counterparty as it enters the hash chain. AEGS-0.1-CTRL-6a.

    `jurisdictionState` always travels; `jurisdiction` only when an operator declared something.
    Both are needed downstream, and the state is the one that cannot be reconstructed from the
    value: `None` is emitted for *asked and does not know* and would be indistinguishable from
    *never asked* without it.

    Adding a key changes the hash of entries written from now on and none of the entries already
    written, because each entry commits to its own payload. That is the point of the chain and
    not a migration.
    """
    payload: dict[str, Any] = {
        "id": vendor.id,
        "name": vendor.display,
        "sanctioned": vendor.sanctioned,
        "jurisdictionState": vendor.jurisdiction_state,
    }
    if vendor.jurisdiction_state != "undeclared":
        payload["jurisdiction"] = vendor.declared_jurisdiction
    return payload


class AuditLog:
    def __init__(self, path: str | Path, labels: dict[str, str] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Free-form provenance stamped onto every entry this instance writes --
        # which framework and provider produced the decision. Deliberately kept
        # out of the sqlite schema and out of `agent_id`: every treasury envelope
        # is agent-scoped, so labelling frameworks through `agent_id` would
        # silently split one shared budget into three.
        self.labels = dict(labels or {})

    # --- reads -------------------------------------------------------------
    def __iter__(self) -> Iterator[AuditEntry]:
        if not self.path.exists():
            return iter(())
        entries: list[AuditEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            entries.append(
                AuditEntry(
                    seq=int(raw["seq"]),
                    at=str(raw["at"]),
                    prev_hash=str(raw["prev_hash"]),
                    entry_hash=str(raw["entry_hash"]),
                    payload={
                        k: v
                        for k, v in raw.items()
                        if k not in ("seq", "at", "prev_hash", "entry_hash")
                    },
                )
            )
        return iter(entries)

    def entries(self) -> list[AuditEntry]:
        return list(iter(self))

    def head(self) -> tuple[int, str]:
        """(next sequence number, hash to chain from)."""
        last: AuditEntry | None = None
        for last in iter(self):  # noqa: B007 - we want the final value
            pass
        return (0, GENESIS) if last is None else (last.seq + 1, last.entry_hash)

    # --- writes ------------------------------------------------------------
    def append(
        self,
        request: PaymentRequest,
        decision: Decision,
        settlement: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
        identity: dict[str, Any] | None = None,
    ) -> AuditEntry:
        seq, prev = self.head()
        at = decision.decided_at.isoformat()

        payload: dict[str, Any] = {
            "agent": request.agent_id,
            "vendor": _vendor_payload(request.vendor),
            "transaction": {
                "id": request.id,
                "resource": request.resource,
                "amountUsd": float(atomic_to_usd(request.amount_atomic)),
                "purpose": request.purpose.value,
            },
            "decision": decision.as_dict(),
            "settlement": settlement,
        }
        if intent is not None:
            payload["intent"] = intent
        if identity is not None:
            payload["identity"] = identity
        if self.labels:
            payload["labels"] = dict(self.labels)

        entry_hash = _hash_entry(seq, at, prev, payload)
        record = {
            "seq": seq,
            "at": at,
            "prev_hash": prev,
            "entry_hash": entry_hash,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")

        return AuditEntry(seq, at, prev, entry_hash, payload)

    def attach_settlement(self, request_id: str, settlement: dict[str, Any]) -> bool:
        """Record a settlement as a *new* entry rather than editing the old one.

        The log is append-only; mutating a past record would break the chain, which
        is the whole point of having one.
        """
        seq, prev = self.head()
        at = datetime.now().astimezone().isoformat()
        payload = {"settlement_update": {"requestId": request_id, **settlement}}
        entry_hash = _hash_entry(seq, at, prev, payload)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "seq": seq,
                        "at": at,
                        "prev_hash": prev,
                        "entry_hash": entry_hash,
                        **payload,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
        return True

    # --- verification ------------------------------------------------------
    def verify(self) -> tuple[bool, list[str]]:
        """Walk the chain. Returns (ok, problems)."""
        problems: list[str] = []
        prev = GENESIS
        expected_seq = 0

        for entry in iter(self):
            if entry.seq != expected_seq:
                problems.append(
                    f"seq {entry.seq}: out of order, expected {expected_seq}"
                )
            if entry.prev_hash != prev:
                problems.append(
                    f"seq {entry.seq}: prev_hash {entry.prev_hash} does not match "
                    f"the previous entry's hash {prev} -- a record was altered or removed"
                )
            recomputed = _hash_entry(entry.seq, entry.at, entry.prev_hash, entry.payload)
            if recomputed != entry.entry_hash:
                problems.append(
                    f"seq {entry.seq}: content hash mismatch "
                    f"(stored {entry.entry_hash}, recomputed {recomputed}) -- record edited"
                )
            prev = entry.entry_hash
            expected_seq = entry.seq + 1

        return (not problems), problems
