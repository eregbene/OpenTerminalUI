from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from backend.brokers.mt5 import account_registry
from backend.brokers.mt5 import bridge as mt5_bridge
from backend.brokers.mt5.multi_account import config_for_profile
from backend.brokers.mt5.prop_risk import challenge_status, ftmo_2step_limits


class _Account:
    login = 123
    server = "MetaQuotes-Demo"
    company = "MetaQuotes"
    currency = "USD"
    trade_mode = 0
    balance = Decimal("25000")
    equity = Decimal("25000")
    margin = Decimal("0")
    free_margin = Decimal("25000")


def test_four_mt5_profiles_register_independently(monkeypatch):
    monkeypatch.setenv("MT5_ENABLED", "true")
    # Pinned explicitly: the real container env has these three overridden (2026-08-18 broker
    # migration to ICMarketsSC-Demo changed real balances to 35k/60k/101k without renaming the
    # internal account_id/prefix) -- this test is about the CODED DEFAULT, not today's real
    # deployed override.
    for prefix in ("25K", "50K", "100K"):
        monkeypatch.delenv(f"MT5_ACCOUNT_{prefix}_INITIAL_BALANCE", raising=False)
        monkeypatch.delenv(f"MT5_{prefix}_INITIAL_BALANCE", raising=False)

    profiles = account_registry.configured_profiles()

    assert [p.account_id for p in profiles] == ["demo_10k", "ftmo_demo_25k", "ftmo_demo_50k", "ftmo_demo_100k"]
    assert {p.expected_initial_balance for p in profiles} == {Decimal("10000"), Decimal("25000"), Decimal("50000"), Decimal("100000")}
    assert all(p.strategy_profile == "ACTIVE_MT5" for p in profiles)


def test_terminal_path_selected_per_ftmo_account(monkeypatch):
    monkeypatch.setenv("MT5_25K_TERMINAL_PATH", r"E:\MT5\Bensim-25k\terminal64.exe")
    monkeypatch.setenv("MT5_50K_TERMINAL_PATH", r"E:\MT5\Bensim-50k\terminal64.exe")
    monkeypatch.setenv("MT5_100K_TERMINAL_PATH", r"E:\MT5\Bensim-100k\terminal64.exe")

    by_id = {p.account_id: p for p in account_registry.configured_profiles()}

    assert by_id["ftmo_demo_25k"].terminal_path.endswith(r"Bensim-25k\terminal64.exe")
    assert by_id["ftmo_demo_50k"].terminal_path.endswith(r"Bensim-50k\terminal64.exe")
    assert by_id["ftmo_demo_100k"].terminal_path.endswith(r"Bensim-100k\terminal64.exe")


def test_account_specific_bridge_ports_and_config(monkeypatch):
    monkeypatch.delenv("MT5_ACCOUNT_25K_LOGIN", raising=False)
    monkeypatch.delenv("MT5_ACCOUNT_25K_SERVER", raising=False)
    monkeypatch.delenv("MT5_ACCOUNT_25K_BRIDGE_PORT", raising=False)
    # 2026-08-18: the real container env now has MT5_ACCOUNT_25K_ENABLED=false (temporarily
    # disabled to stop its portfolio-execution-monitor background loop from repeatedly disrupting
    # a live broker re-login) -- this takes priority over the MT5_25K_ENABLED alias this test
    # sets, per _env_bool_alias's fallback order, so it must be explicitly cleared here too.
    monkeypatch.delenv("MT5_ACCOUNT_25K_ENABLED", raising=False)
    monkeypatch.setenv("MT5_25K_ENABLED", "true")
    monkeypatch.setenv("MT5_25K_LOGIN", "250001")
    monkeypatch.setenv("MT5_25K_SERVER", "FTMO-Demo")
    monkeypatch.setenv("MT5_25K_BRIDGE_PORT", "8871")
    profile = account_registry.profile_by_id("ftmo_demo_25k")

    cfg = config_for_profile(profile)

    assert cfg.enabled is True
    assert cfg.login == 250001
    assert cfg.server == "FTMO-Demo"
    assert cfg.path.endswith(r"Bensim-25k\terminal64.exe")
    assert cfg.bridge_port == 8871
    assert cfg.account_mode == "DEMO"


def test_enabled_ftmo_profile_fails_closed_without_expected_identity(monkeypatch):
    monkeypatch.delenv("MT5_ACCOUNT_25K_ENABLED", raising=False)
    monkeypatch.setenv("MT5_25K_ENABLED", "true")
    monkeypatch.delenv("MT5_ACCOUNT_25K_LOGIN", raising=False)
    monkeypatch.delenv("MT5_ACCOUNT_25K_SERVER", raising=False)
    monkeypatch.delenv("MT5_25K_LOGIN", raising=False)
    monkeypatch.delenv("MT5_25K_SERVER", raising=False)

    health = account_registry.profile_health(account_registry.profile_by_id("ftmo_demo_25k"))

    assert health["health"] == "BLOCKED"
    assert "EXPECTED_LOGIN_NOT_CONFIGURED" in health["blockers"]
    assert "EXPECTED_SERVER_NOT_CONFIGURED" in health["blockers"]


def test_wrong_login_gets_execution_context_mismatch(monkeypatch):
    monkeypatch.setenv("MT5_25K_LOGIN", "999")
    monkeypatch.setenv("MT5_25K_SERVER", "MetaQuotes-Demo")
    profile = account_registry.profile_by_id("ftmo_demo_25k")

    blockers = account_registry.validate_profile_account(profile, _Account())

    assert "ACCOUNT_EXECUTION_CONTEXT_MISMATCH" in blockers


def test_ftmo_limit_math_for_25_50_100k():
    assert ftmo_2step_limits(Decimal("25000"))["daily_loss_limit"] == "1250.00"
    assert ftmo_2step_limits(Decimal("50000"))["daily_loss_limit"] == "2500.00"
    assert ftmo_2step_limits(Decimal("100000"))["daily_loss_limit"] == "5000.00"
    assert ftmo_2step_limits(Decimal("25000"))["max_loss_limit"] == "2500.00"
    assert ftmo_2step_limits(Decimal("50000"))["max_loss_limit"] == "5000.00"
    assert ftmo_2step_limits(Decimal("100000"))["max_loss_limit"] == "10000.00"
    assert ftmo_2step_limits(Decimal("100000"))["profit_target"] == "10000.00"


def test_daily_loss_uses_equity_including_floating_pnl():
    status = challenge_status(
        initial_balance=Decimal("25000"),
        daily_baseline_equity=Decimal("25000"),
        current_balance=Decimal("25000"),
        current_equity=Decimal("24000"),
    )

    assert status["daily_loss_used"] == "1000.00"
    assert status["daily_loss_remaining"] == "250.00"
    assert status["challenge_status"] == "ACTIVE"


def test_prop_limit_breach_blocks_new_entries_but_status_is_account_scoped():
    status = challenge_status(
        initial_balance=Decimal("25000"),
        daily_baseline_equity=Decimal("25000"),
        current_balance=Decimal("25000"),
        current_equity=Decimal("23750"),
    )

    assert status["challenge_status"] == "DAILY_LOSS_BREACHED"
    assert status["new_entries_allowed"] is False
    assert "PROP_DAILY_LOSS_BREACHED" in status["entry_blockers"]


def test_internal_buffer_blocks_before_official_failure_boundary():
    status = challenge_status(
        initial_balance=Decimal("25000"),
        daily_baseline_equity=Decimal("25000"),
        current_balance=Decimal("25000"),
        current_equity=Decimal("24000"),
        daily_entry_block_utilization=Decimal("0.80"),
    )

    assert status["official_daily_loss_limit"] == "1250.00"
    assert status["internal_daily_entry_limit"] == "1000.00"
    assert "PROP_DAILY_LOSS_BUFFER" in status["entry_blockers"]


def test_account_registry_reads_prompt_env_aliases_and_masks_login(monkeypatch):
    monkeypatch.setenv("MT5_ACCOUNT_25K_ENABLED", "true")
    monkeypatch.setenv("MT5_ACCOUNT_25K_TERMINAL_PATH", r"E:\MT5\Bensim-25k\terminal64.exe")
    monkeypatch.setenv("MT5_ACCOUNT_25K_LOGIN", "250001234")
    monkeypatch.setenv("MT5_ACCOUNT_25K_SERVER", "FTMO-Demo")

    health = account_registry.profile_health(account_registry.profile_by_id("ftmo_demo_25k"))

    assert health["expected_login_configured"] is True
    assert health["expected_login_masked"] == "25***34"
    assert "expected_login" not in health
    assert health["expected_server"] == "FTMO-Demo"


class _BridgeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, account: dict):
        self.account = account
        self.order_send_calls = 0

    def symbols_get(self, group=None):
        return []

    def order_check(self, request):
        return {"retcode": 0}

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        return -25.0

    def order_send(self, request):
        self.order_send_calls += 1
        return {"retcode": 10009, "order": 1}


class _BridgeClient:
    native = None

    def __init__(self, config):
        self.config = config
        self.native = self.__class__.native

    def connect(self):
        return {"initialized": True, "account": self.account_info()}

    def ensure_ready(self):
        return self.native

    def account_info(self):
        return dict(self.native.account)

    def terminal_info(self):
        return {"connected": True}

    def version(self):
        return (5, 0, "test")

    def last_error(self):
        return None

    def shutdown(self):
        return None


def test_bridge_worker_rejects_wrong_requested_account_before_mutation(monkeypatch):
    monkeypatch.delenv("MT5_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("MT5_25K_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("MT5_ACCOUNT_25K_BRIDGE_API_KEY", raising=False)
    monkeypatch.setenv("MT5_ACCOUNT_25K_ENABLED", "true")
    monkeypatch.setenv("MT5_ACCOUNT_25K_LOGIN", "250001")
    monkeypatch.setenv("MT5_ACCOUNT_25K_SERVER", "FTMO-Demo")
    _BridgeClient.native = _BridgeMT5({"login": 250001, "server": "FTMO-Demo", "trade_mode": 0, "balance": 25000, "equity": 25000})
    monkeypatch.setattr("backend.brokers.mt5.client.MT5Client", _BridgeClient)

    app = mt5_bridge.create_app("ftmo_demo_25k")
    response = TestClient(app).post(
        "/order-send",
        json={"account_id": "ftmo_demo_50k", "request": {"symbol": "EURUSD", "volume": 1}},
        headers={"X-MT5-Account-ID": "ftmo_demo_50k"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ACCOUNT_EXECUTION_CONTEXT_MISMATCH"
    assert _BridgeClient.native.order_send_calls == 0


def test_bridge_worker_revalidates_actual_login_before_mutation(monkeypatch):
    monkeypatch.delenv("MT5_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("MT5_25K_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("MT5_ACCOUNT_25K_BRIDGE_API_KEY", raising=False)
    monkeypatch.setenv("MT5_ACCOUNT_25K_ENABLED", "true")
    monkeypatch.setenv("MT5_ACCOUNT_25K_LOGIN", "250001")
    monkeypatch.setenv("MT5_ACCOUNT_25K_SERVER", "FTMO-Demo")
    _BridgeClient.native = _BridgeMT5({"login": 999999, "server": "FTMO-Demo", "trade_mode": 0, "balance": 25000, "equity": 25000})
    monkeypatch.setattr("backend.brokers.mt5.client.MT5Client", _BridgeClient)

    app = mt5_bridge.create_app("ftmo_demo_25k")
    response = TestClient(app).post(
        "/order-send",
        json={"account_id": "ftmo_demo_25k", "request": {"symbol": "EURUSD", "volume": 1}},
        headers={"X-MT5-Account-ID": "ftmo_demo_25k"},
    )

    assert response.status_code == 409
    assert "ACCOUNT_EXECUTION_CONTEXT_MISMATCH" in response.text
    assert _BridgeClient.native.order_send_calls == 0


def test_bridge_order_send_reports_real_mt5_call_timing(monkeypatch):
    """Phase 23 (corpus-expansion-throughput directive): order_send p95 latency is ~3.8s
    end-to-end (real ExecutionOrderORM data); this timing lets the bridge distinguish how much of
    that is the actual MetaTrader5.order_send() call versus network/FastAPI overhead the bridge
    can't see. Observability only -- must never affect the order response itself."""
    monkeypatch.delenv("MT5_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("MT5_25K_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("MT5_ACCOUNT_25K_BRIDGE_API_KEY", raising=False)
    monkeypatch.setenv("MT5_ACCOUNT_25K_ENABLED", "true")
    monkeypatch.setenv("MT5_ACCOUNT_25K_LOGIN", "250001")
    monkeypatch.setenv("MT5_ACCOUNT_25K_SERVER", "FTMO-Demo")
    _BridgeClient.native = _BridgeMT5({"login": 250001, "server": "FTMO-Demo", "trade_mode": 0, "balance": 25000, "equity": 25000})
    monkeypatch.setattr("backend.brokers.mt5.client.MT5Client", _BridgeClient)

    app = mt5_bridge.create_app("ftmo_demo_25k")
    response = TestClient(app).post(
        "/order-send",
        json={"account_id": "ftmo_demo_25k", "request": {"symbol": "EURUSD", "volume": 1}},
        headers={"X-MT5-Account-ID": "ftmo_demo_25k"},
    )

    assert response.status_code == 200
    body = response.json()
    assert _BridgeClient.native.order_send_calls == 1
    assert body["retcode"] == 10009  # the underlying order response is unchanged by the added timing
    assert "_bridge_mt5_call_ms" in body
    assert isinstance(body["_bridge_mt5_call_ms"], (int, float))
    assert body["_bridge_mt5_call_ms"] >= 0
