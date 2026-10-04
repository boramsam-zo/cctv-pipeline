"""Independent worker. Restart reuses queue/scene/report receipts."""
import json
import time
from .queue_store import connect
from .flow_service import tick


def main():
    while True:
        try:
            with connect() as conn: tick(conn)
        except KeyboardInterrupt: return
        except Exception as exc:
            print(json.dumps({'worker_error':getattr(exc,'code','worker_cycle_failed')}),flush=True)
        time.sleep(5)


if __name__=='__main__':main()
