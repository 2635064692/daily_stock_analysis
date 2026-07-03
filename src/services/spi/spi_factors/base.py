from abc import ABC, abstractmethod


class SpiFactor(ABC):
    name: str
    weight: float

    @abstractmethod
    def compute(self, ema_series: dict, last_close: float) -> float:
        """Returns a normalized float. Range defined per factor."""
