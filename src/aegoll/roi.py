"""Moved to `aegoll.engines.economic.roi` in S6.

Kept as a re-export so no import site had to change during the move. A
regrouping that forced every caller to be edited would be a rewrite
wearing a refactor's clothes, and the point of S6 was that behaviour --
and therefore the sealed experiments -- stay identical.

Safe as a plain re-export because engines are stateless: pure functions
over value types, with no module-level mutable state that two import
paths could disagree about.
"""

from .engines.economic.roi import *  # noqa: F401,F403
