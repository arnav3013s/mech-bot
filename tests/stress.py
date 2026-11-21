import os
import sys,copy,json,gzip,time,statistics,math
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
KIT = Path(os.environ.get('MECHFIGHT_KIT', ROOT.parent/'work'/'mechfight-kit'))
from survivor import Bot
with gzip.open(KIT/'check'/'stream.jsonl.gz','rt') as f:
 h=json.loads(next(f));o=json.loads(next(f))
h['map']['dir']=str(KIT/'arena');b=Bot(h)
x,y,z=o['self']['pos'];o['self'].update(hp=200,energy=100)
o['mechs']=[dict(id=i,kind='mech',pos=[x+30+i*4,y+10,z],vel=[0,10,3],aim=[3,0],charging=True) for i in range(15)]
o['projectiles']=[dict(id=i,kind='gun' if i%3 else 'mortar',pos=[x-20,y+(i%10)-5,z+4+i%15],vel=[90,0,-3]) for i in range(120)]
latencies=[]
for i in range(1500):
 o.update(t=i*.1,tick=i*2);start=time.perf_counter();a=b.act(o);latencies.append((time.perf_counter()-start)*1000)
 assert not a['jump'] and math.hypot(*a['move'])<=1.00001
print(json.dumps(dict(decisions=len(latencies),p50_ms=statistics.median(latencies),p99_ms=sorted(latencies)[int(.99*len(latencies))],maximum_ms=max(latencies),over_50_ms=sum(x>50 for x in latencies))))
