#!/usr/bin/env python3
"""
Deriv REST Proxy – Fixed API endpoint format
"""

import json
import time
import threading
import os
import logging
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== CONFIG =====
API_TOKEN = 'pat_4d0ef5186ccd100fff6c8e6221a99f894a91276218adbe13a363c2dbd2228c31'
BASE_URL = 'https://api.deriv.com/v3'  # Correct: NO trailing slash, NO method in URL

app = Flask(__name__)
CORS(app)

# ===== STATE =====
trade_history = []
current_symbol = 'stpRNG'
latest_price = None
last_error = None

# ===== CORRECT API CALL =====
def api_call(method, params):
    """
    Make a POST request to Deriv's REST API.
    method: string like 'ticks', 'proposal', 'buy', etc.
    params: dict of parameters for that method
    """
    try:
        # Build the body: method name as key + params + authorize
        body = {method: params.get(method, params)}
        # If params already has the method key, use it; otherwise add method
        if method not in body:
            body[method] = params
        body['authorize'] = API_TOKEN
        
        logger.info(f"📤 API Call: {method} -> {body}")
        response = requests.post(BASE_URL, json=body, timeout=10)
        
        logger.info(f"📨 Response Status: {response.status_code}")
        logger.info(f"📨 Response Text: {response.text[:200]}")
        
        if response.status_code != 200:
            return {'success': False, 'error': f'HTTP {response.status_code}: {response.text[:100]}'}
        
        if not response.text or response.text.strip() == '':
            return {'success': False, 'error': 'Empty response from server'}
        
        data = response.json()
        if 'error' in data:
            error_msg = data['error'].get('message', 'Unknown error')
            logger.error(f"❌ API Error: {error_msg}")
            return {'success': False, 'error': error_msg}
        
        return {'success': True, 'data': data}
    except requests.exceptions.Timeout:
        logger.error("❌ API Timeout")
        return {'success': False, 'error': 'Request timeout'}
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON Parse Error: {e}")
        logger.error(f"Raw response: {response.text[:200]}")
        return {'success': False, 'error': f'Invalid JSON response: {response.text[:100]}'}
    except Exception as e:
        logger.error(f"❌ API Exception: {e}")
        return {'success': False, 'error': str(e)}

# ===== FETCH CURRENT PRICE =====
def fetch_price(symbol='stpRNG'):
    result = api_call('ticks', {'ticks': symbol, 'subscribe': 0})
    if result['success'] and 'data' in result:
        data = result['data']
        if 'ticks' in data and len(data['ticks']) > 0:
            return data['ticks'][-1]['quote']
    return None

# ===== PLACE TRADE =====
def place_trade(contract_type, symbol, stake, duration, allow_equal):
    # Proposal
    proposal_params = {
        'proposal': 1,
        'amount': stake,
        'basis': 'stake',
        'contract_type': contract_type,
        'currency': 'USD',
        'duration': duration,
        'duration_unit': 's',
        'symbol': symbol,
    }
    if allow_equal:
        proposal_params['allow_equals'] = 1
    
    proposal_result = api_call('proposal', proposal_params)
    if not proposal_result['success']:
        return proposal_result
    
    proposal_data = proposal_result['data']
    if 'proposal' not in proposal_data or 'id' not in proposal_data['proposal']:
        return {'success': False, 'error': 'No proposal ID received'}
    
    proposal_id = proposal_data['proposal']['id']
    price = proposal_data['proposal']['ask_price']
    
    # Buy
    buy_result = api_call('buy', {'buy': proposal_id, 'price': price})
    if not buy_result['success']:
        return buy_result
    
    buy_data = buy_result['data']
    if 'buy' not in buy_data or 'contract_id' not in buy_data['buy']:
        return {'success': False, 'error': 'Buy failed'}
    
    contract_id = buy_data['buy']['contract_id']
    
    # Track the trade
    trade_info = {
        'contract_id': contract_id,
        'symbol': symbol,
        'stake': stake,
        'duration': duration,
        'contract_type': contract_type,
        'status': 'active',
        'start_time': time.time(),
        'result': None,
        'profit': 0
    }
    trade_history.append(trade_info)
    
    def poll_trade():
        nonlocal trade_info
        start = time.time()
        timeout = duration + 10
        while time.time() - start < timeout:
            time.sleep(2)
            status_result = api_call('contract_update', {'contract_id': contract_id})
            if status_result['success']:
                data = status_result['data']
                if 'contract_update' in data:
                    contract = data['contract_update']
                    if contract.get('is_sold', False):
                        profit = contract.get('profit', 0)
                        trade_info['status'] = 'sold'
                        trade_info['result'] = 'WIN' if profit > 0 else 'LOSS'
                        trade_info['profit'] = profit
                        logger.info(f"📈 Contract {contract_id}: {trade_info['result']} | Profit: ${profit:.2f}")
                        return
        if trade_info['status'] == 'active':
            trade_info['status'] = 'expired'
            logger.info(f"⏰ Contract {contract_id} expired")
    
    threading.Thread(target=poll_trade, daemon=True).start()
    return {'success': True, 'contract_id': contract_id, 'message': 'Trade placed'}

# ===== FLASK ROUTES =====
@app.route('/ping')
def ping():
    global latest_price
    price = fetch_price(current_symbol)
    if price:
        latest_price = price
    return jsonify({
        'status': 'alive',
        'time': time.time(),
        'connected': True,
        'price': latest_price,
        'symbol': current_symbol,
        'trades': len(trade_history),
        'last_error': last_error
    })

@app.route('/')
def index():
    return jsonify({
        'service': 'Deriv REST Proxy',
        'status': 'Connected (REST)',
        'price': latest_price,
        'symbol': current_symbol,
        'trades': len(trade_history),
        'last_error': last_error
    })

@app.route('/status')
def status():
    completed = [t for t in trade_history if t['status'] in ['sold', 'expired']]
    active = [t for t in trade_history if t['status'] == 'active']
    return jsonify({
        'connected': True,
        'status': 'Connected (REST)',
        'price': latest_price,
        'symbol': current_symbol,
        'trades': completed[-10:],
        'active_trades': len(active),
        'total_trades': len(trade_history),
        'last_error': last_error
    })

@app.route('/trade/rise', methods=['POST'])
def trade_rise():
    return handle_trade('CALL')

@app.route('/trade/fall', methods=['POST'])
def trade_fall():
    return handle_trade('PUT')

@app.route('/trade/change_symbol', methods=['POST'])
def change_symbol():
    global current_symbol
    data = request.json
    symbol = data.get('symbol', 'stpRNG')
    current_symbol = symbol
    price = fetch_price(symbol)
    if price:
        global latest_price
        latest_price = price
    return jsonify({'success': True, 'symbol': current_symbol, 'price': latest_price})

def handle_trade(contract_type):
    global latest_price
    data = request.json
    symbol = data.get('symbol', current_symbol)
    stake = float(data.get('stake', 1.0))
    duration = int(data.get('duration', 300))
    allow_equal = data.get('allow_equal', False)
    
    logger.info(f"📤 Trade request: {contract_type} on {symbol} | Stake: ${stake} | Duration: {duration}s | Equal: {allow_equal}")
    result = place_trade(contract_type, symbol, stake, duration, allow_equal)
    
    if result['success']:
        price = fetch_price(symbol)
        if price:
            latest_price = price
        return jsonify({'success': True, 'contract_id': result.get('contract_id')})
    else:
        return jsonify({'success': False, 'error': result.get('error', 'Trade failed')}), 400

# ===== PERIODIC PRICE UPDATE =====
def update_price_loop():
    global latest_price
    while True:
        try:
            price = fetch_price(current_symbol)
            if price:
                latest_price = price
        except Exception as e:
            logger.error(f"Price update error: {e}")
        time.sleep(5)

# ===== START =====
if __name__ == '__main__':
    logger.info("🚀 Deriv REST Proxy starting (correct API format)...")
    price_thread = threading.Thread(target=update_price_loop)
    price_thread.daemon = True
    price_thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
