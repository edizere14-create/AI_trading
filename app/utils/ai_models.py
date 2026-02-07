# AI model utilities for trading strategies
import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestClassifier  # type: ignore
from sklearn.metrics import accuracy_score  # type: ignore
import tensorflow as tf
from tensorflow import keras  # type: ignore

def train_random_forest(X: NDArray[np.float64], y: NDArray[np.int_]) -> RandomForestClassifier:
	model = RandomForestClassifier(n_estimators=100)
	model.fit(X, y)
	return model

def predict_random_forest(model: RandomForestClassifier, X: NDArray[np.float64]) -> NDArray[np.int_]:
	return np.asarray(model.predict(X), dtype=np.int_)

def evaluate_model(model: RandomForestClassifier, X: NDArray[np.float64], y: NDArray[np.int_]) -> float:
	preds = model.predict(X)
	return float(accuracy_score(y, preds))

def build_tf_model(input_shape: int) -> keras.Sequential:
	model = keras.Sequential([
		keras.layers.Dense(64, activation='relu', input_shape=(input_shape,)),
		keras.layers.Dense(32, activation='relu'),
		keras.layers.Dense(1, activation='sigmoid')
	])
	model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
	return model

def train_tf_model(model: keras.Sequential, X: NDArray[np.float64], y: NDArray[np.float64], epochs: int = 10) -> keras.Sequential:
	model.fit(X, y, epochs=epochs, verbose=0)
	return model

def predict_tf_model(model: keras.Sequential, X: NDArray[np.float64]) -> NDArray[np.float64]:
	return model.predict(X)
