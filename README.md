# survivor-py
Mech Royale bot. Python stdlib only. Files: Dockerfile, mechfight.py (wrapper), survivor.py (bot).

## Regression checks

Extract the official kit and set `MECHFIGHT_KIT` to its root (containing
`arena` and `check`), or place it at `../work/mechfight-kit` relative to this
repository. Run:

```text
python tests/test_behavior.py
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
