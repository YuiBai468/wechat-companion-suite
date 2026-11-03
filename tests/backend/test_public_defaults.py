"""Offline checks for portable paths and public HTTP access boundaries."""
import ast
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desktop'))
import config_loader


def handler_class(config, token):
    # Compile the actual nested handler without importing or starting the bot.
    tree = ast.parse((ROOT / 'desktop/wechat-bot.py').read_text(encoding='utf-8'))
    server = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'HealthServer')
    start = next(n for n in server.body if isinstance(n, ast.FunctionDef) and n.name == 'start')
    handler = next(n for n in start.body if isinstance(n, ast.ClassDef) and n.name == 'H')
    outer = SimpleNamespace(token=token, port=0, bot=SimpleNamespace(health_debug=False))
    namespace = {'http': http, 'CFG': config, 'outer': outer,
                 '_is_local_client': lambda addr: ipaddress.ip_address(addr).is_loopback,
                 'log': lambda *args: None, 'Dict': Dict, 'Any': Any, 'List': List, 'Optional': Optional}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), '<public-handler>', 'exec'), namespace)
    return namespace['H'], outer, namespace


class PublicDefaultsTest(unittest.TestCase):
    def test_env_paths_refs_and_bare_tool(self):
        with mock.patch.dict(os.environ, {'TEST_RELATIVE_PATH': 'private-local/data'}):
            cfg = config_loader._resolve_value({'contextFile': '${TEST_RELATIVE_PATH}',
                    'image': {'refs': ['assets/refs/example.png']}, 'ffmpeg': 'ffmpeg'}, root=str(ROOT))
        self.assertEqual(cfg['contextFile'], str(ROOT / 'private-local/data'))
        self.assertEqual(cfg['image']['refs'], [str(ROOT / 'assets/refs/example.png')])
        self.assertEqual(cfg['ffmpeg'], 'ffmpeg')

    def test_autonomous_and_health_defaults_are_off(self):
        cfg = json.loads((ROOT / 'desktop/bot-config.example.json').read_text(encoding='utf-8'))
        for name in ('proactive', 'schedule', 'lifeplan', 'health', 'image', 'vision', 'asr'):
            self.assertFalse(cfg[name]['enabled'], name)
        self.assertEqual(cfg['chats'], [])
        self.assertEqual(cfg['health']['token'], '${HEALTH_PUSH_TOKEN}')


class PublicHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler, cls.outer, cls.scope = handler_class({'health': {'enabled': False}}, 'synthetic-test-token')
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.outer.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.scope['CFG'] = {'health': {'enabled': False}}
        self.outer.token = 'synthetic-test-token'

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.outer.port, timeout=3)
        try:
            conn.request(method, path, headers=headers or {}, body=body)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_foreign_host_cannot_read_private_api(self):
        code, _ = self.request('GET', '/api/state', {'Host': 'foreign.invalid'})
        self.assertEqual(code, 403)

    def test_cross_origin_cannot_read_or_mutate(self):
        for method, path in [('GET', '/api/state'), ('POST', '/api/config')]:
            code, _ = self.request(method, path, {'Origin': 'https://foreign.invalid'}, '{}')
            self.assertEqual(code, 403)

    def test_disabled_health_rejects_even_with_correct_token(self):
        code, _ = self.request('POST', '/health', {'X-Token': self.outer.token}, '{}')
        self.assertEqual(code, 403)

    def test_enabled_health_requires_nonempty_token(self):
        self.scope['CFG']['health']['enabled'] = True
        self.outer.token = ''
        code, _ = self.request('POST', '/health', body='{}')
        self.assertEqual(code, 403)

    def test_enabled_health_rejects_bad_token_before_parsing(self):
        self.scope['CFG']['health']['enabled'] = True
        code, _ = self.request('POST', '/health', {'X-Token': 'wrong'}, 'not-json')
        self.assertEqual(code, 403)

    def test_valid_token_reaches_payload_validation(self):
        self.scope['CFG']['health']['enabled'] = True
        code, _ = self.request('POST', '/health', {'X-Token': self.outer.token}, 'not-json')
        self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
