from __future__ import annotations
import json,re,time,requests
BASE='https://portal.scourt.go.kr'
H={'User-Agent':'Mozilla/5.0 MacroWatch/1.0'}
def get(path):
 for attempt in range(5):
  try:
   r=requests.get(BASE+path,headers=H,timeout=12)
   print(json.dumps({'path':path,'attempt':attempt+1,'status':r.status_code,'len':len(r.text),'url':r.url},ensure_ascii=False))
   return r
  except Exception as e:
   print(json.dumps({'path':path,'attempt':attempt+1,'error':repr(e)},ensure_ascii=False)); time.sleep(2)
 return None
for path in [
 '/pgp/main.on?c=900&w2xPath=PGP441M01',
 '/pgp/index.on?m=PGP441M01&l=N&c=900',
 '/pgp/ui/pageUserComm/main.xml',
 '/pgp/ui/pageUserComm/header.xml']:
 r=get(path)
 if not r: continue
 t=r.text
 print(json.dumps({'path':path,'actions':re.findall(r'action=["\']([^"\']+)',t),'xmls':sorted(set(re.findall(r'["\']([^"\']+\.xml[^"\']*)',t)))[:200],'pgp441ctx':[t[max(0,m.start()-1000):m.start()+2000] for m in list(re.finditer('PGP441',t,re.I))[:10]]},ensure_ascii=False))
 for token in ['mvPage','w2xPath','getParameter','location','menu','m=']:
  for m in list(re.finditer(token,t,re.I))[:20]: print(json.dumps({'path':path,'token':token,'ctx':t[max(0,m.start()-500):m.start()+1200]},ensure_ascii=False))
