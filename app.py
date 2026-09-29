from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, render_template, request

from services.hindsight_service import (
    check_hindsight,
    investigate_with_hindsight,
    search_incidents,
    store_incident,
)

app = Flask(__name__)
DATABASE = "incidentmind.db"
last_memory_match_count = 0

DEMO_INCIDENT_KEY = "INC-019"
DEMO_DESCRIPTION = """HTTP 500 errors are increasing after deployment v2.4.1.
API latency has increased to approximately 1840 ms.
Database connections are at 98/100 and database timeout errors are occurring."""
SIMULATED_TELEMETRY = {
    "error_rate": "38%",
    "latency": "1840 ms",
    "database_connections": "98 / 100",
    "deployment": "v2.4.1",
    "database_timeouts": "Detected",
    "service_status": "Degraded",
    "source": "DEMO / SIMULATED TELEMETRY",
}
DEMO_CURRENT_EVIDENCE = [
    "HTTP 500 errors increased after deployment (DEMO / SIMULATED TELEMETRY)",
    "Error rate: 38% (DEMO / SIMULATED TELEMETRY)",
    "Latency: 1840 ms (DEMO / SIMULATED TELEMETRY)",
    "Database connections: 98 / 100 (DEMO / SIMULATED TELEMETRY)",
    "Database timeout errors detected (DEMO / SIMULATED TELEMETRY)",
    "Recent deployment: v2.4.1",
    "Simulated log: payment-api HTTP 500 rate increased after deployment",
    "Deployment history: v2.4.1 preceded the observed errors",
]
DEMO_HISTORICAL_MEMORY = """Historical incident INC-001
Service: payment-api
Symptoms: HTTP 500 errors after deployment, 98/100 database connections active, increased database timeout errors, and high latency.
Root cause: Database connection pool exhaustion.
Historical resolution: Rollback deployment v2.4.1, restart the payment service, and adjust the database connection pool.
Historical result: 42% error rate -> approximately 1.8%.
Lessons learned: For deployment-related Payment API failures, check database connection saturation and connection timeout errors first."""

STATES = {
    "DETECTED",
    "TRIAGED",
    "TRIAGING",
    "INVESTIGATING",
    "AWAITING_APPROVAL",
    "APPROVED",
    "RECOVERING",
    "RECOVERED",
    "POST_MORTEM",
    "MEMORY_RETAINED",
    "REJECTED",
    "FAILED",
}
ACTIVE_STATES = {"DETECTED", "TRIAGED", "TRIAGING", "INVESTIGATING", "AWAITING_APPROVAL", "APPROVED", "RECOVERING"}
STATE_TRANSITIONS = {
    "DETECTED": {"TRIAGED", "TRIAGING", "INVESTIGATING"},
    "TRIAGED": {"INVESTIGATING"},
    "TRIAGING": {"TRIAGED", "INVESTIGATING"},
    "INVESTIGATING": {"AWAITING_APPROVAL", "FAILED"},
    "AWAITING_APPROVAL": {"APPROVED", "REJECTED", "FAILED"},
    "APPROVED": {"RECOVERING", "FAILED"},
    "RECOVERING": {"RECOVERED", "FAILED"},
    "RECOVERED": {"POST_MORTEM", "FAILED"},
    "POST_MORTEM": {"MEMORY_RETAINED", "FAILED"},
    "MEMORY_RETAINED": set(),
    "REJECTED": set(),
    "FAILED": set(),
}


def now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_service(service: str) -> str:
    normalized = re.sub(r"[\s_]+", "-", str(service or "").strip().lower())
    aliases = {
        "authentication-service": "auth-service",
        "payment-api-service": "payment-api",
    }
    return aliases.get(normalized, normalized)


def telemetry_for_incident(incident: dict[str, Any]) -> dict[str, Any]:
    service = normalize_service(incident.get("service", ""))
    deployment = str(incident.get("deployment", "Unknown"))
    if service in {"auth-service", "authentication service", "authentication-service"}:
        values = {
            "error_rate": "22%",
            "latency": "3200 ms",
            "redis_cache_hit_rate": "41%",
            "authentication_request_queue": "184 requests",
            "deployment": deployment,
            "authentication_timeouts": "Detected",
            "service_status": "Degraded",
            "source": "DEMO / SIMULATED TELEMETRY",
            "metrics": [
                {"label": "AUTH ERROR RATE", "value": "22%"},
                {"label": "LOGIN LATENCY", "value": "3200 ms"},
                {"label": "REDIS CACHE HIT RATE", "value": "41%"},
                {"label": "AUTH REQUEST QUEUE", "value": "184 requests"},
                {"label": "DEPLOYMENT", "value": deployment},
                {"label": "AUTH TIMEOUTS", "value": "Detected"},
                {"label": "SERVICE", "value": "Degraded"},
            ],
        }
        return values
    if service == "payment-api":
        values = dict(SIMULATED_TELEMETRY)
        values["deployment"] = deployment
        values["metrics"] = [
            {"label": "ERROR RATE", "value": values["error_rate"]},
            {"label": "LATENCY", "value": values["latency"]},
            {"label": "DB CONNECTIONS", "value": values["database_connections"]},
            {"label": "DEPLOYMENT", "value": deployment},
            {"label": "DATABASE TIMEOUTS", "value": values["database_timeouts"]},
            {"label": "SERVICE", "value": values["service_status"]},
        ]
        return values
    return {
        "service": service or "unknown",
        "deployment": deployment,
        "service_status": "Unobserved",
        "source": "DEMO / SIMULATED TELEMETRY",
        "metrics": [
            {"label": "SERVICE", "value": service or "Unknown"},
            {"label": "DEPLOYMENT", "value": deployment},
            {"label": "TELEMETRY", "value": "No service profile"},
        ],
    }


def evidence_for_incident(incident: dict[str, Any], telemetry: dict[str, Any] | None = None) -> list[str]:
    service = normalize_service(incident.get("service", ""))
    deployment = str(incident.get("deployment", "Unknown"))
    description = str(incident.get("description", "")).strip()
    telemetry = telemetry or telemetry_for_incident(incident)
    if service in {"auth-service", "authentication service", "authentication-service"}:
        evidence = []
        if re.search(r"auth|login|timeout", f"{incident.get('title', '')} {description}", re.IGNORECASE):
            evidence.append("Authentication request timeouts (DEMO / SIMULATED TELEMETRY)")
        evidence.extend([
            f"Affected service: {service}",
            f"Increased login latency: {telemetry['latency']} (DEMO / SIMULATED TELEMETRY)",
            f"Low Redis cache hit rate: {telemetry['redis_cache_hit_rate']} (DEMO / SIMULATED TELEMETRY)",
            f"Growing authentication request queue: {telemetry['authentication_request_queue']} (DEMO / SIMULATED TELEMETRY)",
            f"Recent deployment: {deployment}",
        ])
        if description:
            evidence.append(f"Incident description: {description}")
        return evidence
    if service == "payment-api" and incident.get("incident_key") == DEMO_INCIDENT_KEY:
        return [*DEMO_CURRENT_EVIDENCE, f"Affected service: {service}"]
    evidence = [description] if description else []
    if deployment and deployment != "Unknown":
        evidence.append(f"Recent deployment: {deployment}")
    evidence.append(f"Affected service: {service or 'Unknown'}")
    return evidence


def filter_memories_for_service(memories: list[str], service: str) -> list[str]:
    normalized_service = normalize_service(service)
    if not normalized_service:
        return memories
    filtered = []
    for memory in memories:
        declared_service = re.search(r"(?:^|[\n,\{\s])(?:service|affected_service)\s*[:=]\s*['\"]?([a-z0-9_\s-]+?)(?:['\",\r\n]|$)", memory, re.IGNORECASE)
        if declared_service and normalize_service(declared_service.group(1)) != normalized_service:
            continue
        filtered.append(memory)
    return filtered


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def status_for_state(state: str) -> str:
    if state in {"RECOVERED", "POST_MORTEM", "MEMORY_RETAINED"}:
        return "RESOLVED"
    if state in {"REJECTED", "FAILED"}:
        return "FAILED"
    return "ACTIVE"


def append_timeline(conn: sqlite3.Connection, incident_id: int, event: str) -> list[dict[str, str]]:
    row = conn.execute("SELECT timeline FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    timeline = json_load(row["timeline"] if row else None, [])
    if timeline and timeline[-1].get("event") == event:
        return timeline
    single_occurrence_events = {
        "Incident detected",
        "Current evidence collected",
        "Triage completed",
        "Hindsight recall completed",
        "Historical evidence compared",
        "Recommendation generated",
        "Human approval recorded",
        "Simulated recovery completed",
        "Post-mortem generated",
        "Knowledge retained",
    }
    if event in single_occurrence_events and any(item.get("event") == event for item in timeline):
        return timeline
    timeline.append({"event": event, "at": now_text()})
    conn.execute("UPDATE incidents SET timeline = ? WHERE id = ?", (json_dump(timeline), incident_id))
    return timeline


def record_memory_operation(conn: sqlite3.Connection, incident_key: str, operation: str, status: str, error: str | None = None) -> None:
    conn.execute(
        "INSERT INTO memory_operations (incident_key, operation, status, error, created_at) VALUES (?, ?, ?, ?, ?)",
        (incident_key, operation, status, error, now_text()),
    )


def migrate_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(incidents)").fetchall()}
    additions = {
        "incident_key": "TEXT",
        "state": "TEXT NOT NULL DEFAULT 'DETECTED'",
        "detection_time": "TEXT",
        "resolution_time": "TEXT",
        "symptoms": "TEXT",
        "current_evidence": "TEXT",
        "historical_evidence": "TEXT",
        "investigation": "TEXT",
        "recommendation": "TEXT",
        "approval_status": "TEXT",
        "action_taken": "TEXT",
        "recovery_result": "TEXT",
        "postmortem": "TEXT",
        "lessons_learned": "TEXT",
        "memory_status": "TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED'",
        "timeline": "TEXT",
        "telemetry": "TEXT",
        "is_demo": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")

    conn.execute("UPDATE incidents SET state = CASE status WHEN 'RESOLVED' THEN 'RECOVERED' WHEN 'FAILED' THEN 'FAILED' ELSE 'DETECTED' END WHERE state IS NULL OR state = ''")
    conn.execute("UPDATE incidents SET status = CASE WHEN state IN ('RECOVERED', 'POST_MORTEM', 'MEMORY_RETAINED') THEN 'RESOLVED' WHEN state IN ('FAILED', 'REJECTED') THEN 'FAILED' ELSE 'ACTIVE' END")
    conn.execute("UPDATE incidents SET incident_key = 'INC-' || printf('%03d', id) WHERE incident_key IS NULL")
    conn.execute("UPDATE incidents SET detection_time = created_at WHERE detection_time IS NULL")
    conn.execute("UPDATE incidents SET timeline = '[]' WHERE timeline IS NULL")
    conn.execute("UPDATE incidents SET memory_status = 'NOT_ATTEMPTED' WHERE memory_status IS NULL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def clean_legacy_test_rows(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM app_metadata WHERE key = 'legacy_demo_cleanup_v1'").fetchone():
        return
    conn.execute(
        """
        DELETE FROM incidents
        WHERE title = 'API Latency Spike'
          AND description = 'API response time increased to 4.8 seconds and database connection requests are timing out.'
          AND service = 'Payment API'
          AND deployment = 'v2.4.1'
          AND severity = 'Critical'
          AND is_demo = 0
        """
    )
    conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('legacy_demo_cleanup_v1', 'complete')")


def seed_demo_incident(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT id, state, status FROM incidents WHERE incident_key = ?", (DEMO_INCIDENT_KEY,)).fetchone()
    if existing:
        # Only update static metadata fields — NEVER overwrite state/status/timeline
        # so a completed INC-019 retains its resolved/retained state across restarts.
        conn.execute(
            """
            UPDATE incidents
            SET title = ?, description = ?, service = ?, severity = ?, deployment = ?,
                symptoms = ?, current_evidence = ?, telemetry = ?, is_demo = 1
            WHERE id = ?
            """,
            (
                "Payment API Connection Exhaustion",
                DEMO_DESCRIPTION,
                "payment-api",
                "CRITICAL",
                "v2.4.1",
                json_dump(DEMO_CURRENT_EVIDENCE),
                json_dump([DEMO_DESCRIPTION, *DEMO_CURRENT_EVIDENCE]),
                json_dump(SIMULATED_TELEMETRY),
                existing["id"],
            ),
        )
        return
    conn.execute(
        """
        INSERT INTO incidents (
            incident_key, title, description, service, deployment, severity, status, state,
            detection_time, symptoms, current_evidence, memory_status, timeline, telemetry, is_demo
        ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', 'DETECTED', ?, ?, ?, 'NOT_ATTEMPTED', ?, ?, 1)
        """,
        (
            DEMO_INCIDENT_KEY,
            "Payment API Connection Exhaustion",
            DEMO_DESCRIPTION,
            "payment-api",
            "v2.4.1",
            "CRITICAL",
            now_text(),
            json_dump(DEMO_CURRENT_EVIDENCE),
            json_dump([DEMO_DESCRIPTION, *DEMO_CURRENT_EVIDENCE]),
            json_dump([{"event": "Incident detected", "at": now_text()}]),
            json_dump(SIMULATED_TELEMETRY),
        ),
    )


def init_db() -> None:
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            service TEXT,
            deployment TEXT,
            severity TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    migrate_columns(conn)
    clean_legacy_test_rows(conn)
    seed_demo_incident(conn)
    conn.commit()
    conn.close()


def row_to_incident(row: sqlite3.Row) -> dict[str, Any]:
    incident = dict(row)
    incident["incident_key"] = incident.get("incident_key") or f"INC-{int(incident['id']):03d}"
    incident["state"] = incident.get("state") or "DETECTED"
    incident["status"] = status_for_state(incident["state"])
    incident["resolution_time"] = incident.get("resolution_time") or ""
    for field in ("symptoms", "current_evidence", "historical_evidence", "investigation", "recommendation", "recovery_result", "postmortem", "timeline", "telemetry"):
        if field in incident:
            incident[field] = json_load(incident[field], [] if field in {"symptoms", "current_evidence", "historical_evidence", "timeline"} else {})
    if not incident.get("telemetry"):
        incident["telemetry"] = telemetry_for_incident(incident)
    auth_service = str(incident.get("service", "")).strip().lower() in {"auth-service", "authentication service", "authentication-service"}
    if not incident.get("current_evidence") or (auth_service and incident.get("current_evidence") == [incident.get("description")]):
        incident["current_evidence"] = evidence_for_incident(incident, incident["telemetry"])
        incident["symptoms"] = incident["current_evidence"]
    return incident


def find_incident(conn: sqlite3.Connection, reference: str | int | None) -> sqlite3.Row | None:
    if reference is None:
        return None
    return conn.execute(
        "SELECT * FROM incidents WHERE incident_key = ? OR id = ? LIMIT 1",
        (str(reference), str(reference)),
    ).fetchone()


def require_incident(reference: str | int | None) -> tuple[sqlite3.Connection, sqlite3.Row] | tuple[None, None]:
    conn = get_db()
    row = find_incident(conn, reference)
    if not row:
        conn.close()
        return None, None
    return conn, row


def current_evidence_for(incident: dict[str, Any]) -> list[str]:
    evidence = incident.get("current_evidence")
    if isinstance(evidence, list) and evidence:
        return evidence
    return [incident.get("description", "")]


def compare_evidence(current: list[str], historical: list[str], service: str = "") -> dict[str, Any]:
    current_text = " ".join(current).lower()
    historical_text = " ".join(historical).lower()
    signal_pairs = [
        ("Authentication timeouts", ("authentication request timeout", "authentication timeout", "login timeout")),
        ("Low Redis cache hit rate", ("redis", "cache hit")),
        ("Growing authentication request queue", ("authentication request queue", "auth request queue", "queue")),
        ("Increased login latency", ("login latency", "authentication latency", "auth latency")),
        ("HTTP 500 errors", ("500", "http")),
        ("Recent deployment", ("deployment", "v2.4.1")),
        ("Database connection saturation", ("connection", "pool", "database")),
        ("Database timeout errors", ("timeout", "timing out")),
        ("Increased latency", ("latency", "slow")),
    ]
    matching = [label for label, terms in signal_pairs if any(term in historical_text for term in terms) and any(term in current_text for term in terms)]
    normalized_service = normalize_service(service)
    if normalized_service and normalized_service in historical_text and normalized_service in current_text:
        matching.insert(0, "Same affected service")
    conflicting = []
    if any(term in historical_text for term in ("connection", "pool", "database")) and any(term in current_text for term in ("normal connection", "low connection", "healthy connection")):
        conflicting.append("Database connection saturation")
    if "latency" in historical_text and any(term in current_text for term in ("normal latency", "latency recovered", "low latency")):
        conflicting.append("Increased latency")
    if "deployment" in historical_text and any(term in current_text for term in ("no deployment", "unchanged deployment")):
        conflicting.append("Recent deployment")
    return {
        "matching_signals": matching,
        "matching_count": len(matching),
        "current_count": len(current),
        "conflicting_evidence": conflicting,
        "historical_relevance": "HIGH" if matching else "NONE",
        "hypothesis_notice": "Historical memory supports this hypothesis, but current evidence must be verified before action.",
    }


async def investigate_record(incident: dict[str, Any]) -> dict[str, Any]:
    global last_memory_match_count
    current = current_evidence_for(incident)
    service = str(incident.get("service", "unknown"))
    incident["telemetry"] = incident.get("telemetry") or telemetry_for_incident(incident)
    query = (
        f"Current incident {incident['incident_key']} service {service}; "
        f"title {incident.get('title', '')}; symptoms {incident.get('description', '')}; "
        f"deployment {incident.get('deployment', '')}; current evidence {'; '.join(current)}; "
        f"simulated service telemetry {json.dumps(incident['telemetry'])}"
    )
    result = await investigate_with_hindsight(
        {**incident, "current_evidence": current, "telemetry": json.dumps(incident["telemetry"])},
        query,
    )
    memories = filter_memories_for_service(result.get("memories", []), service)
    last_memory_match_count = len(memories)
    historical = memories[:3]
    comparison = compare_evidence(current, historical, service)
    analysis = result.get("analysis") or {}
    if memories and not analysis.get("confidence"):
        analysis["confidence"] = comparison["historical_relevance"]
    if memories:
        analysis["hypothesis_notice"] = comparison["hypothesis_notice"]
    return {"memories": memories, "historical_evidence": historical, "analysis": analysis, "analysis_text": result.get("analysis_text", ""), "evidence": result.get("evidence", []), "comparison": comparison}


async def seed_hindsight_memory_if_needed(incident_key: str) -> None:
    conn = get_db()
    already = conn.execute("SELECT 1 FROM memory_operations WHERE incident_key = 'INC-001' AND operation = 'demo_seed' AND status = 'SUCCESS' LIMIT 1").fetchone()
    conn.close()
    if already:
        return
    try:
        await store_incident(DEMO_HISTORICAL_MEMORY, document_id="INC-001")
        conn = get_db()
        record_memory_operation(conn, "INC-001", "demo_seed", "SUCCESS")
        conn.commit()
        conn.close()
    except Exception as exc:
        conn = get_db()
        record_memory_operation(conn, "INC-001", "demo_seed", "FAILED", str(exc))
        conn.commit()
        conn.close()
        raise


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "IncidentMind"})


@app.route("/api/hindsight/status")
async def hindsight_status():
    return jsonify(await check_hindsight())


@app.route("/api/demo/observability")
def demo_observability():
    reference = request.args.get("incident_key")
    if reference:
        conn, row = require_incident(reference)
        if not row:
            return jsonify({"error": "Incident not found."}), 404
        incident = row_to_incident(row)
        conn.close()
    else:
        incident = {"incident_key": DEMO_INCIDENT_KEY, "service": "payment-api", "deployment": "v2.4.1"}
    telemetry = telemetry_for_incident(incident)
    return jsonify({"telemetry": telemetry, "evidence": evidence_for_incident(incident, telemetry)})


@app.route("/api/incidents", methods=["POST"])
def create_incident():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Incident data must be a JSON object."}), 400
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    service = str(data.get("service", "")).strip()
    severity = str(data.get("severity", "")).strip().upper()
    deployment = str(data.get("deployment", "")).strip()
    if not title or not description or not service or not severity or not deployment:
        return jsonify({"error": "Title, description, affected service, severity, and deployment are required."}), 400
    if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return jsonify({"error": "Severity must be Critical, High, Medium, or Low."}), 400
    if data.get("incident_key"):
        return jsonify({"error": "Incident ID is generated by the backend."}), 400
    conn = get_db()
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM incidents").fetchone()[0]
    incident_key = f"INC-{max_id + 1:03d}"
    suffix = max_id + 1
    while find_incident(conn, incident_key):
        suffix += 1
        incident_key = f"INC-{suffix:03d}"
    incident_context = {
        "incident_key": incident_key,
        "title": title,
        "description": description,
        "service": service,
        "deployment": deployment,
        "severity": severity,
    }
    telemetry = telemetry_for_incident(incident_context)
    evidence = evidence_for_incident(incident_context, telemetry)
    cursor = conn.execute(
        """
        INSERT INTO incidents (incident_key, title, description, service, deployment, severity, status, state, detection_time, symptoms, current_evidence, memory_status, timeline, telemetry, is_demo)
        VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', 'DETECTED', ?, ?, ?, 'NOT_ATTEMPTED', ?, ?, 0)
        """,
        (incident_key, title, description, service, deployment, severity, now_text(), json_dump(evidence), json_dump(evidence), json_dump([{"event": "Incident detected", "at": now_text()}]), json_dump(telemetry)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return jsonify({"success": True, "incident": row_to_incident(row)}), 200


@app.route("/api/incidents", methods=["GET"])
def get_incidents():
    conn = get_db()
    rows = conn.execute("SELECT * FROM incidents ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify({"incidents": [row_to_incident(row) for row in rows]}), 200


@app.route("/api/incidents/<reference>")
def get_incident(reference: str):
    conn, row = require_incident(reference)
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    incident = row_to_incident(row)
    conn.close()
    return jsonify({"incident": incident})


@app.route("/api/stats")
def get_stats():
    conn = get_db()
    rows = conn.execute("SELECT state, COUNT(*) AS count FROM incidents GROUP BY state").fetchall()
    total_recalls = conn.execute("SELECT COUNT(*) FROM memory_operations WHERE operation = 'recall' AND status = 'SUCCESS'").fetchone()[0]
    conn.close()
    counts = {row["state"]: row["count"] for row in rows}
    return jsonify({
        "active": sum(counts.get(state, 0) for state in ACTIVE_STATES),
        "resolved": counts.get("RECOVERED", 0) + counts.get("POST_MORTEM", 0) + counts.get("MEMORY_RETAINED", 0),
        "failed": counts.get("FAILED", 0) + counts.get("REJECTED", 0),
        "memory_matches": last_memory_match_count,
        "total_recalls": total_recalls,
    })


@app.route("/api/analytics")
def get_analytics():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    counts = conn.execute("SELECT state, COUNT(*) AS count FROM incidents GROUP BY state").fetchall()
    service = conn.execute("SELECT service FROM incidents WHERE service != '' GROUP BY service ORDER BY COUNT(*) DESC, service LIMIT 1").fetchone()
    failure_row = conn.execute(
        "SELECT title FROM incidents WHERE state IN ('FAILED', 'REJECTED') GROUP BY title ORDER BY COUNT(*) DESC, title LIMIT 1"
    ).fetchone()
    operations = conn.execute("SELECT operation, status, COUNT(*) AS count FROM memory_operations GROUP BY operation, status").fetchall()
    conn.close()
    count_by_state = {row["state"]: row["count"] for row in counts}
    op_counts = {(row["operation"], row["status"]): row["count"] for row in operations}
    return jsonify({
        "total": total,
        "active": sum(count_by_state.get(state, 0) for state in ACTIVE_STATES),
        "resolved": count_by_state.get("RECOVERED", 0) + count_by_state.get("POST_MORTEM", 0) + count_by_state.get("MEMORY_RETAINED", 0),
        "failed": count_by_state.get("FAILED", 0) + count_by_state.get("REJECTED", 0),
        "historical_matches": op_counts.get(("recall", "SUCCESS"), 0),
        "successful_memory_retains": op_counts.get(("retain", "SUCCESS"), 0) + op_counts.get(("demo_seed", "SUCCESS"), 0),
        "failed_memory_operations": sum(row["count"] for row in operations if row["status"] == "FAILED"),
        "memory_matches": last_memory_match_count,
        "most_affected_service": service["service"] if service else None,
        "most_common_failure": failure_row["title"] if failure_row else "No failed incidents",
    })


@app.route("/api/memories")
async def get_memories():
    service = request.args.get("service", "").strip()
    try:
        query = f"IncidentMind {service} root cause symptoms resolution result lessons" if service else "IncidentMind root cause symptoms resolution result lessons"
        memories = await search_incidents(query)
        if service:
            memories = filter_memories_for_service(memories, service)
        conn = get_db()
        # Do NOT add a spurious memory_operation record here — vault browsing is not an incident recall
        operations = conn.execute("SELECT operation, status, COUNT(*) AS count FROM memory_operations GROUP BY operation, status").fetchall()
        conn.close()
        return jsonify({
            "memories": memories,
            "count": len(memories),
            "operations": {
                "successful_recalls": sum(row["count"] for row in operations if row["operation"] == "recall" and row["status"] == "SUCCESS"),
                "successful_retains": sum(row["count"] for row in operations if row["operation"] in {"retain", "demo_seed"} and row["status"] == "SUCCESS"),
                "failed_operations": sum(row["count"] for row in operations if row["status"] == "FAILED"),
            },
        }), 200
    except Exception as exc:
        logging.exception("Unable to load Hindsight Memory Vault")
        conn = get_db()
        record_memory_operation(conn, DEMO_INCIDENT_KEY, "recall", "FAILED", str(exc))
        conn.commit()
        conn.close()
        return jsonify({"error": "Hindsight memory temporarily unavailable.", "reason": str(exc)}), 503


async def run_incident_investigation(reference: str | int):
    global last_memory_match_count
    conn, row = require_incident(reference)
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    incident = row_to_incident(row)
    if incident["state"] not in {"DETECTED", "TRIAGED", "TRIAGING", "INVESTIGATING"}:
        conn.close()
        return jsonify({"error": f"Investigation is not valid from state {incident['state']}."}), 409
    try:
        if incident["state"] == "DETECTED":
            append_timeline(conn, row["id"], "Current evidence collected")
            append_timeline(conn, row["id"], "Triage completed")
            conn.execute("UPDATE incidents SET state = 'INVESTIGATING' WHERE id = ?", (row["id"],))
            conn.commit()
        incident = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone())
        if incident["service"].strip().lower() == "payment-api" and incident["incident_key"] == DEMO_INCIDENT_KEY:
            await seed_hindsight_memory_if_needed(incident["incident_key"])
        result = await investigate_record(incident)
        record_memory_operation(conn, incident["incident_key"], "recall", "SUCCESS")
        append_timeline(conn, row["id"], "Hindsight recall completed")
        append_timeline(conn, row["id"], "Historical evidence compared")
        has_recommendation = bool(result["memories"] and result["analysis"].get("recommended_fix"))
        if has_recommendation:
            append_timeline(conn, row["id"], "Recommendation generated")
        else:
            append_timeline(conn, row["id"], "No relevant historical incident found")
        timeline = json_load(conn.execute("SELECT timeline FROM incidents WHERE id = ?", (row["id"],)).fetchone()["timeline"], [])
        next_state = "AWAITING_APPROVAL" if has_recommendation else "INVESTIGATING"
        memory_status = "RECALLED" if result["memories"] else "EMPTY"
        conn.execute(
            "UPDATE incidents SET state = ?, historical_evidence = ?, investigation = ?, recommendation = ?, memory_status = ? WHERE id = ?",
            (next_state, json_dump(result["historical_evidence"]), json_dump({"analysis": result["analysis"], "evidence": result["evidence"], "comparison": result["comparison"]}), json_dump(result["analysis"]), memory_status, row["id"]),
        )
        conn.commit()
        last_memory_match_count = len(result["memories"])
        return jsonify({"success": True, "incident": row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone()), "current_incident": incident, "memories": result["memories"], "previous_incidents": result["memories"], "analysis": result["analysis"], "analysis_text": result["analysis_text"], "evidence": result["evidence"], "comparison": result["comparison"], "timeline": timeline, "analysis_source": "hindsight_reflect", "no_relevant_memory": not bool(result["memories"])})
    except Exception as exc:
        logging.exception("Investigation failed while querying Hindsight")
        record_memory_operation(conn, row["incident_key"], "recall", "FAILED", str(exc))
        conn.execute("UPDATE incidents SET state = 'INVESTIGATING', status = 'ACTIVE', memory_status = 'FAILED' WHERE id = ?", (row["id"],))
        append_timeline(conn, row["id"], "Hindsight recall failed")
        conn.commit()
        return jsonify({"error": "Investigation temporarily unavailable.", "reason": str(exc)}), 503
    finally:
        conn.close()


@app.route("/api/investigate", methods=["POST"])
async def investigate_incident():
    data = request.get_json(silent=True) or {}
    reference = data.get("incident_key") or data.get("id")
    if not reference:
        service_name = normalize_service(data.get("service", ""))
        if service_name == "payment-api":
            await seed_hindsight_memory_if_needed("INC-001")
        query = f"Current incident: {data.get('title', '')} {data.get('description', '')} {data.get('service', '')} {data.get('deployment', '')} {data.get('severity', '')}"
        try:
            result = await investigate_with_hindsight(data, query)
            memories = result.get("memories", [])
            return jsonify({
                "success": True,
                "current_incident": data,
                "memory_match_count": len(memories),
                "previous_incidents": memories,
                "memories": memories,
                "analysis": result.get("analysis", {}),
                "analysis_text": result.get("analysis_text", ""),
                "evidence": result.get("evidence", []),
                "analysis_source": "hindsight_reflect",
            })
        except Exception as exc:
            logging.exception("Legacy investigation failed while querying Hindsight")
            return jsonify({"error": "Investigation temporarily unavailable.", "reason": str(exc)}), 503
    return await run_incident_investigation(reference)


@app.route("/api/incidents/<reference>/recall", methods=["POST"])
async def recall_incident(reference: str):
    return await run_incident_investigation(reference)


@app.route("/api/incidents/<reference>/approve", methods=["POST"])
def approve_action(reference: str):
    return transition_incident(reference, "APPROVED", "Human approval recorded", "APPROVED")


@app.route("/api/incidents/<reference>/reject", methods=["POST"])
def reject_action(reference: str):
    return transition_incident(reference, "REJECTED", "Action rejected by human operator", "REJECTED")


def transition_incident(reference: str, target: str, event: str, approval: str):
    conn, row = require_incident(reference)
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    current = row["state"]
    if target not in STATE_TRANSITIONS.get(current, set()):
        conn.close()
        return jsonify({"error": f"Invalid state transition: {current} -> {target}."}), 409
    if target == "APPROVED":
        recommendation = json_load(row["recommendation"], {})
        if not recommendation.get("recommended_fix"):
            conn.close()
            return jsonify({"error": "A generated recommendation is required before approval."}), 409
    append_timeline(conn, row["id"], event)
    conn.execute("UPDATE incidents SET state = ?, approval_status = ?, status = ? WHERE id = ?", (target, approval, status_for_state(target), row["id"]))
    conn.commit()
    result = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone())
    conn.close()
    return jsonify({"success": True, "incident": result})


@app.route("/api/incidents/<reference>/recover", methods=["POST"])
def recover_incident(reference: str):
    conn, row = require_incident(reference)
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    if row["state"] != "APPROVED":
        conn.close()
        return jsonify({"error": f"Recovery requires APPROVED state, got {row['state']}."}), 409
    incident = row_to_incident(row)
    service = str(incident.get("service", "")).strip().lower()
    before = incident.get("telemetry") or telemetry_for_incident(incident)
    if service in {"auth-service", "authentication service", "authentication-service"}:
        after = {
            "error_rate": "3%",
            "latency": "240 ms",
            "redis_cache_hit_rate": "92%",
            "authentication_request_queue": "8 requests",
            "authentication_timeouts": "Cleared",
            "service_status": "Recovered",
            "source": "DEMO / SIMULATED METRICS",
        }
        action = incident.get("recommendation", {}).get("recommended_fix", "Simulated authentication cache recovery")
    else:
        after = {"error_rate": "2%", "latency": "210 ms", "database_connections": "40 / 100", "source": "DEMO / SIMULATED METRICS"}
        action = incident.get("recommendation", {}).get("recommended_fix", f"Rollback deployment {row['deployment']}")
    recovery = {"action": action, "label": "SIMULATED DEMO ACTION", "before": before, "after": after, "result": "RECOVERY VERIFIED", "source": "DEMO METRICS ONLY"}
    append_timeline(conn, row["id"], "Simulated recovery completed")
    conn.execute("UPDATE incidents SET state = 'RECOVERED', status = 'RESOLVED', action_taken = ?, recovery_result = ?, resolution_time = COALESCE(resolution_time, ?) WHERE id = ?", (recovery["action"], json_dump(recovery), now_text(), row["id"]))
    conn.commit()
    result = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone())
    conn.close()
    return jsonify({"success": True, "incident": result, "recovery": recovery})


@app.route("/api/incidents/<reference>/postmortem", methods=["POST"])
def create_postmortem(reference: str):
    conn, row = require_incident(reference)
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    if row["state"] != "RECOVERED":
        conn.close()
        return jsonify({"error": f"Post-mortem requires RECOVERED state, got {row['state']}."}), 409
    incident = row_to_incident(row)
    service = str(incident.get("service", "")).strip().lower()
    investigation = incident.get("investigation", {})
    analysis = investigation.get("analysis", {})
    auth_incident = service in {"auth-service", "authentication service", "authentication-service"}
    timeline = append_timeline(conn, row["id"], "Post-mortem generated")
    postmortem = {
        "incident_summary": incident["description"],
        "impact": f"{incident['service']} experienced {incident['description']}",
        "timeline": timeline,
        "root_cause": analysis.get("possible_root_cause", "No root cause was verified during this investigation."),
        "root_cause_status": "HYPOTHESIS - OPERATOR VERIFICATION REQUIRED" if analysis.get("possible_root_cause") else "NOT DETERMINED",
        "evidence": incident["current_evidence"],
        "resolution": incident["action_taken"],
        "action_taken": incident["action_taken"],
        "recovery_result": incident["recovery_result"],
        "what_worked": f"The approved simulated response improved {incident['service']} demo metrics: {json.dumps(incident['recovery_result'].get('after', {}))}.",
        "what_did_not_work": "No production action was executed; all recovery metrics are simulated.",
        "lessons_learned": analysis.get("historical_lesson", "Verify current service evidence and telemetry before assigning a root cause."),
        "prevention_recommendation": ("Alert on Redis cache-hit rate, login latency, and authentication queue depth." if auth_incident else "Alert on connection-pool saturation before deployment-related errors cascade."),
        "recommended_follow_up": ("Review authentication cache behavior and request queue thresholds before the next release." if auth_incident else "Review connection-pool capacity and deployment guardrails before the next release."),
    }
    conn.execute("UPDATE incidents SET state = 'POST_MORTEM', postmortem = ?, lessons_learned = ? WHERE id = ?", (json_dump(postmortem), postmortem["lessons_learned"], row["id"]))
    conn.commit()
    result = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone())
    conn.close()
    return jsonify({"success": True, "incident": result, "postmortem": postmortem})


@app.route("/api/incidents/<reference>/retain", methods=["POST"])
async def retain_postmortem(reference: str):
    conn, row = require_incident(reference)
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    if row["state"] != "POST_MORTEM":
        conn.close()
        return jsonify({"error": f"Retention requires POST_MORTEM state, got {row['state']}."}), 409
    incident = row_to_incident(row)
    content = json.dumps({
        "incident_id": incident["incident_key"],
        "service": incident["service"],
        "deployment": incident["deployment"],
        "symptoms": incident["symptoms"],
        "evidence": incident["current_evidence"],
        "root_cause_hypothesis": incident["postmortem"].get("root_cause"),
        "root_cause_status": incident["postmortem"].get("root_cause_status"),
        "action": incident["action_taken"],
        "result": incident["recovery_result"],
        "lessons": incident["postmortem"].get("lessons_learned"),
        "prevention": incident["postmortem"].get("prevention_recommendation"),
    }, indent=2)
    try:
        await store_incident(content, document_id=incident["incident_key"])
        record_memory_operation(conn, incident["incident_key"], "retain", "SUCCESS")
        append_timeline(conn, row["id"], "Knowledge retained")
        conn.execute("UPDATE incidents SET state = 'MEMORY_RETAINED', memory_status = 'RETAINED', status = 'RESOLVED', resolution_time = COALESCE(resolution_time, ?) WHERE id = ?", (now_text(), row["id"],))
        conn.commit()
        result = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone())
        conn.close()
        return jsonify({"success": True, "message": "MEMORY UPDATED", "incident": result})
    except Exception as exc:
        record_memory_operation(conn, incident["incident_key"], "retain", "FAILED", str(exc))
        conn.execute("UPDATE incidents SET memory_status = 'FAILED' WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return jsonify({"error": "MEMORY UPDATE FAILED", "reason": str(exc)}), 503


@app.route("/api/incidents/simulate-similar", methods=["POST"])
def simulate_similar_incident():
    data = request.get_json(silent=True) or {}
    source_key = data.get("source_incident_key")
    conn = get_db()
    if source_key:
        source = find_incident(conn, source_key)
        if not source:
            conn.close()
            return jsonify({"error": "Source incident not found."}), 404
        if source["state"] != "MEMORY_RETAINED":
            conn.close()
            return jsonify({"error": "The source incident must be retained in Hindsight before creating its similar incident."}), 409
        successful_retain = conn.execute(
            "SELECT 1 FROM memory_operations WHERE incident_key = ? AND operation = 'retain' AND status = 'SUCCESS' LIMIT 1",
            (source_key,),
        ).fetchone()
        if not successful_retain:
            conn.close()
            return jsonify({"error": "No successful Hindsight retain is recorded for the source incident."}), 409
        source_service = str(source["service"] or "").strip().lower()
        if source_service in {"auth-service", "authentication service", "authentication-service"}:
            title = "Authentication Cache Failure"
            service = "auth-service"
            deployment = "v3.8.3"
            description = "Authentication requests are timing out. Login latency is elevated. Redis cache hit rate has dropped. Authentication request queue is growing."
            existing = conn.execute("SELECT * FROM incidents WHERE is_demo = 1 AND title = ? AND service = ? AND deployment = ?", (title, service, deployment)).fetchone()
            if existing:
                incident = row_to_incident(existing)
                conn.close()
                return jsonify({"success": True, "created": False, "incident": incident})
            new_key_num = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM incidents").fetchone()[0]
            incident_key = f"INC-{new_key_num:03d}"
            while find_incident(conn, incident_key):
                new_key_num += 1
                incident_key = f"INC-{new_key_num:03d}"
            telemetry = telemetry_for_incident({"service": service, "deployment": deployment})
            symptoms = evidence_for_incident({"title": title, "service": service, "deployment": deployment, "description": description}, telemetry)
            cursor = conn.execute(
                """INSERT INTO incidents (incident_key, title, description, service, deployment, severity, status, state, detection_time, symptoms, current_evidence, memory_status, timeline, telemetry, is_demo)
                VALUES (?, ?, ?, ?, ?, 'HIGH', 'ACTIVE', 'DETECTED', ?, ?, ?, 'NOT_ATTEMPTED', ?, ?, 1)""",
                (incident_key, title, description, service, deployment, now_text(), json_dump(symptoms), json_dump(symptoms), json_dump([{"event": "Incident detected", "at": now_text()}]), json_dump(telemetry)),
            )
            conn.commit()
            incident = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (cursor.lastrowid,)).fetchone())
            conn.close()
            return jsonify({"success": True, "created": True, "incident": incident}), 201
    existing = find_incident(conn, "INC-020")
    if existing:
        result = row_to_incident(existing)
        conn.close()
        return jsonify({"success": True, "created": False, "incident": result})
    cursor = conn.execute(
        """
        INSERT INTO incidents (incident_key, title, description, service, deployment, severity, status, state, detection_time, symptoms, current_evidence, memory_status, timeline, telemetry, is_demo)
        VALUES ('INC-020', 'Payment API Elevated Error Rate', 'HTTP 500 increase, high latency, recent deployment, and high database connection usage.', 'payment-api', 'v2.4.1', 'HIGH', 'ACTIVE', 'DETECTED', ?, ?, ?, 'NOT_ATTEMPTED', ?, '{}', 1)
        """,
        (now_text(), json_dump(["HTTP 500 increase", "High latency", "Recent deployment v2.4.1", "High database connection usage"]), json_dump(["HTTP 500 increase", "High latency", "Recent deployment v2.4.1", "High database connection usage"]), json_dump([{"event": "Incident detected", "at": now_text()}])),
    )
    conn.commit()
    result = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (cursor.lastrowid,)).fetchone())
    conn.close()
    return jsonify({"success": True, "created": True, "incident": result}), 201


@app.route("/api/outcome", methods=["POST"])
async def save_outcome():
    data = request.get_json(silent=True) or {}
    reference = data.get("incident_key") or data.get("incident_id")
    if data.get("status") not in {"success", "failure"}:
        return jsonify({"error": "Invalid outcome status."}), 400
    if not reference:
        return jsonify({"error": "Incident ID is required."}), 400
    conn, row = require_incident(str(reference))
    if not row:
        return jsonify({"error": "Incident not found."}), 404
    if data["status"] == "failure":
        conn.close()
        return reject_action(str(reference))
    if row["state"] not in {"RECOVERED", "POST_MORTEM", "MEMORY_RETAINED"}:
        conn.close()
        return jsonify({"error": "A successful outcome requires verified recovery first."}), 409
    notes = str(data.get("notes", "")).strip()
    if notes:
        existing = row["lessons_learned"] or ""
        updated = f"{existing}\nVerified outcome: {notes}".strip()
        conn.execute("UPDATE incidents SET lessons_learned = ?, resolution_time = COALESCE(resolution_time, ?) WHERE id = ?", (updated, now_text(), row["id"]))
    conn.commit()
    result = row_to_incident(conn.execute("SELECT * FROM incidents WHERE id = ?", (row["id"],)).fetchone())
    conn.close()
    return jsonify({"success": True, "message": "Verified outcome recorded locally.", "status": result["status"], "incident": result})


init_db()

if __name__ == "__main__":
    app.run(debug=True)
