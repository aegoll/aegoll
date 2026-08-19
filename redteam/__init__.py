"""Adversarial suite: can this specific implementation be broken?

Deliberately outside `src/tesoro/`. It forges request ids, edits journals on disk and drives
the clock backwards -- white-box operations that exist to attack the layer, not to be shipped
inside it. Nothing here is importable by a user of the package, and nothing in the package
imports it.

Run it with `python -m redteam.runner` from the repository root, or `--json` for the machine
form. `tests/test_redteam.py` runs the whole catalogue against a recorded baseline, so a
defence that regresses fails CI in the ordinary test run.
"""
