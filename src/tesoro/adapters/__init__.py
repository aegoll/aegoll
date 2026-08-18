"""Adapters: the two boundaries between this layer and everything else.

**Rail** adapters govern the external channel — what an agent pays out. `x402_python` is the
only one, and keeping settlement in a single module is what makes the layer rail-agnostic
(research question 4): nothing in `tesoro/` above this package knows what a 402 is.

**Framework** adapters govern the internal channel — the tokens an agent burns thinking. They
hook a run, and the framework calls them.

The two contracts live in [`base`](base.py) and are deliberately separate; the reasoning is in
that module's docstring, and it comes down to AP2 needing the second and none of the first.

Nothing here is imported by the core, and nothing here is imported *at package import time* —
`import tesoro` must not pull in a framework or a payment SDK, which `tests/test_deps.py`
enforces. Import an adapter explicitly:

    from tesoro.adapters.claude import ClaudeAgentAdapter    # pip install tesoro[claude]
    from tesoro.adapters.adk import GoogleADKAdapter         # pip install tesoro[adk]
    from tesoro.adapters.base import RunGuard                # no extra needed

`RunGuard` needs no extra at all, because it imports nothing: an agent on a framework with no
adapter here can still be governed by making its three calls.
"""

from .base import PaymentClient, RunGuard, conforms_as_payment_client

__all__ = ["PaymentClient", "RunGuard", "conforms_as_payment_client"]
