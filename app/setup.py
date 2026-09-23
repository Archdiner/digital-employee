"""Hire from a job description. One model call proposes the setup; a person approves it on the form.
The model may suggest skills. It cannot create them. That rule is the whole point of this file."""
from . import db, llm

SCHEMA = {
    "name": "employee_setup",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "role", "use_general", "new_skills", "questions_for_the_firm"],
        "properties": {
            "name": {"type": "string", "description": "Short working name, e.g. 'FM Analyst'"},
            "role": {"type": "string", "description": "One line: the job, in the firm's words"},
            "use_general": {"type": "array", "items": {"type": "string"}, "description": "Names of general skills that apply, verbatim"},
            "new_skills": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "body"],
                "properties": {"name": {"type": "string", "description": "kebab-case"}, "body": {"type": "string", "description": "First line: when to use it. Then the method, as a competent analyst would write it for a new joiner. 150-350 words."}}}},
            "questions_for_the_firm": {"type": "array", "items": {"type": "string"}, "description": "What the firm notes must answer before this employee can do the job well"},
        },
    },
}

INSTRUCTIONS = """You are setting up an AI employee from a job description, for a finance firm.
You are given the general skill library (name, when-to-use line, full text). Decide:
1. which general skills apply to this job (use_general, names verbatim);
2. which duties no general skill covers, and draft skills for them (new_skills). Merge duties that share a method: budgeting, forecasting and planning are one skill; audit support and regulatory filings are one skill; trend analysis, performance monitoring and "areas for improvement" are one skill. Aim for 3, never more than 5. Write each the way a good analyst would brief a new joiner: concrete steps, what to check, what to never do. Method, not personality. First line says when to use it.
3. questions_for_the_firm: only what blocks the first piece of work (where the numbers live, the definitions that change a number, the calendar, the mandatory templates). At most 6. Specific, answerable in a sentence each.
Do not invent firm facts. Do not pad. Skip duties that are pure human coordination with no document or number output. Fewer, sharper skills beat a long list nobody reads."""


def draft(job_description: str, firm: str, model: str):
    general = db.q("select name, body from skills where employee_id is null order by name")
    lib = "\n\n".join(f"### {g['name']}\n{g['body']}" for g in general)
    text = f"Firm: {firm or 'unknown'}\n\nJOB DESCRIPTION:\n{job_description}\n\nGENERAL SKILL LIBRARY:\n{lib}"
    out = llm.structured(model=model, instructions=INSTRUCTIONS, text=text, schema=SCHEMA, effort="high")
    names = {g["name"] for g in general}
    out["use_general"] = [n for n in out["use_general"] if n in names]
    return out
