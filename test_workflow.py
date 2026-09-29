import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app
from services import hindsight_service


class IncidentMindWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = app.DATABASE
        app.DATABASE = f"{self.temp_dir.name}/workflow.db"
        app.init_db()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DATABASE = self.database
        self.temp_dir.cleanup()

    def incident(self):
        response = self.client.get("/api/incidents")
        return next(item for item in response.json["incidents"] if item["incident_key"] == "INC-019")

    def test_demo_seed_is_idempotent_and_telemetry_is_backend_data(self):
        app.init_db()
        incidents = self.client.get("/api/incidents").json["incidents"]
        self.assertEqual(sum(item["incident_key"] == "INC-019" for item in incidents), 1)
        telemetry = self.client.get("/api/demo/observability").json["telemetry"]
        self.assertEqual(telemetry["error_rate"], "38%")
        self.assertEqual(telemetry["database_connections"], "98 / 100")

    def test_hindsight_offline_status_explains_missing_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            result = self.client.get("/api/hindsight/status")
        self.assertFalse(result.json["connected"])
        self.assertIn("HINDSIGHT_API_URL", result.json["error"])

    def test_full_workflow_with_mocked_hindsight(self):
        reflection = {
            "memories": [app.DEMO_HISTORICAL_MEMORY],
            "analysis": {
                "possible_root_cause": "Database connection pool exhaustion",
                "recommended_fix": "Rollback deployment v2.4.1 and restore the connection pool",
                "historical_lesson": "Check connection saturation after deployments.",
                "why_relevant": "The current and historical payment-api signals match.",
            },
            "analysis_text": "Evidence-based hypothesis.",
            "evidence": [{"text": "Historical INC-001", "type": "experience"}],
        }
        with patch.object(app, "investigate_with_hindsight", new=AsyncMock(return_value=reflection)), \
             patch.object(app, "seed_hindsight_memory_if_needed", new=AsyncMock()), \
             patch.object(app, "store_incident", new=AsyncMock(return_value=True)):
            investigated = self.client.post("/api/investigate", json={"incident_key": "INC-019"})
            self.assertEqual(investigated.status_code, 200)
            self.assertEqual(investigated.json["incident"]["state"], "AWAITING_APPROVAL")
            self.assertEqual(investigated.json["comparison"]["historical_relevance"], "HIGH")

            approved = self.client.post("/api/incidents/INC-019/approve")
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json["incident"]["state"], "APPROVED")

            recovered = self.client.post("/api/incidents/INC-019/recover")
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(recovered.json["recovery"]["after"]["error_rate"], "2%")

            postmortem = self.client.post("/api/incidents/INC-019/postmortem")
            self.assertEqual(postmortem.status_code, 200)
            self.assertEqual(postmortem.json["incident"]["state"], "POST_MORTEM")

            retained = self.client.post("/api/incidents/INC-019/retain")
            self.assertEqual(retained.status_code, 200)
            self.assertEqual(retained.json["message"], "MEMORY UPDATED")
            self.assertEqual(retained.json["incident"]["state"], "MEMORY_RETAINED")

    def test_rejection_prevents_recovery_and_similar_incident_is_explicit(self):
        with patch.object(app, "seed_hindsight_memory_if_needed", new=AsyncMock(side_effect=RuntimeError("Offline"))), \
             patch.object(app, "investigate_with_hindsight", new=AsyncMock(side_effect=RuntimeError("Offline"))):
            investigated = self.client.post("/api/investigate", json={"incident_key": "INC-019"})
            self.assertEqual(investigated.status_code, 503)
            rejected = self.client.post("/api/incidents/INC-019/reject")
            self.assertEqual(rejected.status_code, 409)

        first = self.client.post("/api/incidents/simulate-similar")
        second = self.client.post("/api/incidents/simulate-similar")
        self.assertTrue(first.json["created"])
        self.assertFalse(second.json["created"])
        self.assertEqual(second.json["incident"]["incident_key"], "INC-020")

    def test_invalid_state_transition_is_rejected(self):
        response = self.client.post("/api/incidents/INC-019/approve")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Invalid state transition", response.json["error"])

    def test_malformed_requests_and_recovery_protection(self):
        self.assertEqual(self.client.post("/api/incidents", json={}).status_code, 400)
        self.assertEqual(self.client.get("/api/incidents/UNKNOWN").status_code, 404)
        self.assertEqual(self.client.post("/api/incidents/INC-019/recover").status_code, 409)
        self.assertEqual(self.client.post("/api/incidents/INC-019/postmortem").status_code, 409)
        self.assertEqual(self.client.post("/api/incidents/INC-019/retain").status_code, 409)
        outcome = self.client.post("/api/outcome", json={"incident_key": "INC-019", "status": "success", "notes": "verified"})
        self.assertEqual(outcome.status_code, 409)

    def test_rejection_blocks_recovery_and_duplicate_approval(self):
        reflection = {
            "memories": [app.DEMO_HISTORICAL_MEMORY],
            "analysis": {"possible_root_cause": "Pool exhaustion", "recommended_fix": "Rollback", "historical_lesson": "Check saturation", "why_relevant": "Matching signals"},
            "analysis_text": "Hypothesis",
            "evidence": [],
        }
        with patch.object(app, "investigate_with_hindsight", new=AsyncMock(return_value=reflection)), patch.object(app, "seed_hindsight_memory_if_needed", new=AsyncMock()):
            self.assertEqual(self.client.post("/api/investigate", json={"incident_key": "INC-019"}).status_code, 200)
            self.assertEqual(self.client.post("/api/incidents/INC-019/reject").json["incident"]["state"], "REJECTED")
            self.assertEqual(self.client.post("/api/incidents/INC-019/recover").status_code, 409)
            self.assertEqual(self.client.post("/api/incidents/INC-019/approve").status_code, 409)

    def test_retention_failure_is_persisted_without_success_message(self):
        reflection = {
            "memories": [app.DEMO_HISTORICAL_MEMORY],
            "analysis": {"possible_root_cause": "Pool exhaustion", "recommended_fix": "Rollback", "historical_lesson": "Check saturation", "why_relevant": "Matching signals"},
            "analysis_text": "Hypothesis",
            "evidence": [],
        }
        with patch.object(app, "investigate_with_hindsight", new=AsyncMock(return_value=reflection)), patch.object(app, "seed_hindsight_memory_if_needed", new=AsyncMock()), patch.object(app, "store_incident", new=AsyncMock(side_effect=RuntimeError("Hindsight retain unavailable"))):
            self.assertEqual(self.client.post("/api/investigate", json={"incident_key": "INC-019"}).status_code, 200)
            self.assertEqual(self.client.post("/api/incidents/INC-019/approve").status_code, 200)
            self.assertEqual(self.client.post("/api/incidents/INC-019/recover").status_code, 200)
            self.assertEqual(self.client.post("/api/incidents/INC-019/postmortem").status_code, 200)
            retained = self.client.post("/api/incidents/INC-019/retain")
            self.assertEqual(retained.status_code, 503)
            self.assertEqual(retained.json["error"], "MEMORY UPDATE FAILED")
            incident = self.client.get("/api/incidents/INC-019").json["incident"]
            self.assertEqual(incident["state"], "POST_MORTEM")
            self.assertEqual(incident["memory_status"], "FAILED")

    def test_auth_service_incident_gets_auth_telemetry_and_structured_evidence(self):
        created = self.client.post("/api/incidents", json={
            "title": "Authentication Service Timeout",
            "description": "Authentication request timeouts after deployment v3.8.2.",
            "service": "auth-service",
            "severity": "High",
            "deployment": "v3.8.2",
        })
        self.assertEqual(created.status_code, 200)
        incident = created.json["incident"]
        telemetry = incident["telemetry"]
        self.assertEqual(telemetry["deployment"], "v3.8.2")
        self.assertIn("redis_cache_hit_rate", telemetry)
        self.assertIn("authentication_request_queue", telemetry)
        self.assertIn("Authentication request timeouts", " ".join(incident["current_evidence"]))
        self.assertIn("Recent deployment: v3.8.2", incident["current_evidence"])
        obs = self.client.get(f"/api/demo/observability?incident_key={incident['incident_key']}")
        self.assertEqual(obs.json["telemetry"]["service_status"], "Degraded")
        self.assertEqual(obs.json["telemetry"]["source"], "DEMO / SIMULATED TELEMETRY")

    def test_auth_service_recovery_uses_auth_metrics(self):
        created = self.client.post("/api/incidents", json={
            "title": "Authentication Service Timeout",
            "description": "Authentication request timeouts after deployment v3.8.2.",
            "service": "auth-service",
            "severity": "High",
            "deployment": "v3.8.2",
        })
        incident_key = created.json["incident"]["incident_key"]
        connection = app.get_db()
        connection.execute("UPDATE incidents SET state='APPROVED', recommendation=? WHERE incident_key=?", (json.dumps({"recommended_fix": "Simulated authentication cache recovery"}), incident_key))
        connection.commit()
        connection.close()
        recovered = self.client.post(f"/api/incidents/{incident_key}/recover")
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json["recovery"]["before"]["redis_cache_hit_rate"], "41%")
        self.assertEqual(recovered.json["recovery"]["after"]["redis_cache_hit_rate"], "92%")
        self.assertEqual(recovered.json["recovery"]["after"]["authentication_request_queue"], "8 requests")

    def test_hindsight_health_success_and_timeout_status(self):
        fake_client = SimpleNamespace(
            aget_version=AsyncMock(return_value="test-version"),
            acreate_bank=AsyncMock(),
            aclose=AsyncMock(),
        )
        with patch.object(hindsight_service, "configuration", return_value={"api_url": "http://hindsight.test", "api_key": "test-key", "bank_id": "incidentmind", "timeout": 0.1}), patch.object(hindsight_service, "Hindsight", return_value=fake_client):
            connected = asyncio.run(hindsight_service.check_hindsight())
        self.assertTrue(connected["connected"])
        self.assertEqual(connected["bank_id"], "incidentmind")

        fake_client.aget_version.side_effect = asyncio.TimeoutError()
        with patch.object(hindsight_service, "configuration", return_value={"api_url": "http://hindsight.test", "api_key": "test-key", "bank_id": "incidentmind", "timeout": 0.1}), patch.object(hindsight_service, "Hindsight", return_value=fake_client):
            offline = asyncio.run(hindsight_service.check_hindsight())
        self.assertFalse(offline["connected"])
        self.assertEqual(offline["error"], "TimeoutError")

    def test_hindsight_recall_empty_does_not_reflect_or_fabricate(self):
        fake_client = SimpleNamespace(
            acreate_bank=AsyncMock(),
            arecall=AsyncMock(return_value=SimpleNamespace(results=[])),
            areflect=AsyncMock(),
            aclose=AsyncMock(),
        )
        incident = {"incident_key": "INC-027", "service": "auth-service", "title": "Authentication Service Timeout"}
        with patch.object(hindsight_service, "configuration", return_value={"api_url": "http://hindsight.test", "api_key": None, "bank_id": "incidentmind", "timeout": 1}), patch.object(hindsight_service, "Hindsight", return_value=fake_client):
            result = asyncio.run(hindsight_service.investigate_with_hindsight(incident, "auth-service timeout"))
        self.assertEqual(result["memories"], [])
        self.assertEqual(result["analysis"], {})
        self.assertIn("No relevant historical incident found", result["analysis_text"])
        fake_client.areflect.assert_not_awaited()

    def test_hindsight_invalid_url_reports_configuration_error(self):
        with patch.object(hindsight_service, "configuration", return_value={"api_url": "not-a-url", "api_key": None, "bank_id": "incidentmind", "timeout": 1}):
            status = asyncio.run(hindsight_service.check_hindsight())
        self.assertFalse(status["connected"])
        self.assertIn("absolute http:// or https:// URL", status["error"])

    def test_explicit_cross_service_memory_is_not_used(self):
        payment_memory = "Historical incident INC-001\nService: payment-api\nRoot cause: connection pool exhaustion"
        self.assertEqual(app.filter_memories_for_service([payment_memory], "auth-service"), [])
        self.assertEqual(app.filter_memories_for_service([payment_memory], "payment-api"), [payment_memory])

    def test_empty_memory_vault_is_successful_empty_result(self):
        with patch.object(app, "search_incidents", new=AsyncMock(return_value=[])):
            response = self.client.get("/api/memories?service=auth-service")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["memories"], [])
        self.assertEqual(response.json["count"], 0)

    def test_auth_retained_incident_can_create_and_recall_similar_case(self):
        created = self.client.post("/api/incidents", json={
            "title": "Authentication Service Timeout",
            "description": "Authentication request timeouts after deployment v3.8.2.",
            "service": "auth-service",
            "severity": "High",
            "deployment": "v3.8.2",
        })
        source_key = created.json["incident"]["incident_key"]
        connection = app.get_db()
        connection.execute("UPDATE incidents SET state='MEMORY_RETAINED', memory_status='RETAINED' WHERE incident_key=?", (source_key,))
        app.record_memory_operation(connection, source_key, "retain", "SUCCESS")
        connection.commit()
        connection.close()

        similar = self.client.post("/api/incidents/simulate-similar", json={"source_incident_key": source_key})
        self.assertEqual(similar.status_code, 201)
        self.assertEqual(similar.json["incident"]["title"], "Authentication Cache Failure")
        self.assertEqual(similar.json["incident"]["service"], "auth-service")
        again = self.client.post("/api/incidents/simulate-similar", json={"source_incident_key": source_key})
        self.assertFalse(again.json["created"])

        historical = "Historical incident INC-027\nService: auth-service\nSymptoms: authentication timeouts, low Redis cache hit rate, growing authentication request queue.\nRoot cause: Redis cache saturation.\nResolution: Restore cache capacity.\nLessons learned: Check cache hit rate and authentication queue after deployment."
        result = {"memories": [historical], "analysis": {"possible_root_cause": "Redis cache saturation", "recommended_fix": "Restore cache capacity", "historical_lesson": "Check cache metrics", "why_relevant": "Same service and signals", "confidence": "HIGH RELEVANCE"}, "analysis_text": "Evidence-based hypothesis", "evidence": [{"text": historical, "type": "experience"}]}
        with patch.object(app, "investigate_with_hindsight", new=AsyncMock(return_value=result)), patch.object(app, "seed_hindsight_memory_if_needed", new=AsyncMock()):
            recalled = self.client.post("/api/investigate", json={"incident_key": similar.json["incident"]["incident_key"]})
        self.assertEqual(recalled.status_code, 200)
        self.assertIn("INC-027", recalled.json["memories"][0])


if __name__ == "__main__":
    unittest.main()
