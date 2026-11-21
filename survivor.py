"""Mech Royale bot with typed targets, ground navigation and gated weapon control."""
import json
import math
import os
import sys
from array import array

import mechfight as mf

UPG_RANK = {
    "armor": 0, "plating": 1, "nanites": 2, "gun_damage": 3, "gun_rate": 4,
    "servos": 5, "gyros": 6, "gun_mag": 7, "laser_damage": 8,
    "laser_charge": 9, "sensors": 10, "gun_velocity": 11, "capacitor": 12,
    "reactor": 13, "jets": 14,
}
_PI = math.pi
_TAU = 2.0 * _PI
_STEER_OFFSETS = tuple(
    (offset, math.cos(offset))
    for offset in (
        k * _PI / 8.0 - (_TAU if k * _PI / 8.0 > _PI else 0.0)
        for k in range(16)
    )
)


def wrap(a):
    # All callers pass differences in [-2π, 2π], so one correction is enough.
    if a > _PI:
        a -= _TAU
    elif a < -_PI:
        a += _TAU
    return a


class World:
    def __init__(self, mapdir, files):
        self.ok = False
        self.solids = {}
        self.mech_radius, self.mech_height, self.step_up = 2.5, 9, 1.2
        try:
            with open(os.path.join(mapdir, "v2/world.json")) as source:
                w = json.load(source)
            t = w["terrain"]
            self.nx, self.ny, self.tminx, self.tminy, self.step = t["nx"], t["ny"], t["minX"], t["minY"], t["step"]
            with open(os.path.join(mapdir, "v2/terrain.i16"), "rb") as source:
                raw = source.read()
            self.h = array("h")
            self.h.frombytes(raw)
            if sys.byteorder == "big":
                self.h.byteswap()
            b = w["bounds"]
            self.minx, self.miny = b["minX"], b["minY"]
            self.maxx, self.maxy = b['maxX'], b['maxY']
            self.trees = w['trees']
            for p in w['prisms']:
                ring = list(zip(p['ring'][::2],p['ring'][1::2]))
                holes = [list(zip(h[::2],h[1::2])) for h in p.get('holes',[]) if h]
                entry = (p['base'],p['top'],ring,holes)
                for bx in range(math.floor(min(x for x,y in ring)/32),math.floor(max(x for x,y in ring)/32)+1):
                    for by in range(math.floor(min(y for x,y in ring)/32),math.floor(max(y for x,y in ring)/32)+1):
                        self.solids.setdefault((bx,by),[]).append(entry)
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

    def solid_at(self, x, y, low, high):
        """Test physical prisms at this height. Trees affect vision, not bullets or walls."""
        for base, top, ring, holes in self.solids.get((math.floor(x/32),math.floor(y/32)), ()):
            if base < high and top > low and self._inside(ring,x,y) and not any(self._inside(h,x,y) for h in holes):
                return True
        return False

    def walk_clear(self, pos, dx, dy, maxd=18):
        if not self.ok:
            return maxd
        x,y,z = pos
        height = self.mech_height
        radius = self.mech_radius
        for d in range(2,int(maxd)+1,2):
            px,py = x+dx*d,y+dy*d
            # A roof remains walkable while its top is beneath the feet.
            floor = self.ground(px,py)
            for base, top, ring, holes in self.solids.get((math.floor(px/32),math.floor(py/32)), ()):
                if top <= z+self.step_up and top>floor and self._inside(ring,px,py) and not any(self._inside(h,px,py) for h in holes):
                    floor = top
            if floor > z+self.step_up or floor < z-3:
                return max(0,d-2)
            if not self.minx+radius<px<self.maxx-radius or not self.miny+radius<py<self.maxy-radius:
                return max(0,d-2)
            for ox,oy in ((0,0),(radius,0),(-radius,0),(0,radius),(0,-radius)):
                if self.solid_at(px+ox,py+oy,z+self.step_up,z+height):
                    return max(0,d-2)
        return maxd

    def los(self, a, b):
        if not self.ok:
            return False
        length = math.dist(a,b)
        samples = max(1,math.ceil(length/2))
        for step in range(1,samples):
            f = step/samples
            x,y,z = (a[i]+(b[i]-a[i])*f for i in range(3))
            if self.ground(x,y)>z or self.solid_at(x,y,z-0.01,z+0.01):
                return False
        return True



class Bot:
    name = 'CipherX-Aware'

    def __init__(self, hello):
        self.cfg = hello['config']
        self.world = World(hello['map']['dir'], hello['map'].get('files'))
        self.source = None
        self.attacker = None
        self.source_until = -1
        self.last_pos = None
        self.stuck = 0
        self.side = 1 if hello['slot'] % 2 else -1
        self.cover_goal = None
        self.cover_until = -1
        self.loot_id = None
        self.loot_since = -1
        self.avoid_loot = {}
        self.previous_move = [0, 0]

    def steer(self, pos, desired, zone, urgent):
        x, y, z = pos
        length = math.hypot(*desired)
        if length < 0.01:
            return [0.0, 0.0]
        heading = math.atan2(desired[1], desired[0])
        best_score, best = -math.inf, [0.0, 0.0]
        current = math.hypot(x-zone['center'][0], y-zone['center'][1])
        # Ground steering uses the mech's current elevation, including roof surfaces.
        for offset, alignment in _STEER_OFFSETS:
            angle = heading + offset
            dx, dy = math.cos(angle), math.sin(angle)
            clear = self.world.walk_clear(pos, dx, dy, 18)
            if clear < 1:
                continue
            travel = min(10.0, clear)
            nx, ny = x+travel*dx, y+travel*dy
            remaining = math.hypot(nx-zone['center'][0], ny-zone['center'][1])
            score = alignment + min(clear, 18)/36
            score += 0.15 * (dx*self.previous_move[0]+dy*self.previous_move[1])
            if urgent:
                score += 4*(current-remaining)/10
            else:
                score -= max(0, remaining-zone['radius']+4)/8
            if self.stuck >= 5:
                score += 0.8*self.side*math.sin(offset)
            if score > best_score:
                best_score, best = score, [dx*min(1.0,length), dy*min(1.0,length)]
        return best

    def target_point(self, enemy, flight=0):
        scale = 1 + self.cfg['cubes'].get('size_per_cube', 0.05)*enemy.get('cubes', 0)
        p, v = enemy['pos'], enemy['vel']
        vertical = v[2]*flight
        # A rising/falling target is predicted with gravity over the bullet flight.
        if abs(v[2]) > 0.5:
            vertical -= 0.5*self.cfg['mech']['gravity']*flight*flight
        return [p[0]+v[0]*flight, p[1]+v[1]*flight, p[2]+4.5*scale+vertical]

    def aim_at(self, origin, point):
        dx, dy, dz = (point[i]-origin[i] for i in range(3))
        return [math.atan2(dy, dx), math.atan2(dz, math.hypot(dx,dy))]

    def aligned(self, aim, desired, distance, radius=2.5, half_height=3.5):
        return (abs(wrap(aim[0]-desired[0])) < math.atan2(radius,max(1,distance)) + 0.008
                and abs(aim[1]-desired[1]) < math.atan2(half_height,max(1,distance)))

    def loot(self, obs, danger, urgent):
        if danger or urgent:
            self.loot_id = None
            return None
        me = obs['self']
        x,y,z = me['pos']
        best, cost = None, math.inf
        held = {w['kind'] for w in me['weapons'] if w}
        # Cubes immediately improve HP and damage. Weapons are selected by reachability.
        for kind, items in (('cube',obs['cubes']), ('weapon',obs['pickups'])):
            for item in items:
                px,py,pz = item['pos']
                distance = math.hypot(px-x,py-y)
                if distance > 55 or abs(pz-z) >= 4 or self.avoid_loot.get(item['id'],-1)>obs['t']:
                    continue
                if math.hypot(px-obs['zone']['center'][0],py-obs['zone']['center'][1]) > obs['zone']['radius']-5:
                    continue
                if kind == 'weapon' and (item['kind'] not in ('laser','lightning') or item['kind'] in held or all(me['weapons'])):
                    continue
                if not self.world.los((x,y,z+2),(px,py,pz+2)):
                    continue
                price = distance + (0 if kind=='cube' else 10 if item['kind']=='lightning' else 15)
                if item['id']==self.loot_id:
                    price -= 5
                if price < cost:
                    cost,best = price,(kind,item,distance)
        if best:
            if best[1]['id'] != self.loot_id:
                self.loot_id,self.loot_since = best[1]['id'],obs['t']
            elif obs['t']-self.loot_since > 8:
                self.avoid_loot[self.loot_id] = obs['t']+20
                self.loot_id = None
                return None
        return best

    def act(self, obs):
        me, now = obs['self'], obs['t']
        x,y,z = me['pos']
        stats = me['stats']
        self.world.mech_radius = stats.get('radius',2.5)
        self.world.mech_height = stats.get('height',9)
        self.world.step_up = stats.get('step_up',1.2)
        weapons = me['weapons']
        action = {'tick':obs['tick'], 'jump':False, 'fire':[False,False]}
        if obs.get('offer'):
            choices = obs['offer']['choices']
            if choices:
                action['pick'] = min(range(len(choices)),key=lambda i:UPG_RANK.get(choices[i]['id'],500))
        enemies = sorted((e for e in obs['mechs'] if e['kind']=='mech' and e.get('hp',1)>0),
                         key=lambda e:math.dist(e['pos'][:2],(x,y)))
        for event in obs['events']:
            if event['type']=='damaged' and event.get('bearing') is not None:
                self.source,self.source_until = event['bearing'],now+2.5
                self.attacker = event.get('by')
        if now >= self.source_until:
            self.source = None
            self.attacker = None
        # Projectile velocities point away from their source.
        nearest_bullet = min(obs['projectiles'],key=lambda p:math.dist(p['pos'],me['pos']),default=None)
        if self.source is None and nearest_bullet:
            vx,vy,_ = nearest_bullet['vel']
            if math.hypot(vx,vy)>1:
                self.source = math.atan2(-vy,-vx)
                self.source_until = now+0.5
        heard = next((s for s in obs['sounds'] if s['kind'] in ('gun','laser','laser_charge','mortar','lightning')
                      and s['dist'] in ('near','mid') and s['age']<=0.25),None)
        if self.source is None and heard:
            self.source,self.source_until = heard['bearing'],now+0.7
        zone = obs['zone']
        current_distance = math.dist((x,y),zone['center'])
        margin = min(15.0, zone['radius']*0.2)
        urgent = not me['in_zone'] or current_distance > max(0,zone['radius']-margin)
        center, radius = zone['center'], zone['radius']
        # Rotate according to travel time to the next circle instead of a fixed early deadline.
        next_distance = math.dist((x,y),zone['next_center'])
        travel_time = max(0,next_distance-zone['next_radius']+10)/max(1,stats.get('walk_speed',11.7))
        if me['in_zone'] and now+travel_time+8 >= zone['shrink_start']:
            center,radius = zone['next_center'],zone['next_radius']
        if not me['in_zone']:
            center = zone['center']
        distance = math.dist((x,y),center)
        want = [0.0,0.0]
        if urgent or distance>max(1,radius*0.65):
            want = [(center[0]-x)/max(distance,0.01),(center[1]-y)/max(distance,0.01)]
        threat = enemies[0] if enemies else None
        hp_frac = me['hp']/max(1,me['max_hp'])
        if threat and not urgent:
            dx,dy = threat['pos'][0]-x,threat['pos'][1]-y
            d = max(0.01,math.hypot(dx,dy))
            radial = -1.0 if d<55 or hp_frac<0.4 or len(enemies)>1 else 0.0
            want[0] += -self.side*dy/d + radial*dx/d
            want[1] += self.side*dx/d + radial*dy/d
            # Stay mobile while choosing actual geometry that screens opponents.
            if self.world.ok and me['grounded'] and now>=self.cover_until:
                self.cover_until = now+0.5
                self.cover_goal = None
                best = -math.inf
                for offset in (0.6,-0.6,1.2,-1.2,1.8,-1.8):
                    angle = math.atan2(-dy,-dx)+offset
                    ux,uy = math.cos(angle),math.sin(angle)
                    reach = min(14,self.world.walk_clear(me['pos'],ux,uy,17))
                    if reach<5:
                        continue
                    nx,ny = x+ux*reach,y+uy*reach
                    if math.dist((nx,ny),zone['center'])>zone['radius']-margin:
                        continue
                    ground = self.world.ground(nx,ny)
                    screened = sum(not self.world.los((e['pos'][0],e['pos'][1],e['pos'][2]+7),(nx,ny,ground+7)) for e in enemies[:2])
                    score = screened*3 + reach/14 - abs(offset)*0.1
                    if screened and score>best:
                        best,self.cover_goal = score,(nx,ny)
            if self.cover_goal:
                dx,dy = self.cover_goal[0]-x,self.cover_goal[1]-y
                d = math.hypot(dx,dy)
                if d>2:
                    want = [dx/d,dy/d]
        elif not threat:
            self.cover_goal = None
        dodge = False
        for pr in obs['projectiles']:
            rx,ry = pr['pos'][0]-x,pr['pos'][1]-y
            vx,vy = pr['vel'][0]-me['vel'][0],pr['vel'][1]-me['vel'][1]
            v2 = vx*vx+vy*vy
            mortar = pr['kind']=='mortar'
            if mortar:
                gravity = self.cfg['weapons']['mortar']['gravity']
                height = pr['pos'][2]-z
                vz = pr['vel'][2]
                root = vz*vz+2*gravity*max(0,height)
                eta = (vz+math.sqrt(root))/gravity
                splash = self.cfg['weapons']['mortar']['splash_radius']+stats.get('radius',2.5)
            else:
                if v2<1:
                    continue
                eta = -(rx*vx+ry*vy)/v2
                splash = stats.get('radius',2.5)+1.5
            if not 0<eta<1.2:
                continue
            cx,cy = rx+vx*eta,ry+vy*eta
            height = pr['pos'][2]-z+(pr['vel'][2]-me['vel'][2])*eta
            if not mortar and not -1<height<stats.get('height',9)+1:
                continue
            separation = math.hypot(cx,cy)
            if separation<splash:
                if separation<0.5:
                    cx,cy = (-vy,vx) if v2>1 else (1.0,0.0)
                    separation = max(0.01,math.hypot(cx,cy))
                if not urgent:
                    want[0] -= 1.6*cx/separation
                    want[1] -= 1.6*cy/separation
                dodge = True
        danger = bool(enemies or self.source is not None or dodge)
        loot = self.loot(obs,danger,urgent)
        if loot:
            kind,item,d = loot
            if kind=='weapon' and d<self.cfg['loot']['pickup_radius'] and abs(item['pos'][2]-z)<4:
                action['take'] = item['id']
            else:
                want = [(item['pos'][0]-x)/max(0.01,d),(item['pos'][1]-y)/max(0.01,d)]
        if self.source is not None and not threat and not urgent and not loot:
            want[0] -= math.cos(self.source)*0.7
            want[1] -= math.sin(self.source)*0.7
        if self.last_pos and math.hypot(*self.previous_move)>0.2:
            self.stuck = self.stuck+1 if math.dist((x,y),self.last_pos)<0.25 else 0
        if self.stuck>=15:
            self.side = -self.side
            self.stuck = 5
        self.last_pos = (x,y)
        movement = self.steer(me['pos'],want,zone,urgent)
        self.previous_move = movement
        action['move'] = movement
        # In-air boost causes ascent. Ground boost is used only for urgent escape.
        action['boost'] = bool(me['grounded'] and me['energy']>12 and math.hypot(*movement)>0.3
                               and (urgent or dodge or (threat and hp_frac<0.4)))
        origin = [x,y,z+stats.get('muzzle_height',7)]
        targeting = sorted(enemies,key=lambda e:e['id']!=self.attacker) if self.attacker is not None else enemies
        max_range = max((w['stats'].get('range',0) for w in weapons if w),default=0)
        target = next((e for e in targeting if math.dist(origin,self.target_point(e))<max_range
                       and self.world.los(origin,self.target_point(e))),None)
        aim = [me['aim'][0],me['aim'][1]]
        if target:
            gun = next((w for w in weapons if w and w['kind']=='gun'),None)
            speed = gun['stats'].get('speed',252) if gun else 252
            flight = max(0,math.dist(origin,self.target_point(target))-3)/speed
            for _ in range(2):
                point = self.target_point(target,flight)
                flight = max(0,math.dist(origin,point)-3)/speed
            gun_point = self.target_point(target,flight)
            direct = self.target_point(target)
            aim = self.aim_at(origin,gun_point)
            laser = next((w for w in weapons if w and w['kind']=='laser'),None)
            laser_active = laser and (laser.get('charge',0)>0 or laser.get('cooldown',0)<=0)
            if laser_active:
                aim = self.aim_at(origin,direct)
            for slot,weapon in enumerate(weapons):
                if not weapon:
                    continue
                spec,kind = weapon['stats'],weapon['kind']
                point = direct if kind!='gun' else gun_point
                desired = self.aim_at(origin,point)
                d = math.dist(origin,point)
                clear = self.world.los(origin,point)
                if kind=='gun':
                    action['fire'][slot] = bool(weapon.get('ammo',0)>0 and weapon.get('reload',0)<=0
                        and d<=spec['range'] and clear and self.aligned(me['aim'],desired,d))
                elif kind=='lightning':
                    action['fire'][slot] = bool(d<=spec['range'] and clear and me['energy']>=spec['energy']
                        and abs(wrap(me['aim'][0]-desired[0]))<math.radians(spec['cone_deg'])/2)
                elif kind=='laser':
                    charge = weapon.get('charge',0)
                    aligned = self.aligned(me['aim'],desired,d)
                    if charge>0:
                        action['fire'][slot] = not (aligned and clear and charge>=spec['charge']*0.9)
                    else:
                        action['fire'][slot] = bool(weapon.get('cooldown',0)<=0 and d<=spec['range'] and clear
                                                   and self.aligned(me['aim'],desired,d,4,4))
        else:
            aim[0] = self.source if self.source is not None else wrap(aim[0]+0.35)
            # Releasing an already charged laser necessarily discharges it under game rules.
        aim[1] = max(stats.get('min_pitch',-0.7),min(stats.get('max_pitch',1.3),aim[1]))
        action['aim'] = aim
        for weapon in weapons:
            if weapon and weapon['kind']=='gun' and weapon.get('reload',0)<=0:
                if weapon.get('ammo',0)==0 or (not danger and weapon['ammo']<weapon['stats']['mag']*0.6):
                    action['reload'] = True
        return action


if __name__ == '__main__':
    mf.run(Bot)
