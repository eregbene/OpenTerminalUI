from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.core.contracts.api import APIErrorCode, APIErrorEnvelope, APIErrorPayload, BensimAPIError
from backend.core.contracts.events import EventEnvelope
from backend.core.contracts.jobs import JobRecord, JobStatus
from backend.core.contracts.market_data import DataFreshness, MarketDataStatus, ProviderHealth
from backend.core.contracts.websocket import websocket_error
from backend.core.observability import get_request_id, request_context_middleware


async def bensim_api_error_handler(request: Request, exc: BensimAPIError) -> JSONResponse:
    envelope = APIErrorEnvelope(
        error=APIErrorPayload(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=get_request_id(request),
        )
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json"))


def test_event_envelope_has_required_fields():
    event = EventEnvelope.create("market.quote.updated", source="test", payload={"symbol": "AAPL"})

    assert event.event_id
    assert event.version == 1
    assert event.timestamp
    assert event.payload["symbol"] == "AAPL"


def test_market_data_status_classifies_stale_quote():
    status = MarketDataStatus.from_quote(
        {"ts": "2020-01-01T00:00:00+00:00"},
        provider="polling",
        symbol="NASDAQ:AAPL",
        asset_class="equity",
    )

    assert status.freshness == DataFreshness.STALE
    assert status.health == ProviderHealth.OK


def test_job_record_defaults_to_queued():
    job = JobRecord(job_id="job_1", job_type="portfolio_backtest")

    assert job.status == JobStatus.QUEUED
    assert job.progress == 0.0


def test_websocket_error_envelope_is_versioned():
    payload = websocket_error("INVALID_SUBSCRIPTION", "No valid symbols", source="quotes")

    assert payload["type"] == "error"
    assert payload["event_type"] == "ws.error"
    assert payload["payload"]["code"] == "INVALID_SUBSCRIPTION"


def test_custom_api_error_uses_standard_envelope():
    app = FastAPI()
    app.middleware("http")(request_context_middleware)
    app.add_exception_handler(BensimAPIError, bensim_api_error_handler)

    @app.get("/boom")
    async def boom():
        raise BensimAPIError(APIErrorCode.MARKET_DATA_UNAVAILABLE, "No quote", status_code=503)

    response = TestClient(app).get("/boom")

    assert response.status_code == 503
    assert response.headers["x-request-id"]
    assert response.json()["error"]["code"] == "MARKET_DATA_UNAVAILABLE"
