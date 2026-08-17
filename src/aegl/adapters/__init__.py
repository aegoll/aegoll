"""Payment-rail adapters. x402 is the only one, and it lives behind this boundary.

Keeping settlement in a single module is what makes the governance layer
rail-agnostic (research question 4): nothing in `aegl/` above this package knows
what a 402 is.
"""
