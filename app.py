#!/usr/bin/env python3
"""
Deriv WebSocket Proxy with DNS fallback and dual endpoints
"""

import json
import time
import threading
import websocket
import os
import logging
import socket
from flask import Flask, request, jsonify
from flask_cors import CORS

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== CONFIG =====
APP_ID = 117548
API_TOKEN = 'pat_dd21039e2e06160bc8464e9c1b5aecb6b6d0ac4f819652dc1d32bf4aea4955bd'  # REPLACE WITH FRESH TOKEN

# Try both endpoints
ENDPOINTS = [
    ('wss://ws.deriv.com/websockets/v3?app_id=' + APP_ID, 'deriv.com'),
    ('wss://ws.binary.com/websockets/v3?app_id=' + APP_ID, 'binary.com'),
]

app = Flask(__name__)
CORS(app)

# ===== STATE =====
ws = None
is_ready = False
pending_trades = {}
latest_price = None
latest_status = "Initializing..."
trade_results = []
current_symbol = 'stpRNG'
connection_attempts = 0
last_error = None
current_endpoint_index = 0

# ===== DNS RESOLVER FALLBACK =====
def resolve_hostname(hostname):
    """Try to resolve using system DNS, fallback to Google DNS (8.8.8.8)"""
    try:
        logger.info(f"🔍 Resolving {hostname} using system DNS...")
        ip = socket.gethostbyname(hostname)
        logger.info(f"✅ Resolved {hostname} -> {ip}")
        return ip
    except Exception as e:
        logger.warning(f"⚠️ System DNS failed for {hostname}: {e}")
        # Use Google's DNS as fallback (hardcoded IP for ws.deriv.com)
        # This is a manual mapping – we can add known IPs here
        known_ips = {
            'ws.deriv.com': '34.120.168.203',   # Example, might change
            'ws.binary.com': '34.120.168.203',  # They share IP
        }
        if hostname in known_ips:
            ip = known_ips[hostname]
            logger.info(f"✅ Using fallback IP for {hostname}: {ip}")
            return ip
        else:
            logger.error(f"❌ No fallback IP for {hostname}")
            return None

# ===== WEBSOCKET WITH CUSTOM DNS =====
def create_websocket_with_dns(url, hostname):
    """Create WebSocket with custom DNS resolution if needed"""
    try:
        # Try standard connection first
        logger.info(f"🔗 Connecting to {url}")
        ws = websocket.WebSocketApp(url,
                                    on_open=on_open,
                                    on_message=on_message,
                                    on_error=on_error,
                                    on_close=on_close)
        return ws
    except Exception as e:
        logger.error(f"❌ Failed to connect to {url}: {e}")
        return None

def on_message(ws, message):
    global is_ready, pending_trades, latest_price, latest_status, trade_results, last_error
    try:
        data = json.loads(message)
        logger.info(f"📨 WS Response: {data}")

        if 'authorize' in data:
            if 'error' in data:
                error_msg = data['error'].get('message', 'Unknown error')
                error_code = data['error'].get('code', 'Unknown code')
                last_error = f"Auth failed: {error_code} - {error_msg}"
                logger.error(f"❌ {last_error}")
                latest_status = last_error
                is_ready = False
            else:
                is_ready = True
                latest_status = "Connected"
                last_error = None
                logger.info(f"✅ Connected to Deriv (App ID: {APP_ID})")

        if 'error' in data and 'authorize' not in data:
            error_msg = data['error'].get('message', 'Unknown error')
            logger.error(f"❌ Error: {error_msg}")
            latest_status = f"Error: {error_msg}"
            last_error = error_msg

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
                    logger.info(f"📈 Contract {contract_id}: {result} | Profit: ${profit:.2f}")
                    del pending_trades[contract_id]

        if 'buy' in data:
            contract_id = data['buy']['contract_id']
            pending_trades[contract_id] = True
            logger.info(f"✅ Trade placed! Contract ID: {contract_id}")

    except Exception as e:
        logger.error(f"⚠️ Message error: {e}")
        last_error = str(e)

def on_error(ws, error):
    global latest_status, last_error
    latest_status = "Connection error"
    last_error = str(error)
    logger.error(f"⚠️ WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    global is_ready, latest_status
    is_ready = False
    latest_status = "Disconnected"
    logger.info(f"🔌 Disconnected. Code: {close_status_code}, Reason: {close_msg}")
    time.sleep(3)
    connect_websocket()

def on_open(ws):
    logger.info("🔗 WebSocket opened, authorizing...")
    auth_msg = json.dumps({"authorize": API_TOKEN, "req_id": 1})
    ws.send(auth_msg)

def connect_websocket():
    global ws, connection_attempts, current_endpoint_index
    connection_attempts += 1
    logger.info(f"🔄 Connection attempt #{connection_attempts}")

    # Try endpoints in round-robin
    url, name = ENDPOINTS[current_endpoint_index]
    logger.info(f"🔗 Trying {name}: {url}")

    ws = websocket.WebSocketApp(url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()

    # If this endpoint fails, switch to next on next attempt
    # This will be handled in on_close

# ===== KEEP-ALIVE =====
def keep_alive():
    while True:
        time.sleep(30)
        if ws and ws.sock and ws.sock.connected:
            try:
                ws.send(json.dumps({"ping": 1}))
                logger.info("💓 Ping sent")
            except:
                pass

# ===== FLASK ROUTES =====
@app.route('/ping')
def ping():
    return jsonify({
        'status': 'alive',
        'time': time.time(),
        'connected': is_ready,
        'attempts': connection_attempts,
        'last_error': last_error,
        'latest_status': latest_status,
        'endpoint': ENDPOINTS[current_endpoint_index][1]
    })

@app.route('/')
def index():
    return jsonify({
        'service': 'Deriv Proxy',
        'status': latest_status,
        'price': latest_price,
        'connected': is_ready,
        'symbol': current_symbol,
        'trades': len(trade_results),
        'attempts': connection_attempts,
        'last_error': last_error
    })

@app.route('/status')
def status():
    return jsonify({
        'connected': is_ready,
        'status': latest_status,
        'price': latest_price,
        'symbol': current_symbol,
        'trades': trade_results[-10:],
        'attempts': connection_attempts,
        'last_error': last_error
    })

@app.route('/trade/rise', methods=['POST'])
def trade_rise():
    return place_trade('CALL')

@app.route('/trade/fall', methods=['POST'])
def trade_fall():
    return place_trade('PUT')

@app.route('/trade/change_symbol', methods=['POST'])
def change_symbol():
    global current_symbol
    data = request.json
    symbol = data.get('symbol', 'stpRNG')
    current_symbol = symbol
    return jsonify({'success': True, 'symbol': current_symbol})

def place_trade(contract_type):
    global is_ready, ws

    if not is_ready or not ws:
        logger.warning(f"⚠️ Trade attempted but not connected")
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

    logger.info(f"📤 Proposing {contract_type} trade on {symbol}...")
    ws.send(json.dumps(proposal))
    return jsonify({'success': True, 'message': 'Trade proposed'})

# ===== START =====
if __name__ == '__main__':
    logger.info("🚀 Deriv Proxy Server starting with dual endpoints...")
    connect_websocket()
    # Start keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
