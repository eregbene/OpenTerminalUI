from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.brokers.models import BrokerConnectionState, BrokerHealthState, BrokerEnvironment


class BrokerHealth(BaseModel):
    broker: str
    state: BrokerHealthState
    connection_state: BrokerConnectionState
    environment: BrokerEnvironment
    connected_host: str | None = None
    port: int | None = None
    client_id: int | None = None
    server_version: str | None = None
    session_start: datetime | None = None
    last_heartbeat: datetime | None = None
    last_successful_request: datetime | None = None
    last_error: str | None = None
    reconnect_count: int = 0
    next_reconnect_time: datetime | None = None
    account_verification_status: str = "UNVERIFIED"
    order_submission_status: str = "DISABLED"
