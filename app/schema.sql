-- One database. Every row hangs off an employee (one employee = one firm, one role, one user).
create table if not exists employees (
  id          serial primary key,
  name        text not null,                  -- "Summer Analyst"
  role        text not null,                  -- one line: the job
  firm        text not null,                  -- "Investcorp"
  firm_notes  text not null default '',       -- how the firm works, where things live
  model       text not null default 'gpt-5.6-sol',
  created_at  timestamptz not null default now()
);

-- Plain text, one per task. First line = when to use it.
-- employee_id null = general library (reused everywhere). Otherwise learned at that firm and private to it.
create table if not exists skills (
  id          serial primary key,
  employee_id int references employees(id) on delete cascade,
  name        text not null,
  body        text not null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create unique index if not exists skills_general_name on skills(name) where employee_id is null;
create unique index if not exists skills_employee_name on skills(employee_id, name) where employee_id is not null;

-- Every file the employee has: sources it reads, past reviews it learns from, documents it produces.
create table if not exists documents (
  id            serial primary key,
  employee_id   int not null references employees(id) on delete cascade,
  filename      text not null,
  kind          text not null default 'source',   -- source | past_review | output
  content_type  text,
  bytes         bytea not null,
  text          text,                             -- extracted plain text (null for outputs)
  doc_date      date,
  created_at    timestamptz not null default now()
);

-- The fact store. The only place numbers may come from. Value kept verbatim as written in the source.
create table if not exists facts (
  id              serial primary key,
  employee_id     int not null references employees(id) on delete cascade,
  document_id     int not null references documents(id) on delete cascade,
  entity          text,               -- fund, company, or portfolio
  metric          text not null,      -- "Revenue", "EBITDA margin", "Net debt"
  period          text,               -- "Q2 2026", "FY2025", "LTM Jun-26"
  value           text not null,      -- "12.4", "18.2%", "(3.1)" exactly as written
  unit            text,               -- "USD m", "%", "x"
  source_location text,               -- "Summary tab row 12", "p.3 table 2"
  doc_date        date,
  created_at      timestamptz not null default now()
);
create index if not exists facts_employee on facts(employee_id);

-- One run = one piece of work asked for. Conversation state lives here so a run can pause on a question and resume.
create table if not exists runs (
  id                 serial primary key,
  employee_id        int not null references employees(id) on delete cascade,
  task               text not null,
  status             text not null default 'running',  -- running | waiting | done | failed
  question           text,                             -- set while status = waiting
  answer             text,
  notes              text,                             -- what it was sure of, guessing at, didn't know
  output_document_id int references documents(id),
  state              jsonb not null default '[]',      -- model conversation (input items)
  error              text,
  created_at         timestamptz not null default now(),
  finished_at        timestamptz
);

-- The work log. Every file opened, every skill used, every fact cited. Never shown to the CFO; read by the employee to explain itself.
create table if not exists worklog (
  id      serial primary key,
  run_id  int not null references runs(id) on delete cascade,
  at      timestamptz not null default now(),
  action  text not null,
  detail  jsonb not null default '{}'
);
create index if not exists worklog_run on worklog(run_id);

-- Saved edits (build step 6). Table exists now so nothing is lost from the first run; nothing reads it yet.
create table if not exists edits (
  id          serial primary key,
  run_id      int not null references runs(id) on delete cascade,
  before_text text,
  after_text  text,
  skills_used text[],
  created_at  timestamptz not null default now()
);

-- Linked accounts (Google Workspace, Microsoft 365). One per provider per employee. Tokens are refreshed in place.
create table if not exists connections (
  id            serial primary key,
  employee_id   int not null references employees(id) on delete cascade,
  provider      text not null,            -- google | microsoft
  account       text,                     -- email of the linked account
  access_token  text not null,
  refresh_token text,
  expires_at    timestamptz,
  scopes        text,
  created_at    timestamptz not null default now(),
  unique (employee_id, provider)
);

-- Every output a run produces (a run can make several files). Set on documents of kind 'output'.
alter table documents add column if not exists run_id int references runs(id) on delete set null;
create index if not exists documents_run on documents(run_id);
-- A run is a conversation: the first message is the task, later messages continue it.
alter table runs add column if not exists title text;
