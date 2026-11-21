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




# =============================================================================
#  SETTINGS: every threshold the bot uses. Change a number here, rebuild, resubmit.
# =============================================================================
SETTINGS = {
    # --- health -------------------------------------------------------------
    "heal_below": 0.50,          # HP fraction below which we stop optional fights and go heal
    "kill_enemy_below": 0.20,    # we may always finish an enemy below this HP fraction
    "solo_kill_range": 60.0,     # a lone enemy closer than this may be engaged when healthy
    "heal_spot_wait_s": 4.0,     # hold a healing spot at least this long before moving again

    # --- crossfire and gunfire -----------------------------------------------
    "shot_window_s": 2.5,        # how far back we remember gunfire
    "wait_after_shots_s": 4.0,   # after a fight is heard, stay put this long before moving toward it
    "fire_sounds": ("gun", "lightning", "laser", "mortar", "npc_gun"),

    # --- enemies ------------------------------------------------------------
    "crowd_count": 3,            # this many visible enemies = leave
    "high_ground_margin": 2.0,   # an enemy this many metres above us holds high ground
    "memory_s": 6.0,             # remember enemies this long after they leave view
    "hunt_enemy_hp_below": 0.25, # hunt a remembered enemy only if its HP is below this
    "hunt_my_hp_above": 0.60,    # ...and our own HP is above this
    "engage_hold_range": 70.0,   # when engaging, try to stay closer than this
    "fire_range": 200.0,
    "laser_min_range": 40.0,
    "laser_max_range": 150.0,
    "lightning_range": 40.0,

    # --- loot and supply drops ----------------------------------------------
    "laser_radius": 80.0,
    "lightning_radius": 70.0,
    "cube_radius": 30.0,
    "drop_lands_within_s": 15.0,
    "drop_radius_max": 120.0,
    "calm_after_hit_s": 3.0,     # no loot runs this soon after taking damage

    # --- cover and high ground ----------------------------------------------
    "camp_radius": 70.0,
    "cover_radius": 60.0,
    "spot_refresh_s": 4.0,
    "spot_arrive_m": 4.0,

    # --- movement -----------------------------------------------------------
    "boost_energy_min": 10.0,
    "retreat_boost_energy": 40.0,
    "stuck_ticks": 8,
    "escape_s": 2.0,

    # --- phases (match seconds) ---------------------------------------------
    "mid_start_s": 120.0,
    "late_start_s": 300.0,
    "late_alive": 5,
}

# Per-phase rules. Early: grab loot, do not camp. Mid: loot, then camp high ground.
# Late: strict fighting, avoid any crossfire, sit at the zone edge with cover.
PHASE = {
    "early": {"camp": False, "crossfire_sectors": 3, "healthy_kill_hp": 0.50, "solo_kill_range": 60.0},
    "mid":   {"camp": True,  "crossfire_sectors": 3, "healthy_kill_hp": 0.55, "solo_kill_range": 55.0},
    "late":  {"camp": True,  "crossfire_sectors": 2, "healthy_kill_hp": 0.65, "solo_kill_range": 45.0},
}

UPGRADE_PREF = ["armor", "plating", "nanites", "gun_damage", "gun_rate", "servos", "gyros",
                "gun_mag", "laser_damage", "laser_charge", "sensors", "gun_velocity",
                "capacitor", "reactor", "jets"]


def wrap(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def hp_frac(m):
    mx = m.get("max_hp") or 0
    if not mx:
        return 1.0
    return clamp(m.get("hp", mx) / mx, 0.0, 1.0)


def threat_score(m):
    """How dangerous an enemy is, from its distance, its loadout and its health.
    Laser beats mortar, mortar beats lightning, lightning beats laser (protocol section 7)."""
    score = 80.0 / max(m.get("_d", 50.0), 1.0)
    if m.get("charging"):
        score += 8.0
    for w in m.get("weapons", []):
        score += {"laser": 6.0, "lightning": 4.0, "mortar": 3.0}.get(w, 1.0)
    score += hp_frac(m)
    return score


def sector(bearing):
    """Eight 45-degree sectors of the compass, used to tell where gunfire comes from."""
    return int(((bearing + math.pi) % (2 * math.pi)) / (math.pi / 4)) % 8


# =============================================================================
#  THE BOT
# =============================================================================
class Bot:
    name = "survivor-py5"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.slot = hello["slot"]
        self.rng = random.Random(hello["seed"])
        self.world = World(hello["map"]["dir"], hello["map"].get("files"))
        # spot memory (hiding or camping)
        self.spot = None
        self.spot_kind = None
        self.spot_t = -99.0
        # damage and gunfire memory
        self.last_hit_t = -99.0
        self.hit_bearing = None
        self.hit_until = -1.0
        self.shots = []            # list of (time, bearing) for gunfire heard recently
        self.last_shot_t = -99.0
        self.shot_bearing = None
        # enemy memory: id -> {pos, vel, hp, seen}
        self.mem = {}
        # movement bookkeeping
        self.last_pos = None
        self.stuck = 0
        self.escape_until = -1.0
        self.escape_dir = 0.0
        self.strafe = 1
        self.strafe_until = 0.0
        self.orbit_sign = 1
        self.prev_base = [0.0, 0.0]
        self.orbit_until = 0.0
        # logging
        self.mode = "idle"
        self.last_log_t = -9.0
        self.errors = 0

    # ------------------------------------------------------------ small helpers
    def phase(self, t, alive):
        if alive <= SETTINGS["late_alive"] or t >= SETTINGS["late_start_s"]:
            return "late"
        if t >= SETTINGS["mid_start_s"]:
            return "mid"
        return "early"

    def height(self, x, y):
        w = self.world
        g = w.ground(x, y)
        k = w.idx(x, y)
        if k >= 0 and w.top[k] > -1e8:
            return max(g, w.top[k])
        return g

    def zone_circles(self, obs):
        """Returns (target_center, target_radius, current_center, current_radius).
        The target is the circle we should be inside soon: the next circle once shrinking is near."""
        z = obs["zone"]
        if obs["t"] >= z["shrink_start"] - 30:
            tc, tr = z["next_center"], z["next_radius"]
        else:
            tc, tr = z["center"], z["radius"]
        return tc, tr, z["center"], z["radius"]

    def aim_at(self, tgt, x, y, z, stats, spd):
        """Lead a moving target. Returns (yaw, pitch, distance)."""
        tt = 0.0
        dist = 1.0
        ap = [tgt["pos"][0], tgt["pos"][1], tgt["pos"][2] + 4.5]
        for _ in range(3):
            ap = [tgt["pos"][0] + tgt["vel"][0] * tt,
                  tgt["pos"][1] + tgt["vel"][1] * tt,
                  tgt["pos"][2] + 4.5 + tgt["vel"][2] * tt]
            dist = math.dist(ap[:2], (x, y)) + 1e-6
            tt = dist / spd + 0.05
        yaw = math.atan2(ap[1] - y, ap[0] - x)
        muzzle = z + stats.get("muzzle_height", 7)
        pitch = clamp(math.atan2(ap[2] - muzzle, dist), -0.7, 1.3)
        return yaw, pitch, dist, ap

    def steer(self, x, y, want, t):
        """Turn a wanted direction into a move that does not walk into walls."""
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

    def toward(self, want_pt, x, y, stop=None):
        """Unit vector toward a point, or zero if we are already within `stop` metres."""
        dx, dy = want_pt[0] - x, want_pt[1] - y
        d = math.hypot(dx, dy)
        if d < (stop if stop is not None else 0.5):
            return [0.0, 0.0], d
        return [dx / d, dy / d], d

    def pick_spot(self, x, y, kind, zc, zr, cur_c, cur_r, avoid, radius):
        """Score candidate spots inside the safe circle.
        kind 'cover': hide behind buildings. kind 'high': stand on higher ground with cover.
        `avoid` is a list of (x, y) points to keep away from (enemies seen or remembered)."""
        w = self.world
        if not w.ok:
            return None
        base_h = self.height(x, y)
        cands = []
        for i in range(120):
            if i == 0:
                px, py = x, y
            else:
                a = self.rng.uniform(0, 2 * math.pi)
                r = radius * math.sqrt(self.rng.random())
                px, py = x + r * math.cos(a), y + r * math.sin(a)
            if math.hypot(px - zc[0], py - zc[1]) > zr * 0.85:
                continue
            if math.hypot(px - cur_c[0], py - cur_c[1]) > cur_r - 25:
                continue
            if w.blocked(px, py):
                continue
            h = self.height(px, py)
            cov = w.cover_score(px, py)
            dist = math.hypot(px - x, py - y)
            sc = -dist / 60.0 + 0.4 * cov
            if kind == "high":
                sc += (h - base_h) * 0.15
            for e in avoid:
                ad = math.hypot(px - e[0], py - e[1])
                if ad < 40:
                    sc -= (40 - ad) / 10.0
            cands.append((sc, (px, py), h))
        cands.sort(key=lambda c: c[0], reverse=True)
        best, bs = None, -1e9
        for sc, (px, py), h in cands[:10]:
            for e in avoid:
                # a spot that an enemy can see is not cover from that enemy; stale sightings weigh less
                ax, ay = e[0], e[1]
                wgt = e[2] if len(e) > 2 else 1.0
                eye_e = [ax, ay, w.ground(ax, ay) + 8.0]
                if w.los(eye_e, [px, py, h + 8.0]):
                    sc -= 6.0 * wgt
            if sc > bs:
                bs, best = sc, (px, py)
        return best

    def get_spot(self, kind, t, x, y, zc, zr, cur_c, cur_r, avoid, radius, refresh_s):
        """Reuse the current spot until it is old or we have arrived; pick a new one otherwise."""
        fresh = self.spot_kind == kind and (t - self.spot_t) < refresh_s and self.spot is not None
        if not fresh:
            self.spot = self.pick_spot(x, y, kind, zc, zr, cur_c, cur_r, avoid, radius)
            self.spot_kind = kind
            self.spot_t = t
        return self.spot

    # ------------------------------------------------------------ reading the observation
    def read_events(self, obs, t):
        for e in obs["events"]:
            if e["type"] == "damaged":
                self.last_hit_t = t
                if e.get("bearing") is not None:
                    self.hit_bearing = e["bearing"]
                    self.hit_until = t + 2.0

    def read_sounds(self, obs, t):
        for s in obs["sounds"]:
            if s["kind"] in SETTINGS["fire_sounds"] and s["age"] < SETTINGS["shot_window_s"]:
                when = t - s["age"]
                self.shots.append((when, s["bearing"]))
                if when > self.last_shot_t:
                    self.last_shot_t = when
                    self.shot_bearing = s["bearing"]
        self.shots = [s for s in self.shots if t - s[0] <= SETTINGS["shot_window_s"]]
        return set(sector(b) for _, b in self.shots)

    def read_enemies(self, obs, t, x, y):
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        for m in mechs:
            self.mem[m["id"]] = {"pos": m["pos"], "vel": m["vel"], "hp": hp_frac(m), "seen": t}
            m["_d"] = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
        for k in [k for k, v in self.mem.items() if t - v["seen"] > SETTINGS["memory_s"]]:
            del self.mem[k]
        mechs.sort(key=lambda m: m["_d"])
        return mechs

    def pick_upgrade(self, obs, act):
        off = obs.get("offer")
        if not off:
            return
        ids = [c["id"] for c in off["choices"]]
        act["pick"] = min(range(len(ids)),
                          key=lambda i: UPGRADE_PREF.index(ids[i]) if ids[i] in UPGRADE_PREF else 99)

    def orbit(self, spot, x, y, t, radius=5.0):
        """Keep moving around a spot instead of standing on it. Direction flips every 1-2.5 s,
        so the bot moves left and right while it waits."""
        if self.orbit_until < t:
            self.orbit_sign = -self.orbit_sign if self.rng.random() < 0.5 else self.orbit_sign
            self.orbit_until = t + self.rng.uniform(1.0, 2.5)
        cx, cy = spot
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy) + 1e-6
        tx, ty = -dy / d * self.orbit_sign, dx / d * self.orbit_sign
        radial = clamp((radius - d) / radius, -1.0, 1.0)
        return [tx * 0.9 + dx / d * radial * 0.4, ty * 0.9 + dy / d * radial * 0.4]

    def seek_or_orbit(self, spot, x, y, t):
        if spot is None:
            # no good spot found: keep moving around where we are rather than standing still
            return self.orbit((x, y), x, y, t, radius=4.0)
        d = math.hypot(spot[0] - x, spot[1] - y)
        if d > 6.0:
            v, _ = self.toward(spot, x, y, 0.5)
            return v
        return self.orbit(spot, x, y, t)

    def projectile_dodge(self, obs, x, y, z):
        """Sidestep enemy bullets and shells that will pass close to us in the next second (v1 rule)."""
        push = [0.0, 0.0]
        for pr in obs["projectiles"]:
            px_, py_ = pr["pos"][0] - x, pr["pos"][1] - y
            vx, vy = pr["vel"][0], pr["vel"][1]
            v2 = vx * vx + vy * vy
            if v2 < 1.0:
                continue
            ts = -(px_ * vx + py_ * vy) / v2
            if 0 < ts < 2.0:
                pz_at = pr["pos"][2] + pr["vel"][2] * ts
                if pz_at > z + 9.5 or pz_at < z - 1.0:
                    continue  # passes over our head or under our feet
                cx, cy = px_ + vx * ts, py_ + vy * ts
                cd = math.hypot(cx, cy)
                if cd < 7.0:
                    if cd < 0.5:
                        cx, cy, cd = -vy, vx, math.hypot(vx, vy)
                    push[0] -= cx / cd * 1.5
                    push[1] -= cy / cd * 1.5
        return push

    def laser_sidestep(self, mechs, x, y):
        """If an enemy is charging a laser at us, step across its line of fire (v1 rule)."""
        push = [0.0, 0.0]
        for m in mechs:
            if not m.get("charging") or m["_d"] > 290:
                continue
            bear = math.atan2(y - m["pos"][1], x - m["pos"][0])
            if abs(wrap(m["aim"][0] - bear)) < 0.2:
                push[0] += -math.sin(bear) * self.orbit_sign * 2.0
                push[1] += math.cos(bear) * self.orbit_sign * 2.0
        return push

    def fresh_enemies(self, t, window=2.0):
        return [v for v in self.mem.values() if t - v["seen"] <= window]

    def weak_enemies(self, t):
        return [v for v in self.mem.values()
                if v["hp"] < SETTINGS["hunt_enemy_hp_below"] and t - v["seen"] <= 3.0]

    # ------------------------------------------------------------ rules
    def may_kill(self, e, hp, visible_count, ph):
        """The kill rule. Finish a nearly dead enemy at any HP. Otherwise only take a lone
        enemy, when healthy, and when it is close."""
        if hp_frac(e) < SETTINGS["kill_enemy_below"]:
            return True
        if hp < SETTINGS["heal_below"]:
            return False
        if visible_count >= 2:
            return False
        P = PHASE[ph]
        return hp >= P["healthy_kill_hp"] and e["_d"] <= min(SETTINGS["solo_kill_range"], P["solo_kill_range"])

    def best_loot(self, obs, x, y, cen, rad, me, ph):
        """Pick the best loot target: a laser first, then lightning, then cubes, then supply drops."""
        S = SETTINGS
        best = None  # (score distance, point, pickup id or None, kind)
        have = {w["kind"] for w in me["weapons"] if w}
        slot1_free = me["weapons"][1] is None
        for p in obs["pickups"]:
            if not slot1_free or p["kind"] in have:
                continue
            d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
            limit = S["laser_radius"] if p["kind"] == "laser" else S["lightning_radius"]
            if d < limit:
                bias = 0.0 if p["kind"] == "laser" else 20.0
                if best is None or d + bias < best[0]:
                    best = (d + bias, p["pos"][:2], p["id"], p["kind"])
        for c in obs["cubes"]:
            d = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
            if d < S["cube_radius"] and (best is None or d + 15 < best[0]):
                best = (d + 15, c["pos"][:2], None, "cube")
        for dp in obs.get("drops", []):
            if dp["lands_in"] > S["drop_lands_within_s"]:
                continue
            if dp["holder"] not in (None, self.slot):
                continue
            if math.hypot(dp["pos"][0] - cen[0], dp["pos"][1] - cen[1]) > rad - 12:
                continue
            d = math.hypot(dp["pos"][0] - x, dp["pos"][1] - y)
            if d < S["drop_radius_max"] and (best is None or d - 10 < best[0]):
                best = (d - 10, dp["pos"][:2], None, "drop")
        return best

    # ------------------------------------------------------------ fire control
    def fire_control(self, me, tgt, may, ph, act_fire):
        """Gun, laser and lightning on a target. Returns (yaw, pitch, fire list)."""
        x, y = me["pos"][0], me["pos"][1]
        z = me["pos"][2]
        stats = me["stats"]
        gun = me["weapons"][0]
        spd = gun["stats"].get("speed", 252) if gun else 252
        yaw, pitch, dist, impact = self.aim_at(tgt, x, y, z, stats, spd)
        err = abs(wrap(yaw - me["aim"][0]))
        aligned = err < 2.2 / dist + 0.02
        fire = [False, False]
        # shoot only with a clear line of sight (buildings in the way block the shot)
        muzzle = [x, y, z + stats.get("muzzle_height", 7)]
        clear = self.world.los(muzzle, impact)
        may = may and clear
        if may and gun and gun.get("ammo", 1) > 0 and gun.get("reload", 0) <= 0 and dist < SETTINGS["fire_range"]:
            if aligned and gun.get("cooldown", 0) <= 0.05:
                fire[0] = True
        w1 = me["weapons"][1]
        if may and w1:
            if w1["kind"] == "lightning":
                if tgt["_d"] < SETTINGS["lightning_range"] and me["energy"] > 10 and err < 0.4:
                    fire[1] = True
            elif w1["kind"] == "laser":
                ch = w1.get("charge", 0.0)
                full = w1["stats"].get("charge", 0.75)
                if ch > 0:
                    # keep holding until it is nearly full, or we are aligned with some charge in
                    ready = ch >= full * 0.9 or (aligned and ch >= full * 0.3)
                    fire[1] = not ready
                elif (w1.get("cooldown", 0) <= 0.05 and aligned
                      and SETTINGS["laser_min_range"] < dist < SETTINGS["laser_max_range"]):
                    fire[1] = True
        return yaw, pitch, fire

    # ------------------------------------------------------------ main decision
    def act(self, obs):
        S = SETTINGS
        me = obs["self"]
        x, y, z = me["pos"]
        t = obs["t"]
        stats = me["stats"]
        hp = me["hp"] / me["max_hp"]
        ph = self.phase(t, obs["alive"])
        P = PHASE[ph]
        act = {"tick": obs["tick"]}
        why = ""

        self.pick_upgrade(obs, act)
        self.read_events(obs, t)
        sectors = self.read_sounds(obs, t)
        mechs = self.read_enemies(obs, t, x, y)
        tc, tr, cc, cr = self.zone_circles(obs)
        crossfire = len(sectors) >= P["crossfire_sectors"] or (
            len([m for m in mechs if t - self.mem[m["id"]]["seen"] <= 2.0]) >= 3 and len(sectors) >= 2)
        remembered = [(v["pos"][0], v["pos"][1], 1.0 if t - v["seen"] <= 2.0 else 0.25)
                      for v in self.mem.values()]

        want = [0.0, 0.0]
        aim_yaw = me["aim"][0]
        pitch = 0.0
        fire = [False, False]
        boost = False
        mode = "idle"

        if not me["in_zone"]:
            # --- 1. Outside the zone: go to the middle of the current circle now.
            mode = "zone_escape"
            want, _ = self.toward(cc, x, y)
            boost = True
            why = "outside zone"
        elif mechs:
            visible = mechs
            n = len(visible)
            primary = max(visible, key=threat_score)
            weakest = min(visible, key=hp_frac)
            tgt_kill_ok = self.may_kill(weakest, hp, n, ph)
            target = weakest if tgt_kill_ok else primary
            kill_ok = self.may_kill(target, hp, n, ph)
            high_enemy = (primary["pos"][2] > z + S["high_ground_margin"]
                          and self.world.los([primary["pos"][0], primary["pos"][1], primary["pos"][2] + 8.0],
                                             [x, y, z + 8.0]))

            if crossfire and not kill_ok:
                # --- 2a. Crossfire from 2+ sides and we are not going for a kill: back out, strafing.
                mode = "crossfire_back_off"
                cx = sum(m["pos"][0] for m in visible) / n
                cy = sum(m["pos"][1] for m in visible) / n
                dx, dy = x - cx, y - cy
                d = math.hypot(dx, dy) + 1e-6
                px, py = -dy / d * self.strafe, dx / d * self.strafe
                want = [dx / d * 0.8 + px * 0.6, dy / d * 0.8 + py * 0.6]
                why = f"crossfire sectors={len(sectors)} enemies={n}"
            elif len(self.fresh_enemies(t)) >= S["crowd_count"] and not kill_ok:
                # --- 2b. Three or more enemies in view: leave the crowd.
                mode = "crowd_leave"
                fresh = self.fresh_enemies(t)
                pts = [v["pos"] for v in fresh] or [m["pos"] for m in visible]
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                dx, dy = x - cx, y - cy
                d = math.hypot(dx, dy) + 1e-6
                want = [dx / d, dy / d]
                why = f"{n} enemies in view"
            elif hp < S["heal_below"] and not kill_ok:
                # --- 2c. Hurt: break line of sight and heal behind cover.
                mode = "retreat_heal"
                spot = self.get_spot("cover", t, x, y, tc, tr, cc, cr, [(m["pos"][0], m["pos"][1]) for m in visible],
                                     S["cover_radius"], S["spot_refresh_s"])
                if spot:
                    want = self.seek_or_orbit(spot, x, y, t)
                why = f"hp {hp:.2f} below heal threshold"
                boost = me["energy"] > S["retreat_boost_energy"]
            elif high_enemy and not kill_ok:
                # --- 2d. An enemy holds higher ground: do not fight it there.
                mode = "avoid_high_ground"
                spot = self.get_spot("cover", t, x, y, tc, tr, cc, cr, [(primary["pos"][0], primary["pos"][1])],
                                     S["cover_radius"], S["spot_refresh_s"])
                if spot:
                    want = self.seek_or_orbit(spot, x, y, t)
                why = "enemy on higher ground"
            elif kill_ok:
                # --- 2e. Take the fight: lead the target, strafe, hold range, shoot.
                mode = "engage"
                if self.strafe_until < t:
                    self.strafe = -self.strafe if self.rng.random() < 0.6 else self.strafe
                    self.strafe_until = t + self.rng.uniform(0.6, 1.4)
                d = target["_d"] + 1e-6
                ux, uy = (target["pos"][0] - x) / d, (target["pos"][1] - y) / d
                want = [-uy * self.strafe * 0.7, ux * self.strafe * 0.7]
                if "lightning" in target.get("weapons", []) and d < 50.0:
                    # lightning reaches 45 m: back out of it first
                    want[0] -= ux * 1.0
                    want[1] -= uy * 1.0
                if d > S["engage_hold_range"]:
                    want[0] += ux * 0.5
                    want[1] += uy * 0.5
                aim_yaw, pitch, fire = self.fire_control(me, target, True, ph, fire)
                err_now = abs(wrap(aim_yaw - me["aim"][0]))
                if fire[0] or fire[1]:
                    # standing almost still while the shot goes off keeps the aim steady
                    want = [want[0] * 0.15, want[1] * 0.15]
                elif err_now > 0.5:
                    # still turning onto the target: keep moving, but slower
                    want = [want[0] * 0.5, want[1] * 0.5]
                why = f"engaging {target['id']} hp={hp_frac(target):.2f}"
            else:
                # --- 2f. Enemy visible but not worth it: stay put behind cover and let it come.
                mode = "watch"
                spot = self.get_spot("cover", t, x, y, tc, tr, cc, cr, [(primary["pos"][0], primary["pos"][1])],
                                     S["cover_radius"], S["spot_refresh_s"])
                if spot:
                    want = self.seek_or_orbit(spot, x, y, t)
                aim_yaw = math.atan2(primary["pos"][1] - y, primary["pos"][0] - x)
                why = f"enemy {primary['id']} not worth it"
        else:
            # --- 3. Nobody in view.
            calm = (t - self.last_hit_t) > S["calm_after_hit_s"]
            loot = self.best_loot(obs, x, y, tc, tr, me, ph)
            heard_recent = (t - self.last_shot_t) < S["wait_after_shots_s"]

            if loot and calm and not heard_recent:
                # --- 3a. Loot first: laser, lightning, cubes, drops.
                mode = "loot_" + loot[3]
                want, dd = self.toward(loot[1], x, y, 0.5)
                if dd < 3.5 and loot[2] is not None:
                    act["take"] = loot[2]
                why = f"{loot[3]} at {dd:.0f} m"
            elif (hp > S["hunt_my_hp_above"] and not heard_recent and self.weak_enemies(t)):
                # --- 3b'. We are healthy and a wounded enemy was seen recently: go finish it.
                mode = "hunt_weak"
                hv = min(self.weak_enemies(t), key=lambda v: math.hypot(v["pos"][0] - x, v["pos"][1] - y))
                want, dd = self.toward(hv["pos"], x, y, 50.0)
                why = f"hunting weakened enemy hp={hv['hp']:.2f} at {dd:.0f} m"
            elif hp < S["heal_below"] or heard_recent:
                # --- 3b. Hurt, or a fight was just heard: hide and wait.
                mode = "heal_hold" if hp < S["heal_below"] else "hold_after_shots"
                spot = self.get_spot("cover", t, x, y, tc, tr, cc, cr, remembered,
                                     S["cover_radius"], S["heal_spot_wait_s"])
                if spot:
                    want = self.seek_or_orbit(spot, x, y, t)
                if self.shot_bearing is not None and heard_recent:
                    aim_yaw = self.shot_bearing
                why = f"hp {hp:.2f}, shots heard {t - self.last_shot_t:.1f}s ago"
            elif P["camp"]:
                # --- 3c. Mid and late game: take high ground with cover and wait there.
                mode = "camp_high"
                spot = self.get_spot("high", t, x, y, tc, tr, cc, cr, remembered,
                                     S["camp_radius"], S["spot_refresh_s"])
                if spot:
                    want = self.seek_or_orbit(spot, x, y, t)
                if self.shot_bearing is not None and (t - self.last_shot_t) < 8.0:
                    aim_yaw = self.shot_bearing
                elif self.hit_bearing is not None and t < self.hit_until:
                    aim_yaw = self.hit_bearing
                why = "camping high ground"
            else:
                # --- 3d. Early game with no loot: move toward the centre of the next circle.
                mode = "drift_to_zone"
                want, _ = self.toward(tc, x, y, tr * 0.5)
                why = "early game, no loot"

            # soft pull toward the safe circle so we never drift out
            dzc = math.hypot(tc[0] - x, tc[1] - y)
            if dzc > tr * 0.8 and mode not in ("engage",):
                want[0] += (tc[0] - x) / (dzc + 1e-6) * 0.5
                want[1] += (tc[1] - y) / (dzc + 1e-6) * 0.5

        # ---------- dodge bullets and laser charges (always on)
        dodge = self.projectile_dodge(obs, x, y, z)
        ls = self.laser_sidestep(mechs, x, y)
        dodge[0] += ls[0]
        dodge[1] += ls[1]
        base = want
        fast = ("zone_escape", "crossfire_back_off", "retreat_heal", "crowd_leave", "avoid_high_ground")
        if mode in fast or fire[0] or fire[1]:
            self.prev_base = base              # urgent or shooting: react at once, no smoothing
        else:
            base = [0.6 * self.prev_base[0] + 0.4 * base[0], 0.6 * self.prev_base[1] + 0.4 * base[1]]
            self.prev_base = base
        want = [base[0] + dodge[0], base[1] + dodge[1]]

        # ---------- movement cleanup: stuck detection and escape
        moving = math.hypot(*want) > 0.2
        shooting = fire[0] or fire[1]
        if self.last_pos is not None and moving and not shooting and math.hypot(x - self.last_pos[0], y - self.last_pos[1]) < 0.4:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = (x, y)
        if self.stuck >= S["stuck_ticks"] and self.escape_until < t:
            self.escape_until = t + S["escape_s"]
            self.escape_dir = self.rng.uniform(-math.pi, math.pi)
            self.stuck = 0
        if self.escape_until > t:
            moving = True
            act["jump"] = True
            boost = me["energy"] > S["boost_energy_min"]
        mv = self.steer(x, y, want, t) if moving else [0.0, 0.0]
        act["move"] = mv
        if mode == "zone_escape" or boost:
            act["boost"] = bool(me["energy"] > S["boost_energy_min"] and math.hypot(*mv) > 0.3)
        else:
            act["boost"] = False

        # ---------- aim
        if aim_yaw is None:
            aim_yaw = me["aim"][0]
        act["aim"] = [aim_yaw, pitch]
        act["fire"] = fire

        # ---------- reload
        gun = me["weapons"][0]
        if gun and gun.get("reload", 0) <= 0:
            if gun.get("ammo", 12) == 0:
                act["reload"] = True
            elif gun.get("ammo", 12) < 4 and not mechs:
                act["reload"] = True

        # ---------- log once per second
        self.mode = mode
        if t - self.last_log_t >= 1.0:
            self.last_log_t = t
            mf.log(f"t={t:6.1f} ph={ph:5s} mode={mode:18s} hp={hp:.2f} vis={len(mechs)} "
                   f"mem={len(self.mem)} sect={len(sectors)} fire={fire} {why}")
        return act


mf.run(Bot)
