"""Visual KB admin: a small FastAPI router mounted at /admin by the web channel.

It's a thin HTTP skin over ``kb.admin.KnowledgeBaseAdmin`` — list/add/delete Q&A,
browse documents and collections, and trigger seed / reindex. A single
self-contained HTML page (no build step, no CDN) drives it from the browser.

Auth: set ``ONBO_ADMIN_TOKEN`` to require an ``X-Admin-Token`` header on the
/api/* routes. Unset means open — fine for a localhost dev box, not for prod.

Like channels/web.py this module avoids ``from __future__ import annotations`` so
FastAPI can resolve real type objects when it builds /openapi.json.
"""
import os

from ..config import Settings


def build_admin_router(settings: Settings):
    from fastapi import APIRouter, Depends, Header, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel

    from ..kb.admin import KnowledgeBaseAdmin

    admin = KnowledgeBaseAdmin(settings)

    def check_token(x_admin_token: str = Header(default=None)):
        expected = os.environ.get("ONBO_ADMIN_TOKEN")
        if expected and x_admin_token != expected:
            raise HTTPException(status_code=401, detail="invalid or missing admin token")

    # Token gate applies to every /api/* route, not the HTML page itself.
    router = APIRouter(prefix="/admin")
    api = APIRouter(prefix="/api", dependencies=[Depends(check_token)])

    class QAPayload(BaseModel):
        question: str
        answer: str
        collection: str = "common"
        department: str | None = None
        roles: list[str] = []
        video_url: str | None = None

    class QAUpdatePayload(BaseModel):
        # PATCH semantics: every field optional, only the ones sent are applied
        # (see exclude_unset below). Lets the admin panel edit just video_url.
        question: str | None = None
        answer: str | None = None
        collection: str | None = None
        department: str | None = None
        roles: list[str] | None = None
        video_url: str | None = None

    @router.get("", response_class=HTMLResponse)
    async def page():
        return _ADMIN_HTML

    @api.get("/stats")
    async def stats():
        return admin.stats()

    @api.get("/collections")
    async def collections():
        return admin.list_collections()

    @api.get("/qa")
    async def qa(collection: str | None = None):
        return admin.list_qa(collection)

    @api.get("/documents")
    async def documents(collection: str | None = None):
        return admin.list_documents(collection)

    @api.post("/qa")
    async def add_qa(payload: QAPayload):
        n = await admin.add_qa(
            payload.question, payload.answer, payload.collection,
            payload.department or None, payload.roles or None, payload.video_url or None,
        )
        return {"indexed": n, "ok": True}

    @api.patch("/qa/{qa_id}")
    async def update_qa(qa_id: int, payload: QAUpdatePayload):
        fields = payload.model_dump(exclude_unset=True)
        if not await admin.update_qa(qa_id, **fields):
            raise HTTPException(status_code=404, detail="q&a not found")
        return {"ok": True}

    @api.delete("/qa/{qa_id}")
    async def delete_qa(qa_id: int):
        return {"deleted": admin.delete_qa(qa_id)}

    @api.post("/reindex")
    async def reindex():
        try:
            total = await admin.reindex()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"reindexed": total, "ok": True}

    @api.post("/seed")
    async def seed():
        return {"seeded": await admin.seed(), "ok": True}

    @api.post("/seed-users")
    async def seed_users():
        from ..auth.profiles import seed_demo_users

        return {"users": seed_demo_users(settings), "ok": True}

    router.include_router(api)
    return router


_ADMIN_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>onbo · админка базы знаний</title>
<style>
  :root { --bg:#0f1220; --card:#191d2e; --line:#2a3050; --fg:#e8eaf2;
          --mut:#9aa3c0; --accent:#6d8cff; --ok:#3ecf8e; --danger:#ff6b6b; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:18px 24px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { font-size:18px; margin:0; font-weight:600; }
  header .stat { color:var(--mut); font-size:13px; }
  main { max-width:1000px; margin:0 auto; padding:24px; }
  .row { display:flex; gap:16px; flex-wrap:wrap; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; margin-bottom:18px; }
  .card h2 { font-size:14px; text-transform:uppercase; letter-spacing:.04em; color:var(--mut); margin:0 0 14px; }
  label { display:block; font-size:12px; color:var(--mut); margin:10px 0 4px; }
  input, textarea, select { width:100%; background:#0f1220; color:var(--fg); border:1px solid var(--line);
          border-radius:8px; padding:9px 11px; font:inherit; }
  textarea { min-height:70px; resize:vertical; }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px; padding:9px 16px;
           font:inherit; font-weight:600; cursor:pointer; }
  button.ghost { background:transparent; border:1px solid var(--line); color:var(--fg); }
  button.danger { background:transparent; border:1px solid var(--danger); color:var(--danger); padding:4px 10px; font-size:12px; }
  button:disabled { opacity:.5; cursor:default; }
  .actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:6px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--mut); font-weight:500; font-size:12px; text-transform:uppercase; }
  .tag { display:inline-block; background:#23294a; color:#b9c2e6; border-radius:6px; padding:1px 8px; font-size:12px; margin:1px 2px; }
  .pub { background:#173a2e; color:#7ff0bf; }
  #toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#23294a;
           border:1px solid var(--line); padding:10px 18px; border-radius:10px; opacity:0; transition:.2s; pointer-events:none; }
  #toast.show { opacity:1; }
  .grow { flex:1 1 320px; }
  .tokbox { display:flex; gap:8px; align-items:center; }
  .tokbox input { width:160px; }
</style>
</head>
<body>
<header>
  <h1>onbo · база знаний</h1>
  <span class="stat" id="stat">…</span>
  <span style="flex:1"></span>
  <span class="tokbox"><label style="margin:0">admin token</label>
    <input id="token" placeholder="если задан" oninput="saveTok()"></span>
</header>
<main>
  <div class="card">
    <h2>Действия</h2>
    <div class="actions">
      <button onclick="seed()">Загрузить стартовый FAQ</button>
      <button onclick="seedUsers()" class="ghost">Создать демо-пользователей</button>
      <button onclick="reindex()" class="ghost">Переиндексировать из Postgres</button>
    </div>
  </div>

  <div class="row">
    <div class="card grow">
      <h2>Добавить вопрос-ответ</h2>
      <label>Вопрос</label><input id="q">
      <label>Ответ</label><textarea id="a"></textarea>
      <div class="row">
        <div class="grow"><label>Коллекция</label><input id="col" value="common"></div>
        <div class="grow"><label>Отдел (пусто = всем)</label><input id="dep" placeholder="accounting"></div>
      </div>
      <label>Роли через запятую (пусто = всем)</label><input id="roles" placeholder="accountant, support">
      <label>Видео (URL, необязательно)</label><input id="vid" placeholder="/media/kb/xxx.mp4">
      <div class="actions"><button onclick="addQA()">Добавить</button></div>
    </div>
  </div>

  <div class="card">
    <h2>Вопрос-ответы <span id="qacount" class="stat"></span></h2>
    <table><thead><tr><th>#</th><th>Коллекция</th><th>Доступ</th><th>Вопрос → ответ</th><th></th></tr></thead>
      <tbody id="qatab"><tr><td colspan="5" class="stat">загрузка…</td></tr></tbody></table>
  </div>

  <div class="card">
    <h2>Документы <span id="doccount" class="stat"></span></h2>
    <table><thead><tr><th>#</th><th>Коллекция</th><th>Источник</th><th>Доступ</th><th>Символов</th></tr></thead>
      <tbody id="doctab"><tr><td colspan="5" class="stat">—</td></tr></tbody></table>
  </div>
</main>
<div id="toast"></div>
<script>
const $ = s => document.querySelector(s);
let TOK = localStorage.getItem('onbo_tok') || '';
let QA_VIDEO = {};   // qa id -> current video_url, so editVideo needs no escaping
function saveTok(){ TOK = $('#token').value; localStorage.setItem('onbo_tok', TOK); }
function esc(s){ return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function toast(m){ const t=$('#toast'); t.textContent=m; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2200); }
async function api(path, opts={}){
  opts.headers = Object.assign({'Content-Type':'application/json','X-Admin-Token':TOK}, opts.headers||{});
  const r = await fetch('/admin'+path, opts);
  if(!r.ok){ const e=await r.json().catch(()=>({detail:r.statusText})); throw new Error(e.detail||r.status); }
  return r.json();
}
function accessTags(dep, roles){
  let h = dep ? `<span class="tag">${esc(dep)}</span>` : '<span class="tag pub">всем</span>';
  (roles||[]).forEach(x => h += `<span class="tag">${esc(x)}</span>`);
  return h;
}
async function refresh(){
  try {
    const s = await api('/api/stats');
    $('#stat').textContent = `БД: ${s.db?'да':'нет'} · коллекций ${s.collections} · Q&A ${s.qa} · документов ${s.documents}`;
  } catch(e){ $('#stat').textContent = 'ошибка: '+e.message; }
  try {
    const qa = await api('/api/qa'); $('#qacount').textContent = `(${qa.length})`;
    QA_VIDEO = {};
    $('#qatab').innerHTML = qa.length ? qa.map(r => { QA_VIDEO[r.id] = r.video_url || '';
      return `<tr><td>${r.id}</td><td>${esc(r.collection)}</td><td>${accessTags(r.department, r.roles)}</td>
       <td><b>${esc(r.question)}</b>${r.video_url?' 🎬':''}<br><span class="stat">${esc(r.answer)}</span></td>
       <td><button class="ghost" onclick="editVideo(${r.id})">видео</button>
           <button class="danger" onclick="delQA(${r.id})">удалить</button></td></tr>`; }).join('')
      : '<tr><td colspan="5" class="stat">пусто — загрузите стартовый FAQ</td></tr>';
  } catch(e){ $('#qatab').innerHTML = `<tr><td colspan="5" class="stat">${e.message}</td></tr>`; }
  try {
    const d = await api('/api/documents'); $('#doccount').textContent = `(${d.length})`;
    $('#doctab').innerHTML = d.length ? d.map(r =>
      `<tr><td>${r.id}</td><td>${esc(r.collection)}</td><td>${esc(r.title||r.source)}</td>
       <td>${accessTags(r.department, [])}</td><td>${r.chars}</td></tr>`).join('')
      : '<tr><td colspan="5" class="stat">нет документов (добавьте через CLI: onbo kb add-doc)</td></tr>';
  } catch(e){ $('#doctab').innerHTML = `<tr><td colspan="5" class="stat">${e.message}</td></tr>`; }
}
async function addQA(){
  const body = { question:$('#q').value.trim(), answer:$('#a').value.trim(), collection:$('#col').value.trim()||'common',
    department:$('#dep').value.trim()||null, roles:$('#roles').value.split(',').map(s=>s.trim()).filter(Boolean),
    video_url:$('#vid').value.trim()||null };
  if(!body.question||!body.answer){ toast('вопрос и ответ обязательны'); return; }
  try { await api('/api/qa',{method:'POST',body:JSON.stringify(body)}); $('#q').value=''; $('#a').value=''; $('#vid').value='';
    toast('добавлено'); refresh(); } catch(e){ toast(e.message); }
}
async function editVideo(id){
  const url = prompt('URL видео для Q&A #'+id+' (пусто — убрать):', QA_VIDEO[id]||'');
  if(url===null) return;
  try { await api('/api/qa/'+id,{method:'PATCH',body:JSON.stringify({video_url:url.trim()||null})});
    toast('видео обновлено'); refresh(); } catch(e){ toast(e.message); }
}
async function delQA(id){ if(!confirm('Удалить Q&A #'+id+'?')) return;
  try { await api('/api/qa/'+id,{method:'DELETE'}); toast('удалено'); refresh(); } catch(e){ toast(e.message); } }
async function seed(){ try { const r=await api('/api/seed',{method:'POST'}); toast('загружено: '+r.seeded); refresh(); } catch(e){ toast(e.message); } }
async function seedUsers(){ try { const r=await api('/api/seed-users',{method:'POST'}); toast('пользователей: '+r.users); } catch(e){ toast(e.message); } }
async function reindex(){ toast('переиндексация…'); try { const r=await api('/api/reindex',{method:'POST'}); toast('переиндексировано: '+r.reindexed); refresh(); } catch(e){ toast(e.message); } }
$('#token').value = TOK;
refresh();
</script>
</body>
</html>"""
