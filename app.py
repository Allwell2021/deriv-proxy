#!/usr/bin/env python3
"""
Deriv WebSocket Proxy for Render.com
Includes dummy endpoint for keep-alive (cron jobs)
"""

import json
import time
import threading
import websocket
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ===== CONFIG =====
APP_ID = 117548
API_TOKEN = 'pat_dd21039e2e06160bc8464e9c1b5aecb6b6d0ac4f819652dc1d32bf4aea4955bd'
WS_URL = f'wss://ws.deriv.com/websockets/v3?app_id={APP_ID}'

app = Flask(__name__)
CORS(app)

# ===== STATE =====
ws = None
is_ready = False
pending_trades = {}
latest_price = None
latest_status = "Disconnected"
trade_results = []
current_symbol = 'stpRNG'

# ===== WEBSOCKET =====
def on_message(ws, message):
    global is_ready, pending_trades, latest_price, latest_status, trade_results
    try:
        data = json.loads(message)

        if 'authorize' in data:
            is_ready = True
            latest_status = "Connected"
            print(f"✅ Connected to Deriv (App ID: {APP_ID})")

        if 'tick' in data:
            latest_price = data['tick']['quote']

        if 'contract_update' in data:
            contract = data['contract_update']
            contract_id = contract['contract_id']
            if contract_id in pending_trades:
                profit = contract.get('profit', 0)
                status = contract.get('status', 'unknown')
                if status == 'sold':
                    result = "WIN" if profit > 0 else "LOSS"
                    trade_results.append({
                        'contract_id': contract_id,
                        'result': result,
                        'profit': profit,
                        'time': time.time()
                    })
                    if len(trade_results) > 20:
                        trade_results.pop(0)
                    print(f"📈 Contract {contract_id}: {result} | Profit: ${profit:.2f}")
                    del pending_trades[contract_id]

        if 'buy' in data:
            contract_id = data['buy']['contract_id']
            pending_trades[contract_id] = True
            print(f"✅ Trade placed! Contract ID: {contract_id}")

        if 'error' in data:
            print(f"❌ Error: {data['error']['message']}")
            latest_status = f"Error: {data['error']['message']}"

    except Exception as e:
        print(f"⚠️ Message error: {e}")

def on_error(ws, error):
    global latest_status
    latest_status = "Connection error"
    print(f"⚠️ WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    global is_ready, latest_status
    is_ready = False
    latest_status = "Disconnected"
    print("🔌 Disconnected. Reconnecting in 3s...")
    time.sleep(3)
    connect_websocket()

def on_open(ws):
    print("🔗 WebSocket opened, authorizing...")
    ws.send(json.dumps({"authorize": API_TOKEN, "req_id": 1}))

def connect_websocket():
    global ws
    ws = websocket.WebSocketApp(WS_URL,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()

# ===== FLASK ROUTES =====

# 1. Keep-alive endpoint (for cron jobs)
@app.route('/ping')
def ping():
    """Dummy endpoint to keep Render service awake"""
    return jsonify({
        'status': 'alive',
        'time': time.time(),
        'connected': is_ready
    })

# 2. Health check
@app.route('/')
def index():
    return jsonify({
        'service': 'Deriv Proxy',
        'status': latest_status,
        'price': latest_price,
        'connected': is_ready,
        'symbol': current_symbol,
        'trades': len(trade_results)
    })

# 3. Status
@app.route('/status')
def status():
    return jsonify({
        'connected': is_ready,
        'status': latest_status,
        'price': latest_price,
        'symbol': current_symbol,
        'trades': trade_results[-10:]
    })

# 4. Trade endpoints
@app.route('/trade/rise', methods=['POST'])
def trade_rise():
    return place_trade('CALL')

@app.route('/trade/fall', methods=['POST'])
def trade_fall():
    return place_trade('PUT')

# 5. Change symbol
@app.route('/trade/change_symbol', methods=['POST'])
def change_symbol():
    global current_symbol
    data = request.json
    symbol = data.get('symbol', 'stpRNG')
    current_symbol = symbol
    return jsonify({'success': True, 'symbol': current_symbol})

# 6. Trade function
def place_trade(contract_type):
    global is_ready, ws

    if not is_ready or not ws:
        return jsonify({'success': False, 'error': 'Not connected to Deriv'}), 503

    data = request.json
    symbol = data.get('symbol', current_symbol)
    stake = float(data.get('stake', 1.0))
    duration = int(data.get('duration', 300))
    allow_equal = data.get('allow_equal', False)

    proposal = {
        "proposal": 1,
        "amount": stake,
        "basis": "stake",
        "contract_type": contract_type,
        "currency": "USD",
        "duration": duration,
        "duration_unit": "s",
        "symbol": symbol
    }
    if allow_equal:
        proposal["allow_equals"] = 1

    print(f"📤 Proposing {contract_type} trade on {symbol}...")
    ws.send(json.dumps(proposal))
    return jsonify({'success': True, 'message': 'Trade proposed'})

# ===== START =====
if __name__ == '__main__':
    print("🚀 Deriv Proxy Server starting...")
    connect_websocket()
    time.sleep(2)
    app.run(host='0.0.0.0', port=5000, debug=False)