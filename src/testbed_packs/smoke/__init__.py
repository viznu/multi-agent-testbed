"""Three tiny deterministic packs, one per shape the vertical slice must prove.

1. `solo_lookup`   -- single-agent deterministic tool task.
2. `coop_codeword` -- cooperative two-agent task with private complementary
   information: neither agent can succeed alone.
3. `mixed_split`   -- mixed-motive two-agent task with hidden information and
   separate individual and team payoffs.

They exist to exercise contracts, not to measure capability. Nothing here is a
capability benchmark and no result from these packs should be reported as one.
"""

from testbed_packs.smoke.pack import PACK, build_pack

__all__ = ["PACK", "build_pack"]
