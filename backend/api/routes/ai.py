from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.ai_provider.base import ProviderRequest
from backend.ai_provider.budgets import budget_manager
from backend.ai_provider.citations import citations_for_bundle
from backend.ai_provider.conversations import conversation_store
from backend.ai_provider.prompt_builder import build_prompt
from backend.ai_provider.provider_registry import provider_registry
from backend.ai_provider.resilience import circuit_breaker
from backend.ai_provider.streaming import sse
from backend.ai_provider.token_budget import enforce_prompt_budget
from backend.ai_provider.usage import usage_ledger
from backend.ai_provider.validation import INSUFFICIENT, validate_output
from backend.ai_assistant import AIAssistantService, ExplanationDomain, get_ai_assistant_service
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.bundles import bundle_service
from backend.ai_assistant.security import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    SafeAPIError,
    bounded_json_bytes,
    rate_limiter,
    sanitize_value,
    validate_identifier,
)
from backend.auth.deps import get_current_user
from backend.models.user import User
from backend.services.ai_research_assistant import AIResearchAssistantService, get_ai_research_assistant_service
from backend.services.ai_service import AIQueryService, get_ai_query_service
from backend.ai_secrets import secret_registry


router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/query", response_model=Dict[str, Any])
async def ai_query(
    request: Request,
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    service: AIQueryService = Depends(get_ai_query_service),
):
    _guard_request(request, payload, current_user, "query")
    query_text = payload.get("query")
    context = payload.get("context", {})
    if not query_text:
        raise _safe_http(422, "INVALID_REFERENCE", "invalid reference", _correlation_id(request))
    data = await service.query(str(current_user.id), query_text, context)
    return _bounded_response(data, _correlation_id(request))


@router.post("/research-brief", response_model=Dict[str, Any])
async def ai_research_brief(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIResearchAssistantService = Depends(get_ai_research_assistant_service),
):
    correlation_id = _correlation_id(request)
    _guard_request(request, payload, current_user, "research_brief")
    symbol = str(payload.get("symbol") or "").strip()
    if not symbol:
        raise _safe_http(422, "INVALID_REFERENCE", "invalid reference", correlation_id)
    try:
        result = await service.build_research_brief(
            symbol=validate_identifier(symbol),
            horizon=str(payload.get("horizon") or "swing")[:40],
            question=payload.get("question"),
            context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
        )
        return _bounded_response(result, correlation_id)
    except SafeAPIError as exc:
        raise _safe_http(exc.status_code, exc.code, exc.message, exc.correlation_id) from exc
    except ValueError as exc:
        raise _safe_http(422, "INVALID_REFERENCE", "invalid reference", correlation_id) from exc


def _explain(
    domain: ExplanationDomain,
    payload: Dict[str, Any],
    request: Request,
    current_user: User,
    service: AIAssistantService,
) -> Dict[str, Any]:
    correlation_id = _correlation_id(request)
    _guard_request(request, payload, current_user, f"explain:{domain.value}")
    try:
        result = service.explain(
            domain=domain,
            payload=payload,
            user=current_user,
            endpoint=str(request.url.path),
            correlation_id=correlation_id,
        )
        return _bounded_response(result, correlation_id)
    except LookupError as exc:
        raise _safe_http(404, "NOT_FOUND", "not found", correlation_id) from exc
    except PermissionError as exc:
        raise _safe_http(403, "FORBIDDEN", "forbidden", correlation_id) from exc
    except SafeAPIError as exc:
        raise _safe_http(exc.status_code, exc.code, exc.message, exc.correlation_id) from exc
    except (KeyError, ValueError) as exc:
        raise _safe_http(422, "INVALID_REFERENCE", "invalid reference", correlation_id) from exc


def _assistant_action(
    payload: Dict[str, Any],
    request: Request,
    current_user: User,
    service: AIAssistantService,
    action: str,
) -> Dict[str, Any]:
    correlation_id = _correlation_id(request)
    _guard_request(request, payload, current_user, f"evidence:{action}")
    try:
        if action == "retrieve":
            result = service.retrieve_evidence(
                payload=payload,
                user=current_user,
                endpoint=str(request.url.path),
                correlation_id=correlation_id,
            )
            return _bounded_response(result, correlation_id)
        if action == "lineage":
            result = service.trace_lineage(
                payload=payload,
                user=current_user,
                endpoint=str(request.url.path),
                correlation_id=correlation_id,
            )
            return _bounded_response(result, correlation_id)
    except LookupError as exc:
        raise _safe_http(404, "NOT_FOUND", "not found", correlation_id) from exc
    except PermissionError as exc:
        raise _safe_http(403, "FORBIDDEN", "forbidden", correlation_id) from exc
    except SafeAPIError as exc:
        raise _safe_http(exc.status_code, exc.code, exc.message, exc.correlation_id) from exc
    except ValueError as exc:
        raise _safe_http(422, "INVALID_REFERENCE", "invalid reference", correlation_id) from exc
    raise _safe_http(400, "BAD_REQUEST", "bad request", correlation_id)


@router.post("/explain/strategy", response_model=Dict[str, Any])
async def explain_strategy(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _explain(ExplanationDomain.STRATEGY, payload, request, current_user, service)


@router.post("/explain/research", response_model=Dict[str, Any])
async def explain_research(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _explain(ExplanationDomain.RESEARCH, payload, request, current_user, service)


@router.post("/explain/risk", response_model=Dict[str, Any])
async def explain_risk(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _explain(ExplanationDomain.RISK, payload, request, current_user, service)


@router.post("/explain/order", response_model=Dict[str, Any])
async def explain_order(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _explain(ExplanationDomain.ORDER, payload, request, current_user, service)


@router.post("/explain/position", response_model=Dict[str, Any])
async def explain_position(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _explain(ExplanationDomain.POSITION, payload, request, current_user, service)


@router.post("/explain/reconciliation", response_model=Dict[str, Any])
async def explain_reconciliation(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _explain(ExplanationDomain.RECONCILIATION, payload, request, current_user, service)


@router.get("/evidence/{bundle_id}", response_model=Dict[str, Any])
async def get_evidence_bundle(
    bundle_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    correlation_id = _correlation_id(request)
    try:
        rate_limiter.check(_rate_key(current_user, request, "bundle"), capacity=120)
        auth_context = _auth_context_for_user(current_user)
        bundle = bundle_service.get(validate_identifier(bundle_id, kind="bundle_id"), auth_context=auth_context)
        if bundle is None:
            raise LookupError("missing bundle")
        return _bounded_response(bundle, correlation_id)
    except LookupError as exc:
        raise _safe_http(404, "NOT_FOUND", "not found", correlation_id) from exc
    except PermissionError as exc:
        raise _safe_http(403, "FORBIDDEN", "forbidden", correlation_id) from exc
    except SafeAPIError as exc:
        raise _safe_http(exc.status_code, exc.code, exc.message, exc.correlation_id) from exc


@router.post("/evidence/retrieve", response_model=Dict[str, Any])
async def retrieve_evidence(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _assistant_action(payload, request, current_user, service, "retrieve")


@router.post("/evidence/lineage", response_model=Dict[str, Any])
async def trace_evidence_lineage(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return _assistant_action(payload, request, current_user, service, "lineage")


@router.post("/chat", response_model=Dict[str, Any])
async def ai_chat(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    return await _chat(payload, request, current_user, service)


@router.post("/chat/stream")
async def ai_chat_stream(
    payload: Dict[str, Any],
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AIAssistantService = Depends(get_ai_assistant_service),
):
    async def events():
        correlation_id = _correlation_id(request)
        prepared = None
        try:
            prepared = await _prepare_chat(payload, request, current_user, service, correlation_id)
            yield sse("started", {"conversation_id": prepared["conversation_id"], "evidence_bundle_id": prepared["bundle"].get("bundle_id") if prepared["bundle"] else None})
            yield sse("metadata", {"provider": prepared["provider_name"], "model": prepared["model"], "provisional": True})
            final_text = ""
            response_usage = None
            async for event in prepared["provider"].stream(prepared["provider_request"]):
                if await request.is_disconnected():
                    budget_manager.reconcile(prepared["reservation"]["reservation_id"], status="cancelled", correlation_id=correlation_id)
                    yield sse("cancelled", {"reason": "client_disconnected"})
                    return
                if event.type == "provisional_delta":
                    final_text += str(event.data.get("text") or "")
                if event.type == "completed":
                    final_text = str(event.data.get("text") or final_text)
                    response_usage = event.data.get("usage")
                    continue
                yield sse(event.type, event.data)
            answer, warnings = validate_output(final_text or INSUFFICIENT, bundle=prepared["bundle"])
            result = _finish_chat(prepared, answer=answer, validation_warnings=warnings, usage_override=response_usage, correlation_id=correlation_id)
            yield sse("validated", {"answer": result["answer"], "grounding": result["grounding"], "citations": result["citations"]})
            yield sse("completed", result)
            yield sse("final", result)
        except SafeAPIError as exc:
            yield sse("error", {"code": exc.code, "message": exc.message, "correlation_id": exc.correlation_id})
        except Exception:
            if prepared:
                result = _finish_chat(prepared, answer=INSUFFICIENT, validation_warnings=["stream_failure"], usage_override=None, correlation_id=correlation_id)
            else:
                result = {"answer": INSUFFICIENT, "grounding": {"validated": False, "warnings": ["stream_failure"]}}
            yield sse("fallback", {"answer": INSUFFICIENT})
            yield sse("completed", result)
            yield sse("final", result)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/conversations", response_model=Dict[str, Any])
async def ai_conversations(current_user: User = Depends(get_current_user)):
    return {"items": conversation_store.list(owner_user_id=str(current_user.id), is_admin=_is_admin(current_user))}


@router.get("/conversations/{conversation_id}", response_model=Dict[str, Any])
async def ai_conversation(conversation_id: str, current_user: User = Depends(get_current_user)):
    try:
        return conversation_store.get(conversation_id, owner_user_id=str(current_user.id), is_admin=_is_admin(current_user))
    except LookupError as exc:
        raise _safe_http(404, "NOT_FOUND", "not found", f"corr_{uuid4().hex[:12]}") from exc
    except PermissionError as exc:
        raise _safe_http(403, "FORBIDDEN", "forbidden", f"corr_{uuid4().hex[:12]}") from exc


@router.delete("/conversations/{conversation_id}", response_model=Dict[str, Any])
async def delete_ai_conversation(conversation_id: str, current_user: User = Depends(get_current_user)):
    try:
        return conversation_store.delete(conversation_id, owner_user_id=str(current_user.id), is_admin=_is_admin(current_user))
    except LookupError as exc:
        raise _safe_http(404, "NOT_FOUND", "not found", f"corr_{uuid4().hex[:12]}") from exc
    except PermissionError as exc:
        raise _safe_http(403, "FORBIDDEN", "forbidden", f"corr_{uuid4().hex[:12]}") from exc


@router.post("/summarize", response_model=Dict[str, Any])
async def ai_summarize(payload: Dict[str, Any], request: Request, current_user: User = Depends(get_current_user), service: AIAssistantService = Depends(get_ai_assistant_service)):
    payload = {**payload, "intent": "SUMMARIZE"}
    return await _chat(payload, request, current_user, service)


@router.post("/explain", response_model=Dict[str, Any])
async def ai_explain(payload: Dict[str, Any], request: Request, current_user: User = Depends(get_current_user), service: AIAssistantService = Depends(get_ai_assistant_service)):
    payload = {**payload, "intent": "EXPLAIN"}
    return await _chat(payload, request, current_user, service)


@router.post("/compare", response_model=Dict[str, Any])
async def ai_compare(payload: Dict[str, Any], request: Request, current_user: User = Depends(get_current_user), service: AIAssistantService = Depends(get_ai_assistant_service)):
    payload = {**payload, "intent": "COMPARE"}
    return await _chat(payload, request, current_user, service)


@router.post("/investigate", response_model=Dict[str, Any])
async def ai_investigate(payload: Dict[str, Any], request: Request, current_user: User = Depends(get_current_user), service: AIAssistantService = Depends(get_ai_assistant_service)):
    payload = {**payload, "intent": "INVESTIGATE"}
    return await _chat(payload, request, current_user, service)


@router.get("/providers", response_model=Dict[str, Any])
async def ai_providers(current_user: User = Depends(get_current_user)):
    return {
        "items": [
            {
                "provider": provider.name,
                "configured": secret_registry.metadata(provider.name, "api_key").present if provider.name != "local" else True,
                "enabled": True,
                "current_model": getattr(provider, "default_model", "local-grounded"),
                "fallback_provider": "local",
                "secret": secret_registry.redacted_status(provider.name, "api_key") if provider.name != "local" else {"provider": "local", "present": True, "source": "built-in"},
            }
            for provider in provider_registry.list()
        ]
    }


@router.get("/providers/health", response_model=Dict[str, Any])
async def ai_provider_health(current_user: User = Depends(get_current_user)):
    statuses = []
    for provider in provider_registry.list():
        configured = secret_registry.metadata(provider.name, "api_key").present if provider.name != "local" else True
        health = circuit_breaker.status(provider.name, configured=configured)
        statuses.append({**health.__dict__, "state": health.state.value, "current_model": getattr(provider, "default_model", "local-grounded"), "fallback_provider": "local"})
    return {"items": statuses}


@router.get("/providers/usage", response_model=Dict[str, Any])
async def ai_provider_usage(current_user: User = Depends(get_current_user)):
    user_id = None if _is_admin(current_user) else str(current_user.id)
    return usage_ledger.aggregate(user_id=user_id)


@router.get("/providers/budgets", response_model=Dict[str, Any])
async def ai_provider_budgets(current_user: User = Depends(get_current_user)):
    return budget_manager.status(user_id=None if _is_admin(current_user) else str(current_user.id))


@router.patch("/providers/{provider}/configuration", response_model=Dict[str, Any])
async def patch_ai_provider_configuration(provider: str, payload: Dict[str, Any], current_user: User = Depends(get_current_user)):
    if not _is_admin(current_user):
        raise _safe_http(403, "FORBIDDEN", "forbidden", f"corr_{uuid4().hex[:12]}")
    clean_provider = validate_identifier(provider)
    # Secrets are intentionally not accepted here. This endpoint records non-secret config intent only.
    return {"provider": clean_provider, "updated": True, "secrets_accepted": False, "configured": secret_registry.metadata(clean_provider, "api_key").present}


@router.post("/providers/{provider}/health-check", response_model=Dict[str, Any])
async def ai_provider_health_check(provider: str, current_user: User = Depends(get_current_user)):
    if not _is_admin(current_user):
        raise _safe_http(403, "FORBIDDEN", "forbidden", f"corr_{uuid4().hex[:12]}")
    clean_provider = validate_identifier(provider)
    configured = clean_provider == "local" or secret_registry.metadata(clean_provider, "api_key").present
    health = circuit_breaker.status(clean_provider, configured=configured)
    return {"provider": clean_provider, "configured": configured, "state": health.state.value, "healthy": health.healthy and configured}


def _guard_request(request: Request, payload: Dict[str, Any], current_user: User, scope: str) -> None:
    if bounded_json_bytes(payload) > MAX_REQUEST_BYTES:
        raise SafeAPIError(413, "PAYLOAD_TOO_LARGE", "request too large")
    capacity = 60
    if scope == "research_brief":
        capacity = 30
    elif scope.startswith("evidence:lineage"):
        capacity = 20
    elif scope.startswith("explain:"):
        capacity = 40
    rate_limiter.check(_rate_key(current_user, request, scope), capacity=capacity)


def _auth_context_for_user(current_user: User) -> AuthorizationContext:
    raw_role = getattr(current_user, "role", "trader")
    role = str(getattr(raw_role, "value", raw_role) or "trader")
    return AuthorizationContext(user_id=str(current_user.id), role=role, research_owner=str(current_user.id))


def _rate_key(current_user: User, request: Request, scope: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"ai:{scope}:{getattr(current_user, 'id', 'anon')}:{client}"


def _correlation_id(request: Request) -> str:
    header = request.headers.get("X-Correlation-ID")
    if header:
        try:
            return validate_identifier(header)
        except SafeAPIError:
            pass
    return f"corr_{uuid4().hex[:12]}"


def _safe_http(status_code: int, code: str, message: str, correlation_id: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "correlation_id": correlation_id})


def _bounded_response(payload: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
    cleaned = sanitize_value(payload)
    if bounded_json_bytes(cleaned) > MAX_RESPONSE_BYTES:
        raise SafeAPIError(413, "RESPONSE_TOO_LARGE", "response too large")
    cleaned.setdefault("correlation_id", correlation_id)
    return cleaned


async def _chat(
    payload: Dict[str, Any],
    request: Request,
    current_user: User,
    service: AIAssistantService,
) -> Dict[str, Any]:
    correlation_id = _correlation_id(request)
    prepared = await _prepare_chat(payload, request, current_user, service, correlation_id)
    try:
        response = await prepared["provider"].complete(prepared["provider_request"])
        answer, validation_warnings = validate_output(response.text or INSUFFICIENT, bundle=prepared["bundle"])
        prepared["provider_response"] = response
        return _finish_chat(prepared, answer=answer, validation_warnings=validation_warnings, correlation_id=correlation_id)
    except Exception:
        budget_manager.reconcile(prepared["reservation"]["reservation_id"], status="failed", correlation_id=correlation_id)
        raise


async def _prepare_chat(
    payload: Dict[str, Any],
    request: Request,
    current_user: User,
    service: AIAssistantService,
    correlation_id: str,
) -> Dict[str, Any]:
    _guard_request(request, payload, current_user, "chat")
    question = str(payload.get("message") or payload.get("question") or payload.get("prompt") or "").strip()
    if not question:
        raise _safe_http(422, "INVALID_REFERENCE", "invalid reference", correlation_id)
    bundle = await _resolve_bundle(payload, request, current_user, service)
    explanation = None
    if payload.get("domain") and payload.get("entity_id"):
        try:
            domain = ExplanationDomain(str(payload["domain"]))
            explanation = service.explain(
                domain=domain,
                payload=payload,
                user=current_user,
                endpoint=str(request.url.path),
                correlation_id=correlation_id,
            )
        except Exception:
            explanation = None
    prompt = build_prompt(user_request=question, bundle=bundle, explanation=explanation)
    budget = enforce_prompt_budget(prompt)
    provider_name = str(payload.get("provider") or "openai")
    provider = provider_registry.get(provider_name)
    model = str(payload.get("model") or getattr(provider, "default_model", "local-grounded"))
    reservation = budget_manager.reserve(
        user_id=str(current_user.id),
        conversation_id=payload.get("conversation_id") if isinstance(payload.get("conversation_id"), str) else None,
        estimated_tokens=budget.prompt_tokens + int(payload.get("max_tokens") or 700),
        estimated_cost=str(budget.estimated_cost),
        correlation_id=correlation_id,
    )
    return {
        "payload": payload,
        "question": question,
        "bundle": bundle,
        "budget": budget,
        "provider_name": provider_name,
        "provider": provider,
        "model": model,
        "current_user": current_user,
        "correlation_id": correlation_id,
        "reservation": reservation,
        "provider_request": ProviderRequest(
            prompt=prompt,
            model=model,
            max_tokens=int(payload.get("max_tokens") or 700),
            temperature=float(payload.get("temperature") or 0.0),
            timeout_seconds=float(payload.get("timeout_seconds") or 30.0),
            provider_request_id=f"prov_{uuid4().hex[:12]}",
            idempotency_key=str(payload.get("idempotency_key") or uuid4().hex),
        ),
        "conversation_id": payload.get("conversation_id") if isinstance(payload.get("conversation_id"), str) else None,
    }


def _finish_chat(
    prepared: Dict[str, Any],
    *,
    answer: str,
    validation_warnings: list[str],
    correlation_id: str,
    usage_override: Any | None = None,
) -> Dict[str, Any]:
    response = prepared.get("provider_response")
    if response is None:
        from backend.ai_provider.openai_provider import _local_fallback

        response = _local_fallback(prepared["provider_request"], provider=prepared["provider_name"], reason="stream_validated")
        if isinstance(usage_override, dict):
            response = response.__class__(
                **{
                    **response.__dict__,
                    "prompt_tokens": int(usage_override.get("prompt_tokens") or response.prompt_tokens),
                    "completion_tokens": int(usage_override.get("completion_tokens") or response.completion_tokens),
                    "cached_tokens": int(usage_override.get("cached_tokens") or usage_override.get("cached_input_tokens") or response.cached_tokens),
                    "reasoning_tokens": int(usage_override.get("reasoning_tokens") or response.reasoning_tokens),
                    "total_tokens": int(usage_override.get("total_tokens") or response.total_tokens),
                    "usage_reported": True,
                    "finish_reason": "stream_validated",
                }
            )
    bundle = prepared["bundle"]
    citations = citations_for_bundle(bundle) if bundle else []
    message = {
        "user": prepared["question"],
        "assistant": answer,
        "evidence_bundle_ids": [bundle["bundle_id"]] if bundle else [],
        "entities": [bundle["primary_entity"]] if bundle else [],
        "token_usage": {
            "prompt_tokens": response.prompt_tokens or prepared["budget"].prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "cached_tokens": response.cached_tokens,
            "reasoning_tokens": response.reasoning_tokens,
            "total_tokens": response.total_tokens or response.prompt_tokens + response.completion_tokens + response.reasoning_tokens,
            "usage_reported": response.usage_reported,
            "estimated_cost": response.estimated_cost,
            "confirmed_cost": response.confirmed_cost,
            "currency": response.currency,
            "pricing_version": response.pricing_version,
            "budget_remaining": prepared["budget"].budget_remaining,
        },
    }
    payload = prepared["payload"]
    current_user = prepared["current_user"]
    conversation_id = payload.get("conversation_id")
    if isinstance(conversation_id, str) and conversation_id.strip():
        conversation = conversation_store.append(validate_identifier(conversation_id), owner_user_id=str(current_user.id), message=message)
    else:
        conversation = conversation_store.create(owner_user_id=str(current_user.id), provider=response.provider, model=response.model, message=message)
    usage_ledger.record(
        user_id=str(current_user.id),
        conversation_id=conversation["conversation_id"],
        research_job_id=payload.get("research_job_id") if isinstance(payload.get("research_job_id"), str) else None,
        provider_response=response,
        reservation_id=prepared["reservation"]["reservation_id"],
        correlation_id=correlation_id,
    )
    budget_manager.reconcile(prepared["reservation"]["reservation_id"], status="completed", actual_tokens=message["token_usage"]["total_tokens"], actual_cost=str(response.confirmed_cost or response.estimated_cost), correlation_id=correlation_id)
    result = {
        "answer": answer,
        "provider": response.provider,
        "model": response.model,
        "conversation_id": conversation["conversation_id"],
        "evidence_bundle_id": bundle.get("bundle_id") if bundle else None,
        "citations": citations,
        "grounding": {"validated": not validation_warnings, "warnings": validation_warnings},
        "token_usage": message["token_usage"],
        "latency_ms": response.latency_ms,
        "time_to_first_token_ms": response.time_to_first_token_ms,
        "stream_duration_ms": response.stream_duration_ms,
        "finish_reason": response.finish_reason,
    }
    return _bounded_response(result, correlation_id)


async def _resolve_bundle(
    payload: Dict[str, Any],
    request: Request,
    current_user: User,
    service: AIAssistantService,
) -> dict[str, Any] | None:
    if isinstance(payload.get("evidence_bundle_id"), str):
        auth_context = _auth_context_for_user(current_user)
        return bundle_service.get(validate_identifier(payload["evidence_bundle_id"], kind="bundle_id"), auth_context=auth_context)
    if payload.get("domain") and payload.get("entity_id"):
        retrieved = service.retrieve_evidence(
            payload=payload,
            user=current_user,
            endpoint=str(request.url.path),
            correlation_id=_correlation_id(request),
        )
        return retrieved["bundle"]
    return None


def _is_admin(current_user: User) -> bool:
    raw_role = getattr(current_user, "role", "")
    return str(getattr(raw_role, "value", raw_role)) == "admin"
