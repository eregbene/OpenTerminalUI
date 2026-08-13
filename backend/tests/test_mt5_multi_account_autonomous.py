import asyncio

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, MT5MultiAccountAutonomousOrchestrator
from backend.brokers.mt5.config import MT5Config


class _FakeAdapter:
    def __init__(self, account_id: str) -> None:
        self.config = MT5Config(account_id=account_id, enabled=True, autonomous_submission_enabled=True)


class _FakeService:
    def __init__(self, account_id: str, status: str = "NO_TRADE", fail: bool = False) -> None:
        self.account_id = account_id
        self.calls: list[tuple[str, bool]] = []
        self._status = status
        self._fail = fail

    async def run_cycle(self, *, owner: str = "local", dry_run: bool = False, cycle_time=None) -> dict:
        self.calls.append((owner, dry_run))
        if self._fail:
            raise RuntimeError("boom")
        cycle_id = "MT5_M5_202608121200" if self.account_id == "demo_10k" else f"{self.account_id}:MT5_M5_202608121200"
        return {"account_id": self.account_id, "cycle_id": cycle_id, "status": self._status, "order_send_calls": 1 if self._status == "ACCEPTED" else 0, "openai_calls": 0}

    def status(self) -> dict:
        return {"account_id": self.account_id, "last_result": {"status": self._status}, "emergency_disable": False}

    def cycles(self) -> list[dict]:
        return [{"account_id": self.account_id, "cycle_id": f"{self.account_id}:C", "created_at": "2026-08-12T12:00:00+00:00"}]

    def candidates(self) -> list[dict]:
        return []

    def decisions(self) -> list[dict]:
        return []

    def trades(self) -> list[dict]:
        return []


def test_multi_account_orchestrator_runs_every_enabled_account(monkeypatch):
    services = {
        "demo_10k": _FakeService("demo_10k", "NO_TRADE"),
        "ftmo_demo_25k": _FakeService("ftmo_demo_25k", "ACCEPTED"),
        "ftmo_demo_50k": _FakeService("ftmo_demo_50k", "NO_TRADE"),
        "ftmo_demo_100k": _FakeService("ftmo_demo_100k", "TRADING_DISABLED"),
    }
    orchestrator = MT5MultiAccountAutonomousOrchestrator(default_service=services["demo_10k"])
    monkeypatch.setattr(orchestrator, "_enabled_services", lambda: services)

    result = asyncio.run(orchestrator.run_cycle(owner="test-owner", dry_run=True))

    assert result["status"] == "MULTI_ACCOUNT_CYCLE"
    assert result["evaluated_accounts"] == ["demo_10k", "ftmo_demo_25k", "ftmo_demo_50k", "ftmo_demo_100k"]
    assert result["summary"] == {
        "demo_10k": "NO_TRADE",
        "ftmo_demo_25k": "ACCEPTED",
        "ftmo_demo_50k": "NO_TRADE",
        "ftmo_demo_100k": "TRADING_DISABLED",
    }
    assert result["order_send_calls"] == 1
    assert all(service.calls == [("test-owner", True)] for service in services.values())


def test_multi_account_orchestrator_isolates_account_failure(monkeypatch):
    services = {
        "demo_10k": _FakeService("demo_10k", "NO_TRADE"),
        "ftmo_demo_25k": _FakeService("ftmo_demo_25k", fail=True),
        "ftmo_demo_50k": _FakeService("ftmo_demo_50k", "ACCEPTED"),
    }
    orchestrator = MT5MultiAccountAutonomousOrchestrator(default_service=services["demo_10k"])
    monkeypatch.setattr(orchestrator, "_enabled_services", lambda: services)

    result = asyncio.run(orchestrator.run_cycle(owner="test-owner", dry_run=True))

    assert result["summary"]["ftmo_demo_25k"] == "ERROR"
    assert result["accounts"]["ftmo_demo_25k"]["error"] == "RuntimeError"
    assert result["summary"]["ftmo_demo_50k"] == "ACCEPTED"


def test_autonomous_service_state_and_lock_keys_are_account_scoped():
    default_service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    ftmo_service = MT5AutonomousTradingService(_FakeAdapter("ftmo_demo_25k"))

    assert default_service._state_key() == "mt5_autonomous"
    assert default_service._lock_key() == "mt5_autonomous_scheduler_lock"
    assert ftmo_service._state_key() == "mt5_autonomous:ftmo_demo_25k"
    assert ftmo_service._lock_key() == "mt5_autonomous_scheduler_lock:ftmo_demo_25k"
