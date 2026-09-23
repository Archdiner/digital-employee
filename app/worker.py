"""The worker. Claims queued runs and documents from Postgres and executes them. Runs inside the container
(MODE=all or MODE=worker). The web tier on Vercel only enqueues. Polling a table beats a queue service at this size."""
import os
import threading
import time
import traceback

from . import agent, db, extract

POLL_SECONDS = int(os.environ.get("WORKER_POLL_SECONDS", "4"))


def claim_run():
    return db.q(
        """update runs set status = 'running' where id = (
             select id from runs where status = 'queued' order by id limit 1 for update skip locked)
           returning id""", one=True)


def claim_document():
    return db.q(
        """update documents set extract_status = 'running' where id = (
             select id from documents where extract_status = 'queued' order by id limit 1 for update skip locked)
           returning id, employee_id""", one=True)


def step() -> bool:
    r = claim_run()
    if r:
        agent.run(r["id"])
        return True
    d = claim_document()
    if d:
        emp = db.q("select model from employees where id = %s", (d["employee_id"],), one=True)
        try:
            extract.extract_facts(d["employee_id"], d["id"], emp["model"])
            db.q("update documents set extract_status = 'done' where id = %s", (d["id"],))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            db.q("update documents set extract_status = 'failed' where id = %s", (d["id"],))
        return True
    return False


def loop():
    while True:
        try:
            if not step():
                time.sleep(POLL_SECONDS)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            time.sleep(POLL_SECONDS)


def recover_orphans():
    """A deploy or crash can leave work marked running. Put it back in the queue; state is saved after every model turn."""
    n = db.q("update runs set status = 'queued' where status = 'running' returning id")
    m = db.q("update documents set extract_status = 'queued' where extract_status = 'running' returning id")
    if n or m:
        print(f"worker: requeued {len(n)} runs, {len(m)} documents")


def start_background():
    recover_orphans()
    threading.Thread(target=loop, daemon=True, name="worker").start()
