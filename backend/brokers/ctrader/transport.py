"""Bridges spotware/OpenApiPy's Twisted-based Client into Bensim's asyncio world.

Why depend on the official SDK rather than hand-rolling a raw asyncio TCP+Protobuf client: the
wire framing (length-prefixed protobuf envelopes) and reconnection handling are simple to get
WRONG in a way that fails silently or intermittently against a real server -- exactly the kind of
subtle protocol bug this project's "never guess, never rebuild what's already correct" discipline
exists to avoid (see Phase 1's symbol_spec()/BrokerSymbolSpec precedent). OpenApiPy (MIT license)
is Spotware's own, officially maintained implementation of this exact wire protocol -- reused, not
reimplemented.

Twisted's `reactor` is a process-wide singleton (only one can run per process), so this module
runs it once, in a single dedicated background thread, for the process's lifetime. A single
CTraderTransport's ONE TCP connection can still authenticate MULTIPLE trader accounts over it
(cTrader's own protocol: repeat ProtoOAAccountAuthReq with different ctidTraderAccountId values on
the same connection) -- this is the intended path for eventual multi-account cTrader support
(Account Registry -> Broker Resolver -> Broker Adapter -> Broker Account), not "one reactor per
account". Only ONE CTraderTransport should ever be constructed per process.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Callable

from backend.brokers.ctrader.exceptions import CTraderUnavailableError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 20.0


class CTraderTransport:
    def __init__(self, *, host: str, port: int, message_callback: Callable[[Any], None] | None = None) -> None:
        self._host = host
        self._port = port
        self._external_message_callback = message_callback
        self._client: Any = None
        self._reactor: Any = None
        self._thread: threading.Thread | None = None
        self._connected_event = threading.Event()
        self._disconnected_event = threading.Event()
        self._last_disconnect_reason: str | None = None
        self._started = False

    @property
    def is_connected(self) -> bool:
        return self._connected_event.is_set() and not self._disconnected_event.is_set()

    def start(self) -> None:
        """Idempotent -- calling twice on an already-started transport is a no-op (safe to call
        from connect()/reconnect() logic without the caller tracking state itself)."""
        if self._started:
            return
        try:
            from twisted.internet import reactor as _reactor
            from ctrader_open_api import Client, TcpProtocol
        except ImportError as exc:
            raise CTraderUnavailableError(f"ctrader-open-api / Twisted not installed: {exc}") from exc

        self._reactor = _reactor
        self._client = Client(self._host, self._port, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)

        self._thread = threading.Thread(target=self._run_reactor, name="ctrader-twisted-reactor", daemon=True)
        self._thread.start()
        self._started = True

    def _run_reactor(self) -> None:
        try:
            self._client.startService()
            self._reactor.run(installSignalHandlers=False)
        except Exception:
            logger.exception("cTrader Twisted reactor thread crashed")

    def _on_connected(self, _client: Any) -> None:
        self._disconnected_event.clear()
        self._connected_event.set()
        logger.info("cTrader transport connected host=%s port=%s", self._host, self._port)

    def _on_disconnected(self, _client: Any, reason: Any = None) -> None:
        self._last_disconnect_reason = str(reason) if reason is not None else None
        self._connected_event.clear()
        self._disconnected_event.set()
        logger.warning("cTrader transport disconnected reason=%s", self._last_disconnect_reason)

    def _on_message(self, _client: Any, message: Any) -> None:
        if self._external_message_callback is not None:
            try:
                self._external_message_callback(message)
            except Exception:
                logger.exception("cTrader message_callback raised")

    async def send(self, message: Any, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> Any:
        """Sends a protobuf request message on the Twisted reactor thread and awaits its
        response, bridged back into the calling asyncio event loop. Raises CTraderUnavailableError
        on timeout or if the transport was never started/is disconnected."""
        if not self._started or self._client is None:
            raise CTraderUnavailableError("cTrader transport not started -- call start()/connect() first")

        loop = asyncio.get_running_loop()
        py_future: concurrent.futures.Future = concurrent.futures.Future()

        def _do_send() -> None:
            try:
                deferred = self._client.send(message)
            except Exception as exc:  # pragma: no cover - defensive, Client.send() itself rarely raises synchronously
                if not py_future.done():
                    py_future.set_exception(exc)
                return
            deferred.addCallbacks(
                lambda result: py_future.done() or py_future.set_result(result),
                lambda failure: py_future.done() or py_future.set_exception(CTraderUnavailableError(str(failure))),
            )

        self._reactor.callFromThread(_do_send)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(py_future, loop=loop), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CTraderUnavailableError(f"cTrader request timed out after {timeout}s") from exc

    def stop(self) -> None:
        if self._reactor is not None and self._started:
            try:
                self._reactor.callFromThread(self._reactor.stop)
            except Exception:
                logger.exception("failed to stop cTrader Twisted reactor cleanly")
        self._started = False
        self._connected_event.clear()
