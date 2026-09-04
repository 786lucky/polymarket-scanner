from http.server import BaseHTTPRequestHandler
import urllib.request
import json
from .brain import run_all_brains

TOPIC = 'praveen-polymarket-bot-2026'

def notify(msg):
    try:
        req = urllib.request.Request('https://ntfy.sh/' + TOPIC, data=msg.encode(), method='POST')
        urllib.request.urlopen(req, timeout=10)
    except: pass

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        brains = run_all_brains()
        btc = brains.get("btc", {})
        all_sigs = brains.get("all_signals", [])
        response = {
            'status': 'alive',
            'brains': {
                'btc': btc,
                'weather': brains.get('weather'),
                'fed': brains.get('fed'),
                'negrisk': brains.get('negrisk'),
                'safe_no': brains.get('safe_no')
            },
            'all_signals': all_sigs[:20],
            'signal_count': len(all_sigs)
        }
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())
        return
