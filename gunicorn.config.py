# gunicorn.config.py

# The log level (debug, info, warning, error, critical)
loglevel = "info"  
accesslog = "./log/access.log"
errorlog = "./log/error.log"

# 捕获输出到错误日志
capture_output = True

timeout = 30
keepalive = 2

workers = 4
threads = 2
worker_class = "sync"

bind = "0.0.0.0:60001"
