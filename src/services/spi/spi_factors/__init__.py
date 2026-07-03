from .base import SpiFactor
from .ema_direction import EmaDirectionFactor
from .alignment import AlignmentFactor
from .separation import SeparationFactor
from .compression import CompressionFactor

REGISTRY: dict = {
    "ema_direction": EmaDirectionFactor(weight=0.30),
    "alignment":     AlignmentFactor(weight=0.30),
    "separation":    SeparationFactor(weight=0.25),
    "compression":   CompressionFactor(weight=0.15),
}
