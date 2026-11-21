"""Mobile Scavenger + Patient Finisher bot for mechfight/1.

Design goals:
- Keep moving; never camp.
- Rotate toward the next circle early.
- Prefer free cubes and a safe second weapon.
- Farm crates only in quiet conditions.
- Never take a fair fight.
- Finish only extremely weak, distracted targets.
- Disengage immediately when an engagement becomes uncertain.

Python 3.9+, standard library only. Place next to mechfight.py.
"""
from __future__ import annotations

import json
import math
import os
import sys
from array import array
from typing import Any, Dict, List, Optional, Sequence, Tuple

import mechfight as mf

CELL = 2.0

Vec2 = Tuple[float, float]
TAU = 2.0 * math.pi


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def add(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Sequence[float], b: Sequence[float]) -> Vec2:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def mul(a: Vec2, k: float) -> Vec2:
    return (a[0] * k, a[1] * k)


def length(a: Vec2) -> float:
    return math.hypot(a[0], a[1])


def unit(a: Vec2) -> Vec2:
    d = length(a)
    return (0.0, 0.0) if d < 1e-9 else (a[0] / d, a[1] / d)


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def yaw_to(a: Sequence[float], b: Sequence[float]) -> float:
    return math.atan2(float(b[1]) - float(a[1]), float(b[0]) - float(a[0]))


def angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % TAU - math.pi


def direction(yaw: float) -> Vec2:
    return (math.cos(yaw), math.sin(yaw))


class World:
    def __init__(self, mapdir, files):
        self.ok = False
        try:
            w = json.load(open(os.path.join(mapdir, "v2/world.json")))
            t = w["terrain"]
            self.nx, self.ny, self.tminx, self.tminy, self.step = t["nx"], t["ny"], t["minX"], t["minY"], t["step"]
            raw = open(os.path.join(mapdir, "v2/terrain.i16"), "rb").read()
            self.h = array("h")
            self.h.frombytes(raw)
            if sys.byteorder == "big":
                self.h.byteswap()
            b = w["bounds"]
            self.minx, self.miny = b["minX"], b["minY"]
            self.gw = int((b["maxX"] - b["minX"]) / CELL) + 2
            self.gh = int((b["maxY"] - b["minY"]) / CELL) + 2
            self.blk = bytearray(self.gw * self.gh)
            self.top = array("f", [-1e9]) * (self.gw * self.gh)
            for p in w["prisms"]:
                self._raster(p)
            # steep terrain
            for j in range(self.gh):
                for i in range(self.gw):
                    x = self.minx + i * CELL
                    y = self.miny + j * CELL
                    g = self.ground(x, y)
                    if abs(self.ground(x + CELL, y) - g) > 2.3 or abs(self.ground(x, y + CELL) - g) > 2.3:
                        self.blk[j * self.gw + i] = 1
            self.dil = bytearray(self.gw * self.gh)
            gw = self.gw
            for j in range(1, self.gh - 1):
                for i in range(1, gw - 1):
                    k = j * gw + i
                    if (self.blk[k] or self.blk[k - 1] or self.blk[k + 1] or self.blk[k - gw] or self.blk[k + gw]
                            or self.blk[k - gw - 1] or self.blk[k - gw + 1] or self.blk[k + gw - 1] or self.blk[k + gw + 1]):
                        self.dil[k] = 1
            self.ok = True
        except Exception as e:
            mf.log("map load failed", repr(e))

    def ground(self, x, y):
        fx = (x - self.tminx) / self.step
        fy = (y - self.tminy) / self.step
        i = int(math.floor(fx))
        j = int(math.floor(fy))
        if i < 0 or j < 0 or i >= self.nx - 1 or j >= self.ny - 1:
            return 0.0
        tx, ty = fx - i, fy - j
        h = self.h
        n = self.nx
        a = h[j * n + i]
        b = h[j * n + i + 1]
        c = h[(j + 1) * n + i]
        d = h[(j + 1) * n + i + 1]
        return 0.01 * ((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty)

    def _raster(self, p):
        ring = p["ring"]
        pts = [(ring[i], ring[i + 1]) for i in range(0, len(ring), 2)]
        holes = []
        for hk in (p.get("holes") or []):
            holes.append([(hk[i], hk[i + 1]) for i in range(0, len(hk), 2)] if isinstance(hk, list) and hk and not isinstance(hk[0], list) else hk)
        if isinstance(p.get("holes"), list) and p["holes"] and isinstance(p["holes"][0], (int, float)):
            hh = p["holes"]
            holes = [[(hh[i], hh[i + 1]) for i in range(0, len(hh), 2)]]
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        i0 = max(0, int((min(xs) - self.minx) / CELL) - 1)
        i1 = min(self.gw - 1, int((max(xs) - self.minx) / CELL) + 1)
        j0 = max(0, int((min(ys) - self.miny) / CELL) - 1)
        j1 = min(self.gh - 1, int((max(ys) - self.miny) / CELL) + 1)
        base, top = p["base"], p["top"]
        for j in range(j0, j1 + 1):
            y = self.miny + (j + 0.5) * CELL
            for i in range(i0, i1 + 1):
                x = self.minx + (i + 0.5) * CELL
                if not self._inside(pts, x, y):
                    continue
                if any(self._inside(hp, x, y) for hp in holes if hp):
                    continue
                g = self.ground(x, y)
                k = j * self.gw + i
                if top > g + 1.2 and base < g + 9.5:
                    self.blk[k] = 1
                if base < g + 9.5 and top > self.top[k]:
                    self.top[k] = top

    @staticmethod
    def _inside(pts, x, y):
        c = False
        n = len(pts)
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
                c = not c
            j = i
        return c

    def idx(self, x, y):
        i = int((x - self.minx) / CELL)
        j = int((y - self.miny) / CELL)
        if i < 1 or j < 1 or i >= self.gw - 1 or j >= self.gh - 1:
            return -1
        return j * self.gw + i

    def blocked(self, x, y):
        k = self.idx(x, y)
        return k < 0 or self.dil[k] == 1

    def clear_dist(self, x, y, ang, maxd=30.0):
        if not self.ok:
            return maxd
        c, s = math.cos(ang), math.sin(ang)
        d = 3.0
        while d <= maxd:
            if self.blocked(x + c * d, y + s * d):
                return d - 3.0
            d += 2.0
        return maxd

    def los(self, a, b):
        """Rough line of sight for shots: building cells whose top is above the line block it."""
        if not self.ok:
            return True
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        dist = math.hypot(dx, dy)
        n = int(dist / 2.0)
        for s in range(1, n):
            f = s / n
            x, y, z = a[0] + dx * f, a[1] + dy * f, a[2] + dz * f
            k = self.idx(x, y)
            if k < 0:
                return False
            if self.top[k] > z and self.top[k] > -1e8:
                # raw ground-inside check: only count cells that are inside a prism
                return False
        return True



class Bot:
    name = "MobileFinisher-w"

    # Conservative strategy thresholds.
    FINISH_HP_FRAC = 0.15
    ENDGAME_FINISH_HP_FRAC = 0.10
    MIN_FIGHT_HP_FRAC = 0.60
    MAX_FIGHT_SECONDS = 2.0
    MAX_FIGHT_HP_LOSS_FRAC = 0.10
    FREE_CUBE_THREAT_RADIUS = 55.0
    CRATE_THREAT_RADIUS = 80.0
    CRATE_MAX_DISTANCE = 25.0
    DROP_THREAT_RADIUS = 120.0
    HARD_ROTATE_LEAD_S = 15.0
    SAFE_MARGIN_M = 25.0

    def __init__(self, hello: mf.Hello):
        self.config = hello.get("config", {})
        self.pickup_radius = float(self.config.get("loot", {}).get("pickup_radius", 4.0))
        self.cube_radius = float(self.config.get("cubes", {}).get("pickup_radius", 3.5))
        self.last_t = 0.0
        self.last_scan_yaw = 0.0
        self.fight_target: Optional[int] = None
        self.fight_started = -1e9
        self.fight_start_hp = 0.0
        self.last_damage_t = -1e9
        self.last_damage_bearing: Optional[float] = None
        self.recent_deaths: List[Tuple[float, float]] = []  # (time, approximate bearing)
        self.world = World(hello["map"]["dir"], hello["map"].get("files"))
        self.clock = 0.0
        self.last_pos: Optional[Vec2] = None
        self.stuck = 0
        self.escape_until = -1.0
        self.escape_dir = 0.0

    def act(self, obs: mf.Observation) -> mf.Action:
        now = float(obs.get("t", 0.0))
        self.clock = now
        me = obs["self"]
        pos = me["pos"]
        hp = float(me.get("hp", 1.0))
        max_hp = max(1.0, float(me.get("max_hp", hp)))
        hp_frac = hp / max_hp
        energy = float(me.get("energy", 0.0))
        max_energy = max(1.0, float(me.get("max_energy", 1.0)))
        energy_frac = energy / max_energy
        alive = int(obs.get("alive", 16))

        mechs = [m for m in obs.get("mechs", []) if m.get("kind") == "mech"]
        npcs = [m for m in obs.get("mechs", []) if m.get("kind") == "npc"]
        damaged = self._read_events(obs, now)

        nearest_enemy = min(mechs, key=lambda m: distance(pos, m["pos"]), default=None)
        nearby_55 = [m for m in mechs if distance(pos, m["pos"]) <= self.FREE_CUBE_THREAT_RADIUS]
        nearby_80 = [m for m in mechs if distance(pos, m["pos"]) <= self.CRATE_THREAT_RADIUS]
        multiple_threats = len(nearby_55) >= 2

        zone = obs["zone"]
        next_safe = self._next_safe_point(pos, zone)
        hard_rotate = self._must_hard_rotate(obs, next_safe)

        # Upgrade offers are one-shot and can accompany any movement decision.
        action: Dict[str, Any] = {}
        offer = obs.get("offer")
        if offer:
            action["pick"] = self._choose_upgrade(offer)

        # Emergency has absolute priority.
        attacked_recently = now - self.last_damage_t <= 1.0
        enemy_too_close = nearest_enemy is not None and distance(pos, nearest_enemy["pos"]) < 24.0
        emergency = (not me.get("in_zone", True)) or multiple_threats or (attacked_recently and hp_frac < 0.85)

        if emergency:
            self._clear_fight()
            move = self._escape_vector(pos, next_safe, mechs)
            aim = self._defensive_aim(pos, nearest_enemy)
            fire = [False, False]
            if nearest_enemy is not None and distance(pos, nearest_enemy["pos"]) < 150.0:
                fire = self._ready_fire_mask(me, allow_secondary=True,
                                             distance_to_target=distance(pos, nearest_enemy["pos"]))
            action.update({
                "move": self._steer(pos, move),
                "aim": [aim, 0.0],
                "boost": bool(energy_frac > 0.22 and (not me.get("in_zone", True) or attacked_recently or enemy_too_close)),
                "fire": fire,
            })
            self._maybe_reload(action, me)
            mf.draw.label("EMERGENCY")
            return action

        # Continue a previously gated finish only while it remains safe.
        target = self._find_by_id(mechs, self.fight_target)
        if target is not None and self._continue_finish(obs, target, hp_frac, now):
            action.update(self._combat_action(obs, target, now))
            mf.draw.label("FINISH")
            return action
        self._clear_fight()

        # Strict new third-party / finishing gate.
        target = self._select_finish_target(obs, mechs, hp_frac)
        if target is not None and not hard_rotate:
            self.fight_target = int(target["id"])
            self.fight_started = now
            self.fight_start_hp = hp
            action.update(self._combat_action(obs, target, now))
            mf.draw.label("FINISH")
            return action

        # Pick up a nearby useful second weapon without stopping rotation.
        pickup = self._best_pickup(obs)
        if pickup is not None and distance(pos, pickup["pos"]) <= self.pickup_radius:
            action["take"] = pickup["id"]

        # Choose movement objective. Cover is treated as route quality, not a camping state.
        objective, state = self._movement_objective(obs, next_safe, hard_rotate, nearby_55, nearby_80)
        desired = unit(sub(objective, pos))
        desired = self._apply_threat_spacing(desired, pos, mechs, next_safe)

        # Scan independently from movement. Watch nearest enemy; otherwise sweep slowly.
        if nearest_enemy is not None:
            aim_yaw = yaw_to(pos, nearest_enemy["pos"])
        else:
            self.last_scan_yaw = (self.last_scan_yaw + 0.22) % TAU
            aim_yaw = self.last_scan_yaw

        action.update({
            "move": self._steer(pos, desired),
            "aim": [aim_yaw, 0.0],
            "boost": bool(hard_rotate and energy_frac > 0.35 and length(desired) > 0.5),
            "fire": [False, False],
        })

        # Quiet crate farming: fire while still moving; abandon immediately on danger.
        if state == "FARM":
            crate = self._nearest(obs.get("crates", []), pos)
            if crate is not None:
                action["aim"] = [yaw_to(pos, crate["pos"]), self._pitch_to(pos, crate["pos"])]
                action["fire"] = self._ready_fire_mask(me, allow_secondary=False)

        self._maybe_reload(action, me)
        mf.draw.label(state)
        return action

    def _steer(self, pos, want):
        """Turn a wanted direction into a move that does not walk into walls; escape if stuck."""
        x, y = float(pos[0]), float(pos[1])
        mag = math.hypot(want[0], want[1])
        if mag < 1e-3:
            self.stuck = 0
            self.last_pos = (x, y)
            return [0.0, 0.0]
        if self.last_pos is not None:
            if math.hypot(x - self.last_pos[0], y - self.last_pos[1]) < 0.4:
                self.stuck += 1
            else:
                self.stuck = 0
        self.last_pos = (x, y)
        if self.stuck >= 8 and self.escape_until < self.clock:
            self.escape_until = self.clock + 1.2
            self.escape_dir = self.rng_angle()
            self.stuck = 0
        da = self.escape_dir if self.escape_until > self.clock else math.atan2(want[1], want[0])
        best, bs = da, -1e9
        for k in range(16):
            a = da + k * math.pi / 8
            cl = self.world.clear_dist(x, y, a)
            s = math.cos(angle_diff(a, da)) + min(cl, 30.0) / 60.0
            if cl < 8.0:
                s -= 2.0 * (8.0 - cl) / 8.0 + 0.5
            if s > bs:
                bs, best = s, a
        return [math.cos(best) * min(1.0, mag), math.sin(best) * min(1.0, mag)]

    def rng_angle(self) -> float:
        return (self.clock * 7919.0) % TAU - math.pi

    def _read_events(self, obs: mf.Observation, now: float) -> bool:
        damaged = False
        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                damaged = True
                self.last_damage_t = now
                bearing = ev.get("bearing")
                if bearing is not None:
                    self.last_damage_bearing = float(bearing)
            elif ev.get("type") == "kill":
                self._clear_fight()
        return damaged

    def _next_safe_point(self, pos: Sequence[float], zone: Dict[str, Any]) -> Vec2:
        center = zone.get("next_center", zone.get("center", [0.0, 0.0]))
        radius = float(zone.get("next_radius", zone.get("radius", 0.0)))
        inward = unit(sub(pos, center))  # center -> current position
        margin = min(self.SAFE_MARGIN_M, max(3.0, radius * 0.35))
        target_r = max(0.0, radius - margin)
        # Stay on our current side of the next circle to avoid center congestion.
        return (float(center[0]) + inward[0] * target_r,
                float(center[1]) + inward[1] * target_r)

    def _must_hard_rotate(self, obs: mf.Observation, target: Vec2) -> bool:
        me = obs["self"]
        if not me.get("in_zone", True):
            return True
        now = float(obs.get("t", 0.0))
        zone = obs["zone"]
        start = float(zone.get("shrink_start", now))
        time_left = start - now
        speed = float(me.get("stats", {}).get("walk_speed", 11.7))
        travel = distance(me["pos"], target) / max(1.0, speed)
        hp_frac = float(me["hp"]) / max(1.0, float(me["max_hp"]))
        return time_left <= self.HARD_ROTATE_LEAD_S or travel + 3.0 >= max(0.0, time_left) or hp_frac < 0.60

    def _movement_objective(self, obs, next_safe, hard_rotate, nearby_55, nearby_80):
        pos = obs["self"]["pos"]
        if hard_rotate:
            return next_safe, "ROTATE"

        cube = self._best_safe_item(obs.get("cubes", []), pos, next_safe, obs.get("mechs", []), 100.0)
        if cube is not None and not nearby_55:
            # Soft rotation: loot dominates, but never move fully away from future safety.
            loot_dir = unit(sub(cube["pos"], pos))
            zone_dir = unit(sub(next_safe, pos))
            blended = unit(add(mul(loot_dir, 0.70), mul(zone_dir, 0.30)))
            return add((float(pos[0]), float(pos[1])), mul(blended, 50.0)), "SCAVENGE"

        pickup = self._best_pickup(obs)
        if pickup is not None and not nearby_55 and distance(pos, pickup["pos"]) <= 75.0:
            return (float(pickup["pos"][0]), float(pickup["pos"][1])), "SCAVENGE"

        crate = self._nearest(obs.get("crates", []), pos)
        quiet = not nearby_80 and not self._recent_combat_sound(obs, near_only=True)
        if crate is not None and quiet and distance(pos, crate["pos"]) <= self.CRATE_MAX_DISTANCE:
            # Keep circling slightly instead of becoming stationary.
            to_crate = unit(sub(crate["pos"], pos))
            tangent = (-to_crate[1], to_crate[0])
            move_point = add((float(pos[0]), float(pos[1])), mul(tangent, 12.0))
            return move_point, "FARM"

        return next_safe, "ROTATE"

    def _best_safe_item(self, items, pos, next_safe, mechs, max_dist):
        best = None
        best_score = 1e30
        for item in items:
            d = distance(pos, item["pos"])
            if d > max_dist:
                continue
            threat = min((distance(item["pos"], m["pos"]) for m in mechs if m.get("kind") == "mech"), default=9999.0)
            if threat < self.FREE_CUBE_THREAT_RADIUS:
                continue
            # Lower score favors close loot that also progresses toward safety.
            score = d + 0.25 * distance(item["pos"], next_safe)
            if score < best_score:
                best_score, best = score, item
        return best

    def _best_pickup(self, obs):
        me = obs["self"]
        held = {w.get("kind") for w in me.get("weapons", []) if w}
        empty = any(w is None for w in me.get("weapons", []))
        if not empty:
            return None
        rank = {"lightning": 0, "laser": 1, "mortar": 2, "gun": 9}
        choices = [p for p in obs.get("pickups", []) if p.get("kind") not in held]
        if not choices:
            return None
        return min(choices, key=lambda p: (rank.get(p.get("kind"), 8), distance(me["pos"], p["pos"])))

    def _select_finish_target(self, obs, mechs, hp_frac):
        if hp_frac < self.MIN_FIGHT_HP_FRAC:
            return None
        threshold = self.ENDGAME_FINISH_HP_FRAC if int(obs.get("alive", 16)) <= 5 else self.FINISH_HP_FRAC
        pos = obs["self"]["pos"]
        candidates = []
        for m in mechs:
            enemy_hp = m.get("hp")
            enemy_max = m.get("max_hp")
            if enemy_hp is None or not enemy_max:
                continue
            frac = float(enemy_hp) / max(1.0, float(enemy_max))
            if frac > threshold:
                continue
            d = distance(pos, m["pos"])
            if not self._within_any_weapon_range(obs["self"], d):
                continue
            # Distracted means torso is not aimed approximately toward us, or active combat is nearby.
            toward_us = yaw_to(m["pos"], pos)
            enemy_aim = float(m.get("aim", [0.0, 0.0])[0])
            distracted = abs(angle_diff(enemy_aim, toward_us)) > math.radians(55) or self._recent_combat_sound(obs)
            other_threat = any(o["id"] != m["id"] and distance(pos, o["pos"]) < 70.0 for o in mechs)
            if distracted and not other_threat:
                candidates.append((frac, d, m))
        return min(candidates, key=lambda x: (x[0], x[1]))[2] if candidates else None

    def _continue_finish(self, obs, target, hp_frac, now):
        if now - self.fight_started > self.MAX_FIGHT_SECONDS:
            return False
        me = obs["self"]
        if hp_frac < self.MIN_FIGHT_HP_FRAC:
            return False
        if self.fight_start_hp - float(me["hp"]) > self.MAX_FIGHT_HP_LOSS_FRAC * float(me["max_hp"]):
            return False
        if not me.get("in_zone", True):
            return False
        others = [m for m in obs.get("mechs", []) if m.get("kind") == "mech" and m.get("id") != target.get("id")]
        if any(distance(me["pos"], o["pos"]) < 60.0 for o in others):
            return False
        return self._within_any_weapon_range(me, distance(me["pos"], target["pos"]))

    def _combat_action(self, obs, target, now):
        me = obs["self"]
        pos = me["pos"]
        tpos = target["pos"]
        d = distance(pos, tpos)
        aim_yaw = self._lead_yaw(me, target)
        aim_pitch = self._pitch_to(pos, tpos)
        # Strafe while finishing, biased toward next-circle safety.
        to_target = unit(sub(tpos, pos))
        side = 1.0 if int(target["id"]) % 2 == 0 else -1.0
        strafe = (-to_target[1] * side, to_target[0] * side)
        safe = unit(sub(self._next_safe_point(pos, obs["zone"]), pos))
        move = unit(add(mul(strafe, 0.65), mul(safe, 0.35)))
        return {
            "move": list(move),
            "aim": [aim_yaw, aim_pitch],
            "fire": self._ready_fire_mask(me, allow_secondary=True, distance_to_target=d),
            "boost": False,
            "reload": self._gun_needs_reload(me),
        }

    def _escape_vector(self, pos, next_safe, mechs):
        toward_safe = unit(sub(next_safe, pos))
        repel = (0.0, 0.0)
        for m in mechs:
            delta = sub(pos, m["pos"])
            d = max(5.0, length(delta))
            repel = add(repel, mul(unit(delta), 1.0 / d))
        if self.last_damage_bearing is not None:
            repel = add(repel, mul(direction(self.last_damage_bearing + math.pi), 0.06))
        return unit(add(mul(toward_safe, 0.65), mul(unit(repel), 0.75)))

    def _apply_threat_spacing(self, desired, pos, mechs, next_safe):
        repel = (0.0, 0.0)
        for m in mechs:
            d = distance(pos, m["pos"])
            if d < 90.0:
                repel = add(repel, mul(unit(sub(pos, m["pos"])), (90.0 - d) / 90.0))
        if length(repel) < 1e-6:
            return desired
        safe = unit(sub(next_safe, pos))
        return unit(add(add(mul(desired, 0.55), mul(safe, 0.25)), mul(unit(repel), 0.75)))

    def _defensive_aim(self, pos, enemy):
        if enemy is not None:
            return yaw_to(pos, enemy["pos"])
        if self.last_damage_bearing is not None:
            return self.last_damage_bearing
        return self.last_scan_yaw

    def _lead_yaw(self, me, target):
        pos = me["pos"]
        tpos = target["pos"]
        # Lead for the gun using its live projectile speed; hitscan weapons tolerate this small lead.
        speed = 252.0
        for w in me.get("weapons", []):
            if w and w.get("kind") == "gun":
                speed = float(w.get("stats", {}).get("speed", speed))
                break
        d = distance(pos, tpos)
        dt = clamp(d / max(1.0, speed), 0.0, 0.8)
        vel = target.get("vel", [0.0, 0.0, 0.0])
        predicted = [float(tpos[0]) + float(vel[0]) * dt,
                     float(tpos[1]) + float(vel[1]) * dt,
                     float(tpos[2]) + float(vel[2]) * dt]
        return yaw_to(pos, predicted)

    def _pitch_to(self, a, b):
        dz = float(b[2]) - float(a[2])
        horizontal = max(0.1, distance(a, b))
        return math.atan2(dz, horizontal)

    def _within_any_weapon_range(self, me, d):
        for w in me.get("weapons", []):
            if not w:
                continue
            kind = w.get("kind")
            stats = w.get("stats", {})
            rng = float(stats.get("range", 45.0 if kind == "lightning" else 230.0))
            if d <= rng * 0.92:
                return True
        return False

    def _ready_fire_mask(self, me, allow_secondary=True, distance_to_target=None):
        mask = [False, False]
        for i, w in enumerate(me.get("weapons", [])[:2]):
            if not w or float(w.get("cooldown", 0.0)) > 0.05:
                continue
            if i == 1 and not allow_secondary:
                continue
            kind = w.get("kind")
            if kind == "lightning" and distance_to_target is not None:
                rng = float(w.get("stats", {}).get("range", 45.0))
                mask[i] = distance_to_target <= rng
            elif kind == "laser":
                # Hold to full charge. Releasing is handled once charge reaches configured charge time.
                charge = float(w.get("charge", 0.0))
                full = float(w.get("stats", {}).get("charge", 0.75))
                mask[i] = charge < full
            else:
                mask[i] = True
        return mask

    def _gun_needs_reload(self, me):
        for w in me.get("weapons", []):
            if w and w.get("kind") == "gun":
                ammo = float(w.get("ammo", 0.0))
                mag = float(w.get("stats", {}).get("mag", 12.0))
                return ammo <= max(1.0, mag * 0.25) and float(w.get("reload", 0.0)) <= 0.0
        return False

    def _maybe_reload(self, action, me):
        if self._gun_needs_reload(me):
            action["reload"] = True

    def _choose_upgrade(self, offer):
        # Prefer survival/information/mobility, then general damage.
        priorities = [
            ("armor", 0), ("damage_taken", 0), ("resist", 0),
            ("sensor", 1), ("vision", 1),
            ("move", 2), ("speed", 2), ("energy", 2),
            ("hp", 3), ("health", 3), ("regen", 3),
            ("damage", 4), ("rate", 5), ("cooldown", 5),
        ]
        best_i, best_score = 0, 999
        for i, choice in enumerate(offer.get("choices", [])):
            text = (str(choice.get("id", "")) + " " + str(choice.get("name", "")) + " " + str(choice.get("desc", ""))).lower()
            score = min((rank for word, rank in priorities if word in text), default=50)
            if score < best_score:
                best_i, best_score = i, score
        return best_i

    def _recent_combat_sound(self, obs, near_only=False):
        combat = {"gun", "mortar", "explosion", "laser", "laser_charge", "lightning", "death", "npc_gun"}
        for s in obs.get("sounds", []):
            if s.get("kind") in combat and float(s.get("age", 99.0)) <= 1.2:
                if not near_only or s.get("dist") == "near":
                    return True
        return bool(obs.get("beams") or obs.get("projectiles"))

    def _nearest(self, items, pos):
        return min(items, key=lambda x: distance(pos, x["pos"]), default=None)

    def _find_by_id(self, items, target_id):
        if target_id is None:
            return None
        return next((x for x in items if int(x.get("id", -1)) == int(target_id)), None)

    def _clear_fight(self):
        self.fight_target = None
        self.fight_started = -1e9
        self.fight_start_hp = 0.0


if __name__ == "__main__":
    mf.run(Bot)
