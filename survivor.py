"""Mech Royale bot with typed targets, ground navigation and gated weapon control."""
import json
import math
import os
import sys
import time
from array import array

import mechfight as mf
from tactics import Navigator, visibility, select_target, choose_evasion, jump_route

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
    _polygon_cache = {}
    def __init__(self, mapdir, files):
        self.ok = False
        self.solids = {}
        self.deadline = math.inf
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
                self._inside(ring,0,0)
                for hole in holes:
                    self._inside(hole,0,0)
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
        # Static polygons are indexed into horizontal bands once. A point query
        # only needs edges that cross its own band, even for a complex stadium.
        key = id(pts)
        cached = World._polygon_cache.get(key)
        if cached is None:
            if not pts:
                return False
            bounds = (min(p[0] for p in pts),max(p[0] for p in pts),
                      min(p[1] for p in pts),max(p[1] for p in pts))
            bands = {}
            for i,(xi,yi) in enumerate(pts):
                xj,yj = pts[(i+1)%len(pts)]
                if yi==yj:
                    continue
                for band in range(math.floor(min(yi,yj)/8),math.floor(max(yi,yj)/8)+1):
                    bands.setdefault(band,[]).append((xi,yi,xj,yj))
            # Retain the polygon so its identity cannot be reused by a new one.
            cached = World._polygon_cache[key] = (pts,bounds,bands)
        _,bounds,bands = cached
        if x<bounds[0] or x>bounds[1] or y<bounds[2] or y>bounds[3]:
            return False
        c = False
        for xi,yi,xj,yj in bands.get(math.floor(y/8),()):
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
                c = not c
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
        previous_floor = z
        previous_terrain = self.ground(x,y)
        slope_limit = math.tan(math.radians(getattr(self,'max_slope',50)))
        for sample in range(1, math.ceil(maxd*2)+1):
            d = min(maxd,sample*0.5)
            if time.perf_counter()>=self.deadline:
                return max(0,d-0.5)
            px,py = x+dx*d,y+dy*d
            # A roof remains walkable while its top is beneath the feet.
            floor = self.ground(px,py)
            for base, top, ring, holes in self.solids.get((math.floor(px/32),math.floor(py/32)), ()):
                if top <= previous_floor+self.step_up and top>floor and self._inside(ring,px,py) and not any(self._inside(h,px,py) for h in holes):
                    floor = top
            terrain = self.ground(px,py)
            steep = terrain > previous_terrain + 0.5*slope_limit + 1e-6
            if floor > previous_floor+self.step_up or floor < previous_floor-3 or (steep and floor==terrain):
                return max(0,d-0.5)
            if not self.minx+radius<px<self.maxx-radius or not self.miny+radius<py<self.maxy-radius:
                return max(0,d-0.5)
            for ox,oy in ((0,0),(radius,0),(-radius,0),(0,radius),(0,-radius)):
                if self.solid_at(px+ox,py+oy,floor+self.step_up,floor+height):
                    return max(0,d-0.5)
            previous_floor, previous_terrain = floor, terrain
        return maxd

    def air_clear(self, pos, dx, dy, maxd=18):
        if not self.ok:
            return maxd
        x,y,z = pos
        radius = self.mech_radius
        for sample in range(1,math.ceil(maxd*2)+1):
            distance = min(maxd,sample*0.5)
            if time.perf_counter()>=self.deadline:
                return max(0,distance-0.5)
            px,py = x+dx*distance,y+dy*distance
            if not self.minx+radius<px<self.maxx-radius or not self.miny+radius<py<self.maxy-radius:
                return max(0,distance-0.5)
            for ox,oy in ((0,0),(radius,0),(-radius,0),(0,radius),(0,-radius)):
                if self.ground(px+ox,py+oy)>z+self.step_up or self.solid_at(px+ox,py+oy,z+0.1,z+self.mech_height):
                    return max(0,distance-0.5)
        return maxd

    @staticmethod
    def segment_hits_solid(a, b, solid):
        base,top,ring,holes = solid
        dz = b[2]-a[2]
        low,high = 0.0,1.0
        if abs(dz)<1e-10:
            if not base<=a[2]<=top:
                return False
        else:
            enter,leave = sorted(((base-a[2])/dz,(top-a[2])/dz))
            low,high = max(low,enter),min(high,leave)
            if low>high:
                return False
        dx,dy = b[0]-a[0],b[1]-a[1]
        cuts = [low,high]
        for polygon in (ring,*holes):
            for i, (x1,y1) in enumerate(polygon):
                x2,y2 = polygon[(i+1)%len(polygon)]
                ex,ey = x2-x1,y2-y1
                cross = dx*ey-dy*ex
                if abs(cross)<1e-10:
                    continue
                rx,ry = x1-a[0],y1-a[1]
                t = (rx*ey-ry*ex)/cross
                u = (rx*dy-ry*dx)/cross
                if low<=t<=high and 0<=u<=1:
                    cuts.append(t)
        cuts.sort()
        probes = cuts + [(left+right)/2 for left,right in zip(cuts,cuts[1:])]
        for t in probes:
            x,y = a[0]+dx*t,a[1]+dy*t
            if World._inside(ring,x,y) and not any(World._inside(h,x,y) for h in holes):
                return True
        return False

    def los(self, a, b):
        if not self.ok:
            return False
        if time.perf_counter()>=self.deadline:
            return False
        length = math.dist(a,b)
        # Intersect complete polygon segments so thin walls cannot fall between ray samples.
        checked = set()
        for bx in range(math.floor(min(a[0],b[0])/32),math.floor(max(a[0],b[0])/32)+1):
            for by in range(math.floor(min(a[1],b[1])/32),math.floor(max(a[1],b[1])/32)+1):
                for solid in self.solids.get((bx,by),()):
                    if time.perf_counter()>=self.deadline:
                        return False
                    identity = id(solid)
                    if identity not in checked:
                        checked.add(identity)
                        if self.segment_hits_solid(a,b,solid):
                            return False
        samples = max(1,math.ceil(length/0.5))
        for step in range(1,samples):
            if step%16==0 and time.perf_counter()>=self.deadline:
                return False
            f = step/samples
            x,y,z = (a[i]+(b[i]-a[i])*f for i in range(3))
            if self.ground(x,y)>z:
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
        self.grounded = True
        self.cover_threat = None
        self.navigator = Navigator()
        self.target_id = None
        self.motion = {}
        self.now = 0
        self.jump_until = -1
        self.jump_goal = None

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
            if time.perf_counter()>=self.world.deadline:
                break
            angle = heading + offset
            dx, dy = math.cos(angle), math.sin(angle)
            clear = (self.world.walk_clear(pos, dx, dy, 10) if self.grounded
                     else self.world.air_clear(pos, dx, dy, 10))
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
            if offset==0 and clear>=10 and self.stuck<5:
                # An unobstructed desired route needs no alternative-ray search.
                break
        return best

    def target_point(self, enemy, flight=0):
        scale = 1 + self.cfg['cubes'].get('size_per_cube', 0.05)*enemy.get('cubes', 0)
        p, v = enemy['pos'], enemy['vel']
        vertical = v[2]*flight
        # A rising/falling target is predicted with gravity over the bullet flight.
        if abs(v[2]) > 0.5:
            acceleration = self.motion.get(enemy['id'],{}).get('az',-self.cfg['mech']['gravity'])
            vertical += 0.5*acceleration*flight*flight
        px,py = p[0]+v[0]*flight,p[1]+v[1]*flight
        floor = self.world.ground(px,py)
        return [px,py,max(floor,p[2]+vertical)+4.5*scale]

    def swap_slot(self, me, kind, radius):
        if any(w and w.get('charge',0)>0 for w in me['weapons']):
            return None
        desired = 'lightning' if radius<90 else 'laser'
        if kind!=desired:
            return None
        return next((i for i,w in enumerate(me['weapons']) if w and w['kind'] not in ('gun',kind)),None)

    def safe_objective(self, obs, point, extra=0):
        zone,me = obs['zone'],obs['self']
        travel = math.dist(me['pos'][:2],point[:2])/max(1,me['stats'].get('walk_speed',11.7))
        if math.dist(point[:2],zone['center'])>zone['radius']-8:
            return False
        return (obs['t']+travel+extra+8<zone['shrink_start'] or
                math.dist(point[:2],zone['next_center'])<zone['next_radius']-8)

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
                if distance > 55 or self.avoid_loot.get(item['id'],-1)>obs['t']:
                    continue
                if abs(pz-z)>=4 and jump_route(self.world,me,item['pos'],self.cfg) is None:
                    continue
                if math.hypot(px-obs['zone']['center'][0],py-obs['zone']['center'][1]) > obs['zone']['radius']-5:
                    continue
                if not self.safe_objective(obs,item['pos']):
                    continue
                if kind == 'weapon' and (item['kind'] not in ('laser','lightning') or item['kind'] in held or
                        (all(me['weapons']) and self.swap_slot(me,item['kind'],obs['zone']['radius']) is None)):
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
        self.world.deadline = time.perf_counter()+0.020
        decision_deadline = self.world.deadline
        me, now = obs['self'], obs['t']
        self.now = now
        x,y,z = me['pos']
        stats = me['stats']
        self.world.mech_radius = stats.get('radius',2.5)
        self.world.mech_height = stats.get('height',9)
        self.world.step_up = stats.get('step_up',1.2)
        self.world.max_slope = stats.get('max_slope_deg',50)
        self.grounded = me['grounded']
        weapons = me['weapons']
        action = {'tick':obs['tick'], 'jump':False, 'fire':[False,False]}
        if obs.get('offer'):
            choices = obs['offer']['choices']
            if choices:
                action['pick'] = min(range(len(choices)),key=lambda i:UPG_RANK.get(choices[i]['id'],500))
        enemies = sorted((e for e in obs['mechs'] if e['kind']=='mech' and e.get('hp',1)>0),
                         key=lambda e:math.dist(e['pos'][:2],(x,y)))
        history = {}
        for e in enemies:
            old = self.motion.get(e['id'])
            az = -self.cfg['mech']['gravity']
            if old and .04<now-old['t']<.4:
                az = max(-self.cfg['mech']['gravity'],min(50,(e['vel'][2]-old['vz'])/(now-old['t'])))
            history[e['id']] = dict(t=now,vz=e['vel'][2],az=az)
        self.motion = history
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
        reloading = any(w and w['kind']=='gun' and (w.get('reload',0)>0 or w.get('ammo',0)==0) for w in weapons)
        hp_frac = me['hp']/max(1,me['max_hp'])
        self.world.deadline = min(decision_deadline,time.perf_counter()+.006)
        if threat and not urgent:
            dx,dy = threat['pos'][0]-x,threat['pos'][1]-y
            d = max(0.01,math.hypot(dx,dy))
            radial = -1.0 if d<55 or hp_frac<0.4 or len(enemies)>1 or reloading else 0.0
            want[0] += -self.side*dy/d + radial*dx/d
            want[1] += self.side*dx/d + radial*dy/d
            # Stay mobile while choosing actual geometry that screens opponents.
            current_threat = (threat['id'],tuple(threat['pos']))
            flanked = (self.cover_threat is not None and
                       (current_threat[0]!=self.cover_threat[0] or
                        math.dist(current_threat[1],self.cover_threat[1])>3))
            if self.cover_goal:
                nx,ny = self.cover_goal
                point = (nx,ny,z+stats.get('muzzle_height',7))
                exposed = any(visibility(self.world,(e['pos'][0],e['pos'][1],e['pos'][2]+7),point) is not False for e in enemies)
                if flanked or exposed or math.dist((nx,ny),zone['center'])>zone['radius']-margin:
                    self.cover_goal = None
                    self.cover_until = -1
            if self.world.ok and me['grounded'] and (flanked or now>=self.cover_until):
                self.cover_threat = current_threat
                self.cover_until = now+0.5
                self.cover_goal = None
                best = -math.inf
                planning_deadline = min(self.world.deadline,time.perf_counter()+0.004)
                for offset in (0.6,-0.6,1.2,-1.2,1.8,-1.8):
                    if time.perf_counter()>=planning_deadline:
                        break
                    angle = math.atan2(-dy,-dx)+offset
                    ux,uy = math.cos(angle),math.sin(angle)
                    reach = min(14,self.world.walk_clear(me['pos'],ux,uy,17))
                    if reach<5:
                        continue
                    nx,ny = x+ux*reach,y+uy*reach
                    if math.dist((nx,ny),zone['center'])>zone['radius']-margin:
                        continue
                    ground = self.world.ground(nx,ny)
                    views = [visibility(self.world,(e['pos'][0],e['pos'][1],e['pos'][2]+7),(nx,ny,ground+7)) for e in enemies]
                    if any(v is None for v in views):
                        break
                    screened = sum(v is False for v in views)
                    score = screened*3 - (len(enemies)-screened)*4 + reach/14 - abs(offset)*0.1
                    if screened and score>best:
                        best,self.cover_goal = score,(nx,ny)
            if self.cover_goal:
                dx,dy = self.cover_goal[0]-x,self.cover_goal[1]-y
                d = math.hypot(dx,dy)
                if d>2:
                    want = [dx/d,dy/d]
        elif not threat:
            self.cover_goal = None
        self.world.deadline = decision_deadline
        want,dodge = choose_evasion(me,obs['projectiles'],enemies,want,zone,urgent,self.cfg,self.side)
        danger = bool(enemies or self.source is not None or dodge)
        loot = self.loot(obs,danger,urgent)
        goal = tuple(center) if urgent or distance>max(1,radius*.65) else None
        if self.cover_goal and not urgent:
            goal = self.cover_goal
        crate = None
        if loot:
            kind,item,d = loot
            goal = item['pos'][:2]
            if kind=='weapon' and d<self.cfg['loot']['pickup_radius'] and abs(item['pos'][2]-z)<4:
                action['take'] = item['id']
                if all(weapons):
                    action['drop'] = self.swap_slot(me,item['kind'],zone['radius'])
            else:
                want = [(item['pos'][0]-x)/max(0.01,d),(item['pos'][1]-y)/max(0.01,d)]
        elif not danger and not urgent:
            drops = [d for d in obs['drops'] if d.get('lands_in',0)<=0 and
                     math.dist(me['pos'][:2],d['pos'][:2])<50 and self.safe_objective(obs,d['pos'],d.get('capture_s',4))]
            if drops:
                drop = min(drops,key=lambda d:math.dist(me['pos'][:2],d['pos'][:2]))
                d = math.dist(me['pos'][:2],drop['pos'][:2])
                goal = drop['pos'][:2] if d>drop.get('radius',8)*.65 else None
                want = [(drop['pos'][0]-x)/max(1,d),(drop['pos'][1]-y)/max(1,d)] if goal else [0,0]
            else:
                crate = next((c for c in obs['crates'] if c.get('hp',0)>0 and
                              math.dist(me['pos'],c['pos'])<45 and self.safe_objective(obs,c['pos'],4)
                              and visibility(self.world,(x,y,z+7),(c['pos'][0],c['pos'][1],c['pos'][2]+2)) is True),None)
        if self.source is not None and not threat and not urgent and not loot:
            want[0] -= math.cos(self.source)*0.7
            want[1] -= math.sin(self.source)*0.7
        if self.last_pos and math.hypot(*self.previous_move)>0.2:
            self.stuck = self.stuck+1 if math.dist((x,y),self.last_pos)<0.25 else 0
        if self.stuck>=15:
            self.side = -self.side
            self.stuck = 5
        self.last_pos = (x,y)
        direct_clear = math.inf
        if goal and me['grounded'] and not dodge:
            dx,dy = goal[0]-x,goal[1]-y
            d = math.hypot(dx,dy)
            direct_clear = self.world.walk_clear(me['pos'],dx/max(.01,d),dy/max(.01,d),min(10,d))
            if direct_clear<min(10,d)-.01 or self.navigator.path or self.navigator.frontier:
                waypoint = self.navigator.route(self.world,me['pos'],goal)
                if waypoint:
                    dx,dy = waypoint[0]-x,waypoint[1]-y
                    length = math.hypot(dx,dy)
                    want = [dx/max(.01,length),dy/max(.01,length)]
        self.world.deadline = min(decision_deadline,time.perf_counter()+.005)
        movement = self.steer(me['pos'],want,zone,urgent)
        self.world.deadline = decision_deadline
        if self.jump_goal and now<self.jump_until and not me['grounded']:
            movement = self.jump_goal
        elif self.jump_goal and now>=self.jump_until:
            self.jump_goal = None
        if goal and (self.stuck>=5 or direct_clear<5) and not dodge and not reloading and now>=self.jump_until and not any(w and w.get('charge',0)>0 for w in weapons):
            jump = jump_route(self.world,me,goal,self.cfg)
            if jump:
                movement,duration,_ = jump
                action['jump'] = True
                self.jump_goal = movement
                self.jump_until = now+duration+.1
        self.previous_move = movement
        action['move'] = movement
        # In-air boost causes ascent. Ground boost is used only for urgent escape.
        action['boost'] = bool(me['grounded'] and me['energy']>12 and math.hypot(*movement)>0.3
                               and not action['jump'] and (urgent or dodge or (threat and hp_frac<0.4)))
        origin = [x,y,z+stats.get('muzzle_height',7)]
        max_range = max((w['stats'].get('range',0) for w in weapons if w),default=0)
        target = select_target(enemies,origin,self.world,max_range,self.target_id,self.attacker)
        self.target_id = target['id'] if target else None
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
            gun_ready = gun and gun.get('ammo',0)>0 and gun.get('reload',0)<=0 and gun.get('cooldown',0)<=.05
            laser_active = laser and not urgent and (laser.get('charge',0)>0 or
                           (laser.get('cooldown',0)<=0 and (not gun_ready or math.dist(origin,direct)>100)))
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
                    action['fire'][slot] = bool(weapon.get('ammo',0)>0 and weapon.get('reload',0)<=0 and weapon.get('cooldown',0)<=0.05
                        and d<=spec['range'] and clear and self.aligned(me['aim'],desired,d))
                elif kind=='lightning':
                    reserve = 15 if urgent or dodge or hp_frac<.4 else 0
                    action['fire'][slot] = bool(d<=spec['range'] and clear and me['energy']>=spec['energy']+reserve and weapon.get('cooldown',0)<=.05
                        and abs(wrap(me['aim'][0]-desired[0]))<math.radians(spec['cone_deg'])/2)
                elif kind=='laser':
                    charge = weapon.get('charge',0)
                    aligned = self.aligned(me['aim'],desired,d)
                    if urgent:
                        # Releasing a charge is mandatory to regain full escape speed.
                        action['fire'][slot] = False
                    elif charge>0:
                        action['fire'][slot] = not (aligned and clear and charge>=spec['charge']*0.9)
                    else:
                        action['fire'][slot] = bool(laser_active and weapon.get('cooldown',0)<=0 and d<=spec['range'] and clear
                                                   and self.aligned(me['aim'],desired,d,4,4))
        else:
            aim[0] = self.source if self.source is not None else wrap(aim[0]+0.35)
            charged = next((w for w in weapons if w and w['kind']=='laser' and w.get('charge',0)>0),None)
            if charged:
                # The protocol has no cancel. Slew toward nearby ground before releasing,
                # keeping the old yaw instead of sweeping a charged beam across the arena.
                aim = [me['aim'][0],stats.get('min_pitch',-0.7)]
                for slot,weapon in enumerate(weapons):
                    if weapon is charged:
                        action['fire'][slot] = bool(not urgent and abs(me['aim'][1]-aim[1])>0.08)
        aim[1] = max(stats.get('min_pitch',-0.7),min(stats.get('max_pitch',1.3),aim[1]))
        action['aim'] = aim
        if crate and not target:
            point = [crate['pos'][0],crate['pos'][1],crate['pos'][2]+2]
            action['aim'] = self.aim_at(origin,point)
            for slot,weapon in enumerate(weapons):
                if weapon and weapon['kind']=='gun':
                    action['fire'][slot] = bool(weapon.get('ammo',0)>0 and weapon.get('reload',0)<=0 and weapon.get('cooldown',0)<=.05
                        and self.aligned(me['aim'],action['aim'],math.dist(origin,point),1.5,1.5))
        for weapon in weapons:
            if weapon and weapon['kind']=='gun' and weapon.get('reload',0)<=0:
                if weapon.get('ammo',0)==0 or (not danger and weapon['ammo']<weapon['stats']['mag']*0.6):
                    action['reload'] = True
        return action


if __name__ == '__main__':
    mf.run(Bot)
