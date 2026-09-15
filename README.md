# Digital Employee

One AI employee per firm. First job: the quarterly portfolio review for a private equity CFO. See `docs/`.

Five things, nothing else:

| Thing | Where |
|---|---|
| Fact store: every number, tagged with file and date. The only source of numbers. | `facts` table, filled by `app/extract.py` |
| Skills: plain text, one per task, first line says when to use it | `skills/general/*.md` (shared) and the `skills` table (per firm) |
| Firm notes: how the firm works, where things live | `employees.firm_notes` |
| Work log: every file opened, skill read, fact cited | `worklog` table, written by `app/agent.py` |
| The document: a .docx the CFO edits | `documents` table, rendered by `app/render.py` |

The employee is a model with seven tools and a log (`app/agent.py`). It decides what to read, what to look up,
what to compute, and when to ask. Two guarantees are enforced in code, not in the prompt:

- A number reaches the document only as a `{{fact:ID}}` reference. The renderer prints the stored value and
  rejects any other digits except years, quarter labels and list numbering. Derived numbers go through the
  `compute` tool, which stores the result as a new fact with its formula.
- Everything it did is in the work log. "Where did this number come from" is answered from the log.

## Run locally

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
az login                                     # model calls use your Entra identity
export DATABASE_URL=$(az keyvault secret show --vault-name zybit-emp-kv -n database-url --query value -o tsv)
uvicorn app.main:app --reload --port 8080    # http://localhost:8080
```

## Deploy

```sh
./infra/deploy.sh infra   # resource group, identity, roles, Key Vault, Postgres, Container Apps env, model deployment
./infra/deploy.sh app     # build the image in ACR, create or update the Container App
```

Everything is plain `az` commands in one file. Names, region and model are variables at the top.

## Model

Azure OpenAI `gpt-5.6-sol`, Entra auth, no keys. Azure OpenAI is a Microsoft first-party service under
Microsoft's DPA/BAA. Model is a per-employee setting; any Azure OpenAI deployment works.

## Linked accounts (Google Workspace, Microsoft 365)

Per employee, under "Linked accounts" in the UI. Plain OAuth; tokens in the `connections` table, refreshed on use.
Once linked the employee gets seven more tools: search, read, import (into the fact store), create a document
(docx / pptx / xlsx, or native Google Doc / Sheet / Slides, saved here or in Drive / OneDrive), update a range in an
existing shared spreadsheet, append to an existing shared document, share. Every string it writes anywhere passes
through the same `{{fact:ID}}` check as the review.

**Google** needs an OAuth client (one-time, ~5 minutes, in the getzybit.com Workspace):

1. console.cloud.google.com → new project `zybit-digital-employee`.
2. APIs & Services → Library → enable **Google Drive API, Google Docs API, Google Sheets API, Google Slides API**.
3. OAuth consent screen → User type **Internal** → app name "Digital Employee" → save.
4. Credentials → Create credentials → OAuth client ID → **Web application** → authorised redirect URIs:
   `http://localhost:8080/connect/google/callback` and
   `https://emp-app.jollymushroom-dec64f0f.centralus.azurecontainerapps.io/connect/google/callback`.
5. Store the pair and redeploy:
   ```sh
   az keyvault secret set --vault-name zybit-emp-kv -n google-client-id --value '<client id>'
   az keyvault secret set --vault-name zybit-emp-kv -n google-client-secret --value '<client secret>'
   SKIP_BUILD=1 ./infra/deploy.sh app
   ```

**Microsoft** is configured: app registration `cd3458ee-…` has delegated `Files.ReadWrite.All`, `Sites.ReadWrite.All`
with tenant-wide admin consent in the zybit tenant; the client secret is in Key Vault (`graph-client-secret`).
The zybit Entra tenant has **no SharePoint Online licence**, so OneDrive/SharePoint/Teams do not exist there; test
Microsoft 365 against a licensed tenant (a Microsoft 365 Business trial on this tenant, or the customer's).

## Connecting a firm's documents

App registration `Zybit Digital Employee` (multi-tenant, application permissions `Sites.Read.All` and
`Files.Read.All`, read-only). A firm's IT approves it once:

```
https://login.microsoftonline.com/organizations/adminconsent?client_id=cd3458ee-bdae-4f32-92e0-fe3057be0759
```

Until then, documents are uploaded in the operator UI.

## Build order and what is done

1. Fact store ✓  2. Document output ✓  3. Skills + firm notes ✓  4. Work log ✓
5. Teams with ask-and-wait: the ask-and-wait primitive exists (`runs.status = waiting`); Drive/OneDrive/SharePoint file
   access exists; the Teams bot transport does not yet (needs a licensed Microsoft 365 tenant to build against).
6. Save edits: table exists, nothing writes to it.  7. Test set  8. Skill updates: not started, by design.
