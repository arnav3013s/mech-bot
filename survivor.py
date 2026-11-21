"""Survival-first mech bot: stay inside the zone, keep still when alone, face and strafe threats,
dodge shots, shoot with lead only when there is line of sight."""
import json
import math
import os
import random
import struct
import sys
from array import array

import mechfight as mf

CELL = 2.0
UPG_PREF = ["armor", "plating", "nanites", "gun_damage", "gun_rate", "servos", "gyros", "gun_mag",
            "laser_damage", "laser_charge", "sensors", "gun_velocity", "capacitor", "reactor", "jets"]


def wrap(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


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

    def cover_score(self, x, y):
        """How many of 12 directions have a tall building within 20 m (hiding spot quality)."""
        if not self.ok:
            return 0
        g = self.ground(x, y)
        n = 0
        for a in range(12):
            ang = a * math.pi / 6
            c, s_ = math.cos(ang), math.sin(ang)
            d = 4.0
            while d <= 20.0:
                k = self.idx(x + c * d, y + s_ * d)
                if k >= 0 and self.top[k] > g + 9.0:
                    n += 1
                    break
                d += 2.0
        return n

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



def hp_frac(m):
    mx = m.get("max_hp") or 0
    return (m.get("hp", mx) / mx) if mx else 1.0


class Bot:
    name = "survivor-py4"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.slot = hello["slot"]
        self.rng = random.Random(hello["seed"])
        self.world = World(hello["map"]["dir"], hello["map"].get("files"))
        self.spot = None
        self.spot_kind = None
        self.spot_t = -99.0
        self.last_hit_t = -99.0
        self.last_seen_enemy_t = -99.0
        self.last_pos = None
        self.stuck = 0
        self.escape_until = -1.0
        self.escape_dir = 0.0
        self.strafe = 1
        self.strafe_until = 0.0
        self.hit_bearing = None
        self.hit_until = -1.0

    # ---------------------------------------------------------- terrain scoring
    def height(self, x, y):
        w = self.world
        g = w.ground(x, y)
        k = w.idx(x, y)
        if k >= 0 and w.top[k] > -1e8:
            return max(g, w.top[k])
        return g

    def pick_spot(self, x, y, kind, cen, rad, ccen, crad, enemies):
        """kind: 'high' (camp on high ground), 'cover' (hide). Returns (px, py) or None."""
        w = self.world
        if not w.ok:
            return None
        R = 70.0
        base_h = self.height(x, y)
        best, bs = None, -1e9
        for i in range(120):
            if i == 0:
                px, py = x, y
            else:
                a = self.rng.uniform(0, 2 * math.pi)
                r = R * math.sqrt(self.rng.random())
                px, py = x + r * math.cos(a), y + r * math.sin(a)
            if math.hypot(px - cen[0], py - cen[1]) > rad * 0.85:
                continue
            if math.hypot(px - ccen[0], py - ccen[1]) > crad - 25:
                continue
            if w.blocked(px, py):
                continue
            h = self.height(px, py)
            cov = w.cover_score(px, py)
            dist = math.hypot(px - x, py - y)
            sc = -dist / 60.0 + 0.4 * cov
            if kind == "high":
                sc += (h - base_h) * 0.15
            # stay away from visible enemies, and avoid enemies on the same high ground
            for e in enemies:
                ed = math.hypot(px - e["pos"][0], py - e["pos"][1])
                if ed < 40:
                    sc -= (40 - ed) / 10.0
                if kind == "high" and e["pos"][2] > h + 2 and ed < 120:
                    sc -= 3.0
            if sc > bs:
                bs, best = sc, (px, py)
        return best

    def steer(self, x, y, want, t):
        mag = math.hypot(*want)
        if mag < 1e-3:
            return [0.0, 0.0]
        da = math.atan2(want[1], want[0])
        if self.escape_until > t:
            da = self.escape_dir
        w = self.world
        best, bs = da, -1e9
        for k in range(16):
            a = wrap(da + k * math.pi / 8)
            cl = w.clear_dist(x, y, a)
            s = math.cos(wrap(a - da)) + min(cl, 30.0) / 60.0
            if cl < 8.0:
                s -= 2.0 * (8.0 - cl) / 8.0 + 0.5
            if s > bs:
                bs, best = s, a
        return [math.cos(best) * min(1.0, mag), math.sin(best) * min(1.0, mag)]

    # ---------------------------------------------------------- main decision
    def act(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        t = obs["t"]
        stats = me["stats"]
        hp = me["hp"] / me["max_hp"]
        act = {"tick": obs["tick"]}

        # upgrades: armor, then HP and regen, then gun
        off = obs.get("offer")
        if off:
            pref = ["armor", "plating", "nanites", "gun_damage", "gun_rate", "servos", "gyros", "gun_mag",
                    "laser_damage", "laser_charge", "sensors"]
            ids = [c["id"] for c in off["choices"]]
            act["pick"] = min(range(len(ids)), key=lambda i: pref.index(ids[i]) if ids[i] in pref else 99)

        for e in obs["events"]:
            if e["type"] == "damaged":
                self.last_hit_t = t
                if e.get("bearing") is not None:
                    self.hit_bearing = e["bearing"]
                    self.hit_until = t + 2.0

        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        for m in mechs:
            m["_d"] = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
        mechs.sort(key=lambda m: m["_d"])
        if mechs:
            self.last_seen_enemy_t = t

        # gunfire heard: group bearings into 45-degree sectors
        shots = [s for s in obs["sounds"] if s["kind"] in ("gun", "lightning", "laser", "mortar", "npc_gun") and s["age"] < 2.0]
        sectors = set(int(((s["bearing"] + math.pi) % (2 * math.pi)) / (math.pi / 4)) for s in shots)
        crossfire = len(sectors) >= 3
        fight_heard = len(sectors) >= 1 and not mechs

        cen, rad = obs["zone"]["next_center"], obs["zone"]["next_radius"]
        ccen, crad = obs["zone"]["center"], obs["zone"]["radius"]
        if obs["zone"]["shrink_start"] - t > 30:
            cen, rad = ccen, crad  # zone is not shrinking yet: use the current circle

        want = [0.0, 0.0]
        mode = "idle"
        aim_yaw = me["aim"][0]
        fire = [False, False]
        pitch = 0.0
        low_hp = hp < 0.5
        boost = False

        # 1) zone is the first priority
        if not me["in_zone"]:
            mode = "zone"
            dz = math.hypot(ccen[0] - x, ccen[1] - y) + 1e-6
            want = [(ccen[0] - x) / dz, (ccen[1] - y) / dz]
            boost = True
        else:
            # target choice
            visible = mechs
            primary = visible[0] if visible else None
            weakest = min(visible, key=hp_frac) if visible else None
            # kill allowed only if enemy is below 20% HP, or we're healthy and it's alone and close
            def may_kill(e):
                if hp_frac(e) < 0.2:
                    return True
                return (not low_hp) and len(visible) == 1 and e["_d"] < 60

            if visible:
                # 2) enemies in view
                crowd = len(visible) >= 3
                enemy_high = primary["pos"][2] > z + 2
                if crossfire or crowd:
                    mode = "back_off"
                    cx = sum(m["pos"][0] for m in visible) / len(visible)
                    cy = sum(m["pos"][1] for m in visible) / len(visible)
                    dd = math.hypot(cx - x, cy - y) + 1e-6
                    perp = (-(cy - y) / dd * self.strafe, (cx - x) / dd * self.strafe)
                    want = [-(cx - x) / dd + perp[0] * 0.5, -(cy - y) / dd + perp[1] * 0.5]
                elif low_hp and not may_kill(primary):
                    mode = "retreat"
                    spot = self.spot if (self.spot_kind == "cover" and t - self.spot_t < 2.5) else None
                    if spot is None:
                        spot = self.pick_spot(x, y, "cover", cen, rad, ccen, crad, visible)
                        self.spot, self.spot_kind, self.spot_t = spot, "cover", t
                    if spot:
                        dg = math.hypot(spot[0] - x, spot[1] - y) + 1e-6
                        want = [(spot[0] - x) / dg, (spot[1] - y) / dg]
                elif enemy_high and not may_kill(primary):
                    mode = "avoid_high"
                    spot = self.pick_spot(x, y, "cover", cen, rad, ccen, crad, visible)
                    if spot:
                        dg = math.hypot(spot[0] - x, spot[1] - y) + 1e-6
                        want = [(spot[0] - x) / dg, (spot[1] - y) / dg]
                else:
                    mode = "engage"
                    tgt = weakest if (weakest and may_kill(weakest)) else primary
                    # strafe while fighting, hold range
                    if t > self.strafe_until:
                        self.strafe = -self.strafe if self.rng.random() < 0.6 else self.strafe
                        self.strafe_until = t + self.rng.uniform(0.6, 1.4)
                    d = tgt["_d"] + 1e-6
                    ux, uy = (tgt["pos"][0] - x) / d, (tgt["pos"][1] - y) / d
                    want = [-uy * self.strafe * 0.7, ux * self.strafe * 0.7]
                    if d > 70:
                        want[0] += ux * 0.5
                        want[1] += uy * 0.5
                    # aim with lead
                    gun = me["weapons"][0]
                    spdb = gun["stats"].get("speed", 252) if gun else 252
                    tt = 0.0
                    for _ in range(3):
                        ap = [tgt["pos"][0] + tgt["vel"][0] * tt, tgt["pos"][1] + tgt["vel"][1] * tt,
                              tgt["pos"][2] + 4.5 + tgt["vel"][2] * tt]
                        dist = math.dist(ap[:2], (x, y)) + 1e-6
                        tt = dist / spdb + 0.05
                    aim_yaw = math.atan2(ap[1] - y, ap[0] - x)
                    pitch = max(-0.7, min(1.3, math.atan2(ap[2] - (z + stats.get("muzzle_height", 7)), dist)))
                    err = abs(wrap(aim_yaw - me["aim"][0]))
                    if (gun and gun.get("ammo", 1) > 0 and gun.get("reload", 0) <= 0 and err < 2.2 / dist + 0.02
                            and dist < 200 and may_kill(tgt)):
                        fire[0] = True
                    w1 = me["weapons"][1]
                    if w1 and w1["kind"] == "lightning" and tgt["_d"] < 40 and me["energy"] > 10 and err < 0.4:
                        fire[1] = True
            else:
                # 3) nobody visible
                # loot first (laser, lightning, cubes, supply drops), only if zone-safe
                loot = None
                have = {w["kind"] for w in me["weapons"] if w}
                for p in obs["pickups"]:
                    if p["kind"] in ("laser", "lightning") and p["kind"] not in have and me["weapons"][1] is None:
                        d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
                        if d < 80 and (loot is None or d < loot[0]):
                            loot = (d, p["pos"][:2], p["id"])
                for c in obs["cubes"]:
                    d = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
                    if d < 30 and (loot is None or d < loot[0]):
                        loot = (d, c["pos"][:2], None)
                for dp in obs.get("drops", []):
                    if dp["lands_in"] <= 15 and dp["holder"] in (None, self.slot):
                        if math.hypot(dp["pos"][0] - cen[0], dp["pos"][1] - cen[1]) < rad - 12:
                            d = math.hypot(dp["pos"][0] - x, dp["pos"][1] - y)
                            if d < 120 and (loot is None or d < loot[0]):
                                loot = (d, dp["pos"][:2], None)
                if loot and t - self.last_hit_t > 3.0:
                    mode = "loot"
                    d = loot[0] + 1e-6
                    want = [(loot[1][0] - x) / d, (loot[1][1] - y) / d]
                    if loot[0] < 3.5:
                        for p in obs["pickups"]:
                            if p["id"] == loot[2] and abs(p["pos"][2] - z) < 3.5:
                                act["take"] = p["id"]
                elif hp < 0.6 or fight_heard:
                    mode = "heal" if hp < 0.6 else "hold"
                    # hide and wait for regen or for the fight to end
                    if not (self.spot_kind == "cover" and t - self.spot_t < 4.0):
                        spot = self.pick_spot(x, y, "cover", cen, rad, ccen, crad, [])
                        self.spot, self.spot_kind, self.spot_t = spot, "cover", t
                    if self.spot:
                        dg = math.hypot(self.spot[0] - x, self.spot[1] - y)
                        if dg > 4:
                            want = [(self.spot[0] - x) / dg, (self.spot[1] - y) / dg]
                else:
                    mode = "camp"
                    if not (self.spot_kind == "high" and t - self.spot_t < 6.0):
                        spot = self.pick_spot(x, y, "high", cen, rad, ccen, crad, [])
                        self.spot, self.spot_kind, self.spot_t = spot, "high", t
                    if self.spot:
                        dg = math.hypot(self.spot[0] - x, self.spot[1] - y)
                        if dg > 4:
                            want = [(self.spot[0] - x) / dg, (self.spot[1] - y) / dg]
                    # face the last heard shot, else keep current aim
                    if shots:
                        aim_yaw = shots[0]["bearing"]
                    elif self.hit_bearing is not None and t < self.hit_until:
                        aim_yaw = self.hit_bearing

            # zone pull, always on as a soft push
            dzc = math.hypot(cen[0] - x, cen[1] - y)
            if dzc > rad * 0.8 and mode not in ("engage",):
                want[0] += (cen[0] - x) / (dzc + 1e-6) * 0.5
                want[1] += (cen[1] - y) / (dzc + 1e-6) * 0.5

        # stuck escape
        moving = math.hypot(*want) > 0.2
        if self.last_pos is not None and moving and math.hypot(x - self.last_pos[0], y - self.last_pos[1]) < 0.4:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = (x, y)
        if self.stuck >= 6 and self.escape_until < t:
            self.escape_until = t + 1.2
            self.escape_dir = self.rng.uniform(-math.pi, math.pi)
            self.stuck = 0
        if self.escape_until > t:
            moving = True
            act["jump"] = True

        mv = self.steer(x, y, want, t) if moving else [0.0, 0.0]
        act["move"] = mv
        act["boost"] = bool((mode == "zone" or self.escape_until > t) and me["energy"] > 10) or (
            mode in ("back_off", "retreat") and me["energy"] > 40 and math.hypot(*mv) > 0.3)
        act["aim"] = [aim_yaw, pitch]
        act["fire"] = fire
        gun = me["weapons"][0]
        if gun and gun.get("ammo", 12) == 0 and gun.get("reload", 0) <= 0:
            act["reload"] = True
        elif gun and gun.get("ammo", 12) < 4 and not mechs and gun.get("reload", 0) <= 0:
            act["reload"] = True
        return act


mf.run(Bot)
