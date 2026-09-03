from http.server import BaseHTTPRequestHandler
import urllib.request
import json

TOPIC = 'praveen-polymarket-bot-2026'

def notify(msg):
    try:
        req = urllib.request.Request(
            'https://ntfy.sh/' + TOPIC,
            data=msg.encode(),
            method='POST'
        )
        urllib.request.urlopen(req, timeout=10)
    except:
        pass

def scan():
    arbs = 0
    negrisk = 0
    weather = 0
    signals = []
    try:
        url = 'https://gamma-api.polymarket.com/markets?closed=false&limit=100&order=volume&ascending=false'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
        
        for m in data:
            q = m.get('question', '')
            op = m.get('outcomePrices', '')
            liq = float(m.get('liquidity', 0) or 0)
            try:
                prices = json.loads(op) if isinstance(op, str) else op
                fl = [float(x) for x in prices]
                tc = round(sum(fl), 4)
                
                if len(fl) == 2 and tc < 1.0 and liq > 100:
                    arbs += 1
                    pp = round((1 - tc) * 100, 2)
                    sig = 'ARB: ' + q[:45] + ' Profit=' + str(pp) + '% Liq=$' + str(int(liq))
                    signals.append(sig)
                    notify(sig)
                
                if len(fl) >= 3 and abs(tc - 1.0) > 0.005 and liq > 500:
                    negrisk += 1
                    act = 'BUY_ALL' if tc < 1.0 else 'SELL_ALL'
                    sig = 'NEGRISK ' + act + ': ' + q[:40] + ' Edge=' + str(round(abs(1-tc)*100,1)) + '%'
                    signals.append(sig)
                    notify(sig)
                
                if 'temperature' in q.lower() or 'weather' in q.lower():
                    weather += 1
                
                if len(fl) == 2 and fl[1] > 0.85 and liq > 100:
                    yld = round((1 - fl[1]) * 100, 1)
                    if yld > 2:
                        sig = 'SAFE_NO: ' + q[:40] + ' Yield=' + str(yld) + '%'
                        signals.append(sig)
                
                if len(fl) == 2 and fl[0] < 0.10 and liq > 100:
                    sig = 'CHEAP_YES: ' + q[:40] + ' Price=' + str(fl[0])
                    signals.append(sig)
                    
            except:
                pass
    except Exception as e:
        signals.append('Scan error: ' + str(e)[:50])
    
    return arbs, negrisk, weather, signals

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        arbs, negrisk, weather, signals = scan()
        
        summary = 'Scanner: ' + str(arbs) + ' arb, ' + str(negrisk) + ' negrisk, ' + str(weather) + ' weather'
        
        if arbs > 0 or negrisk > 0:
            notify(summary + ' SIGNALS FOUND!')
        
        response = {
            'status': 'alive',
            'arbs': arbs,
            'negrisk': negrisk,
            'weather': weather,
            'signals': signals[:10],
            'summary': summary
        }
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())
        return
