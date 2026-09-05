"""Optional, purely diagnostic override for DFlash2's trained draft block
width. Off by default; chad's normal behavior is untouched unless
CHAD_DFLASH_FORCE_BLOCK_SIZE is explicitly set.

Isolated from engine.py/mlx_dflash.py on purpose, per standing instruction
not to disturb chad's existing round-loop/drafter code for what is a
one-off diagnostic. This module owns the override end to end; engine.py
only imports it and calls one function.

Why this exists: chad's own DFlash2 sidecar declares block_size=8 (7 real
draft tokens) in its checkpoint config, matching every other published
DFlash2 checkpoint found while auditing a leaderboard submission that
claimed near-perfect acceptance at width 64 on the same architecture family
(see GLM-5.3-FLASH-LEADERBOARD-CLAIM-AUDIT.md). SGLang's own DFlash2 worker
allows requesting a width past the checkpoint's declared block_size --
it just logs a mismatch warning and proceeds. This module reproduces that
same "allowed, but a real distribution shift" behavior for chad, so the
question "does this actually work, or does it just run" can be measured
directly on our own checkpoint instead of taken on faith from an
unverifiable leaderboard entry.
"""

from . import config


def maybe_force_block_size(dflash_config) -> None:
    """Mutate `dflash_config.block_size` in place if
    CHAD_DFLASH_FORCE_BLOCK_SIZE is set to a nonzero value. No-op otherwise.

    Every downstream site that reads `config.block_size` -- the draft-width
    clamp in Engine._load, `_DFlashDrafter.propose()`'s mask-block
    construction, and `_DFlashDrafter.__init__`'s own cap -- reads this same
    config object, so mutating it once here is sufficient to propagate the
    override everywhere without editing any of those call sites.

    Quality at the forced width is untested by construction: the checkpoint
    was never trained past its real block_size, so positions beyond it are
    extrapolation, not a validated capability. Pair with
    CHAD_DFLASH_DRAFT=<width> to actually request the wider draft --
    setting it also forces dflash_adaptive off, which bypasses
    WidthPolicy's hardcoded MAX_DEPTH=7 table (sized for the trained width
    only; left on, it would silently re-clamp an adaptive-mode request back
    down to 7 regardless of this override).
    """
    wide = config.env_int("CHAD_DFLASH_FORCE_BLOCK_SIZE", 0)
    if wide:
        dflash_config.block_size = wide
