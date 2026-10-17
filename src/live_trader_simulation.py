import os
# CRITICAL FIX: Prevent massive CPU Thread Thrashing on Windows
os.environ['NUMBA_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'

import pandas as pd
import numpy as np
import math
import warnings
import asyncio
import websockets
import json
from datetime import datetime

# CRITICAL FIX: NumPy 2.0+ vs Numba Compatibility Patch
import numpy.polynomial.polynomial
if not hasattr(np.polynomial, 'polynomial'):
    np.polynomial.polynomial = numpy.polynomial.polynomial

import numba as nb

warnings.filterwarnings("ignore")

# ============================================================
# 🚨 SIMULATION & CONNECTION PARAMETERS 🚨
# ============================================================
# --- CONNECTION ---
WEBSOCKET_URI = "ws://localhost:9001" # URI of your C++ WebSocket server

# --- TRADE TRIGGER ---
# The script will look for a signal at this specific time.
TRADE_ENTRY_TIME = "09:16:00"

# --- STRATEGY SETTINGS (Must match your backtester) ---
SL_BPS            = 14.0
TP_BPS            = 25.0 # Using a fixed TP for this simulation
TV_THRESH         = 0.00015
TV4_THRESH        = 0.0006
IV_DROP_THRESH    = 0.1
PARAM_HEDGE_DIV   = 57
PARAM_HEDGE_FRAC  = 1.0
PARAM_STR_D       = 4
N_OPT_SECONDS     = 30 # How many seconds to average for entry/exit prices
N_FUT_SECONDS     = 3

# ============================================================
# HIGH PERFORMANCE BS / GREEKS KERNELS (from your backtester)
# ============================================================
@nb.njit(fastmath=True)
def _norm_cdf(x): return 0.5 * (1.0 + math.erf(x / 1.4142135623730951))

@nb.njit(fastmath=True)
def _norm_pdf(x): return 0.3989422804014327 * math.exp(-0.5 * x * x)

def bs_delta(is_call, S, K, T, r, sig):
    if T <= 0 or sig <= 0: return np.nan
    d1 = (np.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0

def bs_price(is_call, S, K, T, r, sig):
    if T <= 0 or sig <= 0: return max(0.0, S - K) if is_call else max(0.0, K - S)
    d1 = (np.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    d2 = d1 - sig * np.sqrt(T)
    if is_call: return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:       return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

# ============================================================
# LIVE SIMULATION STATE
# ============================================================

class LiveTradeSimulator:
    def __init__(self, entry_time_str, underlying_symbol="NIFTY"):
        self.entry_time = datetime.strptime(entry_time_str, "%H:%M:%S").time()
        self.underlying_symbol = underlying_symbol

        # --- State ---
        self.trade_triggered = False
        self.position_built = False
        self.trade_active = False
        self.eod = False

        # --- Data Buffers ---
        self.market_data = {} # Stores latest tick for each symbol
        self.entry_buffer = [] # Stores ticks for entry price averaging

        # --- Position Details ---
        self.straddle_strike = 0
        self.call_symbol = ""
        self.put_symbol = ""
        self.entry_call_price = 0
        self.entry_put_price = 0
        self.cash = 0
        self.futures_hedge = 0

    def process_tick(self, tick):
        """Main logic to process each incoming tick from the WebSocket."""
        symbol = tick.get("symbol")
        if not symbol: return

        # Update the latest market data for this symbol
        self.market_data[symbol] = tick
        
        # Get the current timestamp from the tick
        # NOTE: Assumes your C++ engine adds a 'timestamp' field.
        # If not, you can use datetime.now() but it's less accurate.
        ts_str = tick.get('timestamp', datetime.now().isoformat())
        current_time = datetime.fromisoformat(ts_str).time()

        # 1. CHECK FOR TRADE TRIGGER
        if not self.trade_triggered and current_time >= self.entry_time:
            self.trigger_trade()

        # 2. BUILD POSITION (AVERAGE PRICES)
        if self.trade_triggered and not self.position_built:
            self.collect_entry_prices(tick)

        # 3. MANAGE ACTIVE TRADE (HEDGING, EXITS)
        if self.trade_active:
            self.manage_position()

        # 4. CHECK FOR END OF DAY
        if current_time >= datetime.strptime("15:27:00", "%H:%M:%S").time():
            if self.trade_active and not self.eod:
                self.close_position_eod()
                self.eod = True

    def trigger_trade(self):
        """Identifies the ATM straddle and starts the entry process."""
        underlying_data = self.market_data.get(self.underlying_symbol)
        if not underlying_data:
            print(f"Warning: No data for underlying '{self.underlying_symbol}' to trigger trade.")
            return

        # Find ATM strike
        underlying_price = underlying_data['ltp']
        self.straddle_strike = round(underlying_price / 50) * 50

        # Construct option symbols (assuming a format like NIFTY|24JUN24|22500|CE)
        # You will need to adjust this to match your exact symbol format.
        expiry_str = "27JUN24" # This needs to be determined dynamically in a real system
        self.call_symbol = f"{self.underlying_symbol}|{expiry_str}|{self.straddle_strike}|CE"
        self.put_symbol = f"{self.underlying_symbol}|{expiry_str}|{self.straddle_strike}|PE"

        self.trade_triggered = True
        self.entry_start_time = datetime.now()
        print("\n" + "="*50)
        print(f"TRADE TRIGGERED at {self.entry_time}")
        print(f"  Underlying: {underlying_price:.2f}")
        print(f"  ATM Strike: {self.straddle_strike}")
        print(f"  Collecting prices for {N_OPT_SECONDS} seconds to build position...")
        print("="*50)

    def collect_entry_prices(self, tick):
        """Collects ticks for the straddle legs for N_OPT_SECONDS."""
        if tick['symbol'] == self.call_symbol or tick['symbol'] == self.put_symbol:
            self.entry_buffer.append(tick)

        if (datetime.now() - self.entry_start_time).total_seconds() >= N_OPT_SECONDS:
            self.build_position()

    def build_position(self):
        """Calculates average entry prices and establishes the initial position."""
        if not self.entry_buffer:
            print("ERROR: No option data received during entry window. Cannot build position.")
            self.trade_triggered = False # Reset
            return

        df = pd.DataFrame(self.entry_buffer)
        
        # We are SELLING, so we execute at the BID price.
        self.entry_call_price = df[df['symbol'] == self.call_symbol]['bid1'].mean()
        self.entry_put_price = df[df['symbol'] == self.put_symbol]['bid1'].mean()

        if pd.isna(self.entry_call_price) or pd.isna(self.entry_put_price):
            print("ERROR: Could not calculate average entry prices. Aborting trade.")
            self.trade_triggered = False
            return

        # --- Establish Position ---
        # 1. Sell the straddle (cash goes up)
        self.cash = self.entry_call_price + self.entry_put_price

        # 2. Initial Delta Hedge
        call_data = self.market_data.get(self.call_symbol)
        put_data = self.market_data.get(self.put_symbol)
        underlying_data = self.market_data.get(self.underlying_symbol)

        if not all([call_data, put_data, underlying_data]):
            print("ERROR: Missing data for initial hedge. Aborting.")
            return

        # Our position is short, so our delta is -(call_delta + put_delta)
        straddle_delta = call_data['delta'] + put_data['delta']
        hedge_qty = -(-straddle_delta) # Buy futures to neutralize

        self.futures_hedge = hedge_qty
        self.cash -= hedge_qty * underlying_data['ltp'] # Cost of buying futures

        self.position_built = True
        self.trade_active = True
        print("\n" + "="*50)
        print("POSITION BUILT")
        print(f"  Avg Entry Call Price (Bid): {self.entry_call_price:.2f}")
        print(f"  Avg Entry Put Price (Bid):  {self.entry_put_price:.2f}")
        print(f"  Initial Premium Received:   {self.cash + (hedge_qty * underlying_data['ltp']):.2f}")
        print(f"  Initial Hedge: Bought {hedge_qty:.2f} futures")
        print("="*50 + "\n")

    def manage_position(self):
        """
        Placeholder for ongoing risk management (re-hedging, checking SL/TP).
        This would be called on every relevant tick.
        """
        # In a full implementation, you would re-calculate the portfolio's net delta here
        # and execute a re-hedge if it exceeds a threshold, similar to your backtester.
        pass

    def close_position_eod(self):
        """Calculates and prints the final P&L at the end of the day."""
        print("\n" + "="*50)
        print("END OF DAY - CLOSING POSITION")
        print("="*50)

        call_data = self.market_data.get(self.call_symbol)
        put_data = self.market_data.get(self.put_symbol)
        underlying_data = self.market_data.get(self.underlying_symbol)

        if not all([call_data, put_data, underlying_data]):
            print("ERROR: Missing data for EOD settlement.")
            self.trade_active = False
            return

        # --- Calculate Final P&L ---
        # 1. Buy back the straddle (at the ASK price)
        exit_call_price = call_data['ask1']
        exit_put_price = put_data['ask1']
        cost_to_close_options = exit_call_price + exit_put_price

        # 2. Liquidate the futures hedge
        futures_pnl = self.futures_hedge * underlying_data['ltp']

        # 3. Calculate total P&L
        # PnL = (Initial Cash) - (Cost to close options) + (Value of futures hedge)
        final_pnl_points = self.cash - cost_to_close_options + futures_pnl

        # In your backtester, PnL is normalized. Here we show points.
        # final_pnl_normalized = (final_pnl_points * 25000.0) / underlying_data['ltp']

        print(f"  Exit Call Price (Ask): {exit_call_price:.2f}")
        print(f"  Exit Put Price (Ask):  {exit_put_price:.2f}")
        print(f"  Cost to Close Options: {cost_to_close_options:.2f}")
        print("\n" + "-"*50)
        print(f"  SIMULATED FINAL P&L (Points): {final_pnl_points:.2f}")
        print("-"*50)

        self.trade_active = False

async def run_simulation():
    """Main function to connect to WebSocket and run the simulation."""
    
    sim = LiveTradeSimulator(entry_time_str=TRADE_ENTRY_TIME)

    print(f"Connecting to WebSocket server at {WEBSOCKET_URI}...")
    print(f"Will look for a trade signal at {TRADE_ENTRY_TIME}")

    try:
        async with websockets.connect(WEBSOCKET_URI, ping_interval=None, max_size=2**24) as websocket:
            print("Connection successful. Listening for analytics data...")
            
            # The C++ server sends a batch of ticks as a JSON array
            async for message in websocket:
                try:
                    batch = json.loads(message)
                    for tick in batch:
                        if tick.get('type') == 'analytics':
                            sim.process_tick(tick)
                    
                    if sim.eod:
                        break # End simulation after EOD

                except json.JSONDecodeError:
                    # Handle cases where a single JSON object is sent
                    try:
                        tick = json.loads(message)
                        if tick.get('type') == 'analytics':
                            sim.process_tick(tick)
                    except json.JSONDecodeError:
                        pass # Ignore non-json messages
    except (websockets.exceptions.ConnectionClosedError, ConnectionRefusedError) as e:
        print(f"\n[ERROR] Connection failed: {e}")
        print("Please ensure the C++ WebSocket server is running and accessible.")

if __name__ == "__main__":
    try:
        asyncio.run(run_simulation())
    except KeyboardInterrupt:
        print("\nSimulation stopped by user.")