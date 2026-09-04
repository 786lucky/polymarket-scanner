from http.server import BaseHTTPRequestHandler
import json
from .brain import run_all_brains

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        brains = run_all_brains()
        response = {
            'status': 'alive',
            'brains': {
                'btc': brains.get('btc'),
                'whales': brains.get('whales'),
                'kalshi_arb': brains.get('kalshi_arb'),
                'fade': brains.get('fade'),
                'weather': brains.get('weather'),
                'fed_negrisk_safeno': brains.get('fed_negrisk_safeno')
            },
            'paper_trades': brains.get('paper_trades', []),
            'paper_stats': brains.get('paper_stats', {}),
            'all_signals': brains.get('all_signals', []),
            'signal_count': brains.get('signal_count', 0)
        }
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())
        return
