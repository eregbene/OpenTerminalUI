from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.portfolio.errors import AllocationValidationError
from backend.portfolio.models import AllocationType, PortfolioStatus


@dataclass(frozen=True)
class AllocationRequest:
  portfolio_status: str
  deployment_approved: bool
  account_paper: bool
  portfolio_equity: float
  allocation_type: str
  allocation_amount: float
  max_gross_exposure: float
  max_net_exposure: float
  max_daily_loss: float
  max_drawdown: float
  emergency_blocked: bool = False
  effective_date: datetime | None = None
  expiry_date: datetime | None = None


def validate_allocation(req: AllocationRequest) -> dict:
  errors: list[str] = []
  if req.portfolio_status != PortfolioStatus.ACTIVE.value:
    errors.append("portfolio must be ACTIVE")
  if not req.deployment_approved:
    errors.append("strategy deployment must be approved")
  if not req.account_paper:
    errors.append("only paper accounts may be attached in Phase 12")
  if req.emergency_blocked:
    errors.append("portfolio emergency block is active")
  if req.allocation_type not in {item.value for item in AllocationType}:
    errors.append("unsupported allocation type")
  if req.allocation_type != AllocationType.OBSERVATION_ONLY.value and req.allocation_amount <= 0:
    errors.append("allocation amount must be positive")
  if req.allocation_type == AllocationType.PORTFOLIO_PERCENT.value and req.allocation_amount > 100:
    errors.append("portfolio percent allocation cannot exceed 100")
  if req.allocation_type == AllocationType.FIXED_CAPITAL.value and req.allocation_amount > req.portfolio_equity:
    errors.append("allocation exceeds portfolio equity")
  if req.max_gross_exposure <= 0 or req.max_net_exposure < 0:
    errors.append("exposure limits must be explicit")
  if req.max_daily_loss <= 0 or req.max_drawdown <= 0:
    errors.append("loss and drawdown limits must be explicit")
  if req.expiry_date and req.effective_date and req.expiry_date <= req.effective_date:
    errors.append("expiry must be after effective date")
  if errors:
    raise AllocationValidationError("; ".join(errors))
  return {
    "valid": True,
    "validated_at": datetime.now(timezone.utc).isoformat(),
    "paper_only": True,
    "requires_authorization": True,
  }
