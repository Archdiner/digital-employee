# Where It Runs and How It Connects

**We run one system, in our own cloud account. Each firm connects their documents and their login to it.**

Same code for every firm. No installs in customer accounts.

## What we connect to

1. **Their documents.** Microsoft Graph for Microsoft 365 firms. Google APIs later, if a Google firm shows up.
2. **Their login.** Single sign-on with their identity provider.

Setting up a new firm: their IT approves our app in their Microsoft admin panel (one screen, read-only scopes), we add them to Teams, done.

## Where we host (current)

Zybit's Azure subscription (existing credits), resource group `zybit-emp-rg`, Central US alongside the other zybit projects. Region is one variable in `infra/deploy.sh`; a Gulf-resident deployment (UAE North, or AWS Bahrain per the original plan) is a config change, not a rewrite.

## Shape

- **One container** running our code (Azure Container Apps).
- **One small Postgres** (fact store, work log, saved edits, documents).
- **Key Vault** for the database URL and any per-firm connection secret.
- **Managed identity** for everything else: registry pull, Key Vault read, model calls. No keys in code.
- **Model**: Azure OpenAI (GPT-5.6 Sol by default), a Microsoft first-party service under Microsoft's DPA/BAA. Model is a setting.

## Keeping firms apart

- Now: one database, every row tagged with the employee/firm.
- From firm two: separate database per firm.
- Never by default: separate infrastructure per firm.

## What their security team will ask for

SOC 2 Type II (start early), data in-region and encrypted, no-training promise, read-only scoped document access, SSO, full access log (the work log), delete on request.

## Do first

Get the Graph app approved in the customer's Microsoft admin. One-screen approval; most likely to sit in an inbox for two weeks.
