"""Exercise the generated service over HTTP without external dependencies."""
from http.client import HTTPConnection
from http.server import HTTPServer
from threading import Thread
import json
import unittest

from app import Handler


class ServiceTests(unittest.TestCase):
    def test_health_and_unknown_routes(self):
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = HTTPConnection('127.0.0.1', server.server_port)
            client.request('GET', '/healthz')
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['status'], 'ok')
            client.request('GET', '/missing')
            response = client.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
