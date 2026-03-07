"""
Signal confidence classifier — replaces linear confidence formula with
a trained GradientBoosting model once enough data is collected.

Training data comes from gate snapshots stored in signal_history.
The target label is whether price moved > 0.5% in the signal direction
within the next 20 bars.

Until the model is trained (needs >= MIN_TRAIN_SAMPLES snapshots with
outcomes), it returns a neutral 50.0 confidence so the rest of the
pipeline is unaffected.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MIN_TRAIN_SAMPLES = int(os.getenv("ML_MIN_TRAIN_SAMPLES", "200"))
RETRAIN_EVERY = int(os.getenv("ML_RETRAIN_EVERY", "500"))

# Feature names (order matters for the vector)
FEATURE_KEYS = [
    "trend_pct",
    "momentum",
    "rsi",
    "macd_histogram",
    "bb_position",
    "vol_ratio",
    "vol_ann",
]


class SignalClassifier:
    """
    Trained on historical gate snapshots.
    Features: trend, momentum, vol, rsi, macd, bb_pos, volume_ratio
    Target: did price move > 0.5% in direction within 20 bars?
    """

    def __init__(self) -> None:
        self.model: Any = None
        self.trained: bool = False
        self._tick_since_train: int = 0
        self._train_count: int = 0

    def _ensure_model(self) -> Any:
        """Lazy-import sklearn to avoid startup cost when not used."""
        if self.model is None:
            try:
                from sklearn.ensemble import GradientBoostingClassifier

                self.model = GradientBoostingClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.1,
                    subsample=0.8,
                    random_state=42,
                )
            except ImportError:
                logger.warning("scikit-learn not installed — ML classifier disabled")
                return None
        return self.model

    @staticmethod
    def feature_vector(ctx: dict[str, Any]) -> list[float]:
        """Extract feature vector from a gate snapshot / context dict."""
        return [float(ctx.get(k, 0.0) or 0.0) for k in FEATURE_KEYS]

    def train(self, snapshots: list[dict[str, Any]], outcomes: list[int]) -> bool:
        """
        Train the classifier.

        Parameters
        ----------
        snapshots : list of gate-snapshot dicts (must contain FEATURE_KEYS)
        outcomes  : list of 0/1 (0 = price did NOT move >0.5%, 1 = it did)

        Returns True if training succeeded.
        """
        model = self._ensure_model()
        if model is None:
            return False

        if len(snapshots) < MIN_TRAIN_SAMPLES:
            logger.debug(
                "ML train skipped: only %d samples (need %d)",
                len(snapshots),
                MIN_TRAIN_SAMPLES,
            )
            return False

        X = np.array([self.feature_vector(s) for s in snapshots], dtype=np.float64)
        y = np.array(outcomes, dtype=np.int32)

        # Need both classes represented
        if len(set(y)) < 2:
            logger.debug("ML train skipped: only one class in outcomes")
            return False

        try:
            model.fit(X, y)
            self.trained = True
            self._train_count += 1
            self._tick_since_train = 0
            logger.info(
                "ML classifier trained (#%d) on %d samples | feature_importances=%s",
                self._train_count,
                len(X),
                [round(fi, 3) for fi in model.feature_importances_],
            )
            return True
        except Exception as exc:
            logger.error("ML train failed: %s", exc)
            return False

    def predict_confidence(self, ctx: dict[str, Any]) -> float:
        """
        Predict signal confidence (0-100).
        Returns 50.0 (neutral) if the model is not trained.
        """
        if not self.trained or self.model is None:
            return 50.0
        try:
            X = np.array([self.feature_vector(ctx)], dtype=np.float64)
            prob = self.model.predict_proba(X)[0][1]
            return round(float(prob) * 100, 2)
        except Exception as exc:
            logger.debug("ML predict failed: %s", exc)
            return 50.0

    def maybe_retrain(self, snapshots: list[dict[str, Any]], outcomes: list[int]) -> bool:
        """
        Called every tick. Retrains when enough new data has accumulated.
        Returns True if a retrain actually happened.
        """
        self._tick_since_train += 1
        if self._tick_since_train < RETRAIN_EVERY:
            return False
        return self.train(snapshots, outcomes)

    def status(self) -> dict[str, Any]:
        """Return classifier status for API/dashboard."""
        return {
            "trained": self.trained,
            "train_count": self._train_count,
            "ticks_since_train": self._tick_since_train,
            "min_samples": MIN_TRAIN_SAMPLES,
            "retrain_every": RETRAIN_EVERY,
            "features": FEATURE_KEYS,
        }
