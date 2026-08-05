class PortfolioError(ValueError):
  code = "PORTFOLIO_ERROR"


class AllocationValidationError(PortfolioError):
  code = "ALLOCATION_VALIDATION_FAILED"


class ImmutableSnapshotError(PortfolioError):
  code = "IMMUTABLE_SNAPSHOT"
