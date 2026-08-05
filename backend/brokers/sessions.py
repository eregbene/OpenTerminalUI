from __future__ import annotations

from backend.brokers.health import BrokerHealth
from backend.brokers.models import BrokerConnectionState, BrokerEnvironment, BrokerHealthState, now_utc


class BrokerSession:
    def __init__(self, broker: str, environment: BrokerEnvironment = BrokerEnvironment.UNVERIFIED) -> None:
        self.broker = broker
        self.environment = environment
        self.state = BrokerConnectionState.DISCONNECTED
        self.reconnect_count = 0
        self.last_error: str | None = None
        self.session_start = None
        self.last_heartbeat = None
        self.last_successful_request = None
        self.server_version: str | None = None
        self.account_verification_status = "UNVERIFIED"
        self.order_submission_status = "DISABLED"

    def connect(self, *, server_version: str, verified: bool, order_enabled: bool) -> None:
        self.state = BrokerConnectionState.CONNECTED if verified else BrokerConnectionState.BLOCKED
        self.environment = BrokerEnvironment.PAPER if verified else BrokerEnvironment.UNVERIFIED
        self.server_version = server_version
        self.session_start = now_utc()
        self.last_heartbeat = self.session_start
        self.last_successful_request = self.session_start
        self.account_verification_status = "PAPER_VERIFIED" if verified else "BROKER_ENVIRONMENT_UNVERIFIED"
        self.order_submission_status = "ENABLED" if verified and order_enabled else "DISABLED"

    def disconnect(self) -> None:
        self.state = BrokerConnectionState.DISCONNECTED
        self.order_submission_status = "DISABLED"

    def reconnecting(self) -> None:
        self.state = BrokerConnectionState.RECONNECTING
        self.reconnect_count += 1

    def fail(self, code: str) -> None:
        self.state = BrokerConnectionState.FAILED
        self.last_error = code
        self.order_submission_status = "DISABLED"

    def health(self, *, host: str, port: int, client_id: int) -> BrokerHealth:
        state = (
            BrokerHealthState.HEALTHY
            if self.state == BrokerConnectionState.CONNECTED
            else BrokerHealthState.BLOCKED
            if self.state == BrokerConnectionState.BLOCKED
            else BrokerHealthState.DISCONNECTED
        )
        return BrokerHealth(
            broker=self.broker,
            state=state,
            connection_state=self.state,
            environment=self.environment,
            connected_host=host,
            port=port,
            client_id=client_id,
            server_version=self.server_version,
            session_start=self.session_start,
            last_heartbeat=self.last_heartbeat,
            last_successful_request=self.last_successful_request,
            last_error=self.last_error,
            reconnect_count=self.reconnect_count,
            account_verification_status=self.account_verification_status,
            order_submission_status=self.order_submission_status,
        )
