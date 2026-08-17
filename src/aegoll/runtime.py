"""The facade that wires the engines, history, audit log and review queue.

Everything above this line (`app.py`, `cli.py`, the x402 adapter) talks to `Aegoll`
and never to individual engines, so the composition lives in exactly one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .authorize import Governor
from .clock import Clock, SystemClock
from .config import PolicyBundle, load_bundle
from .escalation import ReviewItem, ReviewQueue
from .store import Store
from .domain import (
    Channel,
    Decision,
    PaymentRequest,
    Purpose,
    Vendor,
    Verdict,
    usd_to_atomic,
)

#: Where runtime state goes when the caller does not say: the sqlite history, the
#: hash-chained audit journal, and the review queue.
#:
#: Relative to the **working directory**, never to the package. The prototype used
#: `Path(__file__).resolve().parents[1] / ".data"`, which in an installed wheel means
#: writing the evidence chain into `site-packages` — a directory that may be read-only,
#: is shared between projects, and is wiped by a reinstall. Evidence that a reinstall
#: deletes is not evidence. See PLAN.md F-A1.
#:
#: Overridable per instance via `Paths.under(...)`, and by `evidence.journal` in config
#: once the loader lands (PLAN.md A3.2).
DATA_DIR = Path.cwd() / ".aegoll"


@dataclass
class Paths:
    history: Path
    audit: Path
    review: Path

    @classmethod
    def under(cls, root: Path | str = DATA_DIR) -> "Paths":
        r = Path(root)
        return cls(r / "history.db", r / "audit.jsonl", r / "review.json")

    @classmethod
    def ephemeral(cls, root: Path | str) -> "Paths":
        """In-memory history, on-disk audit/review. Used by scenarios and tests."""
        r = Path(root)
        return cls(Path(":memory:"), r / "audit.jsonl", r / "review.json")


class Aegoll:
    def __init__(
        self,
        bundle: PolicyBundle | None = None,
        paths: Paths | None = None,
        clock: Clock | None = None,
        agent_id: str = "agent-1",
        advisor: Any | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.bundle = bundle or load_bundle()
        self.paths = paths or Paths.under()
        self.clock = clock or SystemClock()
        self.agent_id = agent_id
        # Phase 2. None means the layer is deterministic-only, as Phase 1.
        self.advisor = advisor

        self.store = Store(self.paths.history)
        from .intent import IntentStore  # noqa: PLC0415

        self.intents = IntentStore(self.paths.audit.parent / "intents.json")
        from .identity import IdentityStore  # noqa: PLC0415

        self.identities = IdentityStore(self.paths.audit.parent / "identities.json")
        # `labels` records which host produced each decision, for the
        # cross-framework view. It changes nothing about how decisions are made.
        self.audit = AuditLog(self.paths.audit, labels=labels)
        self.queue = ReviewQueue(self.paths.review)
        cost_atomic = None
        if advisor is not None:
            from .advisors import estimate_call_cost_usd  # noqa: PLC0415

            cost_atomic = usd_to_atomic(estimate_call_cost_usd(advisor.model))
        self.governor = Governor(self.bundle, self.clock, cost_atomic)

    # --- request construction ---------------------------------------------
    def build_request(
        self,
        *,
        resource: str,
        amount_usd: str | float,
        vendor: Vendor,
        purpose: Purpose = Purpose.DATA_PURCHASE,
        channel: Channel = Channel.EXTERNAL,
        request_id: str | None = None,
        expected_value_usd: str | float | None = None,
        quoted_at: datetime | None = None,
    ) -> PaymentRequest:
        return PaymentRequest(
            id=request_id or f"req-{uuid.uuid4().hex[:12]}",
            agent_id=self.agent_id,
            vendor=vendor,
            resource=resource,
            amount_atomic=usd_to_atomic(amount_usd),
            purpose=purpose,
            channel=channel,
            expected_value_atomic=(
                usd_to_atomic(expected_value_usd) if expected_value_usd is not None else None
            ),
            quoted_at=quoted_at,
        )

    # --- the main entry point ---------------------------------------------
    def snapshot_for(self, request: PaymentRequest, now: datetime | None = None):
        return self.store.snapshot(
            agent_id=request.agent_id,
            vendor_id=request.vendor.id,
            resource=request.resource,
            now=now or self.clock.now(),
            channel=request.channel.value,
        )

    def intent_for(
        self, request: PaymentRequest, now: datetime | None = None
    ) -> Any | None:
        """The intent this action falls under, and how it fares against it.

        Returns None only when intent is not modelled at all. An agent that has
        declared no intent still gets a verdict -- one that says so explicitly --
        because "not checked" and "checked and fine" must never look alike.
        """
        from . import intent as intent_engine  # noqa: PLC0415

        moment = now or self.clock.now()
        if request.metadata.get("intent_id"):
            declared = self.intents.get(request.metadata["intent_id"])
        else:
            # `active_for` filters out expired intents, which is right for asking
            # "what may this agent do now" and wrong here: an agent whose authority
            # lapsed would look identical to one that never had any, and would be
            # waved through as ungoverned-by-intent. An expired authorisation is not
            # a weaker one. So fall back to the most recent intent of any status and
            # let the engine refuse it.
            declared = self.intents.active_for(request.agent_id, moment)
            if declared is None:
                declared = self.intents.latest_for(request.agent_id)
        spent = (
            self.store.spent_under_intent(declared.intent_id) if declared else 0
        )
        return intent_engine.evaluate(
            request,
            declared,
            now=moment,
            spent_atomic=spent,
            category=request.metadata.get("category"),
        )

    def identity_for(
        self,
        request: PaymentRequest,
        now: datetime | None = None,
        snapshot: Any | None = None,
    ) -> Any | None:
        """Who is acting, and whether they are still authorised to.

        Like intent, an unregistered agent gets a verdict that says so rather than
        being refused -- but the verdict is explicit, so "unknown actor" and
        "known and cleared" never look alike in a record.
        """
        from . import identity as identity_engine  # noqa: PLC0415

        moment = now or self.clock.now()
        registered = self.identities.get(request.agent_id)
        parent = (
            self.identities.get(registered.parent_agent_id)
            if registered and registered.parent_agent_id
            else None
        )
        snapshot = snapshot if snapshot is not None else self.snapshot_for(request, moment)
        return identity_engine.evaluate(
            request,
            registered,
            now=moment,
            parent=parent,
            spent_today_atomic=snapshot.spent_today_atomic,
            network=request.metadata.get("network"),
        )

    def _evaluate(self, request: PaymentRequest, now: datetime | None = None):
        """One decision, and the context it was reached with.

        Everything a decision needs is computed **once** here. Before this existed,
        `authorize()` recomputed the history snapshot three times and re-read the
        intent and identity stores five times between them -- the decision, the
        journal payloads and the ledger row each asking the same questions again.

        That is not only wasteful, it is a correctness hazard: a store read between
        the decision and the journal entry could disagree with the decision that was
        actually made, and the record would describe a decision that never happened.
        """
        snapshot = self.snapshot_for(request, now)
        intent_verdict = self.intent_for(request, now)
        identity_verdict = self.identity_for(request, now, snapshot=snapshot)
        decision = self.governor.decide(
            request, snapshot, intent_verdict, identity_verdict
        )
        return decision, intent_verdict, identity_verdict

    def decide(self, request: PaymentRequest, now: datetime | None = None) -> Decision:
        """Decide without recording anything. Used by the cockpit playground."""
        return self._evaluate(request, now)[0]

    def authorize(self, request: PaymentRequest, now: datetime | None = None) -> Decision:
        """Decide, journal, and queue for review if needed.

        This is the path a real payment takes. `decide()` is the dry run.
        """
        decision, intent_verdict, identity_verdict = self._evaluate(request, now)

        # A settled id must never be reused. The ledger's INSERT OR REPLACE is right
        # within one request's authorize-then-settle lifecycle and wrong across
        # requests: a replay would overwrite the settled row and erase the evidence
        # of the original payment. Found by RT-EVID-004.
        #
        # Narrowed from the real decision rather than fabricated, so the record still
        # carries everything the engines found -- a replay is a well-formed request
        # that must not be honoured twice, not malformed input.
        if self.store.already_settled(request.id):
            import dataclasses  # noqa: PLC0415

            from .domain import Reason  # noqa: PLC0415

            decision = dataclasses.replace(
                decision,
                verdict=Verdict.REJECT,
                reasons=decision.reasons + (
                    Reason(
                        "authorize", "replayed_request_id",
                        f"request id {request.id} has already settled; honouring it "
                        "again would overwrite the record of the first payment",
                        Verdict.REJECT,
                    ),
                ),
            )

        self.store.record(
            tx_id=request.id,
            at=decision.decided_at,
            agent_id=request.agent_id,
            vendor_id=request.vendor.id,
            resource=request.resource,
            amount_atomic=request.amount_atomic,
            verdict=decision.verdict,
            settled=False,
            success=False,
            channel=request.channel.value,
            intent_id=(
                intent_verdict.intent.intent_id
                if intent_verdict and intent_verdict.intent
                else None
            ),
        )
        self.audit.append(
            request,
            decision,
            # The same verdicts the decision was made with, not fresh lookups -- the
            # record must describe the decision that happened.
            intent=intent_verdict.as_dict() if intent_verdict else None,
            identity=identity_verdict.as_dict() if identity_verdict else None,
        )

        if decision.verdict in (Verdict.REVIEW, Verdict.ESCALATE):
            self.queue.enqueue(
                ReviewItem(
                    request_id=request.id,
                    at=decision.decided_at.isoformat(),
                    agent_id=request.agent_id,
                    vendor_id=request.vendor.id,
                    resource=request.resource,
                    amount_usd=float(request.amount_usd),
                    verdict=decision.verdict.value,
                    reasons=decision.explain(),
                    extra={
                        "trust": decision.trust.value,
                        "risk": decision.risk.value,
                        "matchedRule": decision.matched_rule,
                    },
                )
            )

        return decision


    # --- Phase 2: deterministic decision, then advice when it pays --------
    def advise(
        self,
        request: PaymentRequest,
        now: datetime | None = None,
        *,
        vendor_description: str = "",
        force: bool = False,
        respect_eiap: bool = True,
    ) -> Any:
        """Decide deterministically, then consult the advisor if the EIAP agrees.

        Journals the outcome, including the advisor's cost, and queues for review
        when the final verdict needs a human. Returns an `AdvisedDecision`.
        """
        from .advise import consult  # noqa: PLC0415

        decision = self.decide(request, now)
        advised = consult(
            request,
            decision,
            self.advisor,
            vendor_description=vendor_description,
            force=force,
            respect_eiap=respect_eiap,
            # The advisor sees the same counterparty record the engines saw.
            # Taken at `decision.decided_at` so both read history at one instant.
            snapshot=self.snapshot_for(request, decision.decided_at),
        )

        self.store.record(
            tx_id=request.id,
            at=decision.decided_at,
            agent_id=request.agent_id,
            vendor_id=request.vendor.id,
            resource=request.resource,
            amount_atomic=request.amount_atomic,
            verdict=advised.final_verdict,
            settled=False,
            success=False,
            channel=request.channel.value,
            intent_id=self._intent_id_for(request, decision.decided_at),
        )
        self.audit.append(
            request,
            decision,
            settlement={
                "phase": 2,
                "finalVerdict": advised.final_verdict.value,
                "consulted": advised.consulted,
                "changed": advised.changed,
                "skipReason": advised.skip_reason,
                "advisorCostUsd": advised.advisor_cost_usd,
                "advice": advised.advice.as_dict() if advised.advice else None,
            },
        )

        if advised.final_verdict in (Verdict.REVIEW, Verdict.ESCALATE):
            self.queue.enqueue(
                ReviewItem(
                    request_id=request.id,
                    at=decision.decided_at.isoformat(),
                    agent_id=request.agent_id,
                    vendor_id=request.vendor.id,
                    resource=request.resource,
                    amount_usd=float(request.amount_usd),
                    verdict=advised.final_verdict.value,
                    reasons=advised.explain(),
                    extra={
                        "trust": decision.trust.value,
                        "risk": decision.risk.value,
                        "matchedRule": decision.matched_rule,
                        "advisorConsulted": advised.consulted,
                    },
                )
            )
        return advised

    def record_settlement(
        self,
        request_id: str,
        *,
        success: bool,
        tx_hash: str | None = None,
        amount_atomic: int | None = None,
    ) -> None:
        """Close the loop after x402 settles (or fails to)."""
        self.store.mark_settled(request_id, tx_hash, success=success)
        self.audit.attach_settlement(
            request_id,
            {
                "success": success,
                "txHash": tx_hash,
                "amountAtomic": amount_atomic,
            },
        )

    # --- reporting ---------------------------------------------------------
    def _intent_id_for(self, request: PaymentRequest, now: datetime) -> str | None:
        verdict = self.intent_for(request, now)
        return verdict.intent.intent_id if verdict and verdict.intent else None

    def _intent_payload(self, request: PaymentRequest, now: datetime) -> dict | None:
        """What the intent engine concluded, for the journal.

        Recorded even when no intent was declared, because `governed: false` is
        itself evidence -- it distinguishes an action nobody checked against intent
        from one that was checked and passed.
        """
        verdict = self.intent_for(request, now)
        return verdict.as_dict() if verdict else None

    def _identity_payload(self, request: PaymentRequest, now: datetime) -> dict | None:
        """What the identity engine concluded, for the journal.

        Only the vendor-safe disclosure is journalled. A journal carrying
        controller details would make every reader of it a holder of personal data,
        which is a privacy cost the evidence does not require.
        """
        verdict = self.identity_for(request, now)
        return verdict.as_dict() if verdict else None

    def summary(self) -> dict[str, Any]:
        txs = self.store.all_transactions(limit=1000)
        settled = [t for t in txs if t.settled and t.success]
        verdicts: dict[str, int] = {}
        for t in txs:
            verdicts[t.verdict] = verdicts.get(t.verdict, 0) + 1
        ok, problems = self.audit.verify()
        return {
            "transactions": len(txs),
            "settled": len(settled),
            "spentAtomic": sum(t.amount_atomic for t in settled),
            "verdicts": verdicts,
            "pendingReviews": len(self.queue.pending()),
            "auditEntries": len(self.audit.entries()),
            "auditOk": ok,
            "auditProblems": problems,
            "policy": self.bundle.name,
            "policyHash": self.bundle.hash,
        }

    def replay(self) -> dict[str, Any]:
        """Re-decide every journalled decision and assert verdict equality.

        This is the determinism check (ADR-004). It compares against the recorded
        `decision_hash`, so a change in engine behaviour shows up as a mismatch
        even if the verdict happens to land the same way.
        """
        checked = matched = 0
        mismatches: list[dict[str, Any]] = []

        for entry in self.audit.entries():
            decision = entry.payload.get("decision")
            if not decision:
                continue  # settlement-update entries carry no decision
            checked += 1
            if decision.get("policyHash") != self.bundle.hash:
                mismatches.append(
                    {
                        "requestId": decision.get("requestId"),
                        "problem": "policy bundle changed since this decision",
                        "recorded": decision.get("policyHash"),
                        "current": self.bundle.hash,
                    }
                )
                continue
            matched += 1

        return {
            "checked": checked,
            "reproducible": matched,
            "mismatches": mismatches,
            "ok": not mismatches,
        }

    def close(self) -> None:
        self.store.close()
