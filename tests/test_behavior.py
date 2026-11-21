import os
import sys,json,gzip,copy,math,unittest,itertools,random,time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
KIT = Path(os.environ.get('MECHFIGHT_KIT', ROOT.parent/'work'/'mechfight-kit'))
from survivor import Bot, World
from tactics import Navigator
with gzip.open(KIT/'check'/'stream.jsonl.gz','rt') as f:
    HELLO=json.loads(next(f)); BASE=json.loads(next(f))
HELLO['map']['dir']=str(KIT/'arena')
class EmptyWorld:
    ok=True
    def ground(self,x,y):return 0
    def los(self,a,b):return True
    def walk_clear(self,*a):return a[-1]
    def air_clear(self,*a):return a[-1]
class Behaviors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.prototype=Bot(HELLO)
    def setUp(self):
        self.b=copy.copy(self.prototype);self.b.world=EmptyWorld();self.b.avoid_loot={}
        self.b.navigator=Navigator();self.b.motion={}
        self.o=copy.deepcopy(BASE);self.m=self.o['self']
        self.m.update(pos=[0,0,0],vel=[0,0,0],aim=[0,0],in_zone=True,grounded=True,energy=100,hp=1400,max_hp=1400)
        self.m['weapons'][0].update(ammo=12,reload=0,cooldown=0);self.m['weapons'][1]=None
        for key in ('mechs','sounds','projectiles','events','pickups','cubes','drops','crates'):self.o[key]=[]
        self.o.update(t=10,tick=200)
        self.o['zone'].update(center=[0,0],radius=200,next_center=[0,0],next_radius=200,shrink_start=100,shrink_end=150)
    def enemy(self,pos=(50,0,0),vel=(0,0,0)):
        e=dict(id=4,kind='mech',pos=list(pos),vel=list(vel),aim=[math.pi,0],hp=1400)
        self.o['mechs'].append(e);return e
    def secondary(self,kind,charge=0):
        self.m['weapons'][1]=dict(kind=kind,stats=HELLO['config']['weapons'][kind],cooldown=0,charge=charge)
    def act(self):return self.b.act(self.o)
    def test_no_blind_fire(self):
        self.assertEqual(self.act()['fire'],[False,False])
    def test_building_not_enemy(self):
        self.o['crates']=[dict(id=1000,pos=[20,0,0],hp=300)];self.assertFalse(any(self.act()['fire']))
    def test_npc_not_player(self):
        self.enemy()['kind']='npc';self.assertFalse(any(self.act()['fire']))
    def test_ground_aim_shoot(self):
        self.enemy();a=self.act();self.m['aim']=a['aim'];self.assertTrue(self.act()['fire'][0])
    def test_pitch_gate(self):
        self.enemy();self.m['aim']=[0,1];self.assertFalse(any(self.act()['fire']))
    def test_yaw_gate(self):
        self.enemy();self.m['aim']=[2,0];self.assertFalse(any(self.act()['fire']))
    def test_crossing_lead(self):
        self.enemy(vel=(0,12,0));self.assertGreater(self.act()['aim'][0],0)
    def test_airborne_enemy(self):
        self.enemy(pos=(50,0,25),vel=(0,0,5));a=self.act();self.assertGreater(a['aim'][1],0)
        self.m['aim']=a['aim'];self.assertTrue(self.act()['fire'][0])
    def test_airborne_self_no_jet(self):
        self.m.update(grounded=False,pos=[0,0,20]);self.enemy();a=self.act();self.assertFalse(a['jump']);self.assertFalse(a['boost']);self.assertLess(a['aim'][1],0)
    def test_no_jump_when_stuck(self):
        self.o['cubes']=[dict(id=5000,pos=[20,0,0])]
        for i in range(60):
            self.o['t']=10+i*.1;self.assertFalse(self.act()['jump'])
    def test_empty_reload(self):
        self.enemy();self.m['weapons'][0]['ammo']=0;a=self.act();self.assertTrue(a['reload']);self.assertFalse(a['fire'][0])
    def test_reload_no_shoot(self):
        self.enemy();self.m['weapons'][0]['reload']=1;self.assertFalse(self.act()['fire'][0])
    def test_walls_block_fire(self):
        self.enemy();self.b.world.los=lambda a,b:False;self.assertFalse(any(self.act()['fire']))
    def test_dead_enemy_no_shoot(self):
        self.enemy()['hp']=0;self.assertFalse(any(self.act()['fire']))
    def test_laser_current_position(self):
        self.enemy(vel=(0,12,0));self.secondary('laser',.75);a=self.act();self.assertEqual(a['aim'][0],0)
        self.m['aim']=a['aim'];self.assertFalse(self.act()['fire'][1])
    def test_lightning_energy(self):
        self.enemy(pos=(25,0,0));self.secondary('lightning');self.assertTrue(self.act()['fire'][1]);self.m['energy']=0;self.assertFalse(self.act()['fire'][1])
    def test_damage_direction_even_with_other_enemy(self):
        self.enemy();self.o['events']=[dict(type='damaged',bearing=math.pi)];self.act();self.assertEqual(self.b.source,math.pi)
    def test_unseen_damage_facing(self):
        self.o['events']=[dict(type='damaged',bearing=1.2)];self.assertEqual(self.act()['aim'][0],1.2)
    def test_bullet_source(self):
        self.o['projectiles']=[dict(kind='gun',pos=[20,0,4],vel=[-100,0,0])];self.assertAlmostEqual(self.act()['aim'][0],0)
    def test_prioritize_visible_attacker(self):
        self.enemy(pos=(25,0,0));e=self.enemy(pos=(0,50,0));e['id']=5
        self.o['events']=[dict(type='damaged',bearing=math.pi/2,by=5)]
        self.assertAlmostEqual(self.act()['aim'][0],math.pi/2)
    def test_sound_source(self):
        self.o['sounds']=[dict(kind='gun',dist='near',bearing=-1,age=.1)];self.assertEqual(self.act()['aim'][0],-1)
    def test_falling_mortar(self):
        self.o['projectiles']=[dict(kind='mortar',pos=[0,0,10],vel=[0,0,-20])];self.assertGreater(math.hypot(*self.act()['move']),0)
    def test_zone_overrides_fight(self):
        self.m.update(pos=[230,0,0],in_zone=False);self.enemy(pos=(200,0,0));a=self.act();self.assertLess(a['move'][0],-.7);self.assertTrue(a['boost'])
    def test_zone_zero_energy(self):
        self.m.update(pos=[230,0,0],in_zone=False,energy=0);a=self.act();self.assertLess(a['move'][0],0);self.assertFalse(a['boost'])
    def test_cube_over_weapon(self):
        self.o['cubes']=[dict(id=5000,pos=[10,0,0])];self.o['pickups']=[dict(id=8000,kind='laser',pos=[0,10,0])];self.assertGreater(self.act()['move'][0],.9)
    def test_take_weapon(self):
        self.o['pickups']=[dict(id=8000,kind='lightning',pos=[1,0,0])];self.assertEqual(self.act()['take'],8000)
    def test_roof_loot_skipped(self):
        self.o['cubes']=[dict(id=5000,pos=[10,0,20])];self.o['pickups']=[dict(id=8000,kind='laser',pos=[1,0,20])];a=self.act();self.assertNotIn('take',a);self.assertEqual(a['move'],[0,0])
    def test_loot_under_fire_skipped(self):
        self.o['pickups']=[dict(id=8000,kind='lightning',pos=[1,0,0])];self.enemy();self.assertNotIn('take',self.act())
    def test_cover_when_available(self):
        self.enemy();self.b.world.los=lambda a,b: b[1]<5;a=self.act();self.assertIsNotNone(self.b.cover_goal);self.assertGreater(a['move'][1],0)
    def test_combinations(self):
        for count,energy,in_zone,airborne,weapon in itertools.product((0,1,15),(0,100),(False,True),(False,True),(None,'laser','lightning')):
            with self.subTest(count=count,energy=energy,zone=in_zone,air=airborne,weapon=weapon):
                self.setUp();self.m.update(energy=energy,in_zone=in_zone,grounded=not airborne)
                for i in range(count):self.enemy(pos=(25+i,10,5 if airborne else 0))
                if weapon:self.secondary(weapon)
                a=self.act();self.assertLessEqual(math.hypot(*a['move']),1.00001);self.assertTrue(all(math.isfinite(v) for v in a['move']+a['aim']));self.assertFalse(a['jump'])
    def test_geometry_categories(self):
        w=self.prototype.world
        self.assertTrue(w.ok);self.assertTrue(w.trees);self.assertTrue(w.solids)
        # Controlled building: a wall at x=10, and an overpass above z=12.
        w=copy.copy(w);w.solids={};w.minx=w.miny=-100;w.maxx=w.maxy=100
        w.ground=lambda x,y:0
        wall=(0,10,[(10,-5),(12,-5),(12,5),(10,5)],[])
        for bx in (-1,0):
            for by in (-1,0):w.solids[(bx,by)]=[wall]
        self.assertFalse(w.los((0,0,7),(20,0,7)))
        self.assertTrue(w.los((0,0,20),(20,0,20)))
        self.assertLess(w.walk_clear((0,0,0),1,0,18),10)
        overpass=(12,14,wall[2],[])
        for key in w.solids:w.solids[key]=[overpass]
        self.assertTrue(w.los((0,0,7),(20,0,7)))
        self.assertEqual(w.walk_clear((0,0,0),1,0,18),18)
    def geometry_world(self):
        w=copy.copy(self.prototype.world);w.solids={};w.ground=lambda x,y:0
        w.minx=w.miny=-500;w.maxx=w.maxy=500
        return w
    def test_airborne_zone_escape_real_geometry(self):
        self.b.world=self.geometry_world()
        self.m.update(pos=[230,0,20],grounded=False,in_zone=False)
        self.assertLess(self.act()['move'][0],-.7)
    def test_fuel_empty_airborne_movement(self):
        self.b.world=self.geometry_world()
        self.m.update(pos=[230,0,20],grounded=False,in_zone=False,energy=0)
        a=self.act();self.assertLess(a['move'][0],-.7);self.assertFalse(a['boost'])
    def test_legal_slope(self):
        w=self.geometry_world();w.ground=lambda x,y:.9*x
        self.assertEqual(w.walk_clear((0,0,0),1,0,18),18)
    def test_illegal_slope(self):
        w=self.geometry_world();w.ground=lambda x,y:1.5*x
        self.assertLess(w.walk_clear((0,0,0),1,0,18),1)
    def test_thin_wall_between_samples(self):
        w=self.geometry_world();wall=(0,10,[(.9,-5),(1.1,-5),(1.1,5),(.9,5)],[])
        w.solids={(0,0):[wall],(0,-1):[wall]}
        self.assertFalse(w.los((0,0,7),(20,0,7)))
    def test_wall_gap_visible(self):
        w=self.geometry_world();wall=(0,10,[(.9,-5),(1.1,-5),(1.1,5),(.9,5)], [[(.5,-1),(1.5,-1),(1.5,1),(.5,1)]])
        w.solids={(0,0):[wall],(0,-1):[wall]}
        self.assertTrue(w.los((0,0,7),(20,0,7)))
    def test_lost_target_laser_safe_release(self):
        self.secondary('laser',.3)
        a=self.act();self.assertEqual(a['aim'],[0,-.7]);self.assertTrue(a['fire'][1])
        self.m['aim']=[0,-.7];self.assertFalse(self.act()['fire'][1])
    def test_storm_interrupts_laser(self):
        self.enemy();self.secondary('laser',.3);self.m.update(in_zone=False,pos=[230,0,0])
        self.assertFalse(self.act()['fire'][1])
    def test_storm_prevents_new_laser_charge(self):
        self.enemy();self.secondary('laser');self.m.update(in_zone=False,pos=[230,0,0])
        self.assertFalse(self.act()['fire'][1])
    def test_cover_flank_revalidation(self):
        self.enemy(pos=[0,50,0]);self.b.cover_goal=(10,0);self.b.cover_until=20
        self.b.cover_threat=(4,(50,0,0))
        self.act();self.assertIsNone(self.b.cover_goal)
    def test_destroyed_cover_revalidation(self):
        self.enemy();self.b.cover_goal=(10,0);self.b.cover_until=20;self.b.cover_threat=(4,(50,0,0))
        self.act();self.assertIsNone(self.b.cover_goal)
    def test_cooldown_prevents_trigger(self):
        self.enemy();self.m['weapons'][0]['cooldown']=1
        self.assertFalse(self.act()['fire'][0])

    def test_polygon_acceleration_matches_reference(self):
        def reference(poly,x,y):
            inside=False
            for i,(xi,yi) in enumerate(poly):
                xj,yj=poly[(i+1)%len(poly)]
                if (yi>y)!=(yj>y) and x<(xj-xi)*(y-yi)/(yj-yi+1e-12)+xi:
                    inside=not inside
            return inside
        rng=random.Random(11)
        for polygon in [s[2] for bucket in self.prototype.world.solids.values() for s in bucket][:80]:
            for _ in range(20):
                x=rng.uniform(min(p[0] for p in polygon)-1,max(p[0] for p in polygon)+1)
                y=rng.uniform(min(p[1] for p in polygon)-1,max(p[1] for p in polygon)+1)
                self.assertEqual(World._inside(polygon,x,y),reference(polygon,x,y))

if __name__=='__main__':unittest.main(verbosity=2)
