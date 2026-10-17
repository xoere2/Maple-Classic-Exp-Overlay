"""Farm analytics for Maple Classic Exp Overlay.

Everything here works from timestamped observations (events) and never touches the game:
the overlay (or a recorded video) produces events, and every metric is worked out from
them, labelled with where it came from (observed, estimated, inferred or entered by hand).

The logic modules (events, timing, economy, gauges, combat, stats, analytics, store,
export) have no screen or UI dependency, so they're tested without a game running.
"""

FARMKIT_VERSION = "1.0.0"
