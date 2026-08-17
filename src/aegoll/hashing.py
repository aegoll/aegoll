"""How long a hash is here, and why.

One constant, because the length was previously written as a bare `[:16]` in five places and
nothing tied them together. A number repeated five times is a number that drifts.

**128 bits retained, from SHA-256.** Required by AEGS-0.1-EVID-5 for the evidence chain, and
applied to every hash in the package for the same underlying reason.

The arithmetic that fixes the number. Altering a hashed artefact undetectably means finding a
**second preimage** — different content, same truncated digest. At 64 bits that is 2⁶⁴ work,
which commodity GPUs reach in months; at 128 bits it is 2¹²⁸, which nobody reaches. Collision
resistance is the less interesting half, since a birthday collision even at 64 bits needs
around 4 × 10⁹ artefacts and no realistic journal is that long.

This package previously retained **64 bits**, and the specification's own clause caught it.
Changing it rewrites every hash, which is why it was worth doing before anything published
depended on the old ones.

Three distinct uses, all governed by the same second-preimage concern:

* **the evidence chain** — an entry commits to its predecessor. A second preimage lets an
  entry be rewritten with the chain still verifying. AEGS-0.1-EVID-5.
* **the decision hash** — `aegoll replay` re-derives past decisions and compares. A second
  preimage lets a different decision pass as the recorded one, so the determinism check
  would confirm something false.
* **content hashes for config and policy** — the AEGS Policy schema prefers a content hash to
  a label precisely because a label can be reused across edited rules. A second preimage
  restores the weakness the hash was chosen to remove: a policy swapped for a different one
  that hashes the same.
"""

from __future__ import annotations

import hashlib

#: Retained hash length, in hexadecimal characters. 32 hex = 128 bits.
HASH_HEX = 32

#: The same figure in bits, for anything that reports strength rather than slicing a string.
HASH_BITS = HASH_HEX * 4

#: The function itself, named so an auditor does not have to read the source to find it.
#: AEGS-0.1-EVID-5 requires both the function and the retained length to be declared.
HASH_NAME = "sha256"


def digest(blob: str) -> str:
    """The package's hash of a string, truncated to `HASH_HEX`.

    Every hash in this package goes through here. The alternative — `hashlib` called directly
    with a slice at each site — is how the length came to be written five times and agreed
    with itself only by luck.
    """
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:HASH_HEX]


__all__ = ["HASH_BITS", "HASH_HEX", "HASH_NAME", "digest"]
