from __future__ import annotations

import importlib
import logging
import os
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Any

from backend.brokers.mt5.config import MT5Config, mt5_config
from backend.brokers.mt5.diagnostics import assert_demo_account
from backend.brokers.mt5.exceptions import MT5UnavailableError

logger = logging.getLogger(__name__)

# MT5's raw "time" fields (candles/positions/history deals&orders/ticks) are the broker
# SERVER's wall clock, not UTC -- a well-documented MT5 quirk. EURUSD is used as the reference
# symbol for live-offset detection because every forex broker quotes it continuously, so a tick
# is always available regardless of which symbols a given account happens to trade.
_BROKER_OFFSET_REFERENCE_SYMBOL = "EURUSD"
_BROKER_OFFSET_CACHE_SECONDS_DEFAULT = 3600


class MT5Client:
    def __init__(self, config: MT5Config | None = None) -> None:
        self.config = config or mt5_config()
        self._mt5: ModuleType | None = None
        self.initialized = False
        self._broker_utc_offset: timedelta | None = None
        self._broker_utc_offset_computed_at: datetime | None = None

    @property
    def mt5(self) -> ModuleType:
        if self._mt5 is None:
            try:
                self._mt5 = importlib.import_module("MetaTrader5")
            except Exception as exc:
                if self.config.bridge_api_key:
                    from backend.brokers.mt5.bridge import MT5BridgeClient

                    self._mt5 = MT5BridgeClient(self.config)  # type: ignore[assignment]
                    return self._mt5
                raise MT5UnavailableError(f"MetaTrader5 package is not installed or cannot load: {exc}") from exc
        return self._mt5

    def connect(self) -> dict[str, Any]:
        if not self.config.enabled:
            raise MT5UnavailableError("MT5_ENABLED is false")
        mt5 = self.mt5
        init_kwargs: dict[str, Any] = {}
        if self.config.path:
            init_kwargs["path"] = self.config.path
        init_kwargs["timeout"] = self.config.timeout_ms
        init_kwargs["portable"] = self.config.portable
        credentials_passed_to_initialize = self.config.login is not None and self.config.password
        if credentials_passed_to_initialize:
            # mt5.initialize() accepts login/password/server directly and performs the full
            # connect+auth handshake atomically -- required for accounts whose terminal instance
            # isn't already GUI-authenticated (a bare initialize() then fails with a generic
            # "Authorization failed" before a separate mt5.login() is ever reached).
            init_kwargs["login"] = self.config.login
            init_kwargs["password"] = self.config.password
            if self.config.server:
                init_kwargs["server"] = self.config.server
        ok = bool(mt5.initialize(**init_kwargs))
        self.initialized = ok
        if not ok:
            raise MT5UnavailableError(f"MT5 initialize failed: {self.last_error()}")
        if self.config.login is not None and self.config.password and not credentials_passed_to_initialize:
            login_kwargs = {"login": self.config.login}
            login_kwargs["password"] = self.config.password
            if self.config.server:
                login_kwargs["server"] = self.config.server
            if not bool(mt5.login(**login_kwargs)):
                raise MT5UnavailableError(f"MT5 login failed: {self.last_error()}")
        account = mt5.account_info()
        if account is None:
            raise MT5UnavailableError(f"MT5 account_info unavailable: {self.last_error()}")
        data = account._asdict() if hasattr(account, "_asdict") else dict(account)
        assert_demo_account(self.config, data)
        return {"initialized": True, "account": data}

    def shutdown(self) -> None:
        if self._mt5 is not None and self.initialized:
            self._mt5.shutdown()
        self.initialized = False

    def ensure_ready(self) -> ModuleType:
        if not self.initialized:
            self.connect()
        return self.mt5

    def terminal_info(self) -> dict[str, Any] | None:
        info = self.ensure_ready().terminal_info()
        return info._asdict() if hasattr(info, "_asdict") else dict(info) if info else None

    def version(self) -> tuple[int, int, str] | None:
        version = self.ensure_ready().version()
        return tuple(version) if version else None

    def account_info(self) -> dict[str, Any] | None:
        info = self.ensure_ready().account_info()
        return info._asdict() if hasattr(info, "_asdict") else dict(info) if info else None

    def last_error(self) -> tuple[int, str] | None:
        try:
            err = self.mt5.last_error()
            return tuple(err) if err else None
        except Exception:
            return None

    def broker_utc_offset(self) -> timedelta:
        """2026-08-19: root cause of a ~3h skew found in every MT5-sourced timestamp (candles,
        positions, history deals/orders, quotes) -- confirmed in production by 529 of 534
        tracked adaptive-management positions showing closed_detected_at earlier than opened_at,
        a chronological impossibility, because opened_at (parsed from MT5's raw epoch AS IF it
        were UTC) was silently ~3h ahead of true UTC while closed_detected_at (Python's own
        utcnow()) was correct. This broker's offset (~+3h) matches IC Markets' EEST summer
        server time.

        Detected dynamically, NEVER hardcoded -- the true offset shifts with DST twice a year
        (and would differ entirely for a different broker), so a fixed constant would silently
        go stale again. Computed by comparing a fresh reference tick's raw epoch against true
        UTC, rounded to the nearest 15 minutes (real-world UTC offsets always land on a 15/30/
        60-minute boundary; rounding absorbs request/network latency so it doesn't leak into the
        computed offset). Cached for MT5_BROKER_UTC_OFFSET_CACHE_SECONDS (default 1h) so this
        self-corrects across DST transitions on its own without a redeploy, while not hitting
        the broker on every single timestamp parse. Fails open (returns the previous cached
        value, or timedelta(0) if never successfully computed) on any error -- a broker outage
        or an unselected reference symbol must not block trading, it should just leave
        timestamps unadjusted until the next successful detection."""
        now = datetime.now(timezone.utc)
        try:
            cache_seconds = int(os.getenv("MT5_BROKER_UTC_OFFSET_CACHE_SECONDS", str(_BROKER_OFFSET_CACHE_SECONDS_DEFAULT)))
        except Exception:
            cache_seconds = _BROKER_OFFSET_CACHE_SECONDS_DEFAULT
        if (
            self._broker_utc_offset is not None
            and self._broker_utc_offset_computed_at is not None
            and (now - self._broker_utc_offset_computed_at).total_seconds() < cache_seconds
        ):
            return self._broker_utc_offset
        try:
            mt5 = self.ensure_ready()
            try:
                mt5.symbol_select(_BROKER_OFFSET_REFERENCE_SYMBOL, True)
            except Exception:
                pass  # best-effort -- symbol_info_tick below still works if already selected
            tick = mt5.symbol_info_tick(_BROKER_OFFSET_REFERENCE_SYMBOL)
            if tick is None:
                raise MT5UnavailableError("no reference tick available for broker UTC offset detection")
            data = tick._asdict() if hasattr(tick, "_asdict") else dict(tick)
            raw_time = data.get("time")
            if not raw_time:
                raise MT5UnavailableError("reference tick has no time field")
            broker_reported = datetime.fromtimestamp(int(raw_time), tz=timezone.utc)
            raw_offset_minutes = (broker_reported - now).total_seconds() / 60.0
            offset = timedelta(minutes=round(raw_offset_minutes / 15.0) * 15)
        except Exception as exc:
            logger.warning("MT5 broker UTC offset detection failed, keeping previous value: %s", exc.__class__.__name__)
            return self._broker_utc_offset or timedelta(0)
        self._broker_utc_offset = offset
        self._broker_utc_offset_computed_at = now
        return offset
