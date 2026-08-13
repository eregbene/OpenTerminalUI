from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from backend.brokers.mt5.config import MT5Config, mt5_config
from backend.brokers.mt5.diagnostics import assert_demo_account
from backend.brokers.mt5.exceptions import MT5UnavailableError


class MT5Client:
    def __init__(self, config: MT5Config | None = None) -> None:
        self.config = config or mt5_config()
        self._mt5: ModuleType | None = None
        self.initialized = False

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
        ok = bool(mt5.initialize(**init_kwargs))
        self.initialized = ok
        if not ok:
            raise MT5UnavailableError(f"MT5 initialize failed: {self.last_error()}")
        if self.config.login is not None and self.config.password:
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
