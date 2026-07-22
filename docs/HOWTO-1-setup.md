# How-to 1. Run and configure onbo for your own project

*English · [Русский](HOWTO-1-setup.ru.md)*

Filling the knowledge base, defining commands and wiring the chat is the second
file: [HOWTO-2-kb-and-chat.md](HOWTO-2-kb-and-chat.md).

By the end of this one you will have the assistant running on
`http://localhost:18000`, a model connected (local or hosted), and onbo talking
to your product's backend.

---

## 1. What you need

- Docker + Docker Compose (the whole application lives in containers — nothing
  is installed into your system);
- ~6 GB of disk: images, embedding weights (~1 GB) and the speech model;
- an OpenAI API key — the default setup uses it for both the chat model and the
  embeddings. You can swap in another vendor or fully local models instead
  (sections 3 and 4).

## 2. Three commands to a running stack

```bash
git clone git@github.com:japanes/onbo.git && cd onbo
cp .env.example .env          # put your key in OPENAI_API_KEY
docker compose up -d
```

Everything comes up at once:

| Service        | What it is                                              | Host port |
|----------------|---------------------------------------------------------|-----------|
| `app`          | the assistant itself (FastAPI)                          | 18000     |
| `bootstrap`    | one-shot init: self-docs, starter FAQ, demo users       | —         |
| `postgres`     | canonical store (users, knowledge base)                 | 15432     |
| `qdrant`       | vector index (derived from Postgres)                    | 16333     |
| `redis`        | sessions and pending confirmations                      | 16379     |
| `demo-backend` | fake "product backend" so actions actually execute      | 18100     |

Check it:

```bash
curl -s -X POST http://localhost:18000/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"acc1","text":"how do I reset my password?"}'
```

Useful commands:

```bash
docker compose logs -f app          # follow the assistant
docker compose restart app          # re-read .env / config after edits
docker compose run --rm test        # run the test suite (dev profile)
docker compose down                 # stop
docker compose down -v              # stop and wipe data (Postgres, Qdrant, model cache)
```

The first start is slower: images and model weights are being downloaded. The
weights live in a separate `modelcache` volume and survive rebuilds, so the
second start is fast.

> `bootstrap` indexes the starter FAQ, which means computing embeddings. With
> the default hosted embedding model that needs a valid key — without one it
> fails with `AuthenticationError`. Either put the key in `.env`, or switch to a
> local embedding model (section 4).

## 3. Connecting a model

The model is what the classifier uses: it turns "change my email and show me how
to issue a refund" into a list of actions. The default is OpenAI
`gpt-5.6-terra`, so `OPENAI_API_KEY=sk-...` in `.env` is all it takes.
**Without a reachable model onbo does not break** — it falls back to keyword
heuristics, so simple commands still work but free-form phrasing does not.

Switching to another model is one block in `.env`. There is a single rule:

> Set `LLM_API_BASE` **only** for a server of your own (Ollama, vLLM, …). For
> hosted vendors (OpenAI, Anthropic, Gemini) leave it empty, otherwise requests
> go to the wrong host.

### Flagship hosted models

The key always goes into the same `LLM_API_KEY` (or the vendor's own env var,
e.g. `OPENAI_API_KEY`); the provider is selected by the model prefix. Do not set
`LLM_API_BASE`.

```ini
# OpenAI — the default; a bare OPENAI_API_KEY=sk-... in .env is enough
LLM_MODEL=gpt-5.6-terra
LLM_API_KEY=sk-...
```

```ini
# Anthropic
LLM_MODEL=anthropic/claude-sonnet-4-5
LLM_API_KEY=sk-ant-...
```

```ini
# Google Gemini
LLM_MODEL=gemini/gemini-2.0-flash
LLM_API_KEY=AIza...
```

LiteLLM sits underneath, so any model string from its catalogue works (Azure
OpenAI, Bedrock, Mistral and so on — those have their own environment
variables; add them to `.env` and they reach the container as-is).

### Open-weight models on your own hardware

Nothing leaves your network, and there is no per-request bill.

**Option A — Ollama on the host (uses your GPU, recommended):**

```bash
# on the host
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b
```

`.env`:

```ini
LLM_MODEL=ollama_chat/qwen2.5:7b
LLM_API_BASE=http://host.docker.internal:11434
```

One catch: by default Ollama listens on `127.0.0.1` only, and the container
cannot reach it (`Connection refused`). Open it up for Docker:

```bash
sudo systemctl edit ollama     # add: [Service] Environment="OLLAMA_HOST=0.0.0.0"
sudo systemctl restart ollama
```

**Option B — Ollama inside Docker** (nothing to install on the host, but CPU
only — GPU passthrough needs `nvidia-container-toolkit`, so replies are slow):

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3.2:3b
```

`.env`:

```ini
LLM_MODEL=ollama_chat/llama3.2:3b
LLM_API_BASE=http://ollama:11434
```

**Option C — any OpenAI-compatible server**: vLLM, LM Studio, llama.cpp
(`llama-server`), OpenRouter, a corporate proxy:

```ini
LLM_MODEL=openai/Qwen/Qwen2.5-7B-Instruct
LLM_API_BASE=http://host.docker.internal:8000/v1
LLM_API_KEY=not-needed
```

About model size: the classifier asks for strict JSON. 3B-class models often
return it malformed — onbo survives that (it drops to the heuristic), but the
parsing quality is noticeably worse. The practical minimum is 7–8B
(`qwen2.5:7b`, `llama3.1:8b`); instruct models that follow formats well are
better still.

After editing `.env`:

```bash
docker compose restart app
```

### Verify the model actually answers

Send a phrase the heuristic cannot parse:

```bash
curl -s -X POST http://localhost:18000/chat -H 'Content-Type: application/json' \
  -d '{"user_id":"acc1","text":"switch the interface to English and also change my email to a@b.com"}'
```

With a working model the reply contains **two** results (language switched plus
a confirmation request for the email). If the model is unreachable you will see
the classification failing in `docker compose logs app`, and the reply will be
poorer.

## 4. Embeddings (knowledge-base search)

An embedding model turns text into a vector; search compares vectors. It is a
separate choice from the chat model — the two do not have to come from the same
vendor.

**A flagship hosted model (the default).** Out of the box this is OpenAI
`text-embedding-3-large` (3072 dimensions), covered by the same
`OPENAI_API_KEY`. For another vendor, set the model plus a key — the vendor is
picked from the model string, exactly like the chat model:

| Vendor | `EMBED_MODEL` | Vector size |
|---|---|---|
| OpenAI | `text-embedding-3-small` / `text-embedding-3-large` | 1536 / 3072 |
| Google Gemini | `gemini/gemini-embedding-001` | 3072 |
| Anthropic → Voyage AI | `voyage/voyage-3-large`, `voyage/voyage-3.5-lite` | 1024 |
| Cohere, Mistral, Bedrock, Azure… | e.g. `cohere/embed-multilingual-v3.0` | vendor-specific |

```ini
EMBED_MODEL=text-embedding-3-large
EMBED_API_KEY=sk-...
```

Anthropic publishes no embedding model of its own and recommends Voyage AI, so
that is the "Anthropic" line here; the key comes from voyageai.com.

**Open-source, on your own machine.** Runs through `fastembed`: no key, no
external service, company content never leaves the machine. The best
multilingual option is `intfloat/multilingual-e5-large` (1024 dimensions); a
lighter one:

```ini
EMBED_MODEL=intfloat/multilingual-e5-large                                 # 1024d
EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2   # 384d
```

The same rule as for the chat model applies: **`EMBED_API_BASE` is only for a
server of your own** (vLLM, LM Studio, a proxy) — leave it unset for hosted
vendors.

`EMBED_PROVIDER=auto` (the default) works out local-vs-hosted from the model
string: a bare name (`text-embedding-3-small`) or a known vendor prefix means
the API, anything shaped like a HuggingFace repo id means fastembed. Force it
with `EMBED_PROVIDER=api` or `local` when your model name breaks that guess —
for example an open-weight model served by your own vLLM:

```ini
EMBED_PROVIDER=api
EMBED_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBED_API_BASE=http://host.docker.internal:8000/v1
EMBED_API_KEY=not-needed
```

What you trade: with a hosted model, the knowledge-base text is sent to the
vendor — once at indexing time and once per question. A local model sends
nothing anywhere but is a little weaker on rare wording. Quality only matters
for search: the answer text itself is whatever you wrote into the knowledge
base.

Any change here changes the vector size, so rebuild the index:

```bash
docker compose exec app onbo kb reindex
```

## 5. Voice input

Speech recognition is `faster-whisper`, also local. There is no GPU inside the
container, so compose pins `STT_DEVICE=cpu`, `STT_COMPUTE=int8` and the `base`
model. Bigger is more accurate but noticeably slower on CPU:

```ini
STT_MODEL=small     # base | small | medium | large-v3
STT_ENABLED=true
```

Wiring the microphone into the web chat is covered in the second file.

## 6. Connecting your product

The assistant does not only answer — it performs commands by calling your
product's HTTP API. While `PRODUCT_API_BASE` is empty, actions run as a dry run:
onbo validates the parameters and reports what it *would* have called, without
making the request. In Docker the bundled demo backend is wired in by default so
you can see the whole flow live.

`.env`:

```ini
PRODUCT_NAME=Acme CRM
PRODUCT_DESCRIPTION=CRM for the sales team
PRODUCT_API_BASE=https://api.acme.example.com
PRODUCT_API_KEY=...              # sent as Authorization: Bearer ...
PRODUCT_AUTH_HEADER=Authorization
PRODUCT_AUTH_SCHEME=Bearer       # empty = send the key raw, with no prefix
```

The commands themselves are described in `config/actions.yaml` — that is the
second file's topic.

## 7. Users, departments and roles

The user's profile (`department`, `roles`) is the single source of the access
filter: it decides which material can appear in an answer. It comes **from the
database**, never from the message text and never from the model.

- Lookup order: the `app_user` table in Postgres → the built-in demo directory
  (`acc1`, `sup1`, `admin`) → an unknown user gets least privilege (public
  material and the "about me" section only).
- For your own project, fill `app_user` (`user_id`, `department`, `roles`) from
  your HR system or directory — sync it on your side.

Inspect the demo directory:

```bash
docker compose exec postgres psql -U onbo -d onbo -c 'select * from app_user'
```

## 8. What you can switch off

Each flag removes a whole subsystem along with its HTTP routes:

```ini
FEATURE_CHAT=true          # /chat, /voice, /confirm
FEATURE_ADMIN=true         # /admin — knowledge-base panel
FEATURE_MEDIA=true         # /media — walkthrough videos
FEATURE_LLM_MANIFEST=true  # /llm.json for external AI agents
FEATURE_WELCOME=true       # proactive welcome digest
FEATURE_ACTIONS=true       # executing commands
FEATURE_RAG=true           # answering from the knowledge base
```

For example, "commands only, no knowledge base" is `FEATURE_RAG=false`.

## 9. Before you show it to real people

- **`/admin` has no password.** Anyone who can reach port 18000 can edit the
  knowledge base. Never expose it — keep it behind a VPN or an authenticating
  proxy. Admin authentication is on the backlog (`.claude/PLAN3.md`, item 20).
- **`user_id` arrives in the request body and is not verified.** For a real
  rollout put your own proxy in front of onbo: it checks the employee's session
  and injects `user_id` itself.
- Drop the demo users and the demo backend from a production config (point
  `PRODUCT_API_BASE` at the real API).
- The Postgres passwords in `docker-compose.yml` are the defaults (`onbo/onbo`).

## 10. When something goes wrong

| Symptom | Cause and fix |
|---|---|
| Replies are primitive, complex phrasing is not understood | The model is unreachable and the heuristic is running. Check `docker compose logs app`, `LLM_MODEL`/`LLM_API_KEY`, and that `LLM_API_BASE` is **not** set for a hosted vendor |
| `Connection refused` to Ollama | Ollama listens on `127.0.0.1`: set `OLLAMA_HOST=0.0.0.0` and restart it |
| `localhost:18000` does not open | `docker compose ps` — is `app` alive? `bootstrap` must exit successfully; `app` waits for it |
| The very first request takes ages | Embedding/whisper weights are downloading. Afterwards they come from the `modelcache` volume |
| Search finds nothing after changing `EMBED_MODEL` | `docker compose exec app onbo kb reindex` |
| `AuthenticationError` on indexing or on a question | A hosted embedding model with no/wrong `EMBED_API_KEY`. Note the key is per-vendor: the embedding key is separate from `LLM_API_KEY` |
| Port already in use | Host ports are shifted by +10000 (15432/16333/16379); the web port is `WEB_PORT` |
| You want a clean slate | `docker compose down -v && docker compose up -d` |
