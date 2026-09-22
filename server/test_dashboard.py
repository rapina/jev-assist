"""Real loopback dashboard auth, trace persistence, and human-review export."""
import http.client
import json
import io
import zipfile
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import dashboard as audit
import jev_server as jev
from local_runtime import LocalServer, open_file, private
from routing_policy import QUESTIONS, LUNA, route_choice


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(audit, "DB_PATH", Path(self.tmp) / "audit.sqlite3"))
        self.enterContext(mock.patch.object(audit, "_logins", {}))
        self.enterContext(mock.patch.object(audit, "_sessions", {}))
        self.enterContext(mock.patch.object(jev, "local_secret", return_value="fixture-local"))
        self.enterContext(mock.patch.object(audit, "local_secret", return_value="fixture-local"))
        self.server = LocalServer(("127.0.0.1", 0), jev.Handler)
        self.server.audit_enabled = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_observation_auth_validation_and_stale_snapshot(self):
        body = {"id": "a" * 64, "session": "fixture", "project": "demo", "client": "fixture-pc",
                "mode": "observe", "updated": 20, "eventCount": 2, "files": ["x.py"],
                "verified": False, "verdict": "block", "events": []}
        self.assertEqual(self.request("POST", "/v1/observations", body)[0], 401)
        self.assertEqual(self.request("POST", "/v1/observations", body, Authorization="Bearer fixture-local")[0], 200)
        audit.observe({**body, "updated": 10, "verified": True})
        self.assertFalse(audit.observations()["sessions"][0]["verified"])
        self.assertEqual(self.request("GET", "/dashboard/api/observations")[0], 401)
        status, _, data = self.request("GET", "/dashboard/api/observations", Cookie=self.login())
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/dashboard/api/observations", Authorization="Bearer fixture-local")[0], 200)
        self.assertEqual(json.loads(data)["sessions"][0]["verdict"], "block")
        self.assertEqual(self.request("POST", "/v1/observations", {**body, "mode": "enforce"}, Authorization="Bearer fixture-local")[0], 400)

    def request(self, method, path, data=None, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers.setdefault("Host", "127.0.0.1:4319")
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
        try:
            connection.request(method, path, None if data is None else json.dumps(data), headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def login(self):
        status, _, body = self.request("POST", "/dashboard/session", {}, Authorization="Bearer fixture-local")
        self.assertEqual(status, 200)
        url = json.loads(body)["url"]
        path = url.removeprefix(audit.ORIGIN)
        status, headers, _ = self.request("GET", path)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/dashboard")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", path)[0], 401)
        return headers["Set-Cookie"].split(";", 1)[0]

    def make_record(self):
        record_id = audit.record(None, "received", task="Review cache logic", policy_version="fixture",
                                 request={"model": "jev-latest", "state": {"task": "Review cache logic"}, "questions": QUESTIONS})
        audit.record(record_id, "completed", selected={"model": LUNA, "effort": "low", "gate": "apply"},
                     outcome={"model": LUNA, "status": 200, "completed_status": 200})
        return record_id

    def test_real_cookie_session_and_browser_boundary(self):
        self.assertEqual(self.request("GET", "/dashboard/api/records")[0], 401)
        self.assertEqual(self.request("POST", "/dashboard/session", {})[0], 401)
        self.assertEqual(self.request("POST", "/dashboard/session", {}, Authorization="Bearer fixture-local", Origin="https://other.invalid")[0], 403)
        cookie = self.login()
        self.assertEqual(self.request("GET", "/dashboard/api/records", Cookie=cookie)[0], 200)
        self.assertEqual(self.request("GET", "/dashboard/api/records", Cookie=cookie, Host="other.invalid")[0], 403)
        self.assertEqual(self.request("POST", "/dashboard/api/review", {}, Cookie=cookie)[0], 404)
        self.assertEqual(self.request("POST", "/ask", {}, Cookie=cookie, Origin=audit.ORIGIN)[0], 401)

    def test_skill_download_and_removed_manual_review(self):
        cookie = self.login()
        for route in ("/dashboard/api/review", "/dashboard/api/export"):
            self.assertEqual(self.request("GET", route, Cookie=cookie)[0], 404)
        self.assertEqual(self.request("POST", "/dashboard/api/review", {}, Cookie=cookie, Origin=audit.ORIGIN)[0], 404)
        status, _, body = self.request("GET", "/dashboard/skill.md")
        self.assertEqual(status, 200)
        self.assertIn(b"name: jev-assist", body)

    def test_client_bundle_contains_only_runtime_and_jev_catalog(self):
        (Path(self.tmp) / 'merged-models.json').write_text(json.dumps({'models': [{'slug': 'jev/auto'}, {'slug': LUNA, 'visibility': 'hide'}, {'slug': 'other'}]}), encoding='utf-8')
        with mock.patch.object(audit, 'STATE', self.tmp):
            status, _, body = self.request('GET', '/dashboard/client.zip')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(set(archive.namelist()), {*audit.CLIENT_FILES, 'models.json'})
            self.assertEqual(json.loads(archive.read('models.json'))['models'], [{'slug': 'jev/auto', 'visibility': 'list'}, {'slug': LUNA, 'visibility': 'list'}])
        self.assertEqual(self.request('GET', '/dashboard/install-client.ps1')[0], 200)

    def test_trace_and_journal_are_private_and_secrets_redacted(self):
        record_id = audit.record(None, "received", task="key apikey_12345678901234567890",
                                 request={"authorization": "private", "state": {"password": "private", "task": "bounded"}})
        item = audit.get_record(record_id)
        self.assertNotIn("apikey_", json.dumps(item))
        self.assertNotIn("request", item)
        self.assertNotIn("task", item)
        for path in (audit.DB_PATH, Path(str(audit.DB_PATH) + "-journal")):
            fd = open_file(path, os.O_RDONLY)
            try:
                self.assertTrue(private(fd))
            finally:
                os.close(fd)

    def test_capture_failure_does_not_change_routing_result(self):
        with mock.patch.object(audit, "database", side_effect=OSError("disk full")):
            self.assertIsInstance(audit.record(None, "received", task="hello"), str)
            self.assertEqual(audit.storage_error, "OSError")
        self.make_record()
        self.assertIsNone(audit.storage_error)

    def test_prompt_privacy_for_new_and_historical_records(self):
        record_id = audit.record(None, 'received', task='PRIVATE_PROMPT', request={'state': 'PRIVATE_INPUT'}, response={'text': 'PRIVATE_RESPONSE'})
        with audit.database() as db:
            stored = db.execute('SELECT body FROM records WHERE id=?', (record_id,)).fetchone()['body']
            self.assertNotIn('PRIVATE_', stored)
            legacy = {'events': [], 'task': 'PRIVATE_PROMPT', 'request': {'state': 'PRIVATE_INPUT'}, 'response': {'text': 'PRIVATE_RESPONSE'}, 'decision': {'model': LUNA}}
            db.execute('UPDATE records SET body=?,review=? WHERE id=?', (json.dumps(legacy), '"PRIVATE_REVIEW"', record_id))
        cookie = self.login()
        for route in ('/dashboard/api/records', '/dashboard/api/record?id=' + record_id):
            status, _, body = self.request('GET', route, Cookie=cookie)
            self.assertEqual(status, 200)
            self.assertNotIn(b'PRIVATE_', body)
            self.assertIn(b'unknown', body)
        self.assertNotIn('PRIVATE_PROMPT', json.dumps(audit.list_records({})))
        audit.record(record_id, 'selected', selected={'model': LUNA, 'source': 'manual'})
        self.assertEqual(audit.get_record(record_id)['difficulty'], 'fixed')

    def test_distribution_uses_all_completed_requests_and_mode_filter(self):
        for source in ('auto', 'auto', 'client_model'):
            record_id = audit.record(None, 'received')
            audit.record(record_id, 'completed', decision={'model': LUNA},
                         selected={'model': LUNA, 'source': source}, outcome={'model': LUNA})
        audit.record(None, 'selected', decision={'model': LUNA}, selected={'model': LUNA})
        data = audit.list_records({'limit': ['1']})
        self.assertEqual(len(data['records']), 1)
        self.assertEqual({r['mode']: r['count'] for r in data['distribution']}, {'auto': 2, 'fixed': 1})
        filtered = audit.list_records({'mode': ['fixed']})
        self.assertEqual(len(filtered['records']), 1)
        self.assertEqual(filtered['records'][0]['difficulty'], 'fixed')

    def test_retained_trace_survives_a_new_database_connection(self):
        record_id = self.make_record()
        self.assertEqual(audit.get_record(record_id)["policy_version"], "fixture")

    def test_real_routing_handler_captures_input_answer_and_terminal_outcome(self):
        answers = {k: {"choice": v} for k, v in route_choice(LUNA, "low").items()}

        def forward(handler, *args):
            handler._attempts.append({"model": LUNA, "status": 200, "terminal_type": "response.completed"})
            handler._json(200, {"status": "completed"})
            return 200, "json", "application/json", False, None, None

        with mock.patch.object(jev, "load_key", return_value="fixture"), \
             mock.patch.object(jev, "call_jev_routed", return_value={"answers": answers}), \
             mock.patch.object(jev.Handler, "_forward", forward), \
             mock.patch.object(jev, "LOG_PATH", str(Path(self.tmp) / "live.jsonl")):
            status, _, _ = self.request("POST", "/v1/responses", {"input": "Reply OK", "model": "auto"}, Authorization="Bearer fixture-local")
            self.assertEqual(status, 200)
            # Completion logging occurs after the response body is written.
            for _ in range(100):
                rows = audit.list_records({})["records"]
                if rows and rows[0]["phase"] == "completed":
                    break
                threading.Event().wait(0.01)
            item = audit.get_record(rows[0]["id"])
            self.assertNotIn("request", item)
            self.assertNotIn("response", item)
            self.assertEqual(item["dimensions"], dict.fromkeys(("visual", "architecture", "coding", "risk"), 0))
            self.assertEqual(item["decision"]["model"], LUNA)
            self.assertEqual(item["outcome"]["completed_status"], 200)

    def test_transport_failure_retains_attempt_evidence(self):
        def fail(handler):
            handler._trace("received", task="transport fixture")
            handler._attempts.append({"model": LUNA, "status": 502})
            raise TimeoutError("private upstream details")

        with mock.patch.object(jev.Handler, "_post", fail):
            status, _, _ = self.request("POST", "/v1/responses", {}, Authorization="Bearer fixture-local")
        self.assertEqual(status, 502)
        row = audit.list_records({})["records"][0]
        item = audit.get_record(row["id"])
        self.assertEqual(item["phase"], "failed")
        self.assertEqual(item["outcome"]["attempts"][0]["status"], 502)
        self.assertNotIn("private upstream", json.dumps(item))

    def test_decision_endpoint_never_executes_and_accepts_bounded_client_outcome(self):
        answers = {k: {'choice': v} for k, v in route_choice(LUNA, 'low').items()}
        with mock.patch.object(jev, 'load_key', return_value='fixture'), \
             mock.patch.object(jev, 'call_jev_routed', return_value={'answers': answers}), \
             mock.patch.object(jev.Handler, '_forward') as forward:
            status, _, data = self.request('POST', '/v1/route', {'state': 'PRIVATE_TASK'}, Authorization='Bearer fixture-local')
            self.assertEqual(status, 200)
            route = json.loads(data)
            self.assertEqual(route['model'], LUNA)
            forward.assert_not_called()
        outcome = {'id': route['id'], 'model': LUNA, 'status': 200, 'total_ms': 123,
                   'usage': {'input_tokens': 10, 'text': 'PRIVATE_OUTPUT'}}
        self.assertEqual(self.request('POST', '/v1/outcome', outcome, Authorization='Bearer fixture-local')[0], 200)
        item = audit.get_record(route['id'])
        self.assertEqual(item['phase'], 'completed')
        self.assertEqual(item['outcome']['usage'], {'input_tokens': 10})
        self.assertNotIn('PRIVATE_', json.dumps(item))
        outcome['model'] = 'deepseek/deepseek-v4.1-flash'
        self.assertEqual(self.request('POST', '/v1/outcome', outcome, Authorization='Bearer fixture-local')[0], 400)

    def test_external_model_is_rejected_before_execution(self):
        with mock.patch.object(jev.Handler, '_forward') as forward:
            status, _, _ = self.request('POST', '/v1/responses',
                {'model': 'deepseek/deepseek-v4.1-flash', 'input': 'fixture'},
                Authorization='Bearer fixture-local')
            self.assertEqual(status, 400)
            forward.assert_not_called()

    def test_direct_model_preserves_selection_without_jev_or_quota_fallback(self):
        def forward(handler, payload, *args):
            self.assertEqual(payload['model'], LUNA)
            self.assertEqual(payload['reasoning']['effort'], 'high')
            handler._json(429, {'error': 'quota fixture'})
            return 429, 'json', 'application/json', True, None, None
        with mock.patch.object(jev, 'call_jev_routed') as choose, \
             mock.patch.object(jev.Handler, '_forward', forward), \
             mock.patch.object(jev, 'LOG_PATH', str(Path(self.tmp) / 'live.jsonl')):
            status, _, _ = self.request('POST', '/v1/responses', {'model': LUNA, 'input': 'fixture', 'reasoning': {'effort': 'high'}}, Authorization='Bearer fixture-local')
            self.assertEqual(status, 429)
            choose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
