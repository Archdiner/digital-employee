"""One Postgres. psycopg3, a connection per unit of work. Small on purpose."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/employee")
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills" / "general"


@contextmanager
def conn():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as c:
        yield c


def q(sql, params=None, one=False):
    with conn() as c:
        cur = c.execute(sql, params)
        if cur.description is None:
            return None
        return cur.fetchone() if one else cur.fetchall()


def init():
    schema = (Path(__file__).parent / "schema.sql").read_text()
    with conn() as c:
        c.execute(schema)
        # derived facts (compute tool) were added after the first schema
        c.execute("alter table facts alter column document_id drop not null")
        c.execute("alter table facts add column if not exists formula text")
        c.execute("alter table facts add column if not exists derived_from int[]")
    seed_general_skills()


def seed_general_skills():
    """The general library lives in the repo. Load it into the table so the UI can list it."""
    with conn() as c:
        for path in sorted(SKILLS_DIR.glob("*.md")):
            c.execute(
                """insert into skills (employee_id, name, body) values (null, %s, %s)
                   on conflict (name) where employee_id is null
                   do update set body = excluded.body, updated_at = now() where skills.body <> excluded.body""",
                (path.stem, path.read_text()),
            )


def log(run_id, action, **detail):
    q("insert into worklog (run_id, action, detail) values (%s, %s, %s)", (run_id, action, Jsonb(detail)))
