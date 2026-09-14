"""One process owns the bounded search slot. Put this server behind HTTPS."""
import os
bind = '127.0.0.1:' + os.environ.get('APF_PORT', '8090')
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 30
graceful_timeout = 270
umask = 0o077
accesslog = None
errorlog = '-'
capture_output = False

# Avoid creating an unneeded process-control socket outside the private runtime directory.
control_socket_disable = True
