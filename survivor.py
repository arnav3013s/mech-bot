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
    name = "survivor-py"

    def __init__(self, hello):
        cfg = hello["config"]
        self.cfg = cfg
        self.slot = hello["slot"]
        self.rng = random.Random(hello["seed"])
        self.world = World(hello["map"]["dir"], hello["map"].get("files"))
        self.strafe = 1
        self.strafe_until = 0
        self.stuck = 0
        self.escape_until = -1
        self.escape_dir = 0.0
        self.last_pos = None
        self.scan = 0.0
        self.hit_bearing = None
        self.hit_until = -1
        self.cmd_move = False
        self.laser_hold = False
        mf.log("map ok", self.world.ok)

    # ---------------------------------------------------------------- helpers
    def zone_goal(self, obs):
        z = obs["zone"]
        t = obs["t"]
        cur_c, cur_r = z["center"], z["radius"]
        if t >= z["shrink_start"] - 30:
            return z["next_center"], z["next_radius"], cur_c, cur_r
        return cur_c, cur_r, cur_c, cur_r

    def steer(self, x, y, want, t):
        """want: desired world direction (length 0..1). Returns a move vector avoiding walls."""
        mag = math.hypot(*want)
        if mag < 1e-3:
            return [0.0, 0.0]
        da = math.atan2(want[1], want[0])
        w = self.world
        if self.escape_until > t:
            da = self.escape_dir
        best, bs = da, -1e9
        for k in range(16):
            a = da + wrap(k * math.pi / 8)
            a = wrap(a)
            cl = w.clear_dist(x, y, a)
            diff = abs(wrap(a - da))
            s = math.cos(diff) * 1.0 + min(cl, 30.0) / 30.0 * 0.5
            if cl < 8.0:
                s -= 2.0 * (8.0 - cl) / 8.0 + 0.5
            if s > bs:
                bs, best = s, a
        return [math.cos(best) * min(1.0, mag), math.sin(best) * min(1.0, mag)]

    # ---------------------------------------------------------------- main
    def act(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        t = obs["t"]
        tick = obs["tick"]
        act = {"tick": tick}
        stats = me["stats"]

        # upgrade offer
        off = obs.get("offer")
        if off:
            ids = [c["id"] for c in off["choices"]]
            pick = 0
            bestr = 999
            for i, cid in enumerate(ids):
                r = UPG_PREF.index(cid) if cid in UPG_PREF else 500
                if r < bestr:
                    bestr, pick = r, i
            act["pick"] = pick

        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        for m in mechs:
            m["_d"] = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
        mechs.sort(key=lambda m: m["_d"])

        # damage bearing memory
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("bearing") is not None:
                self.hit_bearing = e["bearing"]
                self.hit_until = t + 2.0

        # ------ targets
        muzzle = [x, y, z + stats.get("muzzle_height", 7)]
        target = None
        seen_los = []
        for m in mechs:
            tp = m["pos"]
            if m["_d"] < 230 and self.world.los(muzzle, [tp[0], tp[1], tp[2] + 4.5]):
                seen_los.append(m)
        if seen_los:
            def _threat(m):
                s = 80.0 / max(m["_d"], 1.0)
                if m.get("charging"):
                    s += 8.0
                ws = m.get("weapons", [])
                if "laser" in ws:
                    s += 6.0
                if "lightning" in ws and m["_d"] < 50:
                    s += 8.0
                if "mortar" in ws:
                    s += 3.0
                return s
            target = max(seen_los, key=_threat)
        threat = mechs[0] if mechs else None

        # ------ desired movement vector
        want = [0.0, 0.0]
        cen, rad, ccen, crad = self.zone_goal(obs)
        dz_c = math.hypot(cen[0] - x, cen[1] - y)
        dcur = math.hypot(ccen[0] - x, ccen[1] - y)
        urgent = (not me["in_zone"]) or dcur > crad - 25
        if dz_c > rad * 0.6 or urgent:
            gv = [(cen[0] - x) / (dz_c + 1e-6), (cen[1] - y) / (dz_c + 1e-6)]
            wgt = 1.0 if (urgent or dz_c > rad * 0.9) else 0.6
            want[0] += gv[0] * wgt
            want[1] += gv[1] * wgt

        fighting = threat is not None and threat["_d"] < 150
        hp_frac = me["hp"] / me["max_hp"]
        if fighting:
            tx, ty = threat["pos"][0] - x, threat["pos"][1] - y
            d = threat["_d"] + 1e-6
            ux, uy = tx / d, ty / d
            if t > self.strafe_until:
                self.strafe = -self.strafe if self.rng.random() < 0.6 else self.strafe
                self.strafe_until = t + self.rng.uniform(0.6, 1.6)
            px, py = -uy * self.strafe, ux * self.strafe
            n_vis = sum(1 for m in mechs if m["_d"] < 150)
            # keep range 50..90: back off when close, close in slightly when far
            radial = 0.0
            if d < 45:
                radial = -1.0
            elif d < 70:
                radial = -0.3
            elif d > 110 and n_vis == 1:
                radial = 0.3
            if n_vis >= 3 or hp_frac < 0.35:
                radial = -1.0
                # flee away from the crowd centroid
                cx = sum(m["pos"][0] for m in mechs if m["_d"] < 150) / n_vis - x
                cy = sum(m["pos"][1] for m in mechs if m["_d"] < 150) / n_vis - y
                cd = math.hypot(cx, cy) + 1e-6
                ux, uy = cx / cd, cy / cd
            want[0] += px * 0.9 + ux * radial
            want[1] += py * 0.9 + uy * radial
            # laser telegraph: sidestep hard
            for m in mechs:
                if m.get("charging") and m["_d"] < 290:
                    bx, by = x - m["pos"][0], y - m["pos"][1]
                    bear = math.atan2(by, bx)
                    if abs(wrap(m["aim"][0] - bear)) < 0.2:
                        lx, ly = -math.sin(bear) * self.strafe, math.cos(bear) * self.strafe
                        want[0] += lx * 2.0
                        want[1] += ly * 2.0
        # npc avoidance
        for n in npcs:
            d = math.hypot(n["pos"][0] - x, n["pos"][1] - y)
            if d < 55:
                want[0] -= (n["pos"][0] - x) / (d + 1e-6) * 0.8
                want[1] -= (n["pos"][1] - y) / (d + 1e-6) * 0.8
        # projectile dodge
        dodge = False
        for pr in obs["projectiles"]:
            px_, py_, pz_ = pr["pos"]
            vx, vy, vz = pr["vel"]
            rx, ry = px_ - x, py_ - y
            v2 = vx * vx + vy * vy
            if v2 < 1:
                continue
            ts = -(rx * vx + ry * vy) / v2
            if 0 < ts < 1.2:
                cx, cy = rx + vx * ts, ry + vy * ts
                if math.hypot(cx, cy) < 7:
                    cd = math.hypot(cx, cy)
                    if cd < 0.5:
                        ox, oy = -vy, vx
                        n_ = math.hypot(ox, oy)
                        cx, cy = ox / n_, oy / n_
                    else:
                        cx, cy = cx / cd, cy / cd
                    want[0] -= cx * 1.5
                    want[1] -= cy * 1.5
                    dodge = True
        # hit from unseen attacker: back away and face it
        unseen_hit = (not mechs) and self.hit_bearing is not None and t < self.hit_until
        if unseen_hit:
            want[0] -= math.cos(self.hit_bearing) * 0.8
            want[1] -= math.sin(self.hit_bearing) * 0.8

        # loot (only when calm): laser > lightning
        safe = (not mechs) and (not unseen_hit) and me["in_zone"] and not urgent
        goal_pick = None
        if safe:
            have = {w["kind"] for w in me["weapons"] if w}
            if me["weapons"][1] is None:
                best = None
                for p in obs["pickups"]:
                    if p["kind"] in ("laser", "lightning") and p["kind"] not in have:
                        d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
                        r = 0 if p["kind"] == "laser" else 25
                        if d < 70 and (best is None or d + r < best[0]):
                            best = (d + r, p, d)
                if best:
                    goal_pick = best[1]
                    d = best[2]
                    if d < 3.5 and abs(goal_pick["pos"][2] - z) < 3.5:
                        act["take"] = goal_pick["id"]
                    else:
                        want[0] += (goal_pick["pos"][0] - x) / (d + 1e-6)
                        want[1] += (goal_pick["pos"][1] - y) / (d + 1e-6)
            for c in obs["cubes"]:
                d = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
                if d < 18 and goal_pick is None:
                    want[0] += (c["pos"][0] - x) / (d + 1e-6)
                    want[1] += (c["pos"][1] - y) / (d + 1e-6)
                    break

        # stuck detection
        moving_cmd = math.hypot(*want) > 0.2
        if self.last_pos is not None and moving_cmd:
            moved = math.hypot(x - self.last_pos[0], y - self.last_pos[1])
            if moved < 0.4:
                self.stuck += 1
            else:
                self.stuck = 0
        else:
            self.stuck = 0
        self.last_pos = (x, y)
        jump = False
        if self.stuck >= 6 and self.escape_until < t:
            self.escape_until = t + 1.2
            self.escape_dir = self.rng.uniform(-math.pi, math.pi)
            self.stuck = 0
        if self.escape_until > t:
            jump = True

        mv = self.steer(x, y, want, t) if moving_cmd else [0.0, 0.0]
        act["move"] = mv
        spd = math.hypot(*mv)
        act["boost"] = bool((not me["in_zone"] or (fighting and hp_frac < 0.4) or self.escape_until > t or dodge)
                            and spd > 0.3 and me["energy"] > 12)
        if jump:
            act["jump"] = True
            act["boost"] = me["energy"] > 5

        # ------ aim & fire
        fire = [False, False]
        yaw_cmd, pit_cmd = me["aim"][0], 0.0
        if target is not None:
            tp = target["pos"]
            tv = target["vel"]
            gun = me["weapons"][0]
            spdb = gun["stats"].get("speed", 252) if gun else 252
            tt = 0.0
            for _ in range(3):
                aimp = [tp[0] + tv[0] * tt, tp[1] + tv[1] * tt, tp[2] + 4.5 + tv[2] * tt]
                dist = math.dist(aimp[:2], (x, y)) + 1e-6
                tt = dist / spdb + 0.05
            yaw_cmd = math.atan2(aimp[1] - y, aimp[0] - x)
            pit_cmd = max(-0.7, min(1.3, math.atan2(aimp[2] - muzzle[2], dist)))
            err = abs(wrap(yaw_cmd - me["aim"][0]))
            tol = 2.2 / dist + 0.02
            if gun and gun.get("ammo", 1) > 0 and gun.get("reload", 0) <= 0 and dist < gun["stats"].get("range", 230) * 0.9 and err < tol:
                fire[0] = True
            w1 = me["weapons"][1]
            if w1 and w1["kind"] == "lightning" and target["_d"] < 40 and err < 0.4 and me["energy"] > 10:
                fire[1] = True
            if w1 and w1["kind"] == "laser":
                ch = w1.get("charge", 0)
                full = w1["stats"].get("charge", 0.75)
                if ch > 0:
                    # releasing = firing; hold until nearly full unless aim is on target
                    fire[1] = not (ch >= full * 0.9 or (ch >= 0.3 and err < tol * 1.5))
                elif w1["cooldown"] <= 0.05 and dist < 150 and err < tol * 2.0:
                    fire[1] = True
        else:
            if unseen_hit:
                yaw_cmd = self.hit_bearing
            elif not mechs:
                self.scan = wrap(self.scan + 0.5)
                yaw_cmd = wrap(me["aim"][0] + 0.45)
            if mechs:  # tracked but no LOS: keep facing it
                tp = mechs[0]["pos"]
                yaw_cmd = math.atan2(tp[1] - y, tp[0] - x)
            w1 = me["weapons"][1]
            if w1 and w1["kind"] == "laser" and w1.get("charge", 0) > 0:
                fire[1] = False
        act["aim"] = [yaw_cmd, pit_cmd]
        act["fire"] = fire
        gun = me["weapons"][0]
        if gun and gun.get("ammo", 12) < gun["stats"].get("mag", 12) * 0.5 and not mechs and gun.get("reload", 0) <= 0:
            act["reload"] = True
        elif gun and gun.get("ammo", 1) == 0 and gun.get("reload", 0) <= 0:
            act["reload"] = True
        return act


mf.run(Bot)
