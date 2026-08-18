"""The engines, grouped by what they answer to.

Ten engines flat in one package had stopped saying anything about their own
structure -- and the roadmap warns that the set "should not simply grow
indefinitely". Two arrived in a week (intent, identity), which is the warning being
tested rather than heeded.

Three families, from `tesoro_aegs.md` §9:

* **economic**  -- may this spend happen, and is it what the agent was sent to do?
* **risk**      -- who is on the other side, and does this look like trouble?
* **evidence**  -- what was recorded, who acted, and when must a human decide?

`authorize.py` deliberately sits **outside** them. It is the composition root: it
reads every family and clamps their verdicts together, so placing it inside one
would make the dependency rule below unstatable, and a rule with an exception
carved for its most important case is not a rule.

The rule: **no family imports another.** Engines depend only on `domain`, `config`
and `store`, which are value types rather than behaviour. `tests/test_engines.py`
fails if that reverses -- which is what stops "families" from becoming folders.
"""
