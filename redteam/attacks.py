"""The attack catalogue — what to try to break, and what a correct defence looks like.

Declared in Python rather than JSON, and that is the opposite of the choice made for
`conformance/`. The two suites answer different questions:

* **Conformance** asks *does an implementation satisfy the standard?* It must run
  against systems that share none of our code, so its cases are data.
* **Red-team** asks *can I break this specific implementation?* It is white-box by
  nature — it manipulates clocks, edits journals, forges request ids — and pretending
  otherwise would mean an attack vocabulary so general it could express nothing sharp.

## The scoring rule

Every attack declares the control that *should* stop it. Three outcomes:

* **DEFENDED** — refused, and by the control that was supposed to refuse it
* **DEFENDED_BY_ACCIDENT** — refused, but by something else. **Counted as a finding,
  not a pass.** A structuring attack that happens to trip a velocity counter is not
  defended: paced differently it succeeds, and the control the attack targets does
  not exist. Treating that as a pass is how a system certifies protection it lacks.
* **UNDEFENDED** — it worked

`expected` records what we *believe* before running. An attack whose result differs
from its expectation is the interesting case in either direction: an unexpected
UNDEFENDED is a vulnerability, and an unexpected DEFENDED means we did not understand
our own system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Attack:
    id: str
    title: str
    threat_class: str
    description: str
    #: The control that should refuse this, as a reason `source`. None means the
    #: attack should fail structurally rather than by a governance decision -- an
    #: overflow that cannot happen, a hash that cannot be forged.
    defence_source: str | None
    #: What we expect before running. Being wrong here is itself a result.
    expected: str = "DEFENDED"
    #: Free-form; the runner interprets these.
    params: dict[str, Any] = field(default_factory=dict)
    #: Why this attack is worth running, for a reader who does not already agree.
    rationale: str = ""


CATALOGUE: tuple[Attack, ...] = (
    # --- numeric --------------------------------------------------------
    Attack(
        "RT-NUM-001", "Negative amount", "numeric",
        "Request a payment of -$1000. If a negative amount passes, every envelope "
        "check inverts: spending becomes earning and a budget becomes a floor.",
        defence_source="treasury", expected="DEFENDED",
        params={"amount": "-1000.00"},
        rationale="The cheapest possible attack on integer money arithmetic.",
    ),
    Attack(
        "RT-NUM-002", "Zero amount", "numeric",
        "A zero-value request. Should be permitted but must not create history that "
        "shifts later statistics -- a run of zeros could flatten the amount z-score "
        "and make a large purchase look ordinary.",
        defence_source=None, expected="DEFENDED",
        params={"amount": "0"},
        rationale="Tests whether free actions can be used to poison the risk baseline.",
    ),
    Attack(
        "RT-NUM-003", "Sub-atomic precision", "numeric",
        "$0.0000001 -- below USDC's six decimals. It must round deterministically "
        "and in the direction that does not favour the spender.",
        defence_source=None, expected="DEFENDED",
        params={"amount": "0.0000001"},
        rationale="Rounding that favours the spender is a slow leak, not an error.",
    ),
    Attack(
        "RT-NUM-004", "Very large amount", "numeric",
        "A 30-digit amount. Python integers do not overflow, but the envelope "
        "arithmetic and the JSON serialisation must both survive it.",
        defence_source="treasury", expected="DEFENDED",
        params={"amount": "999999999999999999999999999999"},
        rationale="Overflow is impossible here by construction; this proves it.",
    ),
    Attack(
        "RT-NUM-005", "Currency confusion", "numeric",
        "An internal-channel action against an intent denominated in USDC. Treating "
        "a dollar as a stablecoin needs a rate this layer does not have.",
        defence_source="intent", expected="DEFENDED",
        params={"channel": "internal", "intent_asset": "USDC"},
        rationale="Silent conversion is how a governance layer starts being wrong "
                  "about money.",
    ),

    # --- economic -------------------------------------------------------
    Attack(
        "RT-ECON-001", "Microtransaction structuring", "economic",
        "Forty payments of $0.001, paced one every five minutes so no velocity "
        "counter fires. Each is individually trivial and inside every envelope; "
        "together they are a $0.04 spend nobody authorised as such.",
        defence_source="treasury", expected="UNDEFENDED",
        params={"count": 40, "amount": "0.001", "spacing_seconds": 300},
        rationale="The named threat in the roadmap, and the one a per-transaction "
                  "cap is structurally unable to see.",
    ),
    Attack(
        "RT-ECON-002", "Budget fragmentation across vendors", "economic",
        "The same total spread over twelve counterparties, so no per-vendor "
        "envelope is approached. Only an agent-level envelope can see it.",
        defence_source="treasury", expected="DEFENDED",
        params={"vendors": 101, "amount": "0.50", "spacing_seconds": 300},
        rationale="Tests whether the per-agent daily envelope actually binds, or "
                  "whether every limit is per-counterparty. **Its parameters have now "
                  "been wrong twice, each time in a way that made the run look "
                  "meaningful.** First: $0.50 across 12 vendors -- $6 total, nowhere "
                  "near the $50 daily. Then $5.00 across 12, which reaches $60 on "
                  "paper but is refused on the *first* call: $1.00 or more to a "
                  "counterparty with no history is REVIEW by "
                  "`review-untrusted-vendor-nontrivial`, so the loop broke at i=0 "
                  "having moved $0.00, and reported a defence. $0.50 is the largest "
                  "per-call amount an unknown vendor gets approved, so 101 of them is "
                  "the smallest run that can cross $50. A test whose parameters cannot "
                  "reach the control it names is not testing it -- and one that stops "
                  "at the first step is not testing the spread at all.",
    ),
    Attack(
        "RT-ECON-003", "Trust farming", "economic",
        "Two hundred settled $0.001 purchases to build earned authority, then one "
        "large purchase at the raised ceiling. Cheap reputation converted into "
        "spending power.",
        defence_source="risk", expected="DEFENDED_BY_ACCIDENT",
        params={"warmup": 200, "warmup_amount": "0.001", "strike_amount": "12.00",
                "warmup_starts_days_ago": 29},
        rationale="Earned authority is a deliberate feature; this asks what it "
                  "costs to earn dishonestly. **The `risk` control it names never "
                  "acts.** The strike is refused, so this is not a hole -- but it is "
                  "refused by `review-negative-roi`, a policy rule that finds a $12 "
                  "data purchase not worth its price. Shaped as a purchase the ROI "
                  "rule likes, nothing here would examine the farmed history at all, "
                  "which is why this scores DEFENDED_BY_ACCIDENT rather than "
                  "DEFENDED. Writing that sharper strike is W7 work; claiming this "
                  "one covers `risk` would be the fiction.\n"
                  "The warmup also used to land 60 to 52 days back, outside the "
                  "30-day window `history_for` reads, so no trust was farmed and the "
                  "attack tested nothing it described. It now lands inside the "
                  "window. That did **not** change the outcome -- the ROI rule fires "
                  "either way -- and knowing the fix changed nothing is worth more "
                  "than a passing run that was never measuring the thing.",
    ),
    Attack(
        "RT-ECON-004", "Velocity evasion", "economic",
        "Ninety-seven transactions an hour, sustained -- three per cent under the "
        "`velocity_1h` ceiling of 100, never over it, for as long as it likes.",
        defence_source="treasury", expected="UNDEFENDED",
        params={"count": 200, "amount": "0.001", "spacing_seconds": 37},
        rationale="A rate limit bounds the rate, not the total. **The parameters "
                  "were wrong and made this look like a hole in the rate limit "
                  "itself.** It ran 30 actions at 21-second spacing -- 171 an hour "
                  "on paper, but only 30 actions over ten minutes, so it stopped "
                  "before the hourly counter reached anything. Run to 120 actions at "
                  "that same spacing, action 100 **is** refused, by "
                  "`treasury/velocity_exceeded:velocity_1h`. The rate limit works.\n"
                  "The real gap is one step narrower: pacing *just under* the "
                  "ceiling is unbounded in total. At 37-second spacing this runs two "
                  "hours and 200 actions with nothing refused, and 97 an hour "
                  "sustained is 2,328 actions a day. Every value envelope stays out "
                  "of reach too -- 2,328 x $0.001 is $2.33 against a $50 daily "
                  "budget. Nothing counts actions over a window longer than an hour, "
                  "which is the thing that does not exist. Third attack in this "
                  "catalogue whose parameters could not reach the control it named.",
    ),

    # --- evidence -------------------------------------------------------
    Attack(
        "RT-EVID-001", "Journal entry tampering", "evidence",
        "Edit an amount in a sealed journal entry and re-verify the chain.",
        defence_source=None, expected="DEFENDED",
        params={"mode": "edit"},
        rationale="The audit chain's entire purpose.",
    ),
    Attack(
        "RT-EVID-002", "Journal truncation", "evidence",
        "Delete the last entries. A chain that only detects edits, not deletions, "
        "lets an attacker erase the refusal that caught them.",
        defence_source=None, expected="UNDEFENDED",
        params={"mode": "truncate"},
        rationale="Truncation is the cheapest tampering and the easiest to miss, and "
                  "this is **open finding 1**, not a regression. Any prefix of a hash "
                  "chain is itself a valid hash chain, so `verify()` reports the "
                  "truncated journal as holding: an agent that gets refused can delete "
                  "the refusal. Editing and middle-deletion are both caught, which is "
                  "what makes this easy to overlook -- two of the three tamper modes "
                  "pass.\n"
                  "`expected` was DEFENDED, inherited from the prototype, which "
                  "believed the chain covered this. It does not, and a suite that "
                  "reports a known gap as a surprise on every run buries the surprises "
                  "that matter. It reads UNDEFENDED until W7.4 lands an external "
                  "anchor; at that point this line flips and the suite says so. Note "
                  "A11.7: a `head.json` beside the journal is **not** the fix -- an "
                  "attacker who can truncate the journal can rewrite the file next to "
                  "it, and it would look like a defence while being none.",
    ),
    Attack(
        "RT-EVID-003", "Middle-entry deletion", "evidence",
        "Remove an entry from the middle and close the gap.",
        defence_source=None, expected="DEFENDED",
        params={"mode": "delete_middle"},
        rationale="Distinguishes a real chain from a per-entry checksum.",
    ),
    Attack(
        "RT-EVID-004", "Request id replay", "evidence",
        "Submit a second, larger payment under the id of one already settled.",
        defence_source="authorize", expected="DEFENDED",
        params={"amount": "0.001", "replay_amount": "5.00"},
        rationale="The ledger writes with INSERT OR REPLACE. First run: UNDEFENDED -- "
                  "the replay was refused only because its amount happened to breach "
                  "an envelope, so a same-amount replay would have overwritten the "
                  "settled row. A guard was added; this entry now records the "
                  "defence rather than the gap.",
    ),

    # --- authority ------------------------------------------------------
    Attack(
        "RT-AUTH-001", "Delegation escalation", "authority",
        "A sub-agent claiming a higher per-action limit than its parent.",
        defence_source="identity", expected="DEFENDED",
        params={"parent_limit": "0.01", "child_limit": "100.00"},
        rationale="Already defended by design; this proves it rather than asserting it.",
    ),
    Attack(
        "RT-AUTH-002", "Revoked identity continues", "authority",
        "An agent transacting after revocation.",
        defence_source="identity", expected="DEFENDED",
        params={},
        rationale="Revocation that can be outlived is advisory.",
    ),
    Attack(
        "RT-AUTH-003", "Expired intent reuse", "authority",
        "Acting under an intent whose window closed an hour ago.",
        defence_source="intent", expected="DEFENDED",
        params={"expired_hours": 1},
        rationale="Caught as a real bug by AEGS-CONF; this keeps it caught.",
    ),
    Attack(
        "RT-AUTH-004", "Intent substitution", "authority",
        "Agent B acting under an intent declared by agent A.",
        defence_source="intent", expected="DEFENDED",
        params={},
        rationale="An intent is a statement of purpose, not a transferable permit.",
    ),
    Attack(
        "RT-AUTH-005", "Sanctions bar under a permissive rule", "authority",
        "A trivial payment to a sanctioned counterparty, small enough that every "
        "amount rule would wave it through.",
        defence_source="sanctions", expected="DEFENDED",
        params={"amount": "0.000001"},
        rationale="If the amount can soften an absolute bar, it is not a bar.",
    ),
)
