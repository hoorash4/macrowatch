from __future__ import annotations
import json,re,requests
BASE='https://portal.scourt.go.kr'
H={'User-Agent':'Mozilla/5.0 MacroWatch/1.0'}
for path in ['/pgp/ui/pageUserComm/main.xml','/pgp/ui/pageUserComm/header.xml','/pgp/ui/pageUserComm/refheader.xml']:
 r=requests.get(BASE+path,headers=H,timeout=30); t=r.text
 print(json.dumps({'path':path,'status':r.status_code,'actions':re.findall(r'action=["\']([^"\']+)',t),'xmls':sorted(set(re.findall(r'["\']([^"\']+\.xml[^"\']*)',t)))[:100]},ensure_ascii=False))
 for token in ['mvPage','menu','PGP441']:
  for m in list(re.finditer(token,t,re.I))[:20]: print(json.dumps({'path':path,'token':token,'ctx':t[max(0,m.start()-500):m.start()+1000]},ensure_ascii=False))
# Try likely menu endpoints discovered in shared page infrastructure.
for url in [
 BASE+'/pgp/pgp001/selectMenuLst.on', BASE+'/pgp/pgp001/selectMenuList.on',
 BASE+'/pgp/pgp001/selectUserMenuLst.on', BASE+'/pgp/pgp001/selectMainMenuLst.on',
 BASE+'/pgp/pgp001/selectCortCdAllLst.on']:
 try:
  r=requests.post(url,headers={**H,'Content-Type':'application/json'},json={},timeout=30)
  print(json.dumps({'url':url,'status':r.status_code,'type':r.headers.get('content-type'),'text':r.text[:5000]},ensure_ascii=False))
 except Exception as e: print(json.dumps({'url':url,'error':repr(e)},ensure_ascii=False))
