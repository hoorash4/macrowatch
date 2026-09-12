from __future__ import annotations
import json,re,time,requests
BASE='https://portal.scourt.go.kr'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/143 Safari/537.36'}

def fetch(path):
    for attempt in range(3):
        try:
            r=requests.get(BASE+path,headers=H,timeout=20)
            print(json.dumps({'path':path,'attempt':attempt+1,'status':r.status_code,'len':len(r.text),'url':r.url},ensure_ascii=False))
            if r.ok: return r.text
        except Exception as e:
            print(json.dumps({'path':path,'attempt':attempt+1,'error':repr(e)},ensure_ascii=False))
        time.sleep(3)
    return ''

main=fetch('/pgp/ui/pageUserComm/main.xml')
if main:
    print(json.dumps({'actions':sorted(set(re.findall(r'action=["\']([^"\']+)',main,re.I))),
                      'srcs':sorted(set(re.findall(r'(?:src|href)=["\']([^"\']+)',main,re.I)))},ensure_ascii=False))
    for token in ['mvPage','w2xPath','menuId','menuUrl','PGP441','submission']:
        contexts=[]
        for m in list(re.finditer(token,main,re.I))[:80]:
            contexts.append(main[max(0,m.start()-1200):m.start()+2400])
        print(json.dumps({'token':token,'contexts':contexts},ensure_ascii=False))

index=fetch('/pgp/index.on?m=PGP441M01&l=N&c=900')
if index:
    for token in ['PGP441','w2xPath','sessionStorage','location']:
        print(json.dumps({'index_token':token,'contexts':[index[max(0,m.start()-800):m.start()+1800] for m in list(re.finditer(token,index,re.I))[:40]]},ensure_ascii=False))
