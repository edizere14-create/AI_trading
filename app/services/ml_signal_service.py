import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from sklearn.ensemble import RandomForestRegressor
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam
import logging
from typing import Tuple, List

logger = logging.getLogger(__name__)

class MLSignalService:
    """Machine Learning signal generation (LSTM + Random Forest ensemble)"""
    
    def __init__(self, lookback_period: int = 60):
        self.lookback_period = lookback_period
        self.scaler = MinMaxScaler()
        self.lstm_model = None
        self.rf_model = None
        
    def prepare_features(self, data: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prepare features for ML models
        
        Features:
        - Price momentum (returns)
        - Volatility (std dev)
        - RSI
        - MACD
        - Bollinger Bands
        """
        features = []
        
        # Returns momentum
        returns = data['close'].pct_change(1).fillna(0)
        features.append(returns.values)
        
        # Volatility (20-period)
        volatility = data['close'].rolling(20).std().fillna(0)
        features.append(volatility.values)
        
        # RSI (14-period)
        rsi = self._calculate_rsi(data['close'], 14)
        features.append(rsi)
        
        # MACD
        macd = self._calculate_macd(data['close'])
        features.append(macd)
        
        # Bollinger Bands
        bb = self._calculate_bollinger_bands(data['close'], 20)
        features.append(bb)
        
        # Volume change
        volume_change = data['volume'].pct_change(1).fillna(0)
        features.append(volume_change.values)
        
        feature_matrix = np.column_stack(features)
        feature_matrix = np.nan_to_num(feature_matrix, 0)
        
        # Scale features
        feature_matrix = self.scaler.fit_transform(feature_matrix)
        
        # Target: next day return (1 if positive, 0 if negative)
        target = (data['close'].pct_change(1).shift(-1) > 0).astype(int).values
        
        return feature_matrix, target
    
    def train_lstm_model(self, X: np.ndarray, y: np.ndarray, epochs: int = 50):
        """Train LSTM for price prediction"""
        logger.info("Training LSTM model...")
        
        # Reshape for LSTM (samples, timesteps, features)
        X_lstm = np.array([X[i-self.lookback_period:i] 
                          for i in range(self.lookback_period, len(X))])
        y_lstm = y[self.lookback_period:]
        
        self.lstm_model = Sequential([
            LSTM(64, activation='relu', input_shape=(self.lookback_period, X.shape[1])),
            Dropout(0.2),
            LSTM(32, activation='relu'),
            Dropout(0.2),
            Dense(16, activation='relu'),
            Dense(1, activation='sigmoid')  # Binary: up/down
        ])
        
        self.lstm_model.compile(optimizer=Adam(learning_rate=0.001), 
                               loss='binary_crossentropy',
                               metrics=['accuracy'])
        
        self.lstm_model.fit(X_lstm, y_lstm, epochs=epochs, batch_size=32, 
                           validation_split=0.2, verbose=0)
        
        logger.info("LSTM training complete")
        return self.lstm_model
    
    def train_rf_model(self, X: np.ndarray, y: np.ndarray):
        """Train Random Forest for signal generation"""
        logger.info("Training Random Forest model...")
        
        self.rf_model = RandomForestRegressor(
            n_estimators=100,
            max_depth=15,
            min_samples_split=5,
            random_state=42,
            n_jobs=-1
        )
        
        self.rf_model.fit(X, y)
        logger.info("Random Forest training complete")
        return self.rf_model
    
    def generate_signals(self, data: pd.DataFrame) -> List[float]:
        """
        Generate buy/sell signals using ensemble
        
        Returns:
            List of signals (0.0-1.0 confidence)
        """
        X, _ = self.prepare_features(data)
        
        # LSTM predictions
        if self.lstm_model:
            X_lstm = np.array([X[i-self.lookback_period:i] 
                              for i in range(self.lookback_period, len(X))])
            lstm_preds = self.lstm_model.predict(X_lstm, verbose=0)
            lstm_preds = np.concatenate([np.zeros((self.lookback_period, 1)), 
                                        lstm_preds])
        else:
            lstm_preds = np.zeros((len(X), 1))
        
        # Random Forest predictions
        if self.rf_model:
            rf_preds = self.rf_model.predict(X).reshape(-1, 1)
        else:
            rf_preds = np.zeros((len(X), 1))
        
        # Ensemble: average both predictions
        signals = (lstm_preds + rf_preds) / 2
        return signals.flatten().tolist()
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> np.ndarray:
        """Calculate Relative Strength Index"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss.replace(0, 0.001)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50).values / 100
    
    def _calculate_macd(self, prices: pd.Series) -> np.ndarray:
        """Calculate MACD"""
        ema12 = prices.ewm(span=12).mean()
        ema26 = prices.ewm(span=26).mean()
        macd = ema12 - ema26
        return macd.fillna(0).values / prices.std()
    
    def _calculate_bollinger_bands(self, prices: pd.Series, period: int = 20) -> np.ndarray:
        """Calculate Bollinger Bands position"""
        sma = prices.rolling(period).mean()
        std = prices.rolling(period).std()
        upper = sma + (std * 2)
        lower = sma - (std * 2)
        
        bb_position = (prices - lower) / (upper - lower)
        return bb_position.fillna(0.5).values