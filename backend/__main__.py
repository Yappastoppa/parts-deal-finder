"""Local development only: python -m backend. Production uses the WSGI entry point."""
import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from .api import application


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == '__main__':
    os.umask(0o077)
    port = int(os.environ.get('APF_PORT', '8090'))
    print(f'Local quote API: http://127.0.0.1:{port} (live search is off unless explicitly enabled)')
    with make_server('127.0.0.1', port, application, server_class=Server, handler_class=QuietHandler) as server:
        server.serve_forever()
