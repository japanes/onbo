# How-to 3. Embedding the chat in your product: users, roles, widget

*English · [Русский](HOWTO-3-embed-chat.ru.md)*

This continues [HOWTO-2-kb-and-chat.md](HOWTO-2-kb-and-chat.md): the stack is
up and the knowledge base has content. Now let's put a chat window inside your
own product, so that a person who logged into *your* app talks to onbo as
themselves — and sees only what their department and roles allow.

Two things have to be done, and neither one works without the other:

1. **Move your users across.** onbo decides what a person may see from its own
   `app_user` table, not from your session. If the table is empty, everyone is
   a stranger and sees public content only.
2. **Put your backend between the browser and onbo.** `POST /chat` has no
   authentication of its own: it trusts the `user_id` in the request body.

---

## 1. The shape of it

```
browser (widget)  →  YOUR backend /api/assistant  →  onbo POST /chat
   session cookie      puts user_id from session      port 18000, private network
```

The browser never talks to onbo directly. Your backend already knows who is
logged in; it is the only place that may say "this request is from user u_1042".

**What happens if you skip the proxy:** anyone can open the console and send
`{"user_id": "ceo@acme.com", "text": "..."}`. onbo will happily answer with the
CEO's material, because from its point of view that *is* the CEO. This is the
one mistake that turns the access control below into decoration.

---

## 2. Moving users and roles across

### 2.1. What onbo stores

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

There is no HTTP endpoint for the directory today; it is CLI or SQL.

---

## 3. The proxy endpoint on your backend

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
- The `results` array in the response is safe to pass through to the browser;
  it contains the same material the person is already allowed to see.

### The response

```json
{
  "text": "…the reply the user reads…",
  "results": [{"type": "rag", "status": "ok", "…": "…"}],
  "welcomed": false
}
```

`welcomed: true` means this was the person's first message and the one-time
welcome digest was prepended to `text`. Show `text` and, optionally, render
cards for any result with `status: "needs_confirm"`.

A result may also carry `links` — `[{"title": "My projects", "url": "…"}]`, the
places in the product the answer points at (see
[guide 2](HOWTO-2-kb-and-chat.md)). The same links are already appended to
`text` as a plain block under a `Ссылки:` heading, so a client that only prints
text loses nothing. If you render the structured ones as buttons, cut that block
off the text first — `chat.html` shows how in a few lines.

---

## 4. The widget in the browser

`docs/examples/chat.html` is a working 160-line chat: text, voice, and the
Ok/Cancel cards for confirm-mode actions. It is a **demo page**, not a widget —
it asks for `user_id` in a text field. To reuse it inside your product:

1. Delete the `user_id` input and the "demo users" line from the header, and
   every `user_id: userInput.value` from the script — the proxy supplies it.
2. Point the requests at your own endpoint: `post('/chat', …)` becomes
   `post('/api/assistant', …)`, `post('/confirm', …)` becomes your confirm
   proxy, and the opening `post('/welcome', …)` call either goes through a third
   proxy route or is dropped (the digest arrives with the first message anyway).
3. Add `credentials: 'same-origin'` to the `fetch` options if your session
   cookie needs it.
4. Drop the markup into your own page, or serve the file from your own domain
   inside an `<iframe>`.

Voice (`POST /voice`) is `multipart/form-data`, so proxying it means forwarding
the uploaded file rather than JSON. If you do not need voice in the embedded
widget, remove the mic button — it is the smaller half of the page.

---

## 5. Closing onbo off

While the stack is only reachable from your machine, the published ports are
convenient. Before real users touch it:

- Remove `ports: - "18000:18000"` from the `app` service if your backend is on
  the same Docker network — `http://app:18000` keeps working, the outside world
  loses its way in. Otherwise put onbo behind a firewall rule that allows only
  your backend's address.
- Set `ONBO_ADMIN_TOKEN` in `.env`. Without it `/admin` is open to anyone who
  can reach the port, and it can edit the knowledge base.
- Same for `demo-backend` (port 18100) — drop the service entirely once your
  actions point at a real backend via `PRODUCT_API_BASE`.

---

## 6. Checklist before showing it to people

- [ ] `onbo users import` has run and `onbo kb status` shows content.
- [ ] Department and role names in the directory match the tags on the material.
- [ ] The browser calls **your** endpoint; `user_id` comes from the session.
- [ ] Logged out → the endpoint returns 401 rather than a public answer.
- [ ] Logged in as someone from another department → their material is absent
      from the reply. Test this with a real account, not by reasoning about it.
- [ ] Port 18000 is not reachable from outside; `ONBO_ADMIN_TOKEN` is set.
