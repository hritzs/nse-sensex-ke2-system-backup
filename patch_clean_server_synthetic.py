from pathlib import Path
import re, ast, shutil

p = Path("minute_simulation_server.py")
backup = p.with_name(p.name + '.before_synthetic_patch')
shutil.copy2(p, backup)
s = p.read_text(encoding='utf-8')

# ------------------------------------------------------------
# Ensure useful imports exist.
# ------------------------------------------------------------
# timedelta is already present in the source.

# ------------------------------------------------------------
# Replace MarketBook completely.
# ------------------------------------------------------------
mb_start = s.find('class MarketBook:')
mb_end = s.find('\n\n@dataclass\nclass Simulation:', mb_start)
if mb_start < 0 or mb_end < 0:
    raise SystemExit('MARKETBOOK_BOUNDARY_NOT_FOUND')

marketbook = r'''class MarketBook:
    """
    One authoritative normalized NIFTY market book.

    Three distinct market values are maintained:

      index_spot
          Raw NIFTY index/underlying spot from analytics.

      actual_future_by_expiry
          Actual NIFTY FUT LTP from dedicated future_quote messages.

      syn_fut_by_expiry
          Synthetic future supplied by the C++ analytics feed.
          THIS is the strategy's simulation underlying/spot.

    The old production engine explicitly uses the selected expiry's
    syn_fut as its simulation underlying, so the clean server mirrors
    that model rather than treating the actual FUT LTP as synthetic.
    """

    def __init__(self):
        self.lock = threading.RLock()

        self.options: dict[tuple[str, int, str], Quote] = {}

        # Actual exchange futures, keyed by expiry.
        self.futures: dict[str, float] = {}
        self.future_timestamps: dict[str, datetime] = {}

        # Synthetic futures, keyed by option expiry.
        self.syn_fut_by_expiry: dict[str, float] = {}
        self.syn_fut_timestamps: dict[str, datetime] = {}

        # Raw index spot. Reference/display only; not the strategy
        # underlying when a valid synthetic future exists.
        self.index_spot: float | None = None

        self.latest_timestamp: Any = None
        self.last_messages = 0

    @staticmethod
    def _nifty_option_identity(raw: dict, symbol: str):
        """Get authoritative NIFTY option identity from structured fields."""
        underlying = str(
            raw.get('underlying', '')
        ).strip().upper()

        expiry = str(
            raw.get('expiry', '')
        ).strip()

        strike = finite(
            raw.get('strike')
        )

        upper = symbol.upper()
        opt_type = None

        if upper.endswith('CE'):
            opt_type = 'CE'
        elif upper.endswith('PE'):
            opt_type = 'PE'
        else:
            parts = symbol.split('|')
            if len(parts) >= 4:
                opt_type = parts[3].strip().upper()

        return underlying, expiry, strike, opt_type

    def update(self, raw: dict) -> None:
        if not isinstance(raw, dict):
            return

        msg_type = str(
            raw.get('type', '')
        ).strip().lower()

        symbol = norm_symbol(
            raw.get('symbol') or raw.get('sym')
        )

        if not symbol:
            return

        upper = symbol.upper()
        now = now_ist()
        ts = raw.get('timestamp') or raw.get('published_at')

        underlying, expiry, strike, opt_type = (
            self._nifty_option_identity(raw, symbol)
        )

        # Strict NIFTY filter. Never allow another instrument to
        # overwrite the NIFTY simulation state.
        if underlying != 'NIFTY' and not upper.startswith('NIFTY|') and not upper.startswith('NIFTY'):
            return

        # --------------------------------------------------------
        # Dedicated ACTUAL FUTURE.
        # This is NOT the synthetic simulation underlying.
        # --------------------------------------------------------
        if msg_type == 'future_quote' or opt_type == 'FUT':
            if not upper.startswith('NIFTY'):
                return

            fut_expiry = str(
                raw.get('expiry') or expiry or ''
            ).strip()

            ltp = finite(
                raw.get('ltp')
            )

            if fut_expiry and ltp is not None and ltp > 0:
                with self.lock:
                    self.futures[fut_expiry] = ltp
                    self.future_timestamps[fut_expiry] = now
                    self.latest_timestamp = ts
                    self.last_messages += 1
            return

        # --------------------------------------------------------
        # Analytics message.
        # Keep RAW INDEX SPOT and SYNTHETIC FUTURE separate.
        # Do NOT return here: analytics messages are also the
        # NIFTY option quote stream.
        # --------------------------------------------------------
        if msg_type == 'analytics':
            raw_spot = finite(raw.get('spot'))
            syn_fut = finite(raw.get('syn_fut'))

            with self.lock:
                if raw_spot is not None and raw_spot > 0:
                    self.index_spot = raw_spot

                if (
                    expiry
                    and syn_fut is not None
                    and syn_fut > 0
                ):
                    self.syn_fut_by_expiry[expiry] = syn_fut
                    self.syn_fut_timestamps[expiry] = now

                self.latest_timestamp = ts
                self.last_messages += 1

        # --------------------------------------------------------
        # Option quote.
        # The compact symbol is allowed, but structured expiry and
        # strike fields are authoritative.
        # --------------------------------------------------------
        if (
            underlying != 'NIFTY'
            and not upper.startswith('NIFTY|')
            and not upper.startswith('NIFTY')
        ):
            return

        if opt_type not in ('CE', 'PE'):
            return

        if not expiry or strike is None:
            return

        ltp = finite(raw.get('ltp'))
        bid = finite(
            raw.get(
                'bid1',
                raw.get(
                    'bid',
                    raw.get('Bid')
                )
            )
        )
        ask = finite(
            raw.get(
                'ask1',
                raw.get(
                    'ask',
                    raw.get('Ask')
                )
            )
        )
        iv = norm_iv(raw.get('iv'))

        q = Quote(
            symbol=symbol,
            expiry=expiry,
            strike=float(strike),
            option_type=opt_type,
            ltp=ltp,
            bid=bid,
            ask=ask,
            iv=iv,
            timestamp=ts,
            delta=finite(raw.get('delta')),
            gamma=finite(raw.get('gamma')),
        )

        if (
            q.ask is not None
            and (
                q.ask <= 0
                or (
                    q.ltp is not None
                    and q.ask > 10.0 * max(q.ltp, 1.0)
                )
            )
        ):
            q.quote_source = 'LTP_FALLBACK'
        elif (
            q.bid is not None
            and q.ask is not None
            and q.bid > 0
            and q.ask >= q.bid
        ):
            q.quote_source = 'BID_ASK'
        elif q.ltp is not None:
            q.quote_source = 'LTP_FALLBACK'

        with self.lock:
            self.options[
                (expiry, int(round(strike)), opt_type)
            ] = q

    def snapshot(self) -> dict:
        with self.lock:
            return {
                'index_spot': self.index_spot,
                'futures': dict(self.futures),
                'future_timestamps': dict(self.future_timestamps),
                'synthetic_futures': dict(self.syn_fut_by_expiry),
                'synthetic_timestamps': dict(self.syn_fut_timestamps),
                'options': dict(self.options),
                'timestamp': self.latest_timestamp,
                'messages': self.last_messages,
            }

    def current_index_spot(self) -> float | None:
        with self.lock:
            return self.index_spot

    def current_actual_future(self, expiry: str | None = None) -> float | None:
        with self.lock:
            now = now_ist()
            candidates: list[tuple[datetime, str, float]] = []

            for exp, px in self.futures.items():
                ts = self.future_timestamps.get(exp)
                if ts is None:
                    continue

                if (now - ts).total_seconds() > 5.0:
                    continue

                if expiry is not None and exp != expiry:
                    continue

                dt = expiry_datetime(exp)
                if dt is not None and dt >= now:
                    candidates.append((dt, exp, px))

            if not candidates:
                return None

            candidates.sort()
            return candidates[0][2]

    def _fresh_syn_fut(self, expiry: str) -> float | None:
        value = self.syn_fut_by_expiry.get(expiry)
        ts = self.syn_fut_timestamps.get(expiry)

        if value is None or ts is None:
            return None

        if (now_ist() - ts).total_seconds() > 5.0:
            return None

        if value <= 0:
            return None

        return value

    def select_expiry(self) -> str | None:
        with self.lock:
            now = now_ist()
            candidates: list[tuple[datetime, str]] = []

            for exp, syn in self.syn_fut_by_expiry.items():
                dt = expiry_datetime(exp)
                if dt is None or dt < now:
                    continue

                if self._fresh_syn_fut(exp) is None:
                    continue

                # A simulation expiry must have at least one complete
                # ATM candidate eventually. We only need the nearest
                # valid weekly-style expiry with CE/PE availability.
                has_ce = any(
                    key[0] == exp and key[2] == 'CE'
                    for key in self.options
                )
                has_pe = any(
                    key[0] == exp and key[2] == 'PE'
                    for key in self.options
                )

                if not (has_ce and has_pe):
                    continue

                candidates.append((dt, exp))

            if not candidates:
                return None

            candidates.sort()
            return candidates[0][1]

    def current_synthetic_future(self, expiry: str | None = None) -> float | None:
        with self.lock:
            selected = expiry or self.select_expiry()
            if selected is None:
                return None
            return self._fresh_syn_fut(selected)

    def current_spot(self, expiry: str | None = None) -> float | None:
        # Strategy spot = selected-expiry synthetic future.
        synthetic = self.current_synthetic_future(expiry)
        if synthetic is not None:
            return synthetic

        # Only a temporary feed fallback. Once syn_fut is present it
        # is authoritative for the simulation.
        return self.current_index_spot()

    def get_quote(self, expiry: str, strike: float, option_type: str) -> Quote | None:
        with self.lock:
            return self.options.get(
                (
                    expiry,
                    int(round(strike)),
                    option_type.upper(),
                )
            )

    def select_atm_pair(self) -> tuple[str, int, Quote, Quote] | None:
        with self.lock:
            expiry = self.select_expiry()
            if expiry is None:
                return None

            synthetic_spot = self.current_synthetic_future(expiry)
            if synthetic_spot is None or synthetic_spot <= 0:
                return None

            # EXACT NIFTY 50-point ATM from synthetic future.
            atm = int(round(synthetic_spot / 50.0) * 50)

            ce = self.options.get((expiry, atm, 'CE'))
            pe = self.options.get((expiry, atm, 'PE'))

            # Never silently jump to 24250/24300/etc. If exact ATM is
            # not available, wait for the missing pair.
            if ce is None or pe is None:
                return None

            if (
                ce.ltp is None
                or pe.ltp is None
                or ce.ltp <= 0
                or pe.ltp <= 0
            ):
                return None

            return expiry, atm, ce, pe
'''

s = s[:mb_start] + marketbook + s[mb_end:]

# ------------------------------------------------------------
# Add new Simulation fields.
# ------------------------------------------------------------
field_anchor = '    start_timestamp: str | None = None\n    exit_timestamp: str | None = None\n'
field_new = '''    start_timestamp: str | None = None
    scheduled_start: str | None = None
    request_timestamp: str | None = None
    exit_timestamp: str | None = None
    missed: bool = False
'''
if field_anchor not in s:
    raise SystemExit('SIM_FIELD_ANCHOR_NOT_FOUND')
s = s.replace(field_anchor, field_new, 1)

entry_anchor = '    entry_spot: float | None = None\n    entry_future: float | None = None\n'
entry_new = '''    entry_spot: float | None = None
    entry_index_spot: float | None = None
    entry_future: float | None = None  # strategy synthetic future; compatibility key
    entry_actual_future: float | None = None
'''
if entry_anchor not in s:
    raise SystemExit('ENTRY_FIELD_ANCHOR_NOT_FOUND')
s = s.replace(entry_anchor, entry_new, 1)

# ------------------------------------------------------------
# Replace build_now.
# ------------------------------------------------------------
start = s.find('    def build_now(self, start_timestamp: datetime | None = None) -> bool:')
end = s.find('\n    def _try_enter', start)
if start < 0 or end < 0:
    raise SystemExit('BUILD_NOW_BOUNDARY_NOT_FOUND')
build = '''    def build_now(self, start_timestamp: datetime | None = None) -> bool:
        """Arm NOW and schedule the build for the next wall-clock minute."""
        with self.lock:
            requested = start_timestamp or now_ist()
            next_minute = (
                requested.replace(second=0, microsecond=0)
                + timedelta(minutes=1)
            )

            self.armed = True
            self.started = False
            self.completed = False
            self.missed = False
            self.request_timestamp = requested.isoformat(timespec="seconds")
            self.scheduled_start = next_minute.isoformat(timespec="seconds")
            self.start_timestamp = self.scheduled_start
            self.entry_block_reason = None
            return False
'''
s = s[:start] + build + s[end:]

# ------------------------------------------------------------
# Replace _try_enter.
# ------------------------------------------------------------
start = s.find('    def _try_enter(self, force: bool = False) -> bool:')
end = s.find('\n    def process(self) -> None:', start)
if start < 0 or end < 0:
    raise SystemExit('TRY_ENTER_BOUNDARY_NOT_FOUND')
try_enter = '''    def _try_enter(self, force: bool = False) -> bool:
        pair = self._current_pair()

        if pair is None:
            self.entry_block_reason = (
                "ATM_CE_PE_NOT_AVAILABLE | waiting for exact synthetic ATM pair"
            )
            return False

        expiry, atm, ce, pe = pair

        # The strategy underlying is the selected expiry's synthetic future.
        spot = self.book.current_synthetic_future(expiry)
        index_spot = self.book.current_index_spot()
        actual_future = self.book.current_actual_future(expiry)

        if spot is None or spot <= 0:
            self.entry_block_reason = "SYNTHETIC_FUTURE_UNAVAILABLE"
            return False

        dte = exact_dte(expiry)
        if dte is None or dte <= 0:
            self.entry_block_reason = "DTE_UNAVAILABLE"
            return False

        ce_px = ce.executable_entry()
        pe_px = pe.executable_entry()

        if ce_px is None or pe_px is None:
            self.entry_block_reason = (
                f"QUOTES_UNAVAILABLE | CE={ce_px!r} PE={pe_px!r}"
            )
            return False

        ce_iv = self._get_iv(ce, spot, atm, dte)
        pe_iv = self._get_iv(pe, spot, atm, dte)

        if ce_iv is None or pe_iv is None:
            self.entry_block_reason = "IV_UNAVAILABLE"
            return False

        iv = 0.5 * (ce_iv + pe_iv)

        build_time = now_ist()
        self.entry_timestamp = build_time.isoformat(timespec="seconds")
        self.entry_expiry = expiry
        self.entry_atm = float(atm)
        self.entry_ce_symbol = ce.symbol
        self.entry_pe_symbol = pe.symbol
        self.entry_ce = float(ce_px)
        self.entry_pe = float(pe_px)
        self.entry_straddle = self.entry_ce + self.entry_pe

        # IMPORTANT: entry_future is synthetic by strategy convention.
        self.entry_future = float(spot)
        self.entry_index_spot = (
            float(index_spot) if index_spot is not None else None
        )
        self.entry_spot = float(spot)
        self.entry_actual_future = (
            float(actual_future) if actual_future is not None else None
        )

        self.entry_iv = float(iv)
        self.entry_dte = float(dte)
        self.entry_quote_source = (
            "BID_ASK"
            if (
                ce.quote_source == "BID_ASK"
                and pe.quote_source == "BID_ASK"
            )
            else "LTP_FALLBACK"
        )

        self.current_ce = ce.mark()
        self.current_pe = pe.mark()
        self.current_straddle = (
            (self.current_ce or 0.0)
            + (self.current_pe or 0.0)
        )

        self._calculate_greeks(
            spot,
            float(atm),
            float(dte),
            float(iv),
        )

        self.hedge = -float(self.delta or 0.0)
        self.hedge_target = self.hedge

        self._calculate_risk(
            spot,
            float(iv),
        )

        self.started = True
        self.completed = False
        self.armed = True
        self.missed = False
        self.entry_block_reason = None

        self._record_minute()
        return True
'''
s = s[:start] + try_enter + s[end:]

# ------------------------------------------------------------
# Replace process so active trade tracks the ENTRY expiry/ATM.
# ------------------------------------------------------------
start = s.find('    def process(self) -> None:')
end = s.find('\n    def _record_minute', start)
if start < 0 or end < 0:
    raise SystemExit('PROCESS_BOUNDARY_NOT_FOUND')
process = '''    def process(self) -> None:
        with self.lock:
            if self.completed:
                return

            if not self.started:
                if not self.armed or self.missed:
                    return

                if self.scheduled_start:
                    try:
                        scheduled = datetime.fromisoformat(
                            self.scheduled_start
                        )
                    except ValueError:
                        scheduled = None

                    if scheduled is not None and now_ist() < scheduled:
                        return

                # Build only from the first live tick at/after the
                # scheduled minute. Never fabricate the minute.
                self._try_enter()
                return

            # ----------------------------------------------------
            # After entry, stay on the IMMUTABLE entry expiry + ATM.
            # Do not jump to another expiry or ATM while the trade is
            # active.
            # ----------------------------------------------------
            if not self.entry_expiry or self.entry_atm is None:
                return

            expiry = self.entry_expiry
            atm = float(self.entry_atm)

            spot = self.book.current_synthetic_future(expiry)
            if spot is None:
                return

            ce = self.book.get_quote(expiry, atm, 'CE')
            pe = self.book.get_quote(expiry, atm, 'PE')

            if ce is None or pe is None:
                return

            dte = exact_dte(expiry)
            if dte is None:
                return

            ce_mark = ce.mark()
            pe_mark = pe.mark()

            if ce_mark is not None:
                self.current_ce = ce_mark
            if pe_mark is not None:
                self.current_pe = pe_mark

            if (
                self.current_ce is not None
                and self.current_pe is not None
            ):
                self.current_straddle = (
                    self.current_ce + self.current_pe
                )

            ce_iv = self._get_iv(ce, spot, atm, dte)
            pe_iv = self._get_iv(pe, spot, atm, dte)
            iv_values = [x for x in (ce_iv, pe_iv) if x is not None]

            if not iv_values:
                return

            iv = sum(iv_values) / len(iv_values)

            self._calculate_greeks(
                spot,
                atm,
                dte,
                iv,
            )

            self._calculate_risk(
                spot,
                iv,
            )

            self._rebalance(spot)

            # P&L uses SYNTHETIC future movement, not actual FUT LTP.
            self._update_pnl(
                spot,
                self.book.current_actual_future(expiry),
            )

            otm = pe if spot > atm else ce

            reason = self._check_exit(
                spot,
                dte,
                otm,
                iv,
            )

            self._record_minute()

            if reason:
                self._finish(reason)
'''
s = s[:start] + process + s[end:]

# ------------------------------------------------------------
# Replace _record_minute with explicit three market values.
# ------------------------------------------------------------
start = s.find('    def _record_minute(self) -> None:')
end = s.find('\n    def state(self) -> dict:', start)
if start < 0 or end < 0:
    raise SystemExit('RECORD_MINUTE_BOUNDARY_NOT_FOUND')
record = '''    def _record_minute(self) -> None:
        minute_key = now_ist().strftime("%Y-%m-%d %H:%M")

        if minute_key == self.last_minute_key:
            return

        self.last_minute_key = minute_key

        synthetic_future = (
            self.book.current_synthetic_future(self.entry_expiry)
            if self.entry_expiry
            else self.book.current_synthetic_future()
        )

        actual_future = self.book.current_actual_future(
            self.entry_expiry
        ) if self.entry_expiry else self.book.current_actual_future()

        index_spot = self.book.current_index_spot()

        self.history.append({
            "timestamp": now_ist().isoformat(timespec="seconds"),
            # "spot" is the STRATEGY spot = synthetic future.
            "spot": synthetic_future,
            "synthetic_future": synthetic_future,
            "index_spot": index_spot,
            "future": actual_future,
            "actual_future": actual_future,
            "atm": self.entry_atm,
            "current_ce": self.current_ce,
            "current_pe": self.current_pe,
            "straddle": self.current_straddle,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "hedge": self.hedge,
            "points_out": self.points_out,
            "points_allowed": self.points_allowed,
            "option_pnl": self.option_pnl,
            "future_pnl": self.future_pnl,
            "total_pnl": self.total_pnl,
            "trade_active": self.started and not self.completed,
            "quote_source": self.entry_quote_source,
        })

        self.history = self.history[-500:]
'''
s = s[:start] + record + s[end:]

# ------------------------------------------------------------
# Replace state to expose index/synthetic/actual distinctly.
# ------------------------------------------------------------
start = s.find('    def state(self) -> dict:')
end = s.find('\n\nclass SimulationController:', start)
if start < 0 or end < 0:
    raise SystemExit('STATE_BOUNDARY_NOT_FOUND')
state = '''    def state(self) -> dict:
        with self.lock:
            if self.entry_expiry:
                synthetic_future = self.book.current_synthetic_future(
                    self.entry_expiry
                )
                actual_future = self.book.current_actual_future(
                    self.entry_expiry
                )
            else:
                synthetic_future = self.book.current_synthetic_future()
                actual_future = self.book.current_actual_future()

            index_spot = self.book.current_index_spot()

            pair = (
                self.book.select_atm_pair()
                if not self.started
                else None
            )

            live_atm = pair[1] if pair is not None else None

            return {
                "run_id": self.run_id,
                "mode": "MINUTE_STRADDLE",
                "armed": self.armed,
                "started": self.started,
                "completed": self.completed,
                "missed": self.missed,
                "start_timestamp": self.start_timestamp,
                "scheduled_start": self.scheduled_start,
                "request_timestamp": self.request_timestamp,
                "exit_timestamp": self.exit_timestamp,

                "market": {
                    # Strategy spot = synthetic future.
                    "spot": synthetic_future,
                    "synthetic_future": synthetic_future,
                    "index_spot": index_spot,
                    # Actual FUT is separate.
                    "future": actual_future,
                    "actual_future": actual_future,
                    "atm": (
                        self.entry_atm
                        if self.started
                        else live_atm
                    ),
                    "atm_source": (
                        "ENTRY_SYNTHETIC_FUTURE"
                        if self.started
                        else "SYNTHETIC_FUTURE"
                    ),
                },

                "entry": {
                    "timestamp": self.entry_timestamp,
                    "expiry": self.entry_expiry,
                    "atm": self.entry_atm,
                    "ce": self.entry_ce_symbol,
                    "pe": self.entry_pe_symbol,
                    "ce_price": self.entry_ce,
                    "pe_price": self.entry_pe,
                    "straddle": self.entry_straddle,
                    "spot": self.entry_spot,
                    "synthetic_future": self.entry_future,
                    # Keep compatibility with the old key.
                    "future": self.entry_future,
                    "actual_future": self.entry_actual_future,
                    "index_spot": self.entry_index_spot,
                    "iv": self.entry_iv,
                    "dte": self.entry_dte,
                    "quote_source": self.entry_quote_source,
                },

                "position": {
                    "active": self.started and not self.completed,
                    "current_ce": self.current_ce,
                    "current_pe": self.current_pe,
                    "current_straddle": self.current_straddle,
                },

                "greeks": {
                    "delta": self.delta,
                    "gamma": self.gamma,
                    "theta": self.theta,
                    "vega": self.vega,
                },

                "hedge": {
                    "current": self.hedge,
                    "target": self.hedge_target,
                    "points_out": self.points_out,
                    "points_allowed": self.points_allowed,
                    "events": self.hedge_events[-100:],
                },

                "pnl": {
                    "option": self.option_pnl,
                    "future": self.future_pnl,
                    "total": self.total_pnl,
                },

                "exit": {
                    "reason": self.exit_reason,
                    "timestamp": self.exit_timestamp,
                },

                "entry_block_reason": self.entry_block_reason,

                "history": self.history[-500:],
                "trade_history": self.trade_history[-50:],
            }
'''
s = s[:start] + state + s[end:]

# ------------------------------------------------------------
# Replace SimulationController completely.
# ------------------------------------------------------------
cs_start = s.find('class SimulationController:')
cs_end = s.find('\n\nCONTROLLER = SimulationController()', cs_start)
if cs_start < 0 or cs_end < 0:
    raise SystemExit('CONTROLLER_BOUNDARY_NOT_FOUND')
controller = '''class SimulationController:
    def __init__(self):
        self.lock = threading.RLock()
        self.book = MarketBook()
        self.runs: dict[str, Simulation] = {
            run_id: Simulation(
                run_id=run_id,
                book=self.book,
            )
            for run_id in RUN_IDS
        }
        self.now_started = False

    @staticmethod
    def _same_minute(now: datetime, target: time) -> bool:
        return (
            now.hour == target.hour
            and now.minute == target.minute
        )

    @staticmethod
    def _past_minute(now: datetime, target: time) -> bool:
        return (now.hour, now.minute) > (
            target.hour,
            target.minute,
        )

    def process_tick(self, tick: dict) -> None:
        with self.lock:
            self.book.update(tick)
            now = now_ist()

            for run_id, sim in self.runs.items():
                if sim.completed or sim.missed:
                    continue

                if run_id == 'NOW':
                    if not self.now_started:
                        continue
                else:
                    target = FIXED_STARTS[run_id]

                    # Start only during the actual target wall-clock
                    # minute. Never backfill a missed 09:16/09:17/etc.
                    if not sim.armed:
                        if self._same_minute(now, target):
                            sim.armed = True
                            sim.scheduled_start = now.replace(
                                second=0,
                                microsecond=0,
                            ).isoformat(timespec='seconds')
                            sim.start_timestamp = sim.scheduled_start
                        elif self._past_minute(now, target):
                            sim.missed = True
                            sim.entry_block_reason = (
                                f'MISSED_START_{target.strftime("%H:%M")}'
                            )
                            continue

                sim.process()

    def start_now(self) -> dict:
        with self.lock:
            sim = Simulation(
                run_id='NOW',
                book=self.book,
            )
            self.runs['NOW'] = sim
            self.now_started = True

            requested = now_ist()
            sim.request_timestamp = requested.isoformat(
                timespec='seconds'
            )

            # START NOW means next wall-clock minute.
            next_minute = (
                requested.replace(
                    second=0,
                    microsecond=0,
                )
                + timedelta(minutes=1)
            )

            sim.armed = True
            sim.scheduled_start = next_minute.isoformat(
                timespec='seconds'
            )
            sim.start_timestamp = sim.scheduled_start

            return sim.state()

    def state(self, run_id: str | None = None) -> Any:
        with self.lock:
            if run_id:
                sim = self.runs.get(run_id)
                return sim.state() if sim else None

            return {
                run_id: sim.state()
                for run_id, sim in self.runs.items()
            }
'''
s = s[:cs_start] + controller + s[cs_end:]

# ------------------------------------------------------------
# Validate.
# ------------------------------------------------------------
ast.parse(s)
p.write_text(s, encoding='utf-8')
print('SYNTHETIC_MODEL_PATCH_OK')
print('Backup:', backup)
