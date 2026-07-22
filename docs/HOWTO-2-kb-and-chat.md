# How-to 2. Knowledge base, commands, chat and voice

*English · [Русский](HOWTO-2-kb-and-chat.ru.md)*

This continues [HOWTO-1-setup.md](HOWTO-1-setup.md): the stack is up
(`docker compose up -d`) and a model is connected. Now let's give the assistant
content, and give your colleagues a chat with voice.

Every command below runs inside the container:
`docker compose exec app onbo ...`. The project directory is mounted into the
container, so file paths are the ordinary ones, relative to the repository root.

---

## 1. How the knowledge base is organised

- A **collection** is a folder of material with access tags (`department`,
  `roles`).
- A collection holds **documents** (long text, split into chunks) and **Q&A
  pairs** (a ready answer to a specific question).
- Postgres is the canonical store — that is what you edit. Qdrant is a derived
  search index, always rebuilt from Postgres (`onbo kb reindex`).
- Access is filtered during retrieval: an employee only sees material for their
  department/roles plus the common one. The filter is built from the profile in
  the database, not from the question text — "show me other people's documents"
  is not a thing that can work.

A pair inherits the collection's tags unless it sets its own.

## 2. Filling it: four ways

### 2.1. Ready-made Q&A from a YAML file (the main one)

A file shaped like `config/kb.example.yaml` (copy it as a starting point):

```yaml
qa:
  - question: "How do I request time off?"
    answer: "In the HR portal: Profile → Time off → New request, 14 days ahead."
    collection: hr
  - question: "How do I issue a refund?"
    answer: "Accounting handles refunds: attach the order number and the reason."
    collection: accounting
    department: accounting      # who sees it: department
    roles: [accountant]         # and/or roles
    video_url: /media/kb/refund.mp4   # optional walkthrough video
    links:                            # optional — where to click, see below
      - title: "Refunds"
        url: "https://app.acme.com/orders/refunds"
```

`links` is a field of the pair, like `question` or `roles` — not something you
write inside the answer. Keep URLs out of `answer`: the text explains what to do,
`links` says where to go. onbo appends the list at the bottom of the reply
("Ссылки:") for plain clients, and hands it to the web channel as structured
`results[].links`, so a widget can render real buttons (see
[guide 3](HOWTO-3-embed-chat.md)). A pair with nothing to link to simply omits
the field.

Import (idempotent — re-importing updates pairs instead of duplicating them):

```bash
docker compose exec app onbo kb import docs/my_faq.yaml
```

### 2.2. Documents: a file, a directory or a website

```bash
docker compose exec app onbo kb add-doc docs/handbook.md --collection hr
docker compose exec app onbo kb add-doc docs/                --collection hr
docker compose exec app onbo kb add-doc https://wiki.acme.com/onboarding --collection common
docker compose exec app onbo kb add-doc docs/finance.md --collection accounting \
    --department accounting --roles accountant
```

Documents are chunked automatically. Formats: `.md`, `.txt`, `.rst`, plus `.pdf`
and `.docx` (their extra dependencies ship in the image). A directory is walked
recursively and unsupported file types are skipped.

### 2.3. A single pair from the command line

```bash
docker compose exec app onbo kb add-qa \
  "How do I get access to the CRM?" "Raise a Jira ticket in project ACCESS, template «CRM»." \
  --collection common
```

### 2.4. The web panel

`http://localhost:18000/admin` — add, edit or delete a pair, attach a video, run
a reindex. The panel is still a single page and has **no password**: fine for a
dozen pairs, not for real volume or an open network (see the backlog in
`.claude/PLAN3.md`).

### 2.5. A draft knowledge base straight from the product's code (Claude Code)

The fastest way to a first knowledge base: let Claude Code read your product's
sources and write the Q&A itself. The repository ships three skills in
`.claude/skills/` — they are picked up automatically when Claude Code is started
**from the onbo repository root**:

| Skill | What it produces |
|---|---|
| `/kb-from-code` | a draft Q&A file for the knowledge base |
| `/actions-from-code` | a draft `config/actions.yaml` (section 4) |
| `/kb-video` | an mp4 walkthrough for a Q&A pair or a welcome digest |

**Building the knowledge base, step by step:**

1. Open Claude Code in the onbo directory and run the skill, giving it the path
   to the product's sources:

   ```
   /kb-from-code ~/projects/acme-crm
   ```

   Optional extras it will otherwise assume: the language of the knowledge base
   (Russian by default — say "in English" if that is what you need), the target
   collection (`common` by default) and any additional languages for a
   multilingual product.

2. It surveys the repository in order of usefulness — README and docs, routes
   and controllers, the settings/profile/onboarding screens, the i18n files
   (the best source of the exact button and menu names), then templates — and
   builds an inventory of features. It does not read the whole repository.

3. It writes 15–40 pairs to
   `tmp/kb-from-code/<project>/draft_faq.yaml`, in the language of a new hire,
   not of an engineer: no API paths, no function or table names. Where a UI path
   could not be confirmed it leaves a `# TODO: check the path in the UI`
   comment. Role guards found in the code (`@requires_role("accountant")`,
   `if user.is_staff`) become `department` / `roles` on the pair, so the
   material stays visible only to the right people.

4. It then reports what it produced — how many pairs, which sections they cover,
   which ones are restricted, where the TODOs are. **Read the draft.** The model
   infers details the product does not actually have, and this is the cheapest
   possible moment to catch that.

5. On your go-ahead it imports:

   ```bash
   onbo kb import tmp/kb-from-code/acme-crm/draft_faq.yaml
   ```

   If onbo is already running, the skill first checks `GET /admin/api/qa` and
   skips questions that already exist. Import is idempotent on
   `collection + question` anyway, so a re-run updates instead of duplicating.

6. Open `/admin` and edit the wording where it reads wrong.

Cases where it asks instead of guessing: an API-only product with no UI (it
cannot write "Settings → …" without one), a product whose interface is not in
Russian, and a monorepo (which service are we onboarding?).

Videos are a separate step: `/kb-video` records a silent screen capture of one
UI flow, voices it over, and attaches the resulting mp4 to the pair — see
section 3.

### 2.6. Housekeeping

```bash
docker compose exec app onbo about        # reindex the self-docs ("what can you do")
docker compose exec app onbo kb status    # how many pairs/documents are in there
docker compose exec app onbo kb reindex   # rebuild Qdrant from Postgres
curl -s http://localhost:18000/admin/api/stats   # how many collections/documents/pairs
```

## 3. Walkthrough videos attached to answers

Put an mp4 into `media/kb/` (the directory is visible both on the host and in the
container) and set `video_url: /media/kb/refund.mp4` on the pair. The web channel
serves `/media` itself. Telegram needs an absolute address — set
`MEDIA_BASE_URL=https://onbo.acme.com` in `.env` and it is prefixed onto links.

## 4. The command set (actions on your product)

A command is an entry in `config/actions.yaml`. Change the registry and the
assistant's behaviour changes — no code involved.

Three modes, by risk:

| Mode | When | Behaviour |
|---|---|---|
| `chat` | low risk (language, theme) | executed immediately |
| `confirm` | important but reversible (email, phone) | an Ok/Cancel card first |
| `link` | sensitive (password, payments) | never executed in chat, a link to the product page is returned |

`sensitive: true` forces the action into `link` mode.

A simple action with no code at all — the `api:` block is executed generically:

```yaml
actions:
  set_language:
    description: "Change the interface language"
    mode: chat
    params:
      lang: { type: enum, values: [ru, en], required: true }
    api:
      method: POST
      path: "/api/users/{user_id}/language"     # relative to PRODUCT_API_BASE
      body: { language: "{lang}" }
      success_message: "Interface language switched to «{lang}»."
```

With confirmation and custom validation:

```yaml
  change_email:
    description: "Change email"
    mode: confirm
    confirm_prompt: "Change the email to {new_email}?"
    params:
      new_email: { type: email, required: true }
    handler: handlers.actions.change_email      # optional: your own Python handler
    api:
      method: POST
      path: "/api/users/{user_id}/email"
      body: { email: "{new_email}" }
      success_message: "Email changed to {new_email}."
```

Sensitive — a link and nothing else:

```yaml
  change_password:
    description: "Change password"
    mode: link
    link_url: "https://app.example.com/settings/security"
    sensitive: true
```

A **pipeline** runs several actions from one phrase with a single confirmation:

```yaml
pipelines:
  new_order:
    description: "Process an order: both invoices, then send to the client"
    mode: confirm
    confirm_prompt: "Process order {order_id} and send the invoices?"
    roles: [accountant]
    params:
      order_id: { type: string, required: true }
    steps:
      - action: create_invoice_internal
        params: { order_id: "{order_id}" }
      - action: create_invoice_client
        params: { order_id: "{order_id}" }
      - action: send_invoice_to_client
        params: { order_id: "{order_id}" }
    on_error: stop        # stop = halt on the first failure, continue = keep going
```

Only a `chat`/`confirm` action may be a pipeline step: sensitive ones never get
batched.

To draft a registry from the product's source:

```bash
docker compose exec app onbo scan /path/to/your/project    # prints a YAML draft
```

or the `/actions-from-code <path>` skill in Claude Code. Always proofread the
draft: generation touches passwords and personal data.

After editing `config/actions.yaml`:

```bash
docker compose restart app
```

## 5. The welcome digest for a new hire

On the first message (or on an explicit call) the assistant sends a digest of
what is available to that particular role. Configured in
`config/settings.yaml`:

```yaml
welcome:
  enabled: true
  video:
    accounting: /media/welcome/accounting.mp4   # starter video per department or role
  text_overrides:
    support: "Hi! Start with the «Handling tickets» section."
```

The digest is **generated from what actually exists**, not written by hand: the
commands in `config/actions.yaml` that this person is allowed to run, plus the
knowledge-base collections they can see and a few sample questions from them. The
model only smooths the wording; it is not asked to invent features.

So if the greeting mentions commands you do not have, they are in
`config/actions.yaml`. The file ships with demo entries — clear them out when you
start on your own product:

```yaml
actions: {}
pipelines: {}
```

```bash
docker compose restart app
# the digest is shown once per person, so clear the mark to see it again:
docker compose exec postgres psql -U onbo -d onbo -c "UPDATE app_user SET welcomed_at = NULL;"
```

`text_overrides` replaces the generated text entirely for a department or role,
and `features.welcome: false` switches the whole thing off.

Check it:

```bash
curl -s -X POST http://localhost:18000/welcome \
  -H 'Content-Type: application/json' -d '{"user_id":"acc1"}'
```

## 6. The web chat, with voice

The repository ships a ready chat page: text, microphone and confirmation cards.
The application serves static files from `media/`, so dropping the page there is
enough — it ends up on the same origin as the API, and no CORS setup is needed:

```bash
cp docs/examples/chat.html media/
# open http://localhost:18000/media/chat.html
```

What the page does:

- a `user_id` field (demo users `acc1`, `sup1`, `admin`) — it decides what the
  person sees and is allowed to do;
- requests the welcome digest on open;
- text goes to `POST /chat`;
- the 🎤 button records from the microphone and posts to `POST /voice`, where
  whisper transcribes it and the exact same pipeline as for text takes over;
- actions with `mode: confirm` appear as a card with Ok/Cancel buttons, and the
  answer goes to `POST /confirm`.

Browsers only grant microphone access in a secure context: `localhost` counts, a
remote host needs HTTPS.

Voice is switched off globally with `STT_ENABLED=false`, or per channel with
`channels.web.accept_voice` in `config/settings.yaml`.

## 7. The HTTP API — for embedding into your own UI

```bash
# a question or a command
curl -s -X POST http://localhost:18000/chat -H 'Content-Type: application/json' \
  -d '{"user_id":"acc1","text":"change my email to new@acme.com","locale":"en"}'
# → {"text":"Confirmation needed:\n• Change the email to new@acme.com?",
#    "results":[{"status":"needs_confirm","action":"change_email",...}],"welcomed":false}

# the confirmation
curl -s -X POST http://localhost:18000/confirm -H 'Content-Type: application/json' \
  -d '{"user_id":"acc1","action":"change_email","approved":true}'
# → {"status":"done","message":"Email changed to new@acme.com."}

# voice: multipart with user_id / audio / locale
curl -s -X POST http://localhost:18000/voice \
  -F user_id=acc1 -F locale=en -F audio=@voice.webm
# → {"text":"...", "transcript":"switch the language to English"}

# the welcome digest
curl -s -X POST http://localhost:18000/welcome -H 'Content-Type: application/json' \
  -d '{"user_id":"acc1"}'
```

Statuses in `results[].status`: `answer` (from the knowledge base), `done`
(executed), `needs_confirm` (waiting for Ok), `needs_input` (missing
parameters), `link` (a link was handed out), `dry_run` (no product backend
configured), `failed`.

The full schema is at `http://localhost:18000/docs` (Swagger).

When embedding the chat into your own frontend, remember that `user_id` is taken
from the request body as-is. Put your own proxy in front of onbo — one that
verifies the employee's session and injects the identifier — or anyone can claim
to be anyone. There is no chat page at `/` yet; the demo page above is it.

## 8. Telegram

```ini
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:AA...
```

```bash
docker compose exec -d app onbo serve telegram
```

The bot accepts voice messages and transcribes them with the same engine;
`/start` sends the welcome digest.

## 9. The manifest for external AI agents

`GET /llm.json` (and `/.well-known/llm.json`) publishes the public part of the
knowledge base and the command list in machine-readable form — so that a
third-party assistant visiting your site understands what the product can do.
Export it as a file for static hosting:

```bash
docker compose exec app onbo llm-export --out llm.json
```

Private material (anything tagged with `department`/`roles`) never reaches the
manifest.

## 10. A running order for a new project

1. Bring the stack up and connect a model — file 1.
2. Set `PRODUCT_API_BASE` and load employee profiles into `app_user` — file 1,
   sections 6–7.
3. Collect 20–30 Q&A pairs from the questions newcomers actually ask
   (`/kb-from-code` → review → `onbo kb import`).
4. Describe 3–5 commands in `config/actions.yaml`, starting with harmless ones
   (`chat`); make anything sensitive `link` from the start.
5. Test against the demo backend (`dry_run`/`done` in the replies), then point
   it at the real API.
6. Hand people `chat.html` (or embed `/chat` into your own UI), with `/admin`
   closed off from outside access.
