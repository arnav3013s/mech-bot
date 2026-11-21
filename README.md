# survivor-py
Mech Royale bot. Python stdlib only. Runtime files: `Dockerfile`,
`mechfight.py` (wrapper), `survivor.py` (bot), and `tactics.py` (planning).

## Regression checks

Extract the official kit and set `MECHFIGHT_KIT` to its root (containing
`arena` and `check`), or place it at `../work/mechfight-kit` relative to this
repository. Run:

```text
python tests/test_behavior.py
python tests/test_tactics.py
python tests/stress.py
```

Behavior checks cover navigation, storm escape, target classification, aiming,
weapon gating, damage direction, cover revalidation and loot decisions.
The stress script reports latency for 1,500 decisions with 15 enemies and
120 projectiles. These are synthetic checks, not scored matches.

Optional geometry work uses a 20 ms decision deadline and cover planning has
a 4 ms budget. These are cooperative limits, not a guarantee against operating
system scheduling delays or the contest's 50 ms end-to-end deadline. Validate
the image with the official checker under the contest runtime limits.

The protocol does not support cancelling a charged laser: releasing fires it.
After losing a target the bot aims downward before releasing when possible;
urgent storm escape releases immediately to restore movement speed.

## Tactical update

- Incremental ground A* searches around obstacles across ticks, with a 2 ms
  slice, a 6 m grid and a 6,000-node limit. Every route edge and next waypoint
  is checked against the geometry. Changing objectives invalidates old routes.
- Cover considers all observed enemies. Visibility that ran out of time is
  unknown, never evidence of protection. Cover work has a 6 ms overall allowance;
  steering has a 5 ms allowance, both inside the 20 ms cooperative decision limit.
- Target retention reduces aim switching. A recent attacker can override it.
  Ready guns retain projectile lead; charged lasers use current target position.
  Observed vertical acceleration improves predictions of powered flight.
- Candidate dodges compare combined projectile and charging-laser risks. Storm
  dodges retain an inward component. Lightning preserves an escape energy reserve.
- Reloading encourages retreat. Safe crates can be deliberately shot for loot;
  crates are not classified as enemy mechs. Landed supply drops are considered
  only without observed danger and with a zone-time margin. Threats abort capture.
- Secondary weapon swaps depend on circle size and never swap a charging laser.
  Ground loot behind a corner can become a routing objective.
- Low-ledge jumps require a sampled ballistic arc, acceleration model, clear
  headroom and a supported landing. No random jumps or automatic jet ascent.

`test_tactics.py` runs the original behavior suite plus sequence and tactical
regressions. These tests use controlled observations and geometry, not a full
game-engine simulation. Passing does not establish a placement score.

Limits: the ground grid can miss narrow routes; it does not plan multi-level
roof/jet routes. Tall inaccessible loot is rejected. Hidden enemies remain
unknown, and evasion prediction does not simulate every future collision or
opponent decision. The sampled jump model needs live-match validation. Docker
must be built and checked under contest CPU/memory limits before submission.
