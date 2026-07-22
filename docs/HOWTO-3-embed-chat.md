# How-to 3. Embedding the chat in your product: users, roles, widget

*English · [Русский](HOWTO-3-embed-chat.ru.md)*

This continues [HOWTO-2-kb-and-chat.md](HOWTO-2-kb-and-chat.md): the stack is
up and the knowledge base has content. Now let's put a chat window inside your
own product, so that a person who logged into *your* app talks to onbo as
themselves — and sees only what their department and roles allow.

---

## 1. The one rule

`POST /chat` has no login of its own. Whatever tells it who is asking decides
what that person is allowed to see, so **it must not come from the browser
unprotected**. If it does, anyone can open the console and send
`{"user_id": "ceo@acme.com", "text": "…"}` and read the CEO's material.

There are two safe ways to satisfy that rule. Pick one:

| | **A. Proxy** | **B. Signed token** |
|---|---|---|
| Who knows the roles | onbo, from its own `app_user` table | your backend, inside the token |
| Directory sync | needed | **none** |
| Browser talks to | your backend | onbo directly |
| Extra setup | one endpoint | a shared secret + a few lines of signing |
| Good for | a few hundred people, a directory that rarely moves | tens of thousands of users, constant joins and leaves |

Mode B exists because copying a user directory into onbo stops being reasonable
at scale: a million people with a thousand joining and a thousand leaving daily
is a synchronisation job nobody wants to own. In mode B onbo stores no users at
all — every request carries the profile with it, signed.

---

## 2. Mode A — your backend proxies the requests

```
browser (widget)  →  YOUR backend /api/assistant  →  onbo POST /chat
   session cookie      puts user_id from session      port 18000, private network
```

### 2.1. What onbo stores about a person

One row per person in the `app_user` Postgres table:

| field        | meaning                                                      |
|--------------|--------------------------------------------------------------|
| `user_id`    | the id your product uses. Anything stable and unique: internal id, email, Telegram id. |
| `department` | one department name, e.g. `accounting`. Optional.            |
| `roles`      | a list, e.g. `[accountant, admin]`. Optional.                |

That is the whole profile. Retrieval builds its visibility filter from those two
fields: a person sees material tagged with their department, material tagged
with one of their roles, and untagged (common) material. The filter is built
from this table alone — never from the text of the question and never from the
model, so "show me the other department's documents" cannot work.

Anyone **not** in the table gets the least-privilege default: no department,
`roles: ["employee"]`, public content only. Nothing breaks; they just see less.

### 2.2. Naming departments and roles

Use the same names here and in the knowledge base tags — they are matched as
plain strings. Pick them once:

```bash
# knowledge base: who may see this material
docker compose exec app onbo kb add-doc docs/payroll.md \
  --collection accounting --department accounting --roles accountant

# directory: who this person is
docker compose exec app onbo users add u_1042 \
  --department accounting --roles accountant
```

A typo (`accountants` vs `accountant`) does not raise an error anywhere — it
just silently hides the material. Keep the list of names short and written down.

### 2.3. Bulk import

Export your directory into a YAML file with a top-level `users:` list — the
shape is documented in `config/users.example.yaml`:

```yaml
users:
  - user_id: "u_1042"
    department: accounting
    roles: [accountant]

  - user_id: "alice@example.com"
    department: support
    roles: [support, admin]
```

```bash
docker compose exec app onbo users import ./my_users.yaml
```

Import is idempotent: re-running it rewrites the department and roles of anyone
already listed, and adds the rest. Nobody is ever deleted by an import, so a
person removed from your HR system keeps their old profile until you clear it.

### 2.4. Keeping it in sync

Pick whichever fits how often your org chart moves:

- **Manual** — run `onbo users add` when someone joins or changes team. Fine for
  a small team.
- **Scheduled** — a cron job on your side dumps the directory to
  `my_users.yaml` and runs `onbo users import`. The usual choice.
- **On login** — your backend upserts the row into the same Postgres database
  when a session is created. The table is plain SQL, nothing onbo-specific:

  ```sql
  INSERT INTO app_user (user_id, department, roles) VALUES ($1, $2, $3)
  ON CONFLICT (user_id) DO UPDATE
    SET department = EXCLUDED.department, roles = EXCLUDED.roles;
  ```

  `roles` is a JSON column, so pass a JSON array. Do not touch `welcomed_at` —
  onbo uses it to decide whether the person has already had the welcome digest.

If keeping this in step with your real directory sounds like a chore, that is
exactly what mode B removes.

### 2.5. The proxy endpoint

One endpoint, no state. Express:

```js
app.post("/api/assistant", async (req, res) => {
  const user = req.session.user;               // your own auth, not onbo's
  if (!user) return res.sendStatus(401);

  const upstream = await fetch("http://app:18000/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      user_id: user.id,                        // NEVER take this from the body
      text: String(req.body.text || "").slice(0, 4000),
      locale: "ru",
    }),
  });
  res.status(upstream.status).json(await upstream.json());
});
```

FastAPI:

```python
@app.post("/api/assistant")
async def assistant(body: dict, user=Depends(current_user)):
    async with httpx.AsyncClient(timeout=60) as client:
        upstream = await client.post(
            "http://app:18000/chat",
            json={"user_id": user.id, "text": body["text"][:4000], "locale": "ru"},
        )
    return upstream.json()
```

Notes that matter:

- `http://app:18000` is the service name from `docker-compose.yml`. If your
  backend runs elsewhere, use the host it can actually reach.
- Set a generous timeout (30–60 s). A cold model plus retrieval is slower than a
  normal API call.
- If you use `mode: confirm` actions, proxy `POST /confirm` the same way
  (`{user_id, action, approved}`) — again injecting `user_id` yourself.
- The widget expects the confirm route to sit next to the chat route: if chat is
  `/api/assistant/chat`, confirm is `/api/assistant/confirm`. Otherwise pass
  `confirmEndpoint` explicitly.

---

## 3. Mode B — a signed token, no directory

Your backend already knows who the visitor is. Instead of mirroring that
knowledge into onbo, it hands the browser a short-lived **signed token** with the
user id, department and roles inside; the browser sends it to onbo with every
question, and onbo trusts the claims because the signature cannot be forged.

The token is not encrypted and does not need to be — its value is that it is
*unforgeable*. Editing `"roles": ["intern"]` into `"roles": ["admin"]` breaks the
signature, and onbo answers 401.

### 3.1. Set the shared secret

```bash
openssl rand -hex 32          # both sides use this same string
```

`.env`:

```ini
ONBO_JWT_SECRET=<the string above>
ONBO_ALLOW_USER_ID=false                 # production: signed token or nothing
ONBO_CORS_ORIGINS=https://app.example.com   # the site the widget runs on
```

```bash
docker compose up -d app
```

`ONBO_ALLOW_USER_ID=false` closes the old door: once the endpoint is reachable
from browsers, a bare `user_id` in the body proves nothing, so it is refused.
`ONBO_CORS_ORIGINS` lists exactly the origins whose pages may call onbo —
scheme, host and port must match. `*` together with no secret is refused at
startup rather than quietly allowed.

### 3.2. Issue the token on your side

It is a plain JWT, HS256. Any library produces a compatible one.

Node (`jsonwebtoken`):

```js
app.get("/api/assistant/token", (req, res) => {
  const user = req.session.user;
  if (!user) return res.sendStatus(401);
  res.json({
    token: jwt.sign(
      { sub: user.id, department: user.department, roles: user.roles },
      process.env.ONBO_JWT_SECRET,
      { expiresIn: "10m" },
    ),
  });
});
```

Python (`pyjwt`):

```python
@app.get("/api/assistant/token")
async def assistant_token(user=Depends(current_user)):
    claims = {
        "sub": user.id,
        "department": user.department,
        "roles": user.roles,
        "exp": int(time.time()) + 600,
    }
    return {"token": jwt.encode(claims, os.environ["ONBO_JWT_SECRET"], algorithm="HS256")}
```

The claims:

| claim | meaning |
|---|---|
| `sub` | the user id. Required. Used for the session and the one-time welcome. |
| `department` (or `dept`) | one department name. Optional. |
| `roles` | list of role names or ids — whatever your system uses. A single value without a list is accepted too. |
| `exp` | expiry, unix seconds. **Required** — a token that never expires is a permanent key. |

Keep the lifetime short (5–15 minutes) and let the widget re-fetch: `getToken()`
is called before every request, so a fresh token costs one cached call on your
side.

Role names must match the tags on your knowledge-base material exactly — the
same rule as §2.2, and the same silent failure when they do not.

### 3.3. Try it by hand

```bash
docker compose exec app onbo token u_1042 --department accounting --roles accountant
# → eyJhbGciOiJIUzI1NiIs…

curl -s -X POST http://localhost:18000/chat -H 'Content-Type: application/json' \
  -d '{"token":"eyJ…","text":"what is my vacation allowance?"}'
```

Edit one character in the middle of the token and the same request returns 401.
That is the whole security model, and it is worth seeing once.

---

## 4. The widget

`docs/examples/onbo-widget.js` is a self-contained chat window: a launcher
bubble, the panel, links as buttons, Ok/Cancel cards for confirm-mode actions,
optional voice. No dependencies, no build step. It renders inside a shadow root,
so your site's CSS cannot leak into it and its own cannot leak out.

**It never sends a user id.** Either your proxy adds it (mode A) or the token
carries it (mode B).

Copy it somewhere your site serves static files:

```bash
cp docs/examples/onbo-widget.js public/
```

### 4.1. Static HTML — no code at all

```html
<script type="module" src="/onbo-widget.js"
        data-endpoint="/api/assistant/chat"
        data-title="Помощник"></script>
```

Or with options, which is the same thing written out:

```html
<script type="module">
  import { init } from '/onbo-widget.js';
  init({ endpoint: '/api/assistant/chat', title: 'Помощник' });
</script>
```

### 4.2. Token mode

`endpoint` points straight at onbo, and `getToken` fetches a fresh token from
your backend:

```js
init({
  endpoint: 'https://onbo.example.com/chat',
  getToken: async () => (await fetch('/api/assistant/token', {
    credentials: 'same-origin',
  }).then(r => r.json())).token,
});
```

This is the setup that needs `ONBO_CORS_ORIGINS` — the browser is talking to
onbo directly.

### 4.3. React

```jsx
import { useEffect, useRef } from 'react';
import { init } from './onbo-widget.js';

export function Assistant() {
  const widget = useRef(null);
  useEffect(() => {
    widget.current = init({ endpoint: '/api/assistant/chat' });
    return () => widget.current.destroy();   // survives StrictMode double-mount
  }, []);
  return null;   // the widget attaches itself to <body>
}
```

### 4.4. Vue

```vue
<script setup>
import { onMounted, onUnmounted } from 'vue';
import { init } from './onbo-widget.js';

let widget;
onMounted(() => { widget = init({ endpoint: '/api/assistant/chat' }) });
onUnmounted(() => widget.destroy());
</script>
```

Angular, Svelte and the rest are the same shape: call `init()` when the
component appears, `destroy()` when it goes away. If your bundler cannot import
the file, load it with a `<script type="module">` tag and use
`window.OnboWidget.init(...)`.

### 4.5. Options

| option | default | meaning |
|---|---|---|
| `endpoint` | `/api/assistant` | where questions go: your proxy route, or onbo's `/chat` |
| `confirmEndpoint` | derived | the confirm route; by default the sibling of `endpoint` |
| `welcomeEndpoint` | derived | the welcome route, same rule |
| `voiceEndpoint` | `null` | multipart voice upload. Unset = no mic button |
| `getToken` | `null` | `() => token`, sync or async. Mode B |
| `headers` | `null` | extra headers (a CSRF token, for instance); object or function |
| `credentials` | `same-origin` | `include` if your proxy sits on another origin |
| `title` / `subtitle` | `Помощник` | panel header |
| `accent` | `#2f6feb` | the one colour everything is built from |
| `position` | `right` | `right` or `left` |
| `theme` | `auto` | `auto` follows the visitor's system setting |
| `locale` | `ru` | passed through to onbo; also the STT language |
| `open` | `false` | start with the panel open |
| `greetOnOpen` | `true` | request the welcome digest when the panel first opens |
| `launcher` | `true` | `false` hides the bubble — open it from your own button |
| `mount` | `<body>` | element to attach to |
| `zIndex` | huge | raise it if your own overlays sit on top |
| `strings` | Russian | every user-visible label, override any subset |

`init()` returns `{ open, close, toggle, ask, isOpen, destroy }` — so your own
"Help" link can do `widget.ask('How do I change my email?')`, which opens the
panel and sends the question.

### 4.6. Voice

Voice is `multipart/form-data`, so in mode A it needs a proxy route that
forwards a file rather than JSON. In mode B point `voiceEndpoint` at onbo's
`/voice` directly. Browsers only grant microphone access in a secure context:
`localhost` counts, a remote host needs HTTPS.

### 4.7. What comes back

```json
{
  "text": "…the reply the user reads…",
  "results": [{"type": "rag", "status": "ok", "links": [{"title": "My projects", "url": "…"}]}],
  "welcomed": false
}
```

`welcomed: true` means this was the person's first message and the one-time
welcome digest was prepended to `text`. `results[].links` are the places in the
product the answer points at; the same links are also appended to `text` as a
plain block under a `Ссылки:` heading, so a text-only client loses nothing. The
widget renders the structured ones as buttons and cuts the duplicate block off
the text — worth knowing if you write your own UI.

`docs/examples/chat.html` remains as a plain reference page (it types a
`user_id` by hand, so it is a local demo, not something to embed). Serve it from
`media/` — the paths in it are relative, so opening the file from disk breaks
every request:

```bash
cp docs/examples/chat.html media/
# open http://localhost:18000/media/chat.html
```

---

## 5. Closing onbo off

While the stack is only reachable from your machine, the published ports are
convenient. Before real users touch it:

- **Mode A:** remove `ports: - "18000:18000"` from the `app` service if your
  backend is on the same Docker network — `http://app:18000` keeps working, the
  outside world loses its way in. Otherwise put onbo behind a firewall rule that
  allows only your backend's address.
- **Mode B:** the port has to be reachable from browsers, so instead put onbo
  behind TLS on your own domain and rely on `ONBO_ALLOW_USER_ID=false` plus a
  precise `ONBO_CORS_ORIGINS`. Never `*`.
- Set `ONBO_ADMIN_TOKEN` in `.env`. Without it `/admin` is open to anyone who
  can reach the port, and it can edit the knowledge base.
- Same for `demo-backend` (port 18100) — drop the service entirely once your
  actions point at a real backend via `PRODUCT_API_BASE`.

---

## 6. Checklist before showing it to people

- [ ] `onbo kb status` shows content, and the names of departments and roles in
      it match the ones your product uses.
- [ ] Mode A: `onbo users import` has run; the browser calls **your** endpoint
      and `user_id` comes from the session.
- [ ] Mode B: `ONBO_ALLOW_USER_ID=false`, `ONBO_CORS_ORIGINS` lists your site
      exactly, tokens expire in minutes.
- [ ] Logged out → the endpoint returns 401 rather than a public answer.
- [ ] Logged in as someone from another department → their material is absent
      from the reply. Test this with a real account, not by reasoning about it.
- [ ] A hand-edited token or user id gets 401.
- [ ] `ONBO_ADMIN_TOKEN` is set and `/admin` asks for it.
