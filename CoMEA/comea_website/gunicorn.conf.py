# Gunicorn configuration file
import multiprocessing

# Bind to all interfaces on port 8000
bind = "0.0.0.0:8000"

# Number of worker processes (reduzido para evitar OOM em containers)
# Em produção com recursos limitados, use menos workers
workers = 2

# Worker class
worker_class = "sync"

# Maximum requests a worker will process before restarting
max_requests = 1000
max_requests_jitter = 50

# Timeout for worker processes (in seconds) - aumentado para operações longas
timeout = 600  # 10 minutos

# Graceful timeout - tempo para workers finalizarem antes de serem killed
graceful_timeout = 120

# Preload app para economizar memória compartilhando código
preload_app = True

# Access log
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr

# Log level
loglevel = "info"
