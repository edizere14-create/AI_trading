# TensorFlow AI strategy for trading
import numpy as np
import tensorflow as tf
from app.strategies.base import StrategyBase

class TensorFlowAIStrategy(StrategyBase):
	def __init__(self, model_path: str):
		self.model = tf.keras.models.load_model(model_path)

	def preprocess(self, data):
		# Example: use close prices as features
		closes = np.array([bar['close'] for bar in data]).reshape(-1, 1)
		return closes

	def generate_signals(self, data):
		if len(data) < 10:
			return None
		X = self.preprocess(data[-10:])
		pred = self.model.predict(X[np.newaxis, ...])[0][0]
		if pred > 0.6:
			return {"action": "buy"}
		elif pred < 0.4:
			return {"action": "sell"}
		return {"action": "hold"}

	def on_order_filled(self, order):
		pass
