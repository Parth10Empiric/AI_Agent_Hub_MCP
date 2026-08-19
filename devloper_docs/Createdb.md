# Creating the database and connecting it to the application

> **Who this is for:** anyone setting this project up on a fresh
> Windows machine, or coming back to it after a reinstall.
>
> This is **Phase 3 step 6** from the preparation plan — the last
> thing to do before writing any `api/` code. Nothing here touches
> `agent/`; Phase 2 keeps working with or without a database.
>
> **Time:** about 15 minutes, most of it the installer.

---

## Table of contents

1. [What we are building](#1-what-we-are-building)
2. [Why not Docker](#2-why-not-docker)
3. [Why not SQLite](#3-why-not-sqlite)
4. [Step 1 — install PostgreSQL](#4-step-1--install-postgresql)
5. [Step 2 — verify the install](#5-step-2--verify-the-install)
6. [Step 3 — create the database in pgAdmin](#6-step-3--create-the-database-in-pgadmin)
7. [Step 4 — configure the application](#7-step-4--configure-the-application)
8. [Step 5 — verify end to end](#8-step-5--verify-end-to-end)
9. [Troubleshooting](#9-troubleshooting)
10. [What comes next](#10-what-comes-next)

---

## 1. What we are building

```
   PostgreSQL 18                    the application
   ─────────────                    ───────────────

   postgresql-x64-18                api/  (Phase 3, not built yet)
   Windows service                        │
        │                                 │ reads DATABASE_URL
        │ listens on                      ▼
        ▼                          SQLAlchemy async engine
   localhost:5432                         │
        │                                 │ speaks the
        ├── postgres    (admin db)        ▼ postgres protocol
        └── agenthub    ← ours       asyncpg driver
              │                           │
              └── public schema ──────────┘
                    │
                    └── tables, created by Alembic in Phase 3.3
```

Three things have to be true before Phase 3 can start:

```
1. the server is running and reachable on 5432
2. a database named `agenthub` exists
3. .env holds a DATABASE_URL the app can actually connect with
```

Everything below exists to make those three true, and
[`scripts/check_db.py`](../scripts/check_db.py) exists to prove it.

---

## 2. Why not Docker

The original plan used a Docker container. We dropped it, and the
reasoning is worth recording because it comes back in Phase 6.

Docker was **only** a delivery mechanism for PostgreSQL. It was never
load-bearing. On this machine:

```
docker --version   ->  29.7.2      the CLI is installed
docker ps          ->  exit 124    the DAEMON never answered (timeout)
```

So Docker Desktop's engine was not running — not a broken install, but
not worth debugging either when a native installer does the same job.

> **The general principle:** when a tool is only a delivery mechanism,
> a delivery problem is not a reason to redesign anything. Swap the
> mechanism.

Docker returns in **Phase 6**, for packaging the whole stack
(frontend, backend, MCP server, postgres, redis). Nothing in Phase 3
depends on it.

---

## 3. Why not SQLite

SQLite needs no install at all, which makes it tempting. It is the
wrong choice here, for one concrete reason:

The Phase 3.2 schema uses two PostgreSQL types that SQLite does not
have:

```
JSONB     messages.routing            RoutingDecision.to_dict()
          tool_executions.arguments   redacted call arguments
          tool_executions.error       ToolError.to_dict()

TEXT[]    plugin_connections.scopes
          tool_executions.coercions
```

You *can* work around both — SQLAlchemy's generic `JSON` type, and
arrays stored as JSON strings. But then you write compatibility shims
now and delete them in Phase 6, and you lose JSONB indexing on exactly
the columns the executions page will filter on.

PostgreSQL is a five-minute install. Take it.

> If the installer genuinely will not cooperate, a free cloud
> PostgreSQL (Neon, Supabase) is a better fallback than SQLite — it is
> still real PostgreSQL, and only `DATABASE_URL` changes.

---

## 4. Step 1 — install PostgreSQL

Download the Windows installer from
[postgresql.org/download/windows](https://www.postgresql.org/download/windows/),
or install it from the command line:

```powershell
winget install PostgreSQL.PostgreSQL.18 --interactive
```

> **Use `--interactive`.** A silent install picks its own superuser
> password, and you then have to go looking for it. The interactive
> installer prompts you to set it — **write that password down**, the
> rest of this document needs it.

Accept the defaults for everything else. Two of them matter later:

```
port                5432
installed as        a Windows service, starting automatically
pgAdmin 4           bundled - you do NOT need a separate download
```

### Where it landed on this machine

The installer offers a location, and on this machine it went to the
**D: drive**, not the usual `C:\Program Files`:

```
D:\Program Files\PostgreSQL\18\
├── bin\                    psql.exe, createdb.exe, pg_ctl.exe
├── data\                   the actual database files - never edit by hand
└── pgAdmin 4\runtime\pgAdmin4.exe
```

Yours may differ. Everywhere this document writes a path, substitute
your own. To find it without guessing:

```powershell
(Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'").PathName
```

---

## 5. Step 2 — verify the install

Three checks, cheapest first. Do them in order — each one rules out a
different failure.

### Is the service running?

```powershell
Get-Service postgresql-x64-18
```

```
Status   Name                  DisplayName
------   ----                  -----------
Running  postgresql-x64-18     postgresql-x64-18 - PostgreSQL Server 18
```

Not running? `Start-Service postgresql-x64-18`

### Is it listening on 5432?

```powershell
netstat -ano | findstr ":5432"
```

```
TCP    0.0.0.0:5432    0.0.0.0:0    LISTENING    10732
```

### Does your password work?

This is the check people skip, and it is the one that fails. It
prompts for the password rather than taking it from a file, so it
tests the password itself and nothing else:

```powershell
& "D:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "select version()"
```

A version string means the server, the port and the password are all
correct. **Do not continue until this works** — every later error will
be this error wearing a different name.

---

## 6. Step 3 — create the database in pgAdmin

**Launch:** Start menu → *pgAdmin 4*, or

```
D:\Program Files\PostgreSQL\18\pgAdmin 4\runtime\pgAdmin4.exe
```

pgAdmin opens in your browser even though it is a desktop app. That is
normal — it runs a small local web server.

### 6.1 The master password

On first launch pgAdmin asks you to **set a master password**.

> **This is not your `postgres` password.** It is pgAdmin's own
> password, encrypting the server passwords pgAdmin saves for you. It
> can be anything you will remember. Confusing the two is the single
> most common first-run mistake.

```
  postgres password   ->  the DATABASE server. Used by the app.
  master password     ->  pgAdmin's saved-password vault. Used by you.
```

### 6.2 Connect to the server

Expand **Servers** in the left tree. The installer normally
pre-registers *PostgreSQL 18* — click it and enter your `postgres`
password, ticking **Save password**.

If nothing is listed, register it: right-click **Servers → Register →
Server…**

| Tab | Field | Value |
|---|---|---|
| General | Name | `Local PG18` (any label you like) |
| Connection | Host name/address | `localhost` |
| Connection | Port | `5432` |
| Connection | Maintenance database | `postgres` |
| Connection | Username | `postgres` |
| Connection | Password | your install password |
| Connection | Save password | ✓ |

**Save.**

> **Maintenance database** is just the database pgAdmin connects to
> first, in order to ask what other databases exist. `postgres` is the
> built-in admin database and always the right answer here. It is not
> the database your app will use.

### 6.3 Create `agenthub`

Right-click **Databases → Create → Database…**

```
Database:   agenthub
Owner:      postgres
```

**Save.**

### 6.4 See it

```
Servers
└── Local PG18
    └── Databases
        ├── agenthub          ← yours
        └── postgres          ← built-in admin database
```

After Alembic runs in Phase 3.3, your tables appear under:

```
agenthub → Schemas → public → Tables
```

> **pgAdmin caches the tree.** After every migration, **right-click →
> Refresh** on the node. Otherwise a migration that worked perfectly
> looks like it did nothing, and you will go debugging Alembic for no
> reason.

### The command-line equivalent

pgAdmin is doing exactly this, if you would rather skip the UI:

```powershell
& "D:\Program Files\PostgreSQL\18\bin\createdb.exe" -U postgres agenthub
```

---

## 7. Step 4 — configure the application

### 7.1 The `.env` block

`.env` is gitignored and holds real secrets. Add:

```ini
# Phase 3 - Backend

DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/agenthub

CREDENTIAL_ENCRYPTION_KEY=<generated, see below>
JWT_SECRET_KEY=<generated, see below>
ACCESS_TOKEN_MINUTES=15
REFRESH_TOKEN_DAYS=30
```

### 7.2 The `+asyncpg` part is not optional

```
postgresql://...              ← WRONG. Picks the SYNCHRONOUS driver.
postgresql+asyncpg://...      ← correct
```

This is the same bug we removed from [`agent/loop.py`](../agent/loop.py)
in step 4 of the preparation plan, in a new place.

A synchronous driver blocks the thread it runs on. In an async server
that thread **is** the event loop, so every database query would
freeze every other user's request — while looking completely normal in
single-user testing. It costs one word to prevent and hours to
diagnose.

`check_db.py` refuses to continue if the URL is missing `+asyncpg`.

### 7.3 Generating the two secrets

```powershell
# CREDENTIAL_ENCRYPTION_KEY - encrypts plugin_connections.credentials_enc
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT_SECRET_KEY - signs access tokens
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

What each one costs you if it changes:

```
CREDENTIAL_ENCRYPTION_KEY   every stored OAuth token becomes
                            permanently unreadable. Back it up
                            before deploying anything real.

JWT_SECRET_KEY              every user is logged out. Annoying,
                            not destructive.
```

### 7.4 Alembic does not need its own URL

An earlier draft of this setup added `ALEMBIC_DATABASE_URL` with a
synchronous driver. **It was removed.** Phase 3.3 uses:

```bash
alembic init -t async alembic
```

The `-t async` template runs migrations through asyncpg, so it reuses
`DATABASE_URL` and no second driver has to be installed.

### 7.5 `.env.example`

[`.env.example`](../.env.example) is committed; `.env` never is. It
lists every key with empty values and the command to generate each
secret, so a new machine can be set up without guessing what
configuration exists.

```powershell
copy .env.example .env      # then fill it in
```

Verify the split is right:

```bash
git check-ignore -v .env            # must be ignored
git check-ignore -v .env.example    # must print nothing
```

---

## 8. Step 5 — verify end to end

```powershell
python scripts/check_db.py
```

```
Phase 3 step 6 - database check
----------------------------------------------------
  OK   DATABASE_URL set, async driver
  OK   connected: PostgreSQL 18.6
  OK   database : agenthub
  OK   JSONB and TEXT[] supported
  OK   CREDENTIAL_ENCRYPTION_KEY encrypts and decrypts
  OK   JWT_SECRET_KEY set (64 chars)
----------------------------------------------------
All checks passed. Ready for Phase3.md step 1 (api/ skeleton).
```

### What each check buys you

| Check | The later bug it prevents |
|---|---|
| async driver | every query silently serialising all users |
| connection | "SQLAlchemy is broken" when it is a stopped service |
| `agenthub` exists | Alembic creating tables in the wrong database |
| JSONB + TEXT[] | discovering at Phase 3.2 that the schema will not build |
| Fernet key valid | plugin connections failing only once a user connects one |
| JWT secret length | a trivially forgeable token in production |

The script exits non-zero on the first failure and prints the fix, so
it is safe to put in CI later.

---

## 9. Troubleshooting

### `password authentication failed for user "postgres"`

By far the most common. The server is fine; the password is wrong.

```
1. Test the password on its own, outside the app:

   & "D:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "select 1"

2. It worked          -> the password in .env has a typo. Watch for a
                         trailing space, or a special character that
                         needs URL-encoding (see below).

3. It also failed     -> you have the wrong password. Reset it:
                         see "resetting a forgotten password" below.
```

**Special characters must be URL-encoded** in `DATABASE_URL`, because
the URL parser sees them first:

```
@  ->  %40        :  ->  %3A        /  ->  %2F
#  ->  %23        ?  ->  %3F        %  ->  %25
```

A password of `p@ss:word` becomes `p%40ss%3Aword`. This produces
exactly the same error message as a genuinely wrong password, which is
why it is worth checking early.

### `database "agenthub" does not exist`

The password is right and you reached the server — you just skipped
[step 3](#6-step-3--create-the-database-in-pgadmin). Create it.

### `ConnectionRefusedError` / `connection refused`

Nothing is listening. The service is stopped:

```powershell
Get-Service postgresql-x64-18
Start-Service postgresql-x64-18
```

If it refuses to start, read the log:
`D:\Program Files\PostgreSQL\18\data\log\`

### `ModuleNotFoundError: No module named 'asyncpg'`

The virtual environment is not active, or dependencies are not
installed:

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### pgAdmin will not accept the master password

The master password is stored separately from any server password and
cannot be recovered. Reset it by deleting pgAdmin's config database —
you lose saved server registrations, nothing else:

```
%APPDATA%\pgAdmin\pgadmin4.db
```

Close pgAdmin first, delete the file, reopen, set a new master
password, and register the server again ([step 6.2](#62-connect-to-the-server)).

### Resetting a forgotten `postgres` password

Only if `psql` genuinely will not let you in.

```
1. Open  D:\Program Files\PostgreSQL\18\data\pg_hba.conf  as Administrator

2. Find the IPv4 local line:

       host    all    all    127.0.0.1/32    scram-sha-256

   Change the last field to `trust`:

       host    all    all    127.0.0.1/32    trust

3. Restart-Service postgresql-x64-18

4. Connect with NO password and set a new one:

       & "D:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost
       ALTER USER postgres WITH PASSWORD 'your-new-password';
       \q

5. PUT `scram-sha-256` BACK and restart again.
```

> **Step 5 is not optional.** `trust` means anyone who can reach port
> 5432 is a superuser with no password at all. Leaving it is how a
> development machine becomes an incident.

### The tables are not showing in pgAdmin

Right-click the node → **Refresh**. pgAdmin caches the tree and does
not notice migrations. If they are still missing, confirm which
database the migration actually hit:

```sql
SELECT current_database();
```

---

## 10. What comes next

Step 6 is the last of the preparation work. With all six checks green:

```
✅ 1. token rotated + purged from git history
✅ 2. requirements.txt UTF-8, Phase 3 dependencies installed
✅ 3. dead code removed
✅ 4. agent/loop.py uses AsyncClient - the event loop is free
✅ 5. run_agent emits on_event - SSE has something to stream
✅ 6. PostgreSQL running, agenthub created, .env verified
      ↓
   Phase3.md step 1 - the api/ skeleton
```

> Then read the **three hard problems** at the top of
> [`phases_doc/Phase3.md`](../phases_doc/Phase3.md) before writing
> code, and do not skip its step 4 — the `MCPSessionProvider` is what
> makes the chat and streaming endpoints possible.

## Related documents

| Document | What it covers |
|---|---|
| [`phases_doc/Phase3.md`](../phases_doc/Phase3.md) | The backend plan this prepares for |
| [`devloper_docs/Understand_Phse2.md`](Understand_Phse2.md) | How the Agent Engine works |
| [`scripts/check_db.py`](../scripts/check_db.py) | The verification script |
| [`.env.example`](../.env.example) | Every configuration key |
