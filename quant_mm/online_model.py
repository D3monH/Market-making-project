from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LinearRegression

from .gate_stream import BookTicker
from .microstructure import RollingTradeFlow, book_imbalance


@dataclass(frozen=True)
class OnlineLinearConfig:
    horizon_ms: int = 5000
    min_sample_interval_ms: int = 500
    train_window: int = 2000
    min_train_samples: int = 200
    retrain_every: int = 20
    max_abs_prediction_bps: float = 10.0


class OnlineMicroLinearModel:
    """Causal online linear regression for short-horizon fair-price prediction."""

    def __init__(self, cfg: OnlineLinearConfig):
        self.cfg = cfg
        self.pending = deque()
        self.x_train = deque(maxlen=cfg.train_window)
        self.y_train = deque(maxlen=cfg.train_window)
        self.model: LinearRegression | None = None
        self.new_labels_since_fit = 0
        self.last_sample_ms = 0
        self.prev_mid: float | None = None
        self.last_return = 0.0

    @staticmethod
    def _feature_vector(book: BookTicker, flow: RollingTradeFlow, recent_return: float, volatility: float) -> np.ndarray:
        spread_bps = (book.ask - book.bid) / book.mid * 10_000.0 if book.mid > 0 else 0.0
        return np.array(
            [book_imbalance(book), flow.imbalance, spread_bps, recent_return * 10_000.0, volatility * 10_000.0],
            dtype=float,
        )

    def observe(self, book: BookTicker, flow: RollingTradeFlow, volatility: float) -> float:
        recent_return = 0.0
        if self.prev_mid and self.prev_mid > 0:
            recent_return = book.mid / self.prev_mid - 1.0
        self.prev_mid = book.mid
        self.last_return = recent_return

        cutoff = book.timestamp_ms - self.cfg.horizon_ms
        while self.pending and self.pending[0][0] <= cutoff:
            _, start_mid, features = self.pending.popleft()
            label = book.mid / start_mid - 1.0
            self.x_train.append(features)
            self.y_train.append(label)
            self.new_labels_since_fit += 1

        if (
            len(self.y_train) >= self.cfg.min_train_samples
            and (self.model is None or self.new_labels_since_fit >= self.cfg.retrain_every)
        ):
            self.model = LinearRegression()
            self.model.fit(np.vstack(self.x_train), np.asarray(self.y_train))
            self.new_labels_since_fit = 0

        features_now = self._feature_vector(book, flow, recent_return, volatility)
        prediction = 0.0
        if self.model is not None:
            prediction = float(self.model.predict(features_now.reshape(1, -1))[0])
            cap = self.cfg.max_abs_prediction_bps / 10_000.0
            prediction = float(np.clip(prediction, -cap, cap))

        if book.timestamp_ms - self.last_sample_ms >= self.cfg.min_sample_interval_ms:
            self.pending.append((book.timestamp_ms, book.mid, features_now.copy()))
            self.last_sample_ms = book.timestamp_ms
        return prediction

    @property
    def train_samples(self) -> int:
        return len(self.y_train)

    @property
    def fitted(self) -> bool:
        return self.model is not None
