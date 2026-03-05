from __future__ import annotations

import logging
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _exit_failed(result: dict[str, Any]) -> bool:
    status = str(result.get("status", "")).strip().lower()
    if status == "no_position":
        return False
    if status != "exit_attempted":
        return True
    chunks = result.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return True
    return any(isinstance(chunk, dict) and ("error" in chunk) for chunk in chunks)


def _risk_manager_equity(risk_manager: Any) -> float:
    if risk_manager is None:
        return 0.0

    getter = getattr(risk_manager, "get_equity", None)
    if callable(getter):
        return _safe_float(getter(), 0.0)

    for attr in ("current_balance", "account_balance"):
        value = getattr(risk_manager, attr, None)
        if value is not None:
            return _safe_float(value, 0.0)
    return 0.0


async def startup_safety_check(
    *,
    execution_engine: Any,
    risk_manager: Any,
    momentum_worker: Any,
    logger: logging.Logger,
) -> None:
    """Validate inherited exchange positions before first worker tick."""
    logger.warning("[STARTUP] Running pre-tick safety check...")

    if execution_engine is None:
        logger.info("[STARTUP] No execution engine available - skipping safety check")
        return
    if bool(getattr(execution_engine, "paper_mode", True)):
        logger.info("[STARTUP] Paper mode enabled - skipping live position safety check")
        return

    positions = await execution_engine.get_open_positions_async()
    equity = _risk_manager_equity(risk_manager)

    if not positions:
        logger.info("[STARTUP] No open positions found - clean start")
        return

    max_leverage_ratio = _safe_float(
        getattr(risk_manager, "max_leverage_ratio", None),
        _safe_float(getattr(execution_engine, "max_leverage_ratio", None), 0.0),
    )
    max_contracts_hard_limit = int(
        _safe_float(getattr(execution_engine, "max_contracts_hard_limit", 0), 0.0)
    )

    for pos in positions:
        symbol = str(pos.get("symbol") or "")
        contracts = abs(_safe_float(pos.get("contracts"), 0.0))
        side = str(pos.get("side") or "buy")
        if not symbol or contracts <= 0:
            continue

        mark_price = _safe_float(pos.get("mark_price"), 0.0)
        if mark_price <= 0:
            mark_price = _safe_float(await execution_engine.get_mark_price_async(symbol), 0.0)
        contract_size = _safe_float(pos.get("contract_size"), 0.0)
        if contract_size <= 0:
            contract_size = _safe_float(execution_engine.get_contract_size(symbol), 1.0)
        inverse = bool(pos.get("inverse", False))
        notional = (
            contracts * contract_size
            if inverse
            else contracts * contract_size * max(0.0, mark_price)
        )
        leverage = (notional / equity) if equity > 0 else float("inf")

        logger.warning(
            "[STARTUP] Found open position | symbol=%s side=%s contracts=%s notional=%.2f leverage=%.2fx equity=%.2f",
            symbol,
            side,
            contracts,
            notional,
            leverage,
            equity,
        )

        violations: list[str] = []
        if max_leverage_ratio > 0 and leverage > max_leverage_ratio:
            violations.append(f"leverage={leverage:.2f}x > max={max_leverage_ratio:.2f}x")
        if max_contracts_hard_limit > 0 and contracts > max_contracts_hard_limit:
            violations.append(f"contracts={contracts:.4f} > limit={max_contracts_hard_limit}")

        if not violations:
            logger.info(
                "[STARTUP] Position within limits - no action needed | symbol=%s leverage=%.2fx",
                symbol,
                leverage,
            )
            continue

        logger.error(
            "[STARTUP] UNSAFE POSITION DETECTED | symbol=%s violations=%s - firing emergency exit before first tick",
            symbol,
            violations,
        )

        result = await execution_engine.emergency_exit_position_async(
            symbol=symbol,
            current_contracts=contracts,
            side=side,
            mark_price=mark_price,
            equity=equity,
            reason="startup_leverage_violation",
            is_exit=True,
        )
        logger.warning("[STARTUP] Exit result | symbol=%s result=%s", symbol, result)
        if _exit_failed(result):
            raise RuntimeError(
                f"[STARTUP] Emergency exit FAILED for {symbol}. "
                f"Worker start aborted. Close position manually before restarting."
            )

    logger.info("[STARTUP] Pre-tick safety check complete")

