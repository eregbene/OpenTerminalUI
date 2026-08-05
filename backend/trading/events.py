from backend.trading.models import AuditRecord


def trading_event(event_type: str, **payload: object) -> AuditRecord:
    return AuditRecord(event_type=event_type, entity_type="trading_event", payload=dict(payload))
