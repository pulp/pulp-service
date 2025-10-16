import logging
import sys
import threading
import traceback

log = logging.getLogger(__name__)

def worker_abort(worker):
    pid = worker.pid
    log.info("Gunicorn worker is being killed - {}".format(pid))
    for th in threading.enumerate():
        traceback.print_stack(sys._current_frames()[th.ident])
