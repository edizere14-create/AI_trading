import pandas as pd
from typing import Callable, Dict, Any, List, Tuple, Optional
import inspect


class BacktestEngine:
    def __init__(
        self,
        strategy_fn: Callable,
        initial_balance: float = 1000.0,
        fee_rate: float = 0.0005,
        slippage_pct: float = 0.0002,
        spread_pct: float = 0.0001,
        latency_steps: int = 1,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        max_holding_bars: Optional[int] = None,
        allow_short_selling: bool = True,
        use_risk_sizing: bool = True,
        risk_per_trade: float = 0.02,  # 2%
        max_leverage: float = 3.0,               # NEW
        enable_margin_checks: bool = True,       # NEW
        train_fn: Optional[Callable[[pd.DataFrame], Dict[str, Any]]] = None,  # NEW
    ):
        self.strategy_fn = strategy_fn
        self.initial_balance = initial_balance
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct
        self.spread_pct = spread_pct
        self.latency_steps = latency_steps
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_holding_bars = max_holding_bars
        self.allow_short_selling = allow_short_selling
        self.use_risk_sizing = use_risk_sizing
        self.risk_per_trade = risk_per_trade
        self.max_leverage = float(max_leverage)
        self.enable_margin_checks = bool(enable_margin_checks)
        self.train_fn = train_fn
        self.equity_curve_ = pd.DataFrame()  # NEW

    def _exec_price(self, px: float, side: str) -> float:
        if side == "buy":
            return px * (1 + self.spread_pct + self.slippage_pct)
        return px * (1 - self.spread_pct - self.slippage_pct)

    def _calc_qty(self, balance: float, px: float, signal_qty: float = 1.0) -> float:
        if self.use_risk_sizing and self.stop_loss_pct and self.stop_loss_pct > 0:
            risk_amount = max(balance, 0.0) * float(self.risk_per_trade)
            stop_distance = px * float(self.stop_loss_pct)
            if stop_distance > 0:
                return max(risk_amount / stop_distance, 0.0)
        return max(float(signal_qty), 0.0)

    def _passes_margin_check(self, balance_before: float, exec_px: float, qty: float) -> bool:
        if not self.enable_margin_checks:
            return True
        notional = abs(exec_px * qty)
        max_notional = max(balance_before, 0.0) * self.max_leverage
        return notional <= max_notional

    def _call_strategy(self, df: pd.DataFrame, params: Optional[Dict[str, Any]] = None):
        if not params:
            return self.strategy_fn(df)
        try:
            return self.strategy_fn(df, **params)
        except TypeError:
            return self.strategy_fn(df)

    def run(self, df: pd.DataFrame, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        if df.empty or "close" not in df.columns:
            self.equity_curve_ = pd.DataFrame()
            return pd.DataFrame()

        balance = float(self.initial_balance)
        position = 0.0          # >0 long, <0 short
        entry_price = 0.0
        entry_index = -1
        trades = []
        equity_rows = []

        signals = self._call_strategy(df, params) or []
        pending = []
        for s in signals:
            idx = int(s.get("index", 0))
            ex_idx = idx + int(self.latency_steps)
            if 0 <= ex_idx < len(df):
                pending.append((ex_idx, str(s.get("action", "")).lower(), float(s.get("quantity", 1.0))))
        pending.sort(key=lambda x: x[0])
        p = 0

        def close_position(i: int, reason: str):
            nonlocal balance, position, entry_price, entry_index
            if position == 0:
                return
            row = df.iloc[i]
            px = float(row["close"])
            side = "sell" if position > 0 else "buy"  # close long by sell, close short by buy
            exec_px = self._exec_price(px, side)
            qty = abs(position)
            fee = abs(qty * exec_px) * self.fee_rate

            pnl = (exec_px - entry_price) * position  # signed position handles long/short
            # cash flow
            if position > 0:   # closing long -> receive cash
                balance += qty * exec_px - fee
            else:              # closing short -> pay cash
                balance -= qty * exec_px + fee

            trades.append({
                "type": side,
                "exit_reason": reason,
                "price": exec_px,
                "qty": qty,
                "fee": fee,
                "pnl": pnl,
                "balance": balance,
                "timestamp": row.get("timestamp", row.name),
                "bar_index": i,
                "position_side": "long" if position > 0 else "short",
            })

            position = 0.0
            entry_price = 0.0
            entry_index = -1

        for i in range(len(df)):
            row = df.iloc[i]
            px = float(row["close"])

            # execute signals scheduled for this bar
            while p < len(pending) and pending[p][0] == i:
                _, action, sig_qty = pending[p]
                p += 1

                # close opposite side first
                if action == "buy" and position < 0:
                    close_position(i, "signal")
                elif action == "sell" and position > 0:
                    close_position(i, "signal")

                # open new side if flat
                if position == 0:
                    if action == "buy":
                        qty = self._calc_qty(balance, px, sig_qty)
                        if qty > 0:
                            ex = self._exec_price(px, "buy")
                            pre_balance = balance
                            if not self._passes_margin_check(pre_balance, ex, qty):
                                trades.append({
                                    "type": "rejected",
                                    "exit_reason": "margin_check_failed_long",
                                    "price": ex,
                                    "qty": qty,
                                    "fee": 0.0,
                                    "pnl": None,
                                    "balance": balance,
                                    "timestamp": row.get("timestamp", row.name),
                                    "bar_index": i,
                                    "position_side": "long",
                                })
                            else:
                                fee = abs(qty * ex) * self.fee_rate
                                balance -= qty * ex + fee
                                position = qty
                                entry_price = ex
                                entry_index = i
                                trades.append({
                                    "type": "buy",
                                    "exit_reason": None,
                                    "price": ex,
                                    "qty": qty,
                                    "fee": fee,
                                    "pnl": None,
                                    "balance": balance,
                                    "timestamp": row.get("timestamp", row.name),
                                    "bar_index": i,
                                    "position_side": "long",
                                })
                    elif action == "sell" and self.allow_short_selling:
                        qty = self._calc_qty(balance, px, sig_qty)
                        if qty > 0:
                            ex = self._exec_price(px, "sell")
                            pre_balance = balance
                            if not self._passes_margin_check(pre_balance, ex, qty):
                                trades.append({
                                    "type": "rejected",
                                    "exit_reason": "margin_check_failed_short",
                                    "price": ex,
                                    "qty": qty,
                                    "fee": 0.0,
                                    "pnl": None,
                                    "balance": balance,
                                    "timestamp": row.get("timestamp", row.name),
                                    "bar_index": i,
                                    "position_side": "short",
                                })
                            else:
                                fee = abs(qty * ex) * self.fee_rate
                                balance += qty * ex - fee
                                position = -qty
                                entry_price = ex
                                entry_index = i
                                trades.append({
                                    "type": "sell",
                                    "exit_reason": None,
                                    "price": ex,
                                    "qty": qty,
                                    "fee": fee,
                                    "pnl": None,
                                    "balance": balance,
                                    "timestamp": row.get("timestamp", row.name),
                                    "bar_index": i,
                                    "position_side": "short",
                                })

            # risk rules for open position
            if position != 0:
                direction = 1.0 if position > 0 else -1.0
                ret = direction * ((px - entry_price) / entry_price) if entry_price else 0.0
                hold_bars = i - entry_index if entry_index >= 0 else 0

                if self.stop_loss_pct is not None and ret <= -float(self.stop_loss_pct):
                    close_position(i, "stop_loss")
                elif self.take_profit_pct is not None and ret >= float(self.take_profit_pct):
                    close_position(i, "take_profit")
                elif self.max_holding_bars is not None and hold_bars >= int(self.max_holding_bars):
                    close_position(i, "max_hold")

            # track equity curve at end of bar
            equity = balance + (position * px)
            equity_rows.append({
                "bar_index": i,
                "timestamp": row.get("timestamp", row.name),
                "balance": balance,
                "position": position,
                "price": px,
                "equity": equity,
            })

        if position != 0:
            close_position(len(df) - 1, "eod")

        self.equity_curve_ = pd.DataFrame(equity_rows)
        return pd.DataFrame(trades)

    def walk_forward(
        self,
        df: pd.DataFrame,
        train_size: float = 0.6,
        test_size: float = 0.2,
        step_size: float = 0.2,
    ) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        n = len(df)
        if n == 0:
            return []

        train_n = max(1, int(train_size * n))
        test_n = max(1, int(test_size * n))
        step_n = max(1, int(step_size * n))

        splits = []
        start = 0
        while start + train_n + test_n <= n:
            train_df = df.iloc[start : start + train_n]
            test_df = df.iloc[start + train_n : start + train_n + test_n]
            splits.append((train_df, test_df))
            start += step_n
        return splits

    def run_walk_forward(
        self,
        df: pd.DataFrame,
        train_size: float = 0.6,
        test_size: float = 0.2,
        step_size: float = 0.2,
    ) -> List[pd.DataFrame]:
        results = []
        for i, (train_df, test_df) in enumerate(self.walk_forward(df, train_size, test_size, step_size), start=1):
            params = self.train_fn(train_df) if self.train_fn else None
            t = self.run(test_df, params=params)
            if not t.empty:
                t["split"] = i
            results.append(t)
        return results

    def train_live(
        self,
        df: pd.DataFrame,
        lookback_rows: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Live auto-training hook.

        Reuses `train_fn` on a rolling live window and returns tuned parameters
        without running a full walk-forward cycle.
        """
        if self.train_fn is None or df is None or df.empty:
            return None

        train_df = df.tail(int(lookback_rows)) if lookback_rows and lookback_rows > 0 else df
        if train_df.empty:
            return None

        params = self.train_fn(train_df)
        return params if isinstance(params, dict) else None
