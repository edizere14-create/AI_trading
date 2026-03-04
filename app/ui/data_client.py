import asyncio
import os
from typing import Any

import asyncpg
import ccxt
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

HTTP_TIMEOUT = 8


class ApiContractError(RuntimeError):
    pass


def _format_kraken_exchange_error(action: str, exc: Exception) -> ApiContractError:
    raw = str(exc).strip() or exc.__class__.__name__
    normalized = raw.lower()
    if any(token in normalized for token in ("incorrect padding", "invalid base64", "non-base64")):
        return ApiContractError(
            "Invalid Kraken API secret format in All-in-One mode (base64/padding error). "
            "Re-copy KRAKEN_API_SECRET exactly as issued, without quotes or extra spaces/newlines, "
            "or switch to Backend API mode."
        )
    return ApiContractError(f"Failed to {action}: {raw}")


def _paper_positions_df() -> pd.DataFrame:
    try:
        import streamlit as st

        rows = st.session_state.get("paper_positions", [])
        if not isinstance(rows, list) or not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def add_paper_trade(side: str, symbol: str, quantity: float, entry_price: float) -> dict[str, Any]:
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise ApiContractError("side must be 'buy' or 'sell'.")
    try:
        import streamlit as st
    except Exception as e:
        raise ApiContractError(f"Paper trade unavailable: {e}") from e

    rows = st.session_state.get("paper_positions", [])
    if not isinstance(rows, list):
        rows = []

    row = {
        "symbol": symbol,
        "side": normalized_side,
        "quantity": float(quantity),
        "entry_price": float(entry_price),
        "current_price": float(entry_price),
        "unrealized_pnl": 0.0,
        "leverage": 1.0,
        "source": "paper",
    }
    rows.append(row)
    st.session_state["paper_positions"] = rows
    return row


def _all_in_one_enabled(api_url: str) -> bool:
    return api_url.strip().lower() in {"all-in-one", "direct"}


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise ApiContractError("DATABASE_URL is not set.")
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgres+asyncpg://"):
        url = "postgres://" + url[len("postgres+asyncpg://") :]
    return url


def _run_async(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


async def _db_fetch(query: str, *params: Any) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(_database_url())
    try:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def _build_exchange() -> ccxt.Exchange:
    api_key = os.getenv("KRAKEN_API_KEY", "").strip()
    api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise ApiContractError("KRAKEN_API_KEY/KRAKEN_API_SECRET must be set for all-in-one mode.")

    exchange = ccxt.krakenfutures(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 30000,
        }
    )
    demo = os.getenv("KRAKEN_FUTURES_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"}
    exchange.set_sandbox_mode(demo)
    return exchange


def _kraken_symbol() -> str:
    return os.getenv("KRAKEN_FUTURES_SYMBOL", "BTC/USD:USD").strip() or "BTC/USD:USD"


def _normalize_futures_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        return "PF_XBTUSD"
    if value.startswith("PI_"):
        return value.replace("PI_", "PF_", 1)
    if value in {"BTC/USD:USD", "XBT/USD:USD", "BTCUSD", "XBTUSD"}:
        return "PF_XBTUSD"
    if value == "ETH/USD:USD":
        return "PF_ETHUSD"
    if value == "SOL/USD:USD":
        return "PF_SOLUSD"
    return value


def _coerce_candles_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        candidate = payload.get("candles", payload.get("result", payload.get("data", payload)))
    else:
        candidate = payload

    if isinstance(candidate, dict):
        if "candles" in candidate and isinstance(candidate.get("candles"), list):
            candidate = candidate.get("candles")
        else:
            candidate = [candidate]

    if not isinstance(candidate, list):
        raise ApiContractError("Contract mismatch: candle payload must be a list or object containing candles list.")

    rows: list[dict[str, Any]] = []
    for row in candidate:
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp", row.get("time", row.get("ts")))
        o = row.get("open", row.get("o"))
        h = row.get("high", row.get("h"))
        l = row.get("low", row.get("l"))
        c = row.get("close", row.get("c"))
        v = row.get("volume", row.get("v", 0.0))
        if ts is None or o is None or h is None or l is None or c is None:
            continue
        rows.append(
            {
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )

    if not rows:
        raise ApiContractError("No valid candles found in payload.")
    return rows


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ApiContractError(f"API request failed: {url} -> {e}") from e


def _post_json(url: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
    try:
        r = requests.post(url, json=body, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ApiContractError(f"API request failed: {url} -> {e}") from e


def _analytics_fallback(reason: str = "Backend data layer issue") -> dict[str, Any]:
    return {
        "bias": "NEUTRAL",
        "confidence": None,
        "vol_forecast": None,
        "pattern_summary": "Demo mode",
        "why": reason,
        "signals": [],
    }


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _extract_contract_size(row: dict[str, Any]) -> float:
    info = row.get("info")
    if isinstance(info, dict):
        size = _safe_float(
            info.get("contractSize", info.get("contract_size", info.get("contractValue")))
        )
        if isinstance(size, float) and size > 0:
            return size
    size = _safe_float(row.get("contractSize", row.get("contract_size")))
    if isinstance(size, float) and size > 0:
        return size
    return 1.0


def _contracts_to_display_quantity(symbol: str, contracts: float, price: float, contract_size: float) -> float:
    qty_contracts = abs(float(contracts))
    if qty_contracts <= 0:
        return 0.0

    size = contract_size if contract_size > 0 else 1.0
    normalized_symbol = str(symbol or "").upper()
    is_inverse_usd_contract = any(
        token in normalized_symbol
        for token in ("/USD:BTC", "/USD:XBT", "/USD:ETH", "/USD:SOL")
    )

    if is_inverse_usd_contract and price > 0:
        return (qty_contracts * size) / price
    return qty_contracts * size


def get_worker_status(api_url: str) -> dict[str, Any]:
    candidates: list[str] = []
    if _all_in_one_enabled(api_url):
        backend_port = (os.getenv("BACKEND_PORT", "8000").strip() or "8000")
        internal_api = os.getenv("INTERNAL_API_URL", "").strip()
        if internal_api:
            candidates.append(internal_api)
        candidates.extend(
            [
                f"http://127.0.0.1:{backend_port}",
                f"http://localhost:{backend_port}",
            ]
        )
    else:
        candidates.append(str(api_url or "").strip())

    seen: set[str] = set()
    normalized_candidates: list[str] = []
    for base in candidates:
        key = base.rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        normalized_candidates.append(key)

    data: Any = None
    for base in normalized_candidates:
        try:
            data = _get_json(f"{base}/momentum/status")
            break
        except ApiContractError:
            continue

    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data.get("data")
    if not isinstance(data, dict):
        return {}

    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return {
        "is_running": bool(data.get("is_running", False)),
        "symbol": str(data.get("symbol", "") or ""),
        "signal_count": _safe_int(data.get("signal_count", 0), 0),
        "execution_count": _safe_int(data.get("execution_count", 0), 0),
        "last_decision_reason": str(data.get("last_decision_reason", "") or ""),
    }


def get_metrics(api_url: str) -> dict[str, Any]:
    if _all_in_one_enabled(api_url):
        exchange = _build_exchange()
        symbol = _kraken_symbol()
        try:
            ticker = exchange.fetch_ticker(symbol)
            last = float(ticker.get("last") or 0.0)
        except Exception:
            last = 0.0

        equity = 0.0
        try:
            balance = exchange.fetch_balance()
            usd_like = ["USD", "USDT", "USDC"]
            for asset in usd_like:
                equity += float((balance.get("total", {}) or {}).get(asset, 0.0) or 0.0)
            if equity <= 0:
                equity = float((balance.get("total", {}) or {}).get("ZUSD", 0.0) or 0.0)
        except Exception:
            equity = 0.0

        ai = get_ai_insight(api_url)
        trades = get_active_trades(api_url)
        daily_pnl = 0.0
        if not trades.empty and "unrealized_pnl" in trades.columns:
            daily_pnl = float(pd.to_numeric(trades["unrealized_pnl"], errors="coerce").fillna(0.0).sum())
        exposure = 0.0
        if not trades.empty and "notional" in trades.columns:
            exposure = float(pd.to_numeric(trades["notional"], errors="coerce").fillna(0.0).sum())
        elif not trades.empty and {"quantity", "entry_price"}.issubset(trades.columns):
            notionals = pd.to_numeric(trades["quantity"], errors="coerce").fillna(0.0) * pd.to_numeric(
                trades["entry_price"], errors="coerce"
            ).fillna(last)
            exposure = float(notionals.sum())

        exposure_pct = (exposure / equity * 100.0) if equity > 0 else 0.0
        return {
            "total_equity": float(equity),
            "daily_pnl": float(daily_pnl),
            "ai_bias": str(ai.get("bias", "NEUTRAL")).upper(),
            "confidence": float(ai.get("confidence", 0.0) or 0.0),
            "risk_exposure": float(exposure_pct),
        }

    # Exact backend routes:
    # - /momentum/status
    # - /risk/status
    m = _get_json(f"{api_url}/momentum/status")
    r = _get_json(f"{api_url}/risk/status")

    if not isinstance(m, dict) or not isinstance(r, dict):
        raise ApiContractError("Contract mismatch: /momentum/status or /risk/status must return objects.")

    # Flexible extraction for current backend shape
    ai_payload = m.get("ai", m.get("analytics", {}))
    ai = ai_payload if isinstance(ai_payload, dict) else {}
    equity = float(r.get("account_balance", r.get("equity", 0.0)) or 0.0)
    reported_pnl = float(r.get("total_pnl", r.get("daily_pnl", 0.0)) or 0.0)
    reported_exposure_pct = float(r.get("exposure_pct", r.get("exposure", 0.0)) or 0.0)

    display_pnl = reported_pnl
    display_exposure_pct = reported_exposure_pct
    try:
        trades = get_active_trades(api_url)
        if not trades.empty:
            unrealized = 0.0
            if "unrealized_pnl" in trades.columns:
                unrealized = float(pd.to_numeric(trades["unrealized_pnl"], errors="coerce").fillna(0.0).sum())
            if abs(display_pnl) < 1e-9 and abs(unrealized) > 1e-9:
                display_pnl = unrealized

            if display_exposure_pct <= 0.0:
                exposure = 0.0
                if "notional" in trades.columns:
                    exposure = float(pd.to_numeric(trades["notional"], errors="coerce").fillna(0.0).sum())
                elif {"quantity", "current_price"}.issubset(trades.columns):
                    exposure = float(
                        (
                            pd.to_numeric(trades["quantity"], errors="coerce").fillna(0.0)
                            * pd.to_numeric(trades["current_price"], errors="coerce").fillna(0.0)
                        ).sum()
                    )
                elif {"quantity", "entry_price"}.issubset(trades.columns):
                    exposure = float(
                        (
                            pd.to_numeric(trades["quantity"], errors="coerce").fillna(0.0)
                            * pd.to_numeric(trades["entry_price"], errors="coerce").fillna(0.0)
                        ).sum()
                    )
                if equity > 0 and exposure > 0:
                    display_exposure_pct = (exposure / equity) * 100.0
    except Exception:
        pass

    return {
        "total_equity": float(equity),
        "daily_pnl": float(display_pnl),
        "ai_bias": str(ai.get("bias", m.get("bias", "NEUTRAL"))).upper(),
        "confidence": float(ai.get("confidence", m.get("confidence", 0.0)) or 0.0),
        "risk_exposure": float(display_exposure_pct),
    }


def get_ai_insight(api_url: str) -> dict[str, Any]:
    if _all_in_one_enabled(api_url):
        symbol = _kraken_symbol()
        exchange = _build_exchange()
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=120)
        except Exception as exc:
            raise _format_kraken_exchange_error("fetch Kraken candles", exc) from exc
        if len(ohlcv) < 30:
            return {
                "bias": "NEUTRAL",
                "confidence": 0.0,
                "vol_forecast": None,
                "pattern_summary": "Insufficient candles",
                "why": "Need more market data to compute signal.",
                "signals": [],
            }

        closes = pd.Series([float(r[4]) for r in ohlcv])
        sma20 = float(closes.rolling(20).mean().iloc[-1])
        sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else sma20
        momentum = float((closes.iloc[-1] / closes.iloc[-11] - 1.0) * 100.0) if len(closes) >= 11 else 0.0
        trend = (sma20 / sma50 - 1.0) * 100.0 if sma50 else 0.0
        score = trend + momentum
        bias = "BUY" if score > 0.05 else "SELL" if score < -0.05 else "NEUTRAL"
        confidence = min(99.0, max(5.0, abs(score) * 250.0))
        vol_forecast = float(closes.pct_change().dropna().tail(60).std() * (24 * 365) ** 0.5)

        return {
            "bias": bias,
            "confidence": confidence,
            "vol_forecast": vol_forecast,
            "pattern_summary": f"trend={trend:.3f}%, momentum10={momentum:.3f}%",
            "why": "Signal derived from SMA trend alignment and recent momentum on Kraken Futures candles.",
            "signals": [],
        }

    try:
        data = _get_json(f"{api_url}/momentum/analytics")
    except ApiContractError:
        return _analytics_fallback()

    if not isinstance(data, dict):
        return _analytics_fallback("Contract mismatch on /momentum/analytics")

    confidence_value = _safe_float(data.get("confidence"))
    vol_forecast_value = _safe_float(data.get("volatility_forecast", data.get("vol_forecast")))
    raw_pattern_summary = data.get("pattern_summary")
    pattern_summary = "Demo mode" if raw_pattern_summary in (None, "") else str(raw_pattern_summary)
    raw_why = data.get("why_trade", data.get("why", ""))
    why = "Backend data layer issue" if raw_why in (None, "") else str(raw_why)
    raw_signals = data.get("signals", [])
    signals = raw_signals if isinstance(raw_signals, list) else []

    return {
        "bias": str(data.get("bias", "NEUTRAL")),
        "confidence": confidence_value,
        "vol_forecast": vol_forecast_value,
        "pattern_summary": pattern_summary,
        "why": why,
        "signals": signals,
    }


def get_active_trades(api_url: str) -> pd.DataFrame:
    if _all_in_one_enabled(api_url):
        exchange = _build_exchange()
        try:
            positions = exchange.fetch_positions()
        except Exception as exc:
            raise _format_kraken_exchange_error("fetch Kraken positions", exc) from exc

        rows: list[dict[str, Any]] = []
        for p in positions or []:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0:
                continue
            symbol = str(p.get("symbol") or p.get("id") or "")
            side = str(p.get("side") or "").lower() or ("buy" if contracts > 0 else "sell")
            entry = float(p.get("entryPrice") or p.get("markPrice") or 0.0)
            mark = float(p.get("markPrice") or entry)
            contract_size = _extract_contract_size(p)
            quantity = _contracts_to_display_quantity(symbol, contracts, mark or entry, contract_size)
            if quantity <= 0:
                quantity = abs(contracts)
            notional = (quantity * mark) if mark > 0 else abs(contracts) * contract_size
            rows.append(
                {
                    "symbol": symbol,
                    "side": "buy" if side in {"long", "buy"} else "sell",
                    "quantity": quantity,
                    "contracts": abs(contracts),
                    "entry_price": entry,
                    "current_price": mark,
                    "unrealized_pnl": float(p.get("unrealizedPnl") or 0.0),
                    "notional": notional,
                    "leverage": float(p.get("leverage") or 0.0),
                    "source": "exchange",
                }
            )
        exchange_df = pd.DataFrame(rows)
        paper_df = _paper_positions_df()
        if exchange_df.empty:
            return paper_df
        if paper_df.empty:
            return exchange_df
        return pd.concat([exchange_df, paper_df], ignore_index=True)

    # Exact backend route replacing removed /trades/active
    data = _get_json(f"{api_url}/risk/positions")
    if isinstance(data, dict):
        data = data.get("positions", [])
    if not isinstance(data, list):
        raise ApiContractError("Contract mismatch: /risk/positions must return list or {'positions': list}.")
    api_df = pd.DataFrame(data)
    paper_df = _paper_positions_df()
    if api_df.empty:
        return paper_df
    if paper_df.empty:
        return api_df
    return pd.concat([api_df, paper_df], ignore_index=True)


def get_open_orders(api_url: str, limit: int = 100) -> pd.DataFrame:
    if _all_in_one_enabled(api_url):
        exchange = _build_exchange()
        symbol = _kraken_symbol()
        try:
            orders = exchange.fetch_open_orders(symbol)
        except Exception:
            try:
                orders = exchange.fetch_open_orders()
            except Exception as exc:
                raise _format_kraken_exchange_error("fetch Kraken open orders", exc) from exc

        rows: list[dict[str, Any]] = []
        for order in orders or []:
            if not isinstance(order, dict):
                continue
            side = str(order.get("side") or "").lower()
            if side not in {"buy", "sell"}:
                continue

            contracts = _safe_float(order.get("remaining"))
            if contracts is None or contracts <= 0:
                contracts = _safe_float(order.get("amount"))
            if contracts is None or contracts <= 0:
                continue

            price = float(order.get("price") or order.get("average") or 0.0)
            symbol_val = str(order.get("symbol") or order.get("id") or "")
            contract_size = _extract_contract_size(order)
            quantity = _contracts_to_display_quantity(symbol_val, contracts, price, contract_size)
            if quantity <= 0:
                quantity = abs(float(contracts))

            rows.append(
                {
                    "order_id": str(order.get("id") or ""),
                    "symbol": symbol_val,
                    "side": side,
                    "quantity": quantity,
                    "contracts": abs(float(contracts)),
                    "price": price,
                    "status": str(order.get("status") or "open").lower(),
                    "timestamp": order.get("datetime") or order.get("timestamp"),
                    "source": "exchange",
                }
            )
        return pd.DataFrame(rows)

    try:
        payload = _get_json(f"{api_url}/momentum/orders-sync", params={"limit": int(limit)})
    except ApiContractError:
        return pd.DataFrame()

    if not isinstance(payload, dict):
        return pd.DataFrame()

    orders = payload.get("orders", [])
    if not isinstance(orders, list):
        return pd.DataFrame()

    open_statuses = {"open", "new", "pending", "submitted"}
    rows: list[dict[str, Any]] = []
    for row in orders:
        if not isinstance(row, dict):
            continue
        progress = str(row.get("progress_state") or "").lower()
        exchange_status = str(row.get("exchange_status") or "").lower()
        if progress not in {"open", "partially_filled_open"} and exchange_status not in open_statuses:
            continue

        amount = _safe_float(row.get("amount")) or 0.0
        remaining = _safe_float(row.get("remaining_quantity")) or 0.0
        filled = _safe_float(row.get("filled_quantity")) or 0.0
        qty = remaining if remaining > 0 else max(0.0, amount - filled)
        if qty <= 0:
            qty = amount
        if qty <= 0:
            continue

        rows.append(
            {
                "order_id": str(row.get("order_id") or ""),
                "symbol": str(row.get("symbol") or ""),
                "side": str(row.get("side") or "").lower(),
                "quantity": float(qty),
                "contracts": float(qty),
                "price": None,
                "status": exchange_status or progress or "open",
                "timestamp": row.get("timestamp"),
                "source": "orders-sync",
            }
        )

    return pd.DataFrame(rows)


def get_candles(api_url: str, limit: int = 300) -> pd.DataFrame:
    if _all_in_one_enabled(api_url):
        exchange = _build_exchange()
        symbol = _kraken_symbol()
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=max(50, int(limit)))
        except Exception as exc:
            raise _format_kraken_exchange_error("fetch Kraken candles", exc) from exc
        rows = [
            {
                "timestamp": pd.to_datetime(int(r[0]), unit="ms", utc=True),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in ohlcv
        ]
        return pd.DataFrame(rows).tail(limit)

    requested_symbol = _normalize_futures_symbol(os.getenv("MOMENTUM_DEFAULT_SYMBOL", "PF_XBTUSD"))
    candle_payload: Any = None
    errors: list[str] = []
    endpoints = [
        (f"{api_url}/data/kraken/ohlcv", {"symbol": requested_symbol, "timeframe": "1m", "limit": max(50, int(limit))}),
        (f"{api_url}/data/ohlcv", {"symbol": requested_symbol, "timeframe": "1m", "limit": max(50, int(limit))}),
        (f"{api_url}/momentum/history", {"limit": limit}),
    ]
    for url, params in endpoints:
        try:
            candle_payload = _get_json(url, params=params)
            candles = _coerce_candles_payload(candle_payload)
            if candles:
                df = pd.DataFrame(candles)
                break
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
    else:
        raise ApiContractError("Unable to load candles from backend routes. " + " | ".join(errors))

    required = ["timestamp", "open", "high", "low", "close", "volume"]
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df.dropna(subset=required).sort_values("timestamp").tail(limit)


def get_portfolio(api_url: str) -> pd.DataFrame:
    if _all_in_one_enabled(api_url):
        trades = get_active_trades(api_url)
        if trades.empty:
            return trades
        trades = trades.copy()
        if "notional" in trades.columns:
            trades["notional"] = pd.to_numeric(trades["notional"], errors="coerce").fillna(0.0)
        else:
            trades["notional"] = pd.to_numeric(trades["quantity"], errors="coerce").fillna(0.0) * pd.to_numeric(
                trades["current_price"], errors="coerce"
            ).fillna(0.0)
        total = float(trades["notional"].sum())
        trades["weight_pct"] = (trades["notional"] / total * 100.0) if total > 0 else 0.0
        return trades

    # Exact backend route replacing removed /risk/portfolio
    data = _get_json(f"{api_url}/risk/positions")
    if isinstance(data, dict):
        data = data.get("positions", [])
    if not isinstance(data, list):
        raise ApiContractError("Contract mismatch: /risk/positions must return list or {'positions': list}.")
    return pd.DataFrame(data)


def get_backtest(api_url: str, lookback_days: int = 90) -> dict[str, Any]:
    if _all_in_one_enabled(api_url):
        try:
            rows = _run_async(
                _db_fetch(
                    """
                    SELECT created_at, initial_capital, final_value, total_return, sharpe_ratio, max_drawdown, total_trades, win_rate
                    FROM backtest_results
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
            )
        except Exception as exc:
            return {
                "stats": {"net_pnl": 0.0, "win_rate": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "trades": 0},
                "equity_curve": [],
                "monthly_performance": [],
                "analytics": {"warning": f"Backtest DB unavailable: {exc}"},
            }
        if not rows:
            return {
                "stats": {"net_pnl": 0.0, "win_rate": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "trades": 0},
                "equity_curve": [],
                "monthly_performance": [],
                "analytics": {},
            }
        row = rows[0]
        start_equity = float(row.get("initial_capital") or 0.0)
        end_equity = float(row.get("final_value") or start_equity)
        created = row.get("created_at")
        t = pd.to_datetime(created, utc=True) if created else pd.Timestamp.utcnow()
        return {
            "stats": {
                "net_pnl": end_equity - start_equity,
                "win_rate": float(row.get("win_rate") or 0.0) * 100.0,
                "sharpe": float(row.get("sharpe_ratio") or 0.0),
                "max_drawdown": float(row.get("max_drawdown") or 0.0),
                "trades": int(row.get("total_trades") or 0),
            },
            "equity_curve": [
                {"t": (t - pd.Timedelta(days=int(lookback_days))).isoformat(), "equity": start_equity},
                {"t": t.isoformat(), "equity": end_equity},
            ],
            "monthly_performance": [],
            "analytics": {},
        }

    data = _get_json(
        f"{api_url}/backtest/summary",
        params={"days": int(lookback_days), "symbol": "PF_XBTUSD", "timeframe": "1h"},
    )
    if not isinstance(data, dict):
        raise ApiContractError("Contract mismatch: /backtest/summary must return an object.")

    start_equity = float(data.get("start_equity", 0.0) or 0.0)
    end_equity = float(data.get("end_equity", start_equity) or start_equity)
    net_pnl = end_equity - start_equity

    curve_rows = []
    for row in data.get("equity_curve", []) or []:
        if isinstance(row, dict):
            curve_rows.append({"t": row.get("timestamp"), "equity": float(row.get("equity", 0.0) or 0.0)})

    return {
        "stats": {
            "net_pnl": net_pnl,
            "win_rate": float(data.get("win_rate_pct", 0.0) or 0.0),
            "sharpe": float(data.get("sharpe_ratio", 0.0) or 0.0),
            "max_drawdown": float(data.get("max_drawdown_pct", 0.0) or 0.0),
            "trades": int(data.get("trades", 0) or 0),
        },
        "equity_curve": curve_rows,
        "monthly_performance": data.get("monthly_performance", []),
        "analytics": data.get("analytics", {}),
    }


def emergency_close_all_positions(api_url: str) -> dict[str, Any]:
    if _all_in_one_enabled(api_url):
        trading_enabled = os.getenv("KRAKEN_TRADING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        if not trading_enabled:
            return {"closed_count": 0, "detail": "Set KRAKEN_TRADING_ENABLED=true to allow live close-all in all-in-one mode."}

        exchange = _build_exchange()
        positions = exchange.fetch_positions()
        closed = 0
        for p in positions or []:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0:
                continue
            symbol = str(p.get("symbol") or "")
            side = str(p.get("side") or "").lower()
            close_side = "sell" if side in {"long", "buy"} else "buy"
            try:
                exchange.create_order(symbol, "market", close_side, abs(contracts), None, {"reduceOnly": True})
                closed += 1
            except Exception:
                continue
        return {"closed_count": closed, "detail": f"Closed {closed} position(s)."}

    data = _post_json(f"{api_url}/risk/close-all")
    if not isinstance(data, dict):
        raise ApiContractError("Contract mismatch: /risk/close-all must return an object.")
    return data


def get_account_balance(api_url: str) -> float:
    if _all_in_one_enabled(api_url):
        exchange = _build_exchange()
        try:
            balance = exchange.fetch_balance()
        except Exception as exc:
            raise _format_kraken_exchange_error("fetch account balance", exc) from exc

        total = balance.get("total", {}) or {}
        equity = 0.0
        for asset in ("USD", "USDT", "USDC", "ZUSD"):
            equity += float(total.get(asset, 0.0) or 0.0)
        return float(equity)

    metrics = get_metrics(api_url)
    return float(metrics.get("total_equity", 0.0) or 0.0)


def open_trade(api_url: str, side: str, amount: float | None = None, symbol: str | None = None) -> dict[str, Any]:
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise ApiContractError("side must be 'buy' or 'sell'.")

    if _all_in_one_enabled(api_url):
        trading_enabled = os.getenv("KRAKEN_TRADING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        if not trading_enabled:
            raise ApiContractError("Live order placement is disabled. Set KRAKEN_TRADING_ENABLED=true to enable.")

        exchange = _build_exchange()
        order_symbol = str(symbol or _kraken_symbol()).strip() or _kraken_symbol()
        order_amount = float(amount if amount is not None else (os.getenv("KRAKEN_ORDER_SIZE", "1.0") or 1.0))
        if order_amount <= 0:
            raise ApiContractError("Order size must be greater than 0.")
        try:
            order = exchange.create_order(order_symbol, "market", normalized_side, order_amount)
        except Exception as e:
            raise ApiContractError(f"Failed to place Kraken order: {e}") from e
        return {
            "status": "submitted",
            "symbol": order_symbol,
            "side": normalized_side,
            "amount": order_amount,
            "order_id": order.get("id"),
            "raw": order,
        }

    raise ApiContractError("open_trade is supported in All-in-One mode only.")


def get_risk_preview(api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    if _all_in_one_enabled(api_url):
        collateral_rows = payload.get("collateral_balances", []) or []
        effective_equity = 0.0
        for item in collateral_rows:
            amount = float(item.get("amount", 0.0) or 0.0)
            usd_price = float(item.get("usd_price", 0.0) or 0.0)
            haircut = float(item.get("haircut_pct", 0.0) or 0.0)
            effective_equity += amount * usd_price * (1.0 - haircut)

        trade = payload.get("trade", {}) or {}
        symbol = str(trade.get("symbol", "") or "")
        entry = float(trade.get("entry_price", 0.0) or 0.0)
        stop = float(trade.get("stop_price", entry) or entry)
        qty = float(trade.get("quantity", 0.0) or 0.0)

        open_positions = payload.get("open_positions", []) or []
        current_risk = 0.0
        for p in open_positions:
            p_entry = float(p.get("entry_price", 0.0) or 0.0)
            p_current = float(p.get("current_price", p_entry) or p_entry)
            p_qty = float(p.get("quantity", 0.0) or 0.0)
            current_risk += abs(p_entry - p_current) * p_qty

        new_risk = abs(entry - stop) * qty
        total_risk = current_risk + new_risk
        risk_pct = (total_risk / effective_equity * 100.0) if effective_equity > 0 else 0.0

        symbol_map = payload.get("symbol_collateral_map", {}) or {}
        bucket_limits = payload.get("collateral_bucket_exposure_limits", {}) or {}
        bucket_asset = str(symbol_map.get(symbol, "USDT") or "USDT")
        bucket_limit = float(bucket_limits.get(bucket_asset, 0.60) or 0.60)

        existing_bucket_exposure = 0.0
        for p in open_positions:
            if str(p.get("symbol", "") or "") == symbol:
                p_qty = float(p.get("quantity", 0.0) or 0.0)
                p_px = float(p.get("current_price", p.get("entry_price", 0.0)) or 0.0)
                existing_bucket_exposure += p_qty * p_px

        new_bucket_exposure = qty * entry
        bucket_exposure = existing_bucket_exposure + new_bucket_exposure
        bucket_exposure_pct = (bucket_exposure / effective_equity) if effective_equity > 0 else 0.0
        approved = risk_pct <= 2.0 and bucket_exposure_pct <= bucket_limit

        return {
            "approved": approved,
            "effective_equity": effective_equity,
            "risk_pct": risk_pct,
            "collateral_bucket_exposure_pct": bucket_exposure_pct,
            "collateral_bucket_limit_pct": bucket_limit,
            "trade_collateral_asset": bucket_asset,
            "reason": "approved" if approved else "risk limit exceeded",
        }

    data = _post_json(f"{api_url}/risk/check", body=payload)
    if not isinstance(data, dict):
        raise ApiContractError("Contract mismatch: /risk/check must return an object.")
    return data
