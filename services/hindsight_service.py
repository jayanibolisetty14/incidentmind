import logging
import math
import os
import re
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from hindsight_client import Hindsight
from pathlib import Path

logger = logging.getLogger(__name__)
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "incidentmind")
INVESTIGATION_SCHEMA = {
    "type": "object",
    "properties": {
        "possible_root_cause": {"type": "string"},
        "recommended_fix": {"type": "string"},
        "historical_lesson": {"type": "string"},
        "why_relevant": {"type": "string"},
    },
    "required": ["possible_root_cause", "recommended_fix", "historical_lesson", "why_relevant"],
    "additionalProperties": False,
}


def normalize_service(service: str) -> str:
    normalized = re.sub(r"[\s_]+", "-", str(service or "").strip().lower())
    aliases = {
        "authentication-service": "auth-service",
        "payment-api-service": "payment-api",
    }
    return aliases.get(normalized, normalized)



def configuration() -> dict[str, str | None]:
    timeout_text = os.getenv("HINDSIGHT_TIMEOUT_SECONDS", "15")
    try:
        timeout = float(timeout_text)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except ValueError:
        timeout = 15.0
        logger.warning("Invalid HINDSIGHT_TIMEOUT_SECONDS; using %.1f seconds", timeout)
    return {
        "api_url": os.getenv("HINDSIGHT_API_URL"),
        "api_key": os.getenv("HINDSIGHT_API_KEY"),
        "bank_id": os.getenv("HINDSIGHT_BANK_ID", "incidentmind"),
        "timeout": timeout,
    }


def _client() -> Hindsight:
    config = configuration()
    api_url = config["api_url"]
    if not api_url:
        raise RuntimeError("HINDSIGHT_API_URL is not configured.")
    parsed = urlsplit(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("HINDSIGHT_API_URL must be an absolute http:// or https:// URL.")
    return Hindsight(
        base_url=api_url.rstrip("/"),
        api_key=config["api_key"],
        timeout=config["timeout"],
        max_attempts=1,
    )


def _error_detail(exc: Exception) -> str:
    detail = str(exc).strip()
    return detail or exc.__class__.__name__


async def _ensure_bank(client: Hindsight, bank_id: str) -> None:
    await client.acreate_bank(
        bank_id=bank_id,
        background="Incident response operational memory for IncidentMind.",
    )


async def check_hindsight() -> dict[str, Any]:
    config = configuration()
    if not config["api_url"]:
        return {"connected": False, "status": "OFFLINE", "error": "HINDSIGHT_API_URL is not configured."}

    client = None
    try:
        client = _client()
        version = await client.aget_version()
        await _ensure_bank(client, config["bank_id"])
        return {
            "connected": True,
            "status": "CONNECTED",
            "bank_id": config["bank_id"],
            "version": str(version),
        }
    except Exception as exc:
        logger.exception(
            "Hindsight health check failed (host=%s bank=%s)",
            urlsplit(config["api_url"]).hostname,
            config["bank_id"],
        )
        return {"connected": False, "status": "OFFLINE", "error": _error_detail(exc)}
    finally:
        if client:
            await client.aclose()


async def store_incident(incident_text: str, document_id: str | None = None) -> bool:
    client = _client()
    config = configuration()
    try:
        await _ensure_bank(client, config["bank_id"])
        await client.aretain(
            bank_id=config["bank_id"],
            content=incident_text,
            document_id=document_id,
            tags=["incidentmind", "operational-memory"],
        )
        return True
    except Exception:
        logger.exception("Hindsight retain failed (bank=%s document=%s)", config["bank_id"], document_id)
        raise
    finally:
        await client.aclose()


async def search_incidents(query: str) -> list[str]:
    client = _client()
    config = configuration()
    try:
        await _ensure_bank(client, config["bank_id"])
        result = await client.arecall(bank_id=config["bank_id"], query=query, include_source_facts=True)
        return [memory.text for memory in result.results if getattr(memory, "text", "")]
    except Exception:
        logger.exception("Hindsight recall failed (bank=%s)", config["bank_id"])
        raise
    finally:
        await client.aclose()


async def investigate_with_hindsight(incident: dict[str, Any], query: str) -> dict[str, Any]:
    client = _client()
    config = configuration()
    prompt = f"""
Act as a cautious engineering incident investigation assistant.
Use recalled operational memories as evidence and hypotheses, never as confirmed truth.

Current incident:
ID: {incident.get('incident_key', incident.get('id', ''))}
Title: {incident.get('title', '')}
Description: {incident.get('description', '')}
Service: {incident.get('service', '')}
Deployment: {incident.get('deployment', '')}
Telemetry: {incident.get('telemetry', '')}
Current evidence: {incident.get('current_evidence', '')}

Return a possible root cause, a recommended action, a historical lesson, and why the
memory is relevant. Distinguish current evidence from historical evidence. Do not invent
facts or claim certainty.
"""
    try:
        await _ensure_bank(client, config["bank_id"])
        recall_result = await client.arecall(
            bank_id=config["bank_id"],
            query=query,
            include_source_facts=True,
        )
        memories = [memory.text for memory in recall_result.results if getattr(memory, "text", "")]
        service = normalize_service(incident.get("service", ""))
        if service:
            memories = [
                text for text in memories
                if not (match := re.search(r"(?:^|[\n,\{\s])(?:service|affected_service)\s*[:=]\s*['\"]?([a-z0-9_\s-]+?)(?:['\",\r\n]|$)", text, re.IGNORECASE))
                or normalize_service(match.group(1)) == service
            ]
        if not memories:
            return {
                "memories": [],
                "analysis": {},
                "analysis_text": "No relevant historical incident found.",
                "evidence": [],
            }
        prompt = f"{prompt}\n\nOnly use these memories returned for this incident query as historical support:\n{memories}"
        reflection = await client.areflect(
            bank_id=config["bank_id"],
            query=prompt,
            budget="mid",
            response_schema=INVESTIGATION_SCHEMA,
            include_facts=True,
        )
        based_on = getattr(reflection, "based_on", None)
        evidence = [
            {"text": fact.text, "type": fact.type}
            for fact in getattr(based_on, "memories", []) or []
            if getattr(fact, "text", "")
        ]
        return {
            "memories": memories,
            "analysis": getattr(reflection, "structured_output", None) or {},
            "analysis_text": getattr(reflection, "text", ""),
            "evidence": evidence,
        }
    except Exception:
        logger.exception("Hindsight investigation failed (bank=%s)", config["bank_id"])
        raise
    finally:
        await client.aclose()
