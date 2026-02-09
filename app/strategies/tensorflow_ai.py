from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import tensorflow as tf

from app.strategies.base import StrategyBase

logger = logging.getLogger(__name__)


class TensorFlowAIStrategy(StrategyBase):
    def __init__(self, model_path: str, lookback: int = 10) -> None:
        self.model = tf.keras.models.load_model(model_path)
        self.lookback = lookback

    def preprocess(self, data: List[Dict[str, float]]) -> np.ndarray:
        closes = np.array([bar["close"] for bar in data], dtype=float).reshape(-1, 1)
        return closes

    def generate_signals(self, data: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
        if len(data) < self.lookback:
            return {"action": "hold"}

        X = self.preprocess(data[-self.lookback :])
        pred = float(self.model.predict(X[np.newaxis, ...], verbose=0)[0][0])

        if pred > 0.6:
            return {"action": "buy", "confidence": pred}
        if pred < 0.4:
            return {"action": "sell", "confidence": 1.0 - pred}
        return {"action": "hold", "confidence": pred}

    def on_order_filled(self, order: Dict[str, Any]) -> None:
        logger.info("Order filled: %s", order)