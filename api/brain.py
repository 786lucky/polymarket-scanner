import urllib.request, json, time

BINANCE = "https://api.binance.com"
GAMMA = "https://gamma-api.polymarket.com"
TOPIC = "praveen-polymarket-bot-2026"

def clamp(x, lo, hi): return max(lo, min(hi, x))

def notify(msg):
    try:
        r = urllib.request.Request("https://ntfy.sh/" + TOPIC, data=msg.encode(), method="POST")
        urllib.request.urlopen(r, timeout=10)
    except: pass

def fetch_price():
    try:
        url = BINANCE + "/api/v3/ticker/price?symbol=BTCUSDT"
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return float(json.loads(urllib.request.urlopen(r, timeout=10).read())["price"])
    except: return None

def fetch_klines():
    try:
        url = BINANCE + "/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=240"
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(r, timeout=10).read())
        return [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4]),"v":float(k[5])} for k in d]
    except: return None

def fetch_poly():
    try:
        url = GAMMA + "/markets?seriesSlug=btc-up-or-down-15m&active=true&closed=false&enableOrderBook=true&limit=10"
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        markets = json.loads(urllib.request.urlopen(r, timeout=10).read())
        now = time.time() * 1000
        live = []
        for m in markets:
            e = m.get("endDate", "")
            if e:
                try:
                    from datetime import datetime
                    ems = datetime.fromisoformat(e.replace("Z", "+00:00")).timestamp() * 1000
                    if now < ems: live.append((ems, m))
                except: pass
        live.sort(key=lambda x: x[0])
        return live[0][1] if live else None
    except: return None

def ema(vals, period):
    if len(vals) < period: return None
    k = 2 / (period + 1)
    prev = vals[0]
    for i in range(1, len(vals)): prev = vals[i] * k + prev * (1 - k)
    return prev

def rsi(closes, p=14):
    if len(closes) < p + 1: return None
    g = sum(max(0, closes[i]-closes[i-1]) for i in range(len(closes)-p, len(closes)))
    l = sum(max(0, closes[i-1]-closes[i]) for i in range(len(closes)-p, len(closes)))
    ag, al = g/p, l/p
    if al == 0: return 100
    return clamp(100 - 100/(1 + ag/al), 0, 100)

def macd(closes, f=12, s=26, sig=9):
    if len(closes) < s + sig: return None
    fe, se = ema(closes, f), ema(closes, s)
    if not fe or not se: return None
    ml = fe - se
    ms = []
    for i in range(len(closes)):
        ff, ss = ema(closes[:i+1], f), ema(closes[:i+1], s)
        if ff and ss: ms.append(ff - ss)
    sl = ema(ms, sig)
    if not sl: return None
    h = ml - sl
    ph = None
    if len(ms) >= sig + 1:
        ps = ema(ms[:-1], sig)
        if ps: ph = ms[-2] - ps
    return {"hist": h, "hd": (h - ph) if ph else None, "macd": ml}

def vwap(candles):
    if not candles: return None
    pv = sum((c["h"]+c["l"]+c["c"])/3*c["v"] for c in candles)
    v = sum(c["v"] for c in candles)
    return pv/v if v > 0 else None

def heiken(candles):
    ha = []
    for i, c in enumerate(candles):
        hc = (c["o"]+c["h"]+c["l"]+c["c"])/4
        ho = (ha[-1]["o"]+ha[-1]["c"])/2 if ha else (c["o"]+c["c"])/2
        ha.append({"o": ho, "c": hc, "g": hc >= ho})
    return ha

def consec(ha):
    if not ha: return None, 0
    t = "green" if ha[-1]["g"] else "red"
    n = 0
    for c in reversed(ha):
        if ("green" if c["g"] else "red") != t: break
        n += 1
    return t, n

def regime(price, vw, vs, crosses, vr, va):
    if price is None or vw is None or vs is None: return "CHOP"
    if vr and va and vr < 0.6*va and abs((price-vw)/vw) < 0.001: return "CHOP"
    if price > vw and vs > 0: return "TREND_UP"
    if price < vw and vs < 0: return "TREND_DOWN"
    if crosses and crosses >= 3: return "RANGE"
    return "RANGE"

def score(price, vw, vs, r, rs, m, hc, hn, fail):
    u, d = 1, 1
    if price and vw:
        if price > vw: u += 2
        if price < vw: d += 2
    if vs is not None:
        if vs > 0: u += 2
        if vs < 0: d += 2
    if r and rs:
        if r > 55 and rs > 0: u += 2
        if r < 45 and rs < 0: d += 2
    if m and m.get("hist") is not None and m.get("hd") is not None:
        if m["hist"] > 0 and m["hd"] > 0: u += 2
        if m["hist"] < 0 and m["hd"] < 0: d += 2
        if m.get("macd", 0) > 0: u += 1
        if m.get("macd", 0) < 0: d += 1
    if hc == "green" and hn >= 2: u += 1
    if hc == "red" and hn >= 2: d += 1
    if fail: d += 3
    return u / (u + d)

def decide(rem, eu, ed, mu=None, md=None):
    if rem > 10: ph, th, mp = "EARLY", 0.05, 0.55
    elif rem > 5: ph, th, mp = "MID", 0.10, 0.60
    else: ph, th, mp = "LATE", 0.20, 0.65
    if eu is None or ed is None: return {"action": "NO_TRADE", "phase": ph, "reason": "no_data"}
    side = "UP" if eu > ed else "DOWN"
    edge = eu if side == "UP" else ed
    model = mu if side == "UP" else md
    if edge < th: return {"action": "NO_TRADE", "phase": ph, "reason": "edge_low"}
    if model and model < mp: return {"action": "NO_TRADE", "phase": ph, "reason": "prob_low"}
    st = "STRONG" if edge >= 0.2 else "GOOD" if edge >= 0.1 else "OPTIONAL"
    return {"action": "ENTER", "side": side, "phase": ph, "strength": st, "edge": round(edge, 4)}

def run_brain():
    try:
        klines = fetch_klines()
        price = fetch_price()
        poly = fetch_poly()
        if not klines or not price: return {"status": "error", "reason": "fetch_fail"}
        closes = [c["c"] for c in klines]
        vw = vwap(klines)
        vs_list = [vwap(klines[:i+1]) for i in range(len(klines))]
        vs = ((vs_list[-1] - vs_list[-5]) / 5) if len(vs_list) >= 5 and vs_list[-1] and vs_list[-5] else None
        r = rsi(closes)
        rs_list = [rsi(closes[:i+1]) for i in range(len(closes)) if rsi(closes[:i+1]) is not None]
        rslope = (rs_list[-1] - rs_list[-4]) if len(rs_list) >= 4 else None
        m = macd(closes)
        ha = heiken(klines)
        hc, hn = consec(ha)
        fail = False
        if vw and len(vs_list) >= 3 and len(closes) >= 2 and vs_list[-2]:
            fail = closes[-1] < vw and closes[-2] > vs_list[-2]
        vr = sum(c["v"] for c in klines[-20:])
        va = sum(c["v"] for c in klines[-120:]) / 6 if len(klines) >= 120 else None
        crosses = 0
        for i in range(max(1, len(vs_list)-20), len(vs_list)):
            if vs_list[i] and vs_list[i-1]:
                if (closes[i] > vs_list[i] and closes[i-1] < vs_list[i-1]) or (closes[i] < vs_list[i] and closes[i-1] > vs_list[i-1]):
                    crosses += 1
        reg = regime(price, vw, vs, crosses, vr, va)
        raw_up = score(price, vw, vs, r, rslope, m, hc, hn, fail)
        rem = 15
        if poly and poly.get("endDate"):
            try:
                from datetime import datetime
                ems = datetime.fromisoformat(poly["endDate"].replace("Z", "+00:00")).timestamp()
                rem = max(0, (ems - time.time()) / 60)
            except: pass
        decay = clamp(rem / 15, 0, 1)
        aup = clamp(0.5 + (raw_up - 0.5) * decay, 0, 1)
        adn = 1 - aup
        my, mn = None, None
        if poly:
            try:
                ps = json.loads(poly.get("outcomePrices", "[]"))
                if len(ps) >= 2: my, mn = float(ps[0]), float(ps[1])
            except: pass
        eu, ed = None, None
        if my is not None and mn is not None:
            t = my + mn
            mu_p = my / t if t > 0 else None
            md_p = mn / t if t > 0 else None
            eu = (aup - mu_p) if mu_p else None
            ed = (adn - md_p) if md_p else None
        rec = decide(rem, eu, ed, aup, adn)
        result = {"status": "ok", "btc": round(price, 2), "regime": reg, "rsi": round(r, 1) if r else None, "model_up": round(aup, 3), "model_dn": round(adn, 3), "edge_up": round(eu, 4) if eu else None, "edge_dn": round(ed, 4) if ed else None, "time_left": round(rem, 1), "decision": rec}
        if rec["action"] == "ENTER":
            notify("BTC BRAIN: " + rec["side"] + " Edge=" + str(round(rec["edge"]*100, 1)) + "% " + rec["strength"] + " " + rec["phase"] + " BTC=$" + str(round(price)) + " " + reg)
        return result
    except Exception as e:
        return {"status": "error", "reason": str(e)[:80]}
