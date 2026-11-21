import math
import time
import unittest
from test_behavior import Behaviors, HELLO
from tactics import Navigator, visibility, choose_evasion, jump_route


class TacticalCases(unittest.TestCase):
    setUpClass = classmethod(Behaviors.setUpClass.__func__)
    setUp = Behaviors.setUp
    enemy = Behaviors.enemy
    secondary = Behaviors.secondary
    act = Behaviors.act
    geometry_world = Behaviors.geometry_world

    def advance(self):
        self.o['t'] += .1
        self.o['tick'] += 2

    def test_timeout_is_unknown_not_cover(self):
        w = self.geometry_world()
        w.deadline = time.perf_counter()-1
        self.assertIsNone(visibility(w,(0,0,7),(20,0,7)))

    def test_target_lock_survives_crossing(self):
        a = self.enemy((40,0,0))
        b = self.enemy((0,41,0)); b['id'] = 5
        self.act()
        for _ in range(10):
            self.advance(); b['pos'][1] = 38
            self.act(); self.assertEqual(self.b.target_id,a['id'])
        a['hp'] = 0
        self.advance(); self.act(); self.assertEqual(self.b.target_id,5)

    def test_attacker_overrides_lock(self):
        self.enemy((40,0,0)); b = self.enemy((0,50,0)); b['id']=5
        self.act(); self.advance()
        self.o['events']=[dict(type='damaged',bearing=math.pi/2,by=5)]
        self.act(); self.assertEqual(self.b.target_id,5)

    def test_ready_gun_keeps_lead_with_idle_laser(self):
        self.enemy(vel=(0,12,0)); self.secondary('laser')
        self.assertGreater(self.act()['aim'][0],0)

    def test_powered_flight_acceleration_and_landing(self):
        e = self.enemy((50,0,15),(0,0,5))
        self.act(); self.advance(); e['vel'][2]=5
        self.act(); self.assertAlmostEqual(self.b.motion[4]['az'],0)
        self.assertAlmostEqual(self.b.target_point(e,.2)[2],20.5)
        self.advance(); e['pos'][2]=0; e['vel'][2]=0
        self.act(); self.assertAlmostEqual(self.b.target_point(e,.2)[2],4.5)

    def test_reverse_motion_updates_lead(self):
        e = self.enemy(vel=(0,12,0)); self.assertGreater(self.act()['aim'][0],0)
        self.advance(); e['vel'][1]=-12; self.assertLess(self.act()['aim'][0],0)

    def test_laser_beam_causes_lateral_escape(self):
        e = self.enemy(); e['charging']=True; e['aim']=[math.pi,-.05]
        movement,dodge = choose_evasion(self.m,[],[e],[0,0],self.o['zone'],False,self.b.cfg,1)
        self.assertTrue(dodge); self.assertGreater(abs(movement[1]),.4)

    def test_opposing_bullets_do_not_cancel(self):
        shots=[dict(kind='gun',pos=[s*30,0,4],vel=[-s*100,0,0]) for s in (-1,1)]
        movement,dodge=choose_evasion(self.m,shots,[],[0,0],self.o['zone'],False,self.b.cfg,1)
        self.assertTrue(dodge); self.assertGreater(abs(movement[1]),.4)

    def test_storm_dodge_retains_inward_progress(self):
        self.m['pos']=[230,0,0]
        shots=[dict(kind='gun',pos=[200,0,4],vel=[100,0,0])]
        move,dodge=choose_evasion(self.m,shots,[],[-1,0],self.o['zone'],True,self.b.cfg,1)
        self.assertTrue(dodge); self.assertLess(move[0],0); self.assertGreater(abs(move[1]),.1)

    def test_lightning_preserves_escape_energy(self):
        self.m.update(pos=[230,0,0],in_zone=False,energy=16)
        self.enemy((250,0,0)); self.secondary('lightning')
        self.assertFalse(self.act()['fire'][1])

    def test_reload_retreat_and_return_fire_sequence(self):
        self.enemy((80,0,0)); self.m['weapons'][0]['ammo']=0
        a=self.act(); self.assertTrue(a['reload']); self.assertLess(a['move'][0],0)
        self.advance(); self.m['weapons'][0].update(ammo=12,reload=1)
        self.assertFalse(self.act()['fire'][0])
        self.advance(); self.m['weapons'][0]['reload']=0
        self.m['aim']=self.act()['aim']; self.assertTrue(self.act()['fire'][0])

    def test_third_attacker_invalidates_cover(self):
        self.enemy((40,0,0)); self.enemy((50,0,0)); self.enemy((0,-80,0))
        self.b.cover_goal=(10,10); self.b.cover_until=100
        self.b.world.los=lambda a,b: a[1]<-50
        self.act(); self.assertNotEqual(self.b.cover_goal,(10,10))

    def test_swap_for_small_circle(self):
        self.o['zone'].update(radius=80,next_radius=80)
        self.secondary('laser')
        self.o['pickups']=[dict(id=8001,kind='lightning',pos=[1,0,0])]
        a=self.act(); self.assertEqual(a['take'],8001); self.assertEqual(a['drop'],1)

    def test_charging_laser_cannot_be_swapped(self):
        self.o['zone'].update(radius=80,next_radius=80); self.secondary('laser',.4)
        self.o['pickups']=[dict(id=8001,kind='lightning',pos=[1,0,0])]
        self.assertNotIn('take',self.act())

    def test_loot_behind_wall_is_considered_for_routing(self):
        self.o['cubes']=[dict(id=5001,pos=[20,0,0])]
        self.b.world.los=lambda a,b:False
        self.assertEqual(self.b.loot(self.o,False,False)[1]['id'],5001)

    def test_crate_fire_only_when_safe_and_aligned(self):
        self.o['crates']=[dict(id=1000,pos=[20,0,0],hp=300)]
        self.m['aim']=self.act()['aim']; self.assertTrue(self.act()['fire'][0])
        self.advance(); self.o['events']=[dict(type='damaged',bearing=1)]
        self.assertFalse(any(self.act()['fire']))

    def test_supply_drop_hold_then_abandon_on_threat(self):
        self.o['drops']=[dict(id=20000,pos=[2,0,0],radius=8,lands_in=0,capture_s=4)]
        for _ in range(5):
            self.advance(); self.assertEqual(self.act()['move'],[0,0])
        self.enemy((30,0,0)); self.advance()
        self.assertGreater(math.hypot(*self.act()['move']),.5)

    def test_unsafe_future_zone_drop_rejected(self):
        self.o['zone'].update(next_center=[-100,0],next_radius=20,shrink_start=11)
        self.o['drops']=[dict(id=20000,pos=[40,0,0],lands_in=0,capture_s=4)]
        self.assertFalse(self.b.safe_objective(self.o,[40,0,0],4))

    def test_route_around_u_wall_over_multiple_slices(self):
        w=self.geometry_world(); nav=Navigator()
        walls=[(0,20,[(8,-18),(12,-18),(12,18),(8,18)],[]),
               (0,20,[(-18,-18),(12,-18),(12,-14),(-18,-14)],[]),
               (0,20,[(-18,14),(12,14),(12,18),(-18,18)],[])]
        for bx in range(-2,3):
            for by in range(-2,3): w.solids[(bx,by)]=walls
        waypoint=None
        for _ in range(250):
            w.deadline=time.perf_counter()+.02
            waypoint=nav.route(w,(0,0,0),(30,0),.002)
            if waypoint: break
        self.assertIsNotNone(waypoint)
        self.assertTrue(any(p[0]<-18 for p in nav.path))
        last=(0,0,0)
        for point in list(nav.path):
            dx,dy=point[0]-last[0],point[1]-last[1]; d=math.hypot(dx,dy)
            w.deadline=math.inf
            self.assertGreaterEqual(w.walk_clear(last,dx/d,dy/d,d),d-.01)
            last=(point[0],point[1],0)

    def test_tall_roof_jump_is_rejected(self):
        w=self.geometry_world()
        self.assertIsNone(jump_route(w,self.m,(5,0,20),self.b.cfg))

    def test_low_ledge_jump_has_clear_arc_and_supported_landing(self):
        w=self.geometry_world()
        ledge=(0,2,[(5.5,-20),(30,-20),(30,20),(5.5,20)],[])
        for bx in (-1,0,1):
            for by in (-1,0): w.solids[(bx,by)]=[ledge]
        self.m['vel']=[11.7,0,0]
        jump=jump_route(w,self.m,(15,0),self.b.cfg)
        self.assertIsNotNone(jump)
        self.assertLessEqual(math.hypot(*jump[0]),1)
        self.assertGreater(jump[2][0],7)
        ceiling=(10,12,[(-10,-20),(30,-20),(30,20),(-10,20)],[])
        for bucket in w.solids.values(): bucket.append(ceiling)
        self.assertIsNone(jump_route(w,self.m,(15,0),self.b.cfg))

    def test_route_goal_change_discards_old_path(self):
        w=self.geometry_world(); nav=Navigator(); w.deadline=math.inf
        nav.route(w,(0,0,0),(12,0),.01)
        self.assertIsNotNone(nav.route(w,(0,0,0),(-12,0),.01))
        self.assertLess(nav.path[-1][0],0)


if __name__=='__main__':
    unittest.main(verbosity=2)
