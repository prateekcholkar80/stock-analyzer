from datetime import datetime

from pydantic import BaseModel


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int