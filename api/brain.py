import urllib.request, json, time

COINGECKO = "https://api.coingecko.com/api/v3"
GAMMA = "https://gamma-api.polymarket.com"
TOPIC = "praveen-polymarket-bot-2026"
LOG_FILE = "/tmp/btc_brain_log.json"
SIG_FILE = "/tmp/last_signals.txt"

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
            if len(lines) > 100: lines = lines[-100:]
            with open(SIG_FILE, "w") as f: f.write("\n".join(lines))
    except: pass

def fetch_json(url):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        resp = urllib.request.urlopen(r, timeout=15)
        return json.loads(resp.read().decode())
    except: return None

def log_signal(entry):
    try:
        logs = []
        try:
            with open(LOG_FILE, "r") as f: logs = json.loads(f.read())
        except: pass
        logs.append(entry)
        if len(logs) > 500: logs = logs[-500:]
        with open(LOG_FILE, "w") as f: f.write(json.dumps(logs))
    except: pass

def get_stats():
    try:
        with open(LOG_FILE, "r") as f: logs = json.loads(f.read())
        enters = [l for l in logs if l.get("decision", {}).get("action") == "ENTER"]
        return {"total": len(logs), "enters": len(enters), "ups": len([e for e in enters if e.get("side") == "UP"]), "downs": len([e for e in enters if e.get("side") == "DOWN"])}
    except: return {"total": 0}

# ═══════════════════════════════════════════════════
# BRAIN 1: BTC TRADING ENGINE
# Source: FrondEnt/PolymarketBTC15mAssistant ($414K bot)
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

def detect_regime(price, vw, vs, crosses, vr, va):
    if price is None or vw is None or vs is None: return "CHOP"
    if vr and va and vr < 0.6*va and abs((price-vw)/vw) < 0.001: return "CHOP"
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
        cg_data = fetch_json(COINGECKO + "/simple/price?ids=bitcoin&vs_currencies=usd")
        price = float(cg_data["bitcoin"]["usd"]) if cg_data else None
        ohlc = fetch_json(COINGECKO + "/coins/bitcoin/ohlc?vs_currency=usd&days=1")
        klines = [{"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4]),"v":0} for k in ohlc] if ohlc else None
        poly = fetch_json(GAMMA + "/markets?seriesSlug=btc-up-or-down-15m&active=true&closed=false&enableOrderBook=true&limit=10")
        if not klines or not price:
            return {"status": "error", "reason": "fetch_fail"}
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
        fail = False
        if vw and len(vs_list) >= 3 and len(closes) >= 2 and vs_list[-2]:
            fail = closes[-1] < vw and closes[-2] > vs_list[-2]
        crosses = 0
        for i in range(max(1, len(vs_list)-20), len(vs_list)):
            if vs_list[i] and vs_list[i-1]:
                if (closes[i] > vs_list[i] and closes[i-1] < vs_list[i-1]) or (closes[i] < vs_list[i] and closes[i-1] > vs_list[i-1]):
                    crosses += 1
        reg = detect_regime(price, vw, vs, crosses, None, None)
        raw_up = score_dir(price, vw, vs, r, rslope, m, hc, hn, fail)
        rem = 15
        if poly and isinstance(poly, list):
            now = time.time() * 1000
            live = []
            for pm in poly:
                e = pm.get("endDate", "")
                if e:
                    try:
                        from datetime import datetime
                        ems = datetime.fromisoformat(e.replace("Z", "+00:00")).timestamp() * 1000
                        if now < ems: live.append((ems, pm))
                    except: pass
            live.sort(key=lambda x: x[0])
            if live:
                try:
                    from datetime import datetime
                    ems = datetime.fromisoformat(live[0][1]["endDate"].replace("Z", "+00:00")).timestamp()
                    rem = max(0, (ems - time.time()) / 60)
                except: pass
        decay = clamp(rem / 15, 0, 1)
        aup = clamp(0.5 + (raw_up - 0.5) * decay, 0, 1)
        adn = 1 - aup
        my, mn = None, None
        if poly and isinstance(poly, list) and live:
            try:
                ps = json.loads(live[0][1].get("outcomePrices", "[]"))
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
        result = {"status": "ok", "btc": round(price, 2), "regime": reg, "rsi": round(r, 1) if r else None, "model_up": round(aup, 3), "model_dn": round(adn, 3), "edge_up": round(eu, 4) if eu else None, "edge_dn": round(ed, 4) if ed else None, "time_left": round(rem, 1), "decision": rec, "stats": get_stats()}
        log_signal({"ts": int(time.time()), "btc": result.get("btc"), "regime": reg, "rsi": result.get("rsi"), "model_up": result.get("model_up"), "edge_up": result.get("edge_up"), "decision": rec})
        if rec["action"] == "ENTER":
            smart_notify("btc_" + rec["side"] + "_" + rec["phase"], "BTC BRAIN: " + rec["side"] + " Edge=" + str(round(rec["edge"]*100, 1)) + "% " + rec["strength"] + " " + rec["phase"] + " BTC=$" + str(round(price)) + " " + reg)
        return result
    except Exception as e:
        return {"status": "error", "reason": str(e)[:100]}

# ═══════════════════════════════════════════════════
# BRAIN 2: WEATHER BRAIN
# Source: fcomp3 strategy ($8,200/day weather markets)
# Uses Open-Meteo GFS ensemble for Tier A cities
# ═══════════════════════════════════════════════════

WEATHER_CITIES = {
    "Miami": {"lat": 25.76, "lon": -80.19},
    "Manila": {"lat": 14.60, "lon": 120.98},
    "Austin": {"lat": 30.27, "lon": -97.74},
    "Dallas": {"lat": 32.78, "lon": -96.80},
    "Houston": {"lat": 29.76, "lon": -95.37},
    "Phoenix": {"lat": 33.45, "lon": -112.07},
    "Chicago": {"lat": 41.88, "lon": -87.63},
    "New York": {"lat": 40.71, "lon": -74.01},
}

def run_weather_brain(markets):
    signals = []
    try:
        weather_markets = []
        for m in markets:
            q = m.get("question", "").lower()
            if any(w in q for w in ["temperature", "weather", "temp ", "high temp", "low temp"]):
                weather_markets.append(m)
        if not weather_markets:
            return {"status": "ok", "signals": [], "cities_checked": 0}
        for city_name, coords in WEATHER_CITIES.items():
            url = "https://api.open-meteo.com/v1/gfs?latitude=" + str(coords["lat"]) + "&longitude=" + str(coords["lon"]) + "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=3"
            data = fetch_json(url)
            if not data or "daily" not in data: continue
            daily = data["daily"]
            if not daily.get("temperature_2m_max"): continue
            for i in range(min(len(daily["temperature_2m_max"]), 3)):
                gfs_max = daily["temperature_2m_max"][i]
                gfs_min = daily["temperature_2m_min"][i]
                if gfs_max is None or gfs_min is None: continue
                date_str = daily["time"][i] if "time" in daily else ""
                for wm in weather_markets:
                    q = wm.get("question", "")
                    ql = q.lower()
                    if city_name.lower() not in ql: continue
                    try:
                        prices = json.loads(wm.get("outcomePrices", "[]")) if isinstance(wm.get("outcomePrices", ""), str) else wm.get("outcomePrices", [])
                        fl = [float(x) for x in prices]
                    except: continue
                    if len(fl) < 2: continue
                    yes_price = fl[0]
                    liq = float(wm.get("liquidity", 0) or 0)
                    if "highest" in ql or "max" in ql or "high" in ql:
                        threshold = int(gfs_max)
                        market_thresh = None
                        for word in q.split():
                            try:
                                val = float(word.strip("°").strip("F").strip("C"))
                                if 50 < val < 130: market_thresh = val
                            except: pass
                        if market_thresh:
                            prob_above = 0.7 if gfs_max > market_thresh else 0.3 if gfs_max > market_thresh - 2 else 0.15
                            edge = prob_above - yes_price
                            if edge > 0.08 and liq > 50:
                                sig = "WEATHER " + city_name + ": GFS max=" + str(round(gfs_max,1)) + " Market thresh=" + str(market_thresh) + " Edge=" + str(round(edge*100,1)) + "%"
                                signals.append(sig)
                                smart_notify("wx_" + city_name + "_" + date_str, sig)
                    if "lowest" in ql or "min" in ql or "low" in ql:
                        market_thresh = None
                        for word in q.split():
                            try:
                                val = float(word.strip("°").strip("F").strip("C"))
                                if -20 < val < 100: market_thresh = val
                            except: pass
                        if market_thresh:
                            prob_below = 0.7 if gfs_min < market_thresh else 0.3 if gfs_min < market_thresh + 2 else 0.15
                            edge = prob_below - yes_price
                            if edge > 0.08 and liq > 50:
                                sig = "WEATHER " + city_name + ": GFS min=" + str(round(gfs_min,1)) + " Market thresh=" + str(market_thresh) + " Edge=" + str(round(edge*100,1)) + "%"
                                signals.append(sig)
                                smart_notify("wx_" + city_name + "_" + date_str, sig)
        return {"status": "ok", "signals": signals, "cities_checked": len(WEATHER_CITIES)}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:100], "signals": signals}

# ═══════════════════════════════════════════════════
# BRAIN 3: FED/MACRO BRAIN
# Source: 13_niakris macro analysis + Sep 16 FOMC setup
# Monitors Fed-related markets for mispricing
# ═══════════════════════════════════════════════════

def run_fed_brain(markets):
    signals = []
    try:
        fed_keywords = ["fed", "fomc", "rate cut", "rate hike", "interest rate", "september fomc", "10-year yield", "treasury", "s&p 500", "recession", "inflation", "cpi", "gdp", "unemployment"]
        fed_markets = []
        for m in markets:
            q = m.get("question", "").lower()
            if any(kw in q for kw in fed_keywords):
                fed_markets.append(m)
        for fm in fed_markets:
            q = fm.get("question", "")
            liq = float(fm.get("liquidity", 0) or 0)
            try:
                prices = json.loads(fm.get("outcomePrices", "[]")) if isinstance(fm.get("outcomePrices", ""), str) else fm.get("outcomePrices", [])
                fl = [float(x) for x in prices]
            except: continue
            if len(fl) < 2: continue
            yes_p = fl[0]
            no_p = fl[1]
            tc = round(sum(fl), 4)
            if tc < 0.98 and liq > 200:
                profit = round((1 - tc) * 100, 2)
                sig = "FED ARB: " + q[:50] + " Profit=" + str(profit) + "% Liq=$" + str(int(liq))
                signals.append(sig)
                smart_notify("fed_arb_" + str(hash(q) % 10000), sig)
            if "fomc" in q.lower() or "fed rate" in q.lower() or "rate cut" in q.lower():
                if yes_p < 0.15 and liq > 500:
                    sig = "FED CHEAP: " + q[:50] + " YES=" + str(yes_p) + " Liq=$" + str(int(liq))
                    signals.append(sig)
                    smart_notify("fed_cheap_" + str(hash(q) % 10000), sig)
                if no_p > 0.85 and liq > 500:
                    yld = round((1 - no_p) * 100, 1)
                    sig = "FED SAFE_NO: " + q[:50] + " Yield=" + str(yld) + "% Liq=$" + str(int(liq))
                    signals.append(sig)
                    smart_notify("fed_safe_" + str(hash(q) % 10000), sig)
        days_to_fomc = 12
        if days_to_fomc <= 14:
            sig = "FED ALERT: " + str(days_to_fomc) + " days to Sep 16 FOMC! Monitor all Fed markets!"
            smart_notify("fomc_countdown_" + str(days_to_fomc), sig)
            signals.append(sig)
        return {"status": "ok", "signals": signals, "fed_markets_found": len(fed_markets), "days_to_fomc": days_to_fomc}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:100], "signals": signals}

# ═══════════════════════════════════════════════════
# BRAIN 4: NEGRISK SCANNER
# Source: Oracle Boar math (sum of outcomes != $1.00)
# Multi-outcome markets where probabilities don't add up
# ═══════════════════════════════════════════════════

def run_negrisk_brain(markets):
    signals = []
    try:
        events_seen = {}
        for m in markets:
            nr = m.get("negRisk", False)
            nr_id = m.get("negRiskMarketID", "")
            if not nr or not nr_id: continue
            if nr_id not in events_seen: events_seen[nr_id] = []
            events_seen[nr_id].append(m)
        for nr_id, group in events_seen.items():
            if len(group) < 3: continue
            total_yes = 0
            valid = True
            for gm in group:
                try:
                    prices = json.loads(gm.get("outcomePrices", "[]")) if isinstance(gm.get("outcomePrices", ""), str) else gm.get("outcomePrices", [])
                    fl = [float(x) for x in prices]
                    if len(fl) >= 1:
                        total_yes += fl[0]
                    else:
                        valid = False
                except:
                    valid = False
            if not valid: continue
            gap = round(abs(1.0 - total_yes), 4)
            if gap > 0.005:
                liq_total = sum(float(gm.get("liquidity", 0) or 0) for gm in group)
                if liq_total > 500:
                    action = "BUY_ALL_YES" if total_yes < 1.0 else "SELL_ALL_YES"
                    event_title = group[0].get("events", [{}])[0].get("title", "Unknown") if group[0].get("events") else "Unknown"
                    sig = "NEGRISK " + action + ": " + event_title[:40] + " Gap=" + str(round(gap*100, 2)) + "% Outcomes=" + str(len(group)) + " Liq=$" + str(int(liq_total))
                    signals.append(sig)
                    smart_notify("nr_" + nr_id[:10], sig)
        return {"status": "ok", "signals": signals, "events_scanned": len(events_seen)}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:100], "signals": signals}

# ═══════════════════════════════════════════════════
# BRAIN 5: SAFE NO COMPOUNDER
# Source: smaaaaliy safety margin + BoneOhio complete-set logic
# Finds NO positions > 85¢ with yield > 3%
# ═══════════════════════════════════════════════════

def run_safe_no_brain(markets):
    signals = []
    try:
        for m in markets:
            q = m.get("question", "")
            liq = float(m.get("liquidity", 0) or 0)
            if liq < 100: continue
            try:
                prices = json.loads(m.get("outcomePrices", "[]")) if isinstance(m.get("outcomePrices", ""), str) else m.get("outcomePrices", [])
                fl = [float(x) for x in prices]
            except: continue
            if len(fl) < 2: continue
            no_p = fl[1]
            yes_p = fl[0]
            if no_p > 0.85:
                yld = round((1 - no_p) * 100, 1)
                if yld >= 3.0:
                    end_date = m.get("endDate", "")
                    days_left = 999
                    if end_date:
                        try:
                            from datetime import datetime
                            end_ts = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
                            days_left = max(1, (end_ts - time.time()) / 86400)
                        except: pass
                    annualized = round(yld * (365 / days_left), 1) if days_left < 365 else yld
                    sig = "SAFE_NO: " + q[:45] + " Yield=" + str(yld) + "% Annual=" + str(annualized) + "% Days=" + str(int(days_left)) + " Liq=$" + str(int(liq))
                    signals.append(sig)
                    if yld >= 5.0:
                        smart_notify("safe_no_" + str(hash(q) % 10000), sig)
        signals.sort(key=lambda x: float(x.split("Yield=")[1].split("%")[0]) if "Yield=" in x else 0, reverse=True)
        return {"status": "ok", "signals": signals[:15]}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:100], "signals": signals}

# ═══════════════════════════════════════════════════
# MASTER RUNNER — Runs all 5 brains
# ═══════════════════════════════════════════════════

def run_all_brains():
    result = {"btc": None, "weather": None, "fed": None, "negrisk": None, "safe_no": None, "all_signals": []}
    result["btc"] = run_btc_brain()
    markets = fetch_json(GAMMA + "/markets?closed=false&limit=100&order=volume&ascending=false")
    if not markets: markets = []
    result["weather"] = run_weather_brain(markets)
    result["fed"] = run_fed_brain(markets)
    result["negrisk"] = run_negrisk_brain(markets)
    result["safe_no"] = run_safe_no_brain(markets)
    for brain_name in ["weather", "fed", "negrisk", "safe_no"]:
        brain = result.get(brain_name, {})
        if brain and brain.get("signals"):
            for sig in brain["signals"]:
                result["all_signals"].append(sig)
    summary_parts = []
    if result["btc"] and result["btc"].get("status") == "ok":
        dec = result["btc"].get("decision", {})
        if dec.get("action") == "ENTER":
            summary_parts.append("BTC:" + dec["side"])
    if result["weather"] and result["weather"].get("signals"):
        summary_parts.append("WX:" + str(len(result["weather"]["signals"])))
    if result["fed"] and result["fed"].get("signals"):
        summary_parts.append("FED:" + str(len(result["fed"]["signals"])))
    if result["negrisk"] and result["negrisk"].get("signals"):
        summary_parts.append("NR:" + str(len(result["negrisk"]["signals"])))
    if result["safe_no"] and result["safe_no"].get("signals"):
        summary_parts.append("SNO:" + str(len(result["safe_no"]["signals"])))
    if summary_parts:
        smart_notify("summary_" + "_".join(summary_parts), "ALL BRAINS: " + " | ".join(summary_parts))
    return result
