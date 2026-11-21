"""Bounded navigation and combat helpers; no simulation or external dependencies."""
import heapq
import math
import time


class Navigator:
    """Incremental A*: keep the search frontier across observations, verify each edge.

    Routes use a 6 m ground grid. Failed/incomplete searches never claim a route.
    This deliberately does not invent a jump trajectory for tall roofs.
    """
    def __init__(self):
        self.goal = None
        self.path = []
        self.frontier = []
        self.cost = {}
        self.parent = {}
        self.closed = set()

    def route(self, world, pos, goal, budget=0.002):
        if not world.ok or not hasattr(world, 'solid_at'):
            return None
        if abs(pos[2]-world.ground(*pos[:2]))>world.step_up:
            return None
        end = min(world.deadline, time.perf_counter()+budget)
        if self.goal is None or math.dist(goal[:2], self.goal)>8:
            self.__init__()
            self.goal = tuple(goal[:2])
        while self.path and math.dist(pos[:2], self.path[0][:2])<3:
            self.path.pop(0)
        if self.path:
            waypoint = self.path[0]
            dx,dy = waypoint[0]-pos[0],waypoint[1]-pos[1]
            d = math.hypot(dx,dy)
            if d and world.walk_clear(pos,dx/d,dy/d,d)>=d-0.01:
                return waypoint
            self.path = []
            self.frontier = []
        if not self.frontier:
            self.origin = tuple(pos)
            self.cost = {(0,0):0.0}
            self.parent = {}
            self.closed = set()
            self.frontier = [(math.dist(pos[:2],goal[:2]),0.0,(0,0))]
        elif math.dist(pos[:2],self.origin[:2])>12:
            self.frontier = []
            return None
        old_deadline = world.deadline
        world.deadline = end
        try:
            expansions = 0
            while self.frontier and time.perf_counter()<end and expansions<32:
                _,cost,node = heapq.heappop(self.frontier)
                if node in self.closed:
                    continue
                x,y = self.origin[0]+node[0]*6,self.origin[1]+node[1]*6
                z = self.origin[2] if node==(0,0) else world.ground(x,y)
                delta = (goal[0]-x,goal[1]-y)
                distance = math.hypot(*delta)
                if distance<9 and (distance<0.1 or world.walk_clear((x,y,z),delta[0]/distance,delta[1]/distance,distance)>=distance-0.01):
                    chain = [(goal[0],goal[1],world.ground(*goal[:2]))]
                    while node!=(0,0):
                        chain.append((self.origin[0]+node[0]*6,self.origin[1]+node[1]*6,0))
                        node = self.parent[node]
                    self.path = list(reversed(chain))
                    return self.path[0]
                # Requeue a partially expanded node when the slice expires.
                finished = True
                for dx,dy in ((1,0),(0,1),(-1,0),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
                    if time.perf_counter()>=end:
                        finished = False
                        break
                    nxt = (node[0]+dx,node[1]+dy)
                    if nxt in self.closed:
                        continue
                    length = 6*math.hypot(dx,dy)
                    newcost = cost+length
                    if newcost>=self.cost.get(nxt,math.inf):
                        continue
                    if world.walk_clear((x,y,z),dx/math.hypot(dx,dy),dy/math.hypot(dx,dy),length)<length-0.01:
                        if time.perf_counter()>=end:
                            finished = False
                        continue
                    self.cost[nxt] = newcost
                    self.parent[nxt] = node
                    nx,ny = x+dx*6,y+dy*6
                    heapq.heappush(self.frontier,(newcost+math.dist((nx,ny),goal[:2]),newcost,nxt))
                if finished:
                    self.closed.add(node)
                else:
                    heapq.heappush(self.frontier,(cost+distance,cost,node))
                    break
                expansions += 1
                if len(self.cost)>6000:
                    self.frontier = []
                    break
        finally:
            world.deadline = old_deadline
        return None


def visibility(world, a, b):
    """True=clear, False=confirmed blocked, None=not evaluated in time."""
    result = world.los(a,b)
    return None if time.perf_counter()>=world.deadline else result


def jump_route(world, me, goal, cfg):
    """Accept only a sampled, unboosted jump with a supported landing footprint."""
    if not hasattr(world,'solid_at') or not world.ok or not me['grounded']:
        return None
    stats = me['stats']
    launch = stats.get('jump_speed',cfg['mech'].get('jump_speed',0))
    gravity = stats.get('gravity',cfg['mech']['gravity'])
    if launch<=0:
        return None
    x,y,z = me['pos']
    dx,dy = goal[0]-x,goal[1]-y
    distance = math.hypot(dx,dy)
    if distance<1:
        return None
    dx,dy = dx/distance,dy/distance
    speed = stats.get('walk_speed',11.7)
    maximum = launch*launch/(2*gravity)
    radius,height = stats.get('radius',2.5),stats.get('height',9)
    # Choose a nearby landing; tall rooftops need a separate jet planner.
    travel = min(distance,speed*launch*1.6/gravity)
    lx,ly = x+dx*travel,y+dy*travel
    floor = world.ground(lx,ly)
    for base,top,ring,holes in world.solids.get((math.floor(lx/32),math.floor(ly/32)),()):
        if top>floor and top-z<maximum and world._inside(ring,lx,ly) and not any(world._inside(h,lx,ly) for h in holes):
            floor = top
    rise = floor-z
    if not stats.get('step_up',1.2)<rise<maximum-.3:
        return None
    duration = (launch+math.sqrt(launch*launch-2*gravity*rise))/gravity
    velocity = min(speed,travel/duration)
    # Account for acceleration from current velocity instead of assuming an
    # instantaneous change to the requested air speed.
    ux,uy = dx*velocity,dy*velocity
    vx,vy = me['vel'][:2]
    dvx,dvy = ux-vx,uy-vy
    change = math.hypot(dvx,dvy)
    acceleration = stats.get('air_accel',cfg['mech'].get('air_accel',12))
    ramp = change/max(.01,acceleration)
    def position(t):
        if ramp<1e-6:
            return x+ux*t,y+uy*t
        r = min(t,ramp)
        return (x+vx*r+.5*dvx*r*r/ramp+ux*max(0,t-ramp),
                y+vy*r+.5*dvy*r*r/ramp+uy*max(0,t-ramp))
    lx,ly = position(duration)
    offsets = ((0,0),(radius,0),(-radius,0),(0,radius),(0,-radius))
    for i in range(1,41):
        if time.perf_counter()>=world.deadline:
            return None
        t = duration*i/40
        px,py = position(t)
        pz = z+launch*t-.5*gravity*t*t
        if not world.minx+radius<px<world.maxx-radius or not world.miny+radius<py<world.maxy-radius:
            return None
        for ox,oy in offsets:
            if world.ground(px+ox,py+oy)>pz+.05 or world.solid_at(px+ox,py+oy,pz+.1,pz+height):
                return None
    for ox,oy in offsets:
        if abs(world.ground(lx+ox,ly+oy)-floor)>.3 and not world.solid_at(lx+ox,ly+oy,floor-.2,floor):
            return None
    return ([dx*velocity/speed,dy*velocity/speed],duration,(lx,ly,floor))


def select_target(enemies, origin, world, max_range, locked, attacker):
    ranked = sorted(enemies,key=lambda e: (
        math.dist(origin,e['pos']) - (35 if e['id']==locked else 0)
        - (80 if e['id']==attacker else 0)
        - (15 if e.get('charging') else 0)
        + min(20,e.get('hp',1400)/100)))
    for enemy in ranked:
        point = (enemy['pos'][0],enemy['pos'][1],enemy['pos'][2]+4.5*(1+.05*enemy.get('cubes',0)))
        if math.dist(origin,point)<=max_range and visibility(world,origin,point) is True:
            return enemy
    return None


def choose_evasion(me, projectiles, enemies, desired, zone, urgent, cfg, side):
    """Evaluate whole candidate velocities, so opposite hazards cannot cancel."""
    speed = me['stats'].get('walk_speed',11.7)
    radius = me['stats'].get('radius',2.5)
    hazards = []
    for p in projectiles:
        if math.dist(p['pos'],me['pos'])>350:
            continue
        if p['kind']=='mortar':
            gravity = cfg['weapons']['mortar']['gravity']
            vz = p['vel'][2]
            eta = (vz+math.sqrt(vz*vz+2*gravity*max(0,p['pos'][2]-me['pos'][2])))/gravity
            if 0<eta<1.2:
                hazards.append(('blast',p,eta,cfg['weapons']['mortar']['splash_radius']+radius))
        else:
            hazards.append(('bullet',p,0,radius+1.5))
    beams = []
    for enemy in enemies:
        if enemy.get('charging') and 'aim' in enemy:
            yaw,pitch = enemy['aim']
            origin = (enemy['pos'][0],enemy['pos'][1],enemy['pos'][2]+7)
            beams.append((origin,(math.cos(pitch)*math.cos(yaw),math.cos(pitch)*math.sin(yaw),math.sin(pitch))))
    if not hazards and not beams:
        return desired,False
    def risk(v):
        total = 0.0
        for kind,p,eta,hit_radius in hazards:
            rx,ry = p['pos'][0]-me['pos'][0],p['pos'][1]-me['pos'][1]
            vx,vy = p['vel'][0]-v[0]*speed,p['vel'][1]-v[1]*speed
            if kind=='bullet':
                eta = -(rx*vx+ry*vy)/max(1e-9,vx*vx+vy*vy)
                if not 0<eta<1.2:
                    continue
                height = p['pos'][2]-me['pos'][2]+(p['vel'][2]-me['vel'][2])*eta
                if not -1<height<me['stats'].get('height',9)+1:
                    continue
            distance = math.hypot(rx+vx*eta,ry+vy*eta)
            total += max(0,1-distance/hit_radius)*(2 if kind=='blast' else 1)/(0.2+eta)
        for origin,direction in beams:
            point = (me['pos'][0]+v[0]*speed*.3,me['pos'][1]+v[1]*speed*.3,me['pos'][2]+4.5)
            delta = tuple(point[i]-origin[i] for i in range(3))
            along = sum(delta[i]*direction[i] for i in range(3))
            if 0<along<280:
                perpendicular = math.sqrt(max(0,sum(n*n for n in delta)-along*along))
                total += 4*max(0,1-perpendicular/(radius+2))
        return total
    length = math.hypot(*desired)
    base = [v/max(1,length) for v in desired]
    baseline = risk(base)
    if baseline<=0:
        return desired,False
    heading = math.atan2(base[1],base[0]) if length else side*math.pi/2
    best,best_score = base,-math.inf
    toward = (zone['center'][0]-me['pos'][0],zone['center'][1]-me['pos'][1])
    for offset in (0,side*.6,-side*.6,side*1.2,-side*1.2,math.pi):
        v = [math.cos(heading+offset),math.sin(heading+offset)]
        if urgent and v[0]*toward[0]+v[1]*toward[1]<=0:
            continue
        score = -risk(v)*5 + .3*(v[0]*base[0]+v[1]*base[1])
        if score>best_score:
            best,best_score = v,score
    return best,True
