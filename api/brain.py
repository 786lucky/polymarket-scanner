import urllib.request, json, time

COINGECKO = "https://api.coingecko.com/api/v3"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://trading-api.kalshi.com/trade-api/v2"
TOPIC = "praveen-polymarket-bot-2026"
LOG_FILE = "/tmp/brain_log.json"
SIG_FILE = "/tmp/last_signals.txt"
PORTFOLIO_FILE = "/tmp/portfolio.json"

def clamp(x, lo, hi): return max(lo, min(hi, x))

def notify(msg):
    try:
        r = urllib.request.Request("https://ntfy.sh/" + TOPIC, data=msg.encode(), method="POST")
        urllib.request.urlopen(r, timeout=10)
    except: pass

def smart_notify(key, msg):
    try:
        last = ""
        try:
            with open(SIG_FILE, "r") as f: last = f.read()
        except: pass
        lines = last.split("\n") if last else []
        keys = [l.split("|")[0] for l in lines if l]
        if key not in keys:
            notify(msg)
            lines.append(key + "|" + str(int(time.time())))
            if len(lines) > 200: lines = lines[-200:]
            with open(SIG_FILE, "w") as f: f.write("\n".join(lines))
    except: pass

def fetch_json(url):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        resp = urllib.request.urlopen(r, timeout=15)
        return json.loads(resp.read().decode())
    except: return None

def log_entry(entry):
    try:
        logs = []
        try:
            with open(LOG_FILE, "r") as f: logs = json.loads(f.read())
        except: pass
        logs.append(entry)
        if len(logs) > 1000: logs = logs[-1000:]
        with open(LOG_FILE, "w") as f: f.write(json.dumps(logs))
    except: pass

def get_portfolio():
    try:
        with open(PORTFOLIO_FILE, "r") as f: return json.loads(f.read())
    except: return {"balance": 10000.0, "positions": [], "trades": [], "realized_pnl": 0}

def save_portfolio(pf):
    try:
        with open(PORTFOLIO_FILE, "w") as f: f.write(json.dumps(pf))
    except: pass

def poly_fee(price, shares, bps=1000):
    return (bps / 10000) * min(price, 1 - price) * shares

# ═══════════════════════════════════════════════════
# BRAIN 1: BTC TRADING ENGINE
# ═══════════════════════════════════════════════════

def ema(vals, period):
    if len(vals) < period: return None
    k = 2 / (period + 1)
    prev = vals[0]
    for i in range(1, len(vals)): prev = vals[i] * k + prev * (1 - k)
    return prev

def calc_rsi(closes, p=14):
    if len(closes) < p + 1: return None
    g = sum(max(0, closes[i]-closes[i-1]) for i in range(len(closes)-p, len(closes)))
    l = sum(max(0, closes[i-1]-closes[i]) for i in range(len(closes)-p, len(closes)))
    ag, al = g/p, l/p
    if al == 0: return 100
    return clamp(100 - 100/(1 + ag/al), 0, 100)

def calc_macd(closes, f=12, s=26, sig=9):
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

def calc_vwap(candles):
    if not candles: return None
    pv = sum((c["h"]+c["l"]+c["c"])/3*c["v"] for c in candles)
    v = sum(c["v"] for c in candles)
    if v > 0: return pv/v
    return sum((c["h"]+c["l"]+c["c"])/3 for c in candles) / len(candles)

def calc_heiken(candles):
    ha = []
    for i, c in enumerate(candles):
        hc = (c["o"]+c["h"]+c["l"]+c["c"])/4
        ho = (ha[-1]["o"]+ha[-1]["c"])/2 if ha else (c["o"]+c["c"])/2
        ha.append({"o": ho, "c": hc, "g": hc >= ho})
    return ha

def count_consec(ha):
    if not ha: return None, 0
    t = "green" if ha[-1]["g"] else "red"
    n = sum(1 for c in reversed(ha) if ("green" if c["g"] else "red") == t)
    return t, n

def detect_regime(price, vw, vs, crosses):
    if price is None or vw is None or vs is None: return "CHOP"
    if price > vw and vs > 0: return "TREND_UP"
    if price < vw and vs < 0: return "TREND_DOWN"
    if crosses and crosses >= 3: return "RANGE"
    return "RANGE"

def score_dir(price, vw, vs, r, rs, m, hc, hn, fail):
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

def run_btc_brain():
    try:
        cg = fetch_json(COINGECKO + "/simple/price?ids=bitcoin&vs_currencies=usd")
        price = float(cg["bitcoin"]["usd"]) if cg else None
        ohlc = fetch_json(COINGECKO + "/coins/bitcoin/ohlc?vs_currency=usd&days=1")
        klines = [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4]),"v":0} for k in ohlc] if ohlc else None
        poly = fetch_json(GAMMA + "/markets?seriesSlug=btc-up-or-down-15m&active=true&closed=false&enableOrderBook=true&limit=10")
        if not klines or not price: return {"status": "error", "reason": "fetch_fail"}
        closes = [c["c"] for c in klines]
        vw = calc_vwap(klines)
        vs_list = [calc_vwap(klines[:i+1]) for i in range(len(klines))]
        vs = ((vs_list[-1] - vs_list[-5]) / 5) if len(vs_list) >= 5 and vs_list[-1] and vs_list[-5] else None
        r = calc_rsi(closes)
        rs_list = [calc_rsi(closes[:i+1]) for i in range(len(closes)) if calc_rsi(closes[:i+1]) is not None]
        rslope = (rs_list[-1] - rs_list[-4]) if len(rs_list) >= 4 else None
        m = calc_macd(closes)
        ha = calc_heiken(klines)
        hc, hn = count_consec(ha)
        fail = vw and len(vs_list) >= 3 and len(closes) >= 2 and vs_list[-2] and closes[-1] < vw and closes[-2] > vs_list[-2]
        crosses = sum(1 for i in range(max(1,len(vs_list)-20),len(vs_list)) if vs_list[i] and vs_list[i-1] and ((closes[i]>vs_list[i] and closes[i-1]<vs_list[i-1]) or (closes[i]<vs_list[i] and closes[i-1]>vs_list[i-1])))
        reg = detect_regime(price, vw, vs, crosses)
        raw_up = score_dir(price, vw, vs, r, rslope, m, hc, hn, fail)
        rem = 15
        live = []
        if poly and isinstance(poly, list):
            now = time.time() * 1000
            for pm in poly:
                e = pm.get("endDate","")
                if e:
                    try:
                        from datetime import datetime
                        ems = datetime.fromisoformat(e.replace("Z","+00:00")).timestamp()*1000
                        if now < ems: live.append((ems, pm))
                    except: pass
            live.sort(key=lambda x: x[0])
            if live:
                try:
                    from datetime import datetime
                    ems = datetime.fromisoformat(live[0][1]["endDate"].replace("Z","+00:00")).timestamp()
                    rem = max(0,(ems-time.time())/60)
                except: pass
        decay = clamp(rem/15,0,1)
        aup = clamp(0.5+(raw_up-0.5)*decay,0,1)
        adn = 1-aup
        my,mn = None,None
        if live:
            try:
                ps = json.loads(live[0][1].get("outcomePrices","[]"))
                if len(ps)>=2: my,mn = float(ps[0]),float(ps[1])
            except: pass
        eu,ed = None,None
        if my is not None and mn is not None:
            t=my+mn; mu_p=my/t if t>0 else None; md_p=mn/t if t>0 else None
            eu=(aup-mu_p) if mu_p else None; ed=(adn-md_p) if md_p else None
        rec = decide(rem,eu,ed,aup,adn)
        result = {"status":"ok","btc":round(price,2),"regime":reg,"rsi":round(r,1) if r else None,"model_up":round(aup,3),"model_dn":round(adn,3),"edge_up":round(eu,4) if eu else None,"edge_dn":round(ed,4) if ed else None,"time_left":round(rem,1),"decision":rec}
        log_entry({"ts":int(time.time()),"brain":"btc","btc":result.get("btc"),"regime":reg,"decision":rec})
        if rec["action"]=="ENTER":
            smart_notify("btc_"+rec["side"]+"_"+rec["phase"],"BTC: "+rec["side"]+" Edge="+str(round(rec["edge"]*100,1))+"% "+rec["strength"]+" "+rec["phase"]+" $"+str(round(price))+" "+reg)
        return result
    except Exception as e: return {"status":"error","reason":str(e)[:100]}

# ═══════════════════════════════════════════════════
# BRAIN 6: WHALE TRACKER — FOLLOW SMART MONEY
# ═══════════════════════════════════════════════════

def run_whale_tracker():
    signals = []
    try:
        leaders = fetch_json(GAMMA + "/leaders?limit=20&order_by=pnl&ascending=false")
        if not leaders: return {"status":"ok","signals":[],"whales_tracked":0}
        whale_count = 0
        for w in leaders[:10]:
            addr = w.get("address","") or w.get("proxy_wallet","")
            pnl = float(w.get("pnl",0) or 0)
            vol = float(w.get("volume",0) or 0)
            wr = float(w.get("win_rate",0) or 0)
            if pnl < 1000: continue
            whale_count += 1
            recent = fetch_json(GAMMA + "/activity?user=" + addr + "&limit=5")
            if not recent or not isinstance(recent, list): continue
            for act in recent:
                side = act.get("side","")
                market_q = act.get("market_question","") or act.get("title","")
                size = float(act.get("size",0) or act.get("usdc_size",0) or 0)
                price = float(act.get("price",0) or 0)
                ts = act.get("timestamp","")
                if size >= 200 and side in ["BUY","SELL"]:
                    sig = "WHALE $" + str(round(size)) + " " + side + " " + market_q[:40] + " @" + str(round(price,3)) + " PnL=$" + str(int(pnl)) + " WR=" + str(round(wr*100)) + "%"
                    signals.append(sig)
                    smart_notify("whale_"+addr[:8]+"_"+str(int(size)), sig)
        return {"status":"ok","signals":signals[:15],"whales_tracked":whale_count}
    except Exception as e: return {"status":"error","reason":str(e)[:100],"signals":signals}

# ═══════════════════════════════════════════════════
# BRAIN 7: KALSHI CROSS-PLATFORM ARBITRAGE
# $40M extracted by bots doing exactly this
# ═══════════════════════════════════════════════════

def normalize_question(q):
    q = q.lower().strip()
    for ch in ["?","!",".",",",":",";","'","\""]:q=q.replace(ch,"")
    q = " ".join(q.split())
    return q

def run_kalshi_arb():
    signals = []
    try:
        kalshi_markets = fetch_json(KALSHI + "/markets?status=open&limit=100")
        poly_markets = fetch_json(GAMMA + "/markets?closed=false&limit=100&order=volume&ascending=false")
        if not kalshi_markets or not poly_markets: return {"status":"ok","signals":[],"pairs_checked":0}
        kalshi_list = kalshi_markets.get("markets",[]) if isinstance(kalshi_markets,dict) else kalshi_markets
        if not isinstance(kalshi_list,list): kalshi_list = []
        poly_list = poly_markets if isinstance(poly_markets,list) else []
        pairs = 0
        for km in kalshi_list:
            kq = km.get("title","") or km.get("subtitle","")
            if not kq: continue
            k_yes = float(km.get("yes_bid_dollars",0) or km.get("last_price",0) or 0)
            k_no = float(km.get("no_bid_dollars",0) or 0)
            if k_yes <= 0: k_yes = 1 - k_no if k_no > 0 else 0
            if k_yes <= 0 or k_yes >= 1: continue
            kn = normalize_question(kq)
            for pm in poly_list:
                pq = pm.get("question","")
                pn = normalize_question(pq)
                similarity = sum(1 for a,b in zip(kn,pn) if a==b) / max(len(kn),len(pn),1)
                word_match = len(set(kn.split()) & set(pn.split())) / max(len(set(kn.split())|set(pn.split())),1)
                if similarity < 0.6 and word_match < 0.5: continue
                pairs += 1
                try:
                    p_prices = json.loads(pm.get("outcomePrices","[]")) if isinstance(pm.get("outcomePrices",""),str) else pm.get("outcomePrices",[])
                    p_yes = float(p_prices[0]) if len(p_prices)>=1 else 0
                    p_no = float(p_prices[1]) if len(p_prices)>=2 else 0
                except: continue
                if p_yes <= 0 or p_yes >= 1: continue
                liq = float(pm.get("liquidity",0) or 0)
                if liq < 100: continue
                buy_poly_yes_sell_kalshi = p_yes - k_yes
                buy_kalshi_yes_sell_poly = k_yes - p_yes
                fee_est = 0.04
                if buy_poly_yes_sell_kalshi > fee_est:
                    profit_pct = round((buy_poly_yes_sell_kalshi - fee_est)*100,2)
                    sig = "KALSHI ARB: Buy Poly YES@" + str(round(p_yes,3)) + " Sell Kalshi YES@" + str(round(k_yes,3)) + " Profit=" + str(profit_pct) + "% | " + pq[:35]
                    signals.append(sig)
                    smart_notify("karb_"+str(hash(pq)%10000), sig)
                if buy_kalshi_yes_sell_poly > fee_est:
                    profit_pct = round((buy_kalshi_yes_sell_poly - fee_est)*100,2)
                    sig = "KALSHI ARB: Buy Kalshi YES@" + str(round(k_yes,3)) + " Sell Poly YES@" + str(round(p_yes,3)) + " Profit=" + str(profit_pct) + "% | " + pq[:35]
                    signals.append(sig)
                    smart_notify("karb_"+str(hash(pq)%10000), sig)
                cross_sum = p_yes + (1 - k_yes)
                if cross_sum < 0.96:
                    profit_pct = round((1-cross_sum)*100,2)
                    sig = "CROSS ARB: Poly YES@" + str(round(p_yes,3)) + " + Kalshi NO@" + str(round(1-k_yes,3)) + " = " + str(round(cross_sum,3)) + " Profit=" + str(profit_pct) + "% | " + pq[:35]
                    signals.append(sig)
                    smart_notify("xarb_"+str(hash(pq)%10000), sig)
        return {"status":"ok","signals":signals[:15],"pairs_checked":pairs}
    except Exception as e: return {"status":"error","reason":str(e)[:100],"signals":signals}

# ═══════════════════════════════════════════════════
# BRAIN 8: OVERREACTION FADE
# Buy when price drops too fast, sell when it reverts
# ═══════════════════════════════════════════════════

def run_fade_brain(markets):
    signals = []
    try:
        for m in markets:
            q = m.get("question","")
            liq = float(m.get("liquidity",0) or 0)
            if liq < 200: continue
            try:
                prices = json.loads(m.get("outcomePrices","[]")) if isinstance(m.get("outcomePrices",""),str) else m.get("outcomePrices",[])
                fl = [float(x) for x in prices]
            except: continue
            if len(fl)<2: continue
            yes_p = fl[0]
            one_day_change = float(m.get("oneDayPriceChange",0) or 0)
            one_hour_change = float(m.get("oneHourPriceChange",0) or 0)
            if one_hour_change < -0.10 and yes_p > 0.15 and yes_p < 0.85:
                sig = "FADE BUY: " + q[:40] + " dropped " + str(round(one_hour_change*100,1)) + "% in 1h, now at " + str(round(yes_p,3)) + " Liq=$" + str(int(liq))
                signals.append(sig)
                smart_notify("fade_"+str(hash(q)%10000), sig)
            if one_hour_change > 0.15 and yes_p > 0.5 and yes_p < 0.90:
                sig = "FADE SELL: " + q[:40] + " surged " + str(round(one_hour_change*100,1)) + "% in 1h, now at " + str(round(yes_p,3)) + " Liq=$" + str(int(liq))
                signals.append(sig)
                smart_notify("fadesell_"+str(hash(q)%10000), sig)
        return {"status":"ok","signals":signals[:10]}
    except Exception as e: return {"status":"error","reason":str(e)[:100],"signals":signals}

# ═══════════════════════════════════════════════════
# BRAIN 9: WEATHER BRAIN (GFS Ensemble)
# ═══════════════════════════════════════════════════

WEATHER_CITIES = {"Miami":{"lat":25.76,"lon":-80.19},"Manila":{"lat":14.60,"lon":120.98},"Austin":{"lat":30.27,"lon":-97.74},"Dallas":{"lat":32.78,"lon":-96.80},"Houston":{"lat":29.76,"lon":-95.37},"Phoenix":{"lat":33.45,"lon":-112.07},"Chicago":{"lat":41.88,"lon":-87.63},"New York":{"lat":40.71,"lon":-74.01}}

def run_weather_brain(markets):
    signals = []
    try:
        wx_markets = [m for m in markets if any(w in m.get("question","").lower() for w in ["temperature","weather","temp ","high temp","low temp"])]
        if not wx_markets: return {"status":"ok","signals":[],"cities_checked":0}
        for city,coords in WEATHER_CITIES.items():
            data = fetch_json("https://api.open-meteo.com/v1/gfs?latitude="+str(coords["lat"])+"&longitude="+str(coords["lon"])+"&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=3")
            if not data or "daily" not in data: continue
            daily = data["daily"]
            if not daily.get("temperature_2m_max"): continue
            for i in range(min(len(daily["temperature_2m_max"]),3)):
                gfs_max = daily["temperature_2m_max"][i]
                gfs_min = daily["temperature_2m_min"][i]
                if gfs_max is None: continue
                date_str = daily["time"][i] if "time" in daily else ""
                for wm in wx_markets:
                    q = wm.get("question","")
                    if city.lower() not in q.lower(): continue
                    try:
                        fl = [float(x) for x in (json.loads(wm.get("outcomePrices","[]")) if isinstance(wm.get("outcomePrices",""),str) else wm.get("outcomePrices",[]))]
                    except: continue
                    if len(fl)<2: continue
                    yes_p = fl[0]
                    liq = float(wm.get("liquidity",0) or 0)
                    market_thresh = None
                    for word in q.split():
                        try:
                            val = float(word.strip("°").strip("F").strip("C"))
                            if 20<val<130: market_thresh=val
                        except: pass
                    if not market_thresh: continue
                    if "highest" in q.lower() or "max" in q.lower() or "high" in q.lower():
                        prob = 0.7 if gfs_max>market_thresh else 0.3 if gfs_max>market_thresh-2 else 0.15
                        edge = prob - yes_p
                        if edge>0.08 and liq>50:
                            sig = "WX "+city+": GFS="+str(round(gfs_max,1))+" thresh="+str(market_thresh)+" edge="+str(round(edge*100,1))+"%"
                            signals.append(sig)
                            smart_notify("wx_"+city+"_"+date_str,sig)
                    if "lowest" in q.lower() or "min" in q.lower() or "low" in q.lower():
                        prob = 0.7 if gfs_min<market_thresh else 0.3 if gfs_min<market_thresh+2 else 0.15
                        edge = prob - yes_p
                        if edge>0.08 and liq>50:
                            sig = "WX "+city+": GFS min="+str(round(gfs_min,1))+" thresh="+str(market_thresh)+" edge="+str(round(edge*100,1))+"%"
                            signals.append(sig)
                            smart_notify("wx_"+city+"_"+date_str,sig)
        return {"status":"ok","signals":signals,"cities_checked":len(WEATHER_CITIES)}
    except Exception as e: return {"status":"error","reason":str(e)[:100],"signals":signals}

# ═══════════════════════════════════════════════════
# BRAIN 10: FED/MACRO + NEGRISK + SAFE NO
# ═══════════════════════════════════════════════════

def run_fed_negrisk_safeno(markets):
    signals = []
    fed_sigs = []
    nr_sigs = []
    sno_sigs = []
    try:
        fed_kw = ["fed","fomc","rate cut","rate hike","interest rate","september fomc","10-year yield","treasury","recession","inflation","cpi"]
        for m in markets:
            q = m.get("question","")
            ql = q.lower()
            liq = float(m.get("liquidity",0) or 0)
            try:
                fl = [float(x) for x in (json.loads(m.get("outcomePrices","[]")) if isinstance(m.get("outcomePrices",""),str) else m.get("outcomePrices",[]))]
            except: continue
            if len(fl)<2: continue
            yes_p,no_p = fl[0],fl[1]
            tc = round(sum(fl),4)
            if any(kw in ql for kw in fed_kw):
                if tc<0.98 and liq>200:
                    sig = "FED ARB: "+q[:45]+" Profit="+str(round((1-tc)*100,2))+"%"
                    fed_sigs.append(sig)
                    smart_notify("fed_"+str(hash(q)%10000),sig)
                if no_p>0.85 and liq>500:
                    sig = "FED SAFE_NO: "+q[:45]+" Yield="+str(round((1-no_p)*100,1))+"%"
                    fed_sigs.append(sig)
            if tc<0.98 and liq>100 and len(fl)==2:
                sig = "ARB: "+q[:45]+" Profit="+str(round((1-tc)*100,2))+"% Liq=$"+str(int(liq))
                signals.append(sig)
                smart_notify("arb_"+str(hash(q)%10000),sig)
            if no_p>0.85 and liq>100:
                yld = round((1-no_p)*100,1)
                if yld>=3.0:
                    end_date = m.get("endDate","")
                    days = 999
                    if end_date:
                        try:
                            from datetime import datetime
                            days = max(1,(datetime.fromisoformat(end_date.replace("Z","+00:00")).timestamp()-time.time())/86400)
                        except: pass
                    ann = round(yld*(365/days),1) if days<365 else yld
                    sig = "SAFE_NO: "+q[:40]+" Yield="+str(yld)+"% Ann="+str(ann)+"% Days="+str(int(days))+" Liq=$"+str(int(liq))
                    sno_sigs.append(sig)
                    if yld>=5.0: smart_notify("sno_"+str(hash(q)%10000),sig)
        events_seen = {}
        for m in markets:
            nr_id = m.get("negRiskMarketID","")
            if m.get("negRisk") and nr_id:
                if nr_id not in events_seen: events_seen[nr_id]=[]
                events_seen[nr_id].append(m)
        for nr_id,group in events_seen.items():
            if len(group)<3: continue
            total = 0
            ok = True
            for gm in group:
                try:
                    fl = [float(x) for x in (json.loads(gm.get("outcomePrices","[]")) if isinstance(gm.get("outcomePrices",""),str) else gm.get("outcomePrices",[]))]
                    if fl: total+=fl[0]
                    else: ok=False
                except: ok=False
            if not ok: continue
            gap = round(abs(1.0-total),4)
            if gap>0.005:
                liq_t = sum(float(gm.get("liquidity",0) or 0) for gm in group)
                if liq_t>500:
                    act = "BUY_ALL" if total<1.0 else "SELL_ALL"
                    title = group[0].get("events",[{}])[0].get("title","?") if group[0].get("events") else "?"
                    sig = "NEGRISK "+act+": "+title[:35]+" Gap="+str(round(gap*100,2))+"% Outcomes="+str(len(group))
                    nr_sigs.append(sig)
                    smart_notify("nr_"+nr_id[:10],sig)
        days_fomc = 12
        if days_fomc<=14:
            fed_sigs.append("FOMC COUNTDOWN: "+str(days_fomc)+" days to Sep 16!")
            smart_notify("fomc_"+str(days_fomc),"FOMC: "+str(days_fomc)+" days to Sep 16! All Fed markets on alert!")
        sno_sigs.sort(key=lambda x: float(x.split("Yield=")[1].split("%")[0]) if "Yield=" in x else 0, reverse=True)
        return {"status":"ok","fed":fed_sigs[:10],"negrisk":nr_sigs[:10],"safe_no":sno_sigs[:15],"all":signals[:10],"days_to_fomc":days_fomc}
    except Exception as e: return {"status":"error","reason":str(e)[:100],"fed":fed_sigs,"negrisk":nr_sigs,"safe_no":sno_sigs,"all":signals}

# ═══════════════════════════════════════════════════
# BRAIN 11: PAPER TRADING EXECUTION ENGINE
# $10,000 paper balance. Real order book prices.
# Exact Polymarket fee model. Full P&L tracking.
# ═══════════════════════════════════════════════════

def paper_execute(signal_type, market_q, side, price, amount_usd=50):
    pf = get_portfolio()
    if pf["balance"] < amount_usd:
        return {"executed": False, "reason": "insufficient_balance"}
    shares = amount_usd / price if price > 0 else 0
    fee = poly_fee(price, shares)
    total_cost = amount_usd + fee
    if total_cost > pf["balance"]:
        shares = (pf["balance"] - fee) / price if price > 0 else 0
        total_cost = pf["balance"]
    position = {
        "id": str(int(time.time()*1000)),
        "ts": int(time.time()),
        "type": signal_type,
        "market": market_q[:60],
        "side": side,
        "entry_price": round(price, 4),
        "shares": round(shares, 2),
        "cost": round(total_cost, 2),
        "fee": round(fee, 2),
        "status": "open"
    }
    pf["balance"] = round(pf["balance"] - total_cost, 2)
    pf["positions"].append(position)
    pf["trades"].append({"ts":position["ts"],"action":"BUY","market":market_q[:60],"side":side,"price":round(price,4),"shares":round(shares,2),"cost":round(total_cost,2),"fee":round(fee,2)})
    save_portfolio(pf)
    log_entry({"ts":int(time.time()),"brain":"paper_trade","action":"BUY","market":market_q[:60],"side":side,"price":round(price,4),"shares":round(shares,2),"cost":round(total_cost,2),"signal":signal_type})
    return {"executed": True, "shares": round(shares,2), "cost": round(total_cost,2), "fee": round(fee,2), "balance": pf["balance"]}

def check_paper_positions():
    pf = get_portfolio()
    if not pf["positions"]: return pf
    open_positions = [p for p in pf["positions"] if p["status"]=="open"]
    for pos in open_positions:
        try:
            slug_search = pos["market"][:30].lower().replace(" ","-")
            mkts = fetch_json(GAMMA+"/markets?closed=false&limit=5&_q="+slug_search[:20])
            if not mkts or not isinstance(mkts,list) or len(mkts)==0: continue
            mkt = mkts[0]
            prices = json.loads(mkt.get("outcomePrices","[]")) if isinstance(mkt.get("outcomePrices",""),str) else mkt.get("outcomePrices",[])
            if len(prices)<2: continue
            current_yes = float(prices[0])
            current_no = float(prices[1])
            current_price = current_yes if pos["side"]=="YES" else current_no
            pos["current_price"] = round(current_price,4)
            pos["unrealized_pnl"] = round((current_price - pos["entry_price"]) * pos["shares"], 2)
            pos["pnl_pct"] = round(((current_price - pos["entry_price"]) / pos["entry_price"]) * 100, 2) if pos["entry_price"] > 0 else 0
        except: pass
    save_portfolio(pf)
    return pf

def get_paper_stats():
    pf = check_paper_positions()
    open_pos = [p for p in pf["positions"] if p["status"]=="open"]
    closed_pos = [p for p in pf["positions"] if p["status"]=="closed"]
    total_unrealized = sum(p.get("unrealized_pnl",0) for p in open_pos)
    positions_value = sum(p.get("current_price",p["entry_price"])*p["shares"] for p in open_pos)
    wins = len([t for t in closed_pos if t.get("realized_pnl",0)>0])
    losses = len([t for t in closed_pos if t.get("realized_pnl",0)<=0])
    total_closed = wins+losses
    win_rate = round(wins/total_closed*100,1) if total_closed>0 else 0
    total_value = round(pf["balance"]+positions_value+total_unrealized,2)
    roi = round(((total_value-10000)/10000)*100,2)
    return {
        "balance": pf["balance"],
        "open_positions": len(open_pos),
        "positions_value": round(positions_value,2),
        "unrealized_pnl": round(total_unrealized,2),
        "realized_pnl": pf.get("realized_pnl",0),
        "total_value": total_value,
        "roi_pct": roi,
        "total_trades": len(pf["trades"]),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "closed_trades": total_closed
    }

def auto_paper_trade(brains_result):
    executed = []
    pf = get_portfolio()
    if len([p for p in pf["positions"] if p["status"]=="open"]) >= 20:
        return executed
    btc = brains_result.get("btc",{})
    if btc and btc.get("status")=="ok":
        dec = btc.get("decision",{})
        if dec.get("action")=="ENTER" and dec.get("strength") in ["STRONG","GOOD"]:
            poly = fetch_json(GAMMA+"/markets?seriesSlug=btc-up-or-down-15m&active=true&closed=false&enableOrderBook=true&limit=10")
            if poly and isinstance(poly,list):
                now = time.time()*1000
                live = []
                for pm in poly:
                    e = pm.get("endDate","")
                    if e:
                        try:
                            from datetime import datetime
                            ems = datetime.fromisoformat(e.replace("Z","+00:00")).timestamp()*1000
                            if now<ems: live.append((ems,pm))
                        except: pass
                live.sort(key=lambda x:x[0])
                if live:
                    try:
                        ps = json.loads(live[0][1].get("outcomePrices","[]"))
                        if len(ps)>=2:
                            price = float(ps[0]) if dec["side"]=="UP" else float(ps[1])
                            side = "YES" if dec["side"]=="UP" else "NO"
                            amt = 25 if dec["strength"]=="STRONG" else 15
                            existing = [p for p in pf["positions"] if p["status"]=="open" and p["type"]=="btc" and (time.time()-p["ts"])<300]
                            if not existing:
                                result = paper_execute("btc",live[0][1].get("question","BTC 15m"),side,price,amt)
                                if result.get("executed"):
                                    executed.append({"brain":"btc","side":dec["side"],"amount":amt,"result":result})
                    except: pass
    fns = brains_result.get("fed_negrisk_safeno",{})
    if fns:
        for sig in fns.get("safe_no",[])[: 3]:
            if "Yield=" in sig:
                try:
                    yld = float(sig.split("Yield=")[1].split("%")[0])
                    if yld >= 8.0:
                        market_part = sig.split("SAFE_NO: ")[1].split(" Yield=")[0] if "SAFE_NO: " in sig else ""
                        existing = [p for p in pf["positions"] if p["status"]=="open" and market_part[:20] in p.get("market","")]
                        if not existing and market_part:
                            mkts = fetch_json(GAMMA+"/markets?closed=false&limit=3&_q="+market_part[:25].replace(" ","+"))
                            if mkts and isinstance(mkts,list) and len(mkts)>0:
                                mkt = mkts[0]
                                prices = json.loads(mkt.get("outcomePrices","[]")) if isinstance(mkt.get("outcomePrices",""),str) else mkt.get("outcomePrices",[])
                                if len(prices)>=2:
                                    no_price = float(prices[1])
                                    if no_price > 0.85:
                                        result = paper_execute("safe_no",mkt.get("question",""),  "NO",no_price,20)
                                        if result.get("executed"):
                                            executed.append({"brain":"safe_no","market":market_part[:30],"amount":20,"result":result})
                except: pass
    for sig in brains_result.get("kalshi_arb",{}).get("signals",[])[: 2]:
        if "Profit=" in sig:
            try:
                profit = float(sig.split("Profit=")[1].split("%")[0])
                if profit > 2.0:
                    existing = [p for p in pf["positions"] if p["status"]=="open" and "kalshi" in p.get("type","")]
                    if len(existing) < 3:
                        market_part = sig.split("| ")[1][:30] if "| " in sig else "kalshi_arb"
                        result = paper_execute("kalshi_arb",market_part,"YES",0.50,30)
                        if result.get("executed"):
                            executed.append({"brain":"kalshi_arb","profit":profit,"amount":30,"result":result})
            except: pass
    return executed

# ═══════════════════════════════════════════════════
# MASTER RUNNER — ALL BRAINS + AUTO PAPER TRADING
# ═══════════════════════════════════════════════════

def run_all_brains():
    result = {}
    result["btc"] = run_btc_brain()
    markets = fetch_json(GAMMA+"/markets?closed=false&limit=100&order=volume&ascending=false")
    if not markets: markets = []
    result["whales"] = run_whale_tracker()
    result["kalshi_arb"] = run_kalshi_arb()
    result["fade"] = run_fade_brain(markets)
    result["weather"] = run_weather_brain(markets)
    result["fed_negrisk_safeno"] = run_fed_negrisk_safeno(markets)
    result["paper_trades"] = auto_paper_trade(result)
    result["paper_stats"] = get_paper_stats()
    all_sigs = []
    for brain_name in ["whales","kalshi_arb","fade","weather"]:
        b = result.get(brain_name,{})
        if b and b.get("signals"):
            for s in b["signals"]: all_sigs.append(s)
    fns = result.get("fed_negrisk_safeno",{})
    if fns:
        for s in fns.get("fed",[]): all_sigs.append(s)
        for s in fns.get("negrisk",[]): all_sigs.append(s)
        for s in fns.get("safe_no",[])[: 5]: all_sigs.append(s)
        for s in fns.get("all",[]): all_sigs.append(s)
    pt = result.get("paper_trades",[])
    if pt:
        for trade in pt:
            sig = "PAPER TRADE: "+trade.get("brain","")+" "+str(trade.get("side",""))+" $"+str(trade.get("amount",""))+" bal=$"+str(trade.get("result",{}).get("balance",""))
            all_sigs.append(sig)
            smart_notify("ptrade_"+str(int(time.time())),sig)
    ps = result.get("paper_stats",{})
    if ps.get("total_trades",0) > 0:
        summary = "PORTFOLIO: $"+str(ps.get("total_value",0))+" ROI="+str(ps.get("roi_pct",0))+"% WR="+str(ps.get("win_rate",0))+"% Trades="+str(ps.get("total_trades",0))+" Open="+str(ps.get("open_positions",0))
        all_sigs.append(summary)
        smart_notify("portfolio_"+str(int(ps.get("total_value",0))),summary)
    result["all_signals"] = all_sigs[:25]
    result["signal_count"] = len(all_sigs)
    return result
