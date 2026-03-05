"""Controls portfolio exposure and leverage."""
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class ExposureController:
    def __init__(self, max_exposure: float = 1.0):
        self.max_exposure = max_exposure
        self.current_exposure = 0.0
    
    def can_add_position(self, size: float) -> bool:
        return (self.current_exposure + size) <= self.max_exposure
    
    def add_exposure(self, size: float):
        self.current_exposure += size
    
    def remove_exposure(self, size: float):
        self.current_exposure = max(0, self.current_exposure - size)
