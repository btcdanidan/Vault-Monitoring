"""SQLAlchemy ORM models."""

from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Import models after Base so they register with Base.metadata; avoid circular import.
from app.models.admin import ApiUsageLog, CostServiceConfig, CostThrottleStatus
from app.models.position import Position
from app.models.position_snapshot import PositionSnapshot
from app.models.price_history import PriceHistory
from app.models.profile import Profile
from app.models.recommendation import Recommendation, RecommendationOutcome
from app.models.risk_score_history import RiskScoreHistory
from app.models.transaction_lot import TransactionLot
from app.models.vault import Vault
from app.models.vault_concentration import VaultConcentration
from app.models.vault_metrics import VaultMetrics
from app.models.wallet import Wallet

__all__ = [
    "Base",
    "Profile",
    "Vault",
    "Wallet",
    "Position",
    "Recommendation",
    "RecommendationOutcome",
    "VaultMetrics",
    "VaultConcentration",
    "RiskScoreHistory",
    "PriceHistory",
    "TransactionLot",
    "PositionSnapshot",
    "ApiUsageLog",
    "CostServiceConfig",
    "CostThrottleStatus",
]
