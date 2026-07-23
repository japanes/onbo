/**
 * onbo chat widget — a floating chat window you can drop into any site.
 *
 * No dependencies, no build step, no framework. Everything lives in a shadow
 * root, so the host page's CSS cannot leak in and the widget's cannot leak out.
 *
 * Static HTML:
 *
 *   <script type="module">
 *     import { init } from '/onbo-widget.js';
 *     init({ endpoint: '/api/assistant' });
 *   </script>
 *
 * ...or with no code at all, configured from the tag itself:
 *
 *   <script type="module" src="/onbo-widget.js"
 *           data-endpoint="/api/assistant" data-title="Помощник"></script>
 *
 * React:
 *
 *   useEffect(() => {
 *     const widget = init({ endpoint: '/api/assistant' });
 *     return () => widget.destroy();     // survives StrictMode double-mount
 *   }, []);
 *
 * Vue:
 *
 *   onMounted(() => { widget = init({ endpoint: '/api/assistant' }) });
 *   onUnmounted(() => widget.destroy());
 *
 * ── Who the user is ────────────────────────────────────────────────────────
 * The widget never sends a user id, because a browser cannot be trusted with
 * one. Two supported setups:
 *
 *  1. Signed token (the normal one). `endpoint` points straight at onbo, and
 *     `tokenEndpoint` at one small route on YOUR site that mints a short-lived
 *     JWT for the logged-in visitor. The widget fetches it, caches it until it
 *     is about to expire and sends it along; onbo reads the user id, department
 *     and roles out of it and rejects anything with a broken signature. That
 *     single route is the whole server-side cost of embedding this.
 *
 *     The token may also carry the person's own API credential, and then
 *     actions run against your product as them, with your usual permission
 *     checks — see docs/examples/nuxt/.
 *
 *  2. Proxy. `endpoint` points at your own backend, which knows the visitor
 *     from its session cookie and forwards each call to onbo with the user id
 *     attached. More routes to write and keep in step, but onbo never has to be
 *     reachable from the browser — use it when it must stay in a closed network.
 */

const DEFAULTS = {
  // Where to POST. Same-origin path (your proxy) or a full onbo URL (token mode).
  endpoint: '/api/assistant',
  confirmEndpoint: null,   // defaults to endpoint with /chat -> /confirm
  welcomeEndpoint: null,   // null + greetOnOpen -> derived the same way
  voiceEndpoint: null,     // null disables the mic button

  getToken: null,          // () => string | Promise<string>, for token mode
  tokenEndpoint: null,     // URL on YOUR site that mints the token; sets getToken for you
  headers: null,           // object or () => object, e.g. a CSRF header
  credentials: 'same-origin',  // 'include' if your proxy is on another origin
  locale: 'ru',
  timeout: 60000,          // a cold model plus retrieval is slow; be generous

  // Looks
  title: 'Помощник',
  subtitle: '',
  accent: '#2f6feb',
  position: 'right',       // 'right' | 'left'
  theme: 'auto',           // 'auto' | 'light' | 'dark'
  zIndex: 2147483000,
  open: false,             // start with the panel open
  greetOnOpen: true,       // ask for the welcome digest on first open
  launcher: true,          // false: no bubble, you call widget.open() yourself
  mount: null,             // where to attach; defaults to <body>

  strings: {
    launcher: 'Задать вопрос',
    placeholder: 'Спросите или дайте команду…',
    send: 'Отправить',
    close: 'Свернуть',
    mic: 'Записать голос',
    recording: 'Записываю… нажмите ещё раз, чтобы отправить',
    transcribing: '… распознаю',
    thinking: '…',
    error: 'Не получилось отправить. Попробуйте ещё раз.',
    ok: 'Ок',
    cancel: 'Отмена',
    links: 'Ссылки:',
  },
};

const CSS = `
:host { all: initial; }
* { box-sizing: border-box; font-family: inherit; }
.root {
  position: fixed; bottom: 20px; z-index: var(--z);
  font: 15px/1.45 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  color: var(--fg);
}
.root.right { right: 20px; align-items: flex-end; }
.root.left  { left: 20px;  align-items: flex-start; }
.root { display: flex; flex-direction: column; gap: 12px; }

.launcher {
  border: 0; border-radius: 999px; padding: 12px 18px; cursor: pointer;
  background: var(--accent); color: #fff; font-size: 15px; font-weight: 500;
  box-shadow: 0 6px 24px rgba(0,0,0,.18);
}
.launcher:hover { filter: brightness(1.07); }

.panel {
  display: none; flex-direction: column; overflow: hidden;
  width: 380px; height: min(560px, calc(100vh - 120px));
  background: var(--bg); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: 0 18px 48px rgba(0,0,0,.22);
}
.panel.open { display: flex; }

.head {
  display: flex; align-items: center; gap: 8px; padding: 12px 14px;
  background: var(--accent); color: #fff;
}
.head .t { font-weight: 600; }
.head .s { font-size: 13px; opacity: .8; }
.head button {
  margin-left: auto; background: transparent; border: 0; color: inherit;
  font-size: 20px; line-height: 1; cursor: pointer; opacity: .85;
}

.log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 85%; padding: 9px 12px; border-radius: 12px; white-space: pre-wrap; word-wrap: break-word; }
.msg.bot { background: var(--bubble); align-self: flex-start; border-bottom-left-radius: 4px; }
.msg.me  { background: var(--accent); color: #fff; align-self: flex-end; border-bottom-right-radius: 4px; }
.msg.sys { align-self: center; background: transparent; font-size: 13px; opacity: .6; }

.links { display: flex; flex-wrap: wrap; gap: 8px; align-self: flex-start; max-width: 90%; }
.links a {
  display: inline-block; padding: 7px 12px; border-radius: 999px;
  border: 1px solid var(--accent); color: var(--accent); text-decoration: none; font-size: 14px;
}
.links a:hover { background: var(--accent); color: #fff; }

.card { align-self: flex-start; max-width: 90%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
.card .row { display: flex; gap: 8px; margin-top: 10px; }
.card button { padding: 6px 14px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: inherit; cursor: pointer; }
.card button.primary { background: var(--accent); color: #fff; border-color: transparent; }

form { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--line); }
input {
  flex: 1; min-width: 0; padding: 9px 12px; border-radius: 10px;
  border: 1px solid var(--line); background: transparent; color: inherit; font-size: 15px;
}
input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
form button {
  padding: 9px 12px; border-radius: 10px; border: 0; cursor: pointer;
  background: var(--accent); color: #fff; font-size: 15px;
}
form button.ghost { background: transparent; color: inherit; border: 1px solid var(--line); }
form button.rec { background: #d94040; color: #fff; }

@media (max-width: 480px) {
  .root { bottom: 0; left: 0; right: 0; align-items: stretch; gap: 0; }
  .panel { width: 100vw; height: 100dvh; border: 0; border-radius: 0; }
  .launcher { margin: 0 12px 12px auto; }
}
`;

const THEMES = {
  light: { bg: '#fff', fg: '#14161a', line: '#00000022', bubble: '#f1f3f6' },
  dark: { bg: '#1b1d21', fg: '#e9eaec', line: '#ffffff26', bubble: '#2a2d33' },
};

/** Derive a sibling endpoint: .../chat -> .../confirm, keeping any prefix. */
function sibling(endpoint, name) {
  return endpoint.replace(/\/[^/?#]*(\?.*)?$/, `/${name}$1`);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;   // textContent: never inject markup
  return node;
}

/** Seconds-since-epoch `exp` out of a JWT, or 0 when it cannot be read. */
function tokenExpiry(token) {
  try {
    // The payload is signed, not encrypted: reading it here is expected, and
    // nothing is trusted from it — onbo re-verifies the signature server-side.
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return Number(payload.exp) || 0;
  } catch {
    return 0;
  }
}

/**
 * Turn a token URL into a getToken(), so the whole token mode is reachable from
 * a plain <script data-token-endpoint="…"> with no glue code on the page.
 *
 * The token is short-lived by design, so it is cached and re-fetched shortly
 * before it expires rather than on every message. One in-flight request is
 * shared: opening the panel fires welcome and the first question together.
 */
function tokenFetcher(url, credentials) {
  let cached = '';
  let expiresAt = 0;
  let inFlight = null;
  const SKEW = 30;   // refresh this many seconds early

  return async () => {
    if (cached && Date.now() / 1000 < expiresAt - SKEW) return cached;
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        // credentials: the session cookie is what proves who is asking.
        const response = await fetch(url, { credentials, headers: { Accept: 'application/json' } });
        if (!response.ok) return '';
        const data = await response.json();
        const token = typeof data === 'string' ? data : (data.token || '');
        if (token) {
          cached = token;
          // No exp we can read: treat as single-use rather than cache forever.
          expiresAt = tokenExpiry(token) || 0;
        }
        return token;
      } catch {
        return '';   // anonymous: onbo answers what it shows to everyone
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  };
}

export function init(options = {}) {
  const opts = { ...DEFAULTS, ...options, strings: { ...DEFAULTS.strings, ...(options.strings || {}) } };
  if (!opts.getToken && opts.tokenEndpoint) {
    // Cross-origin by nature: the token comes from your site, the questions go
    // to onbo, so the cookie has to ride along explicitly.
    opts.getToken = tokenFetcher(opts.tokenEndpoint, 'include');
  }
  const confirmUrl = opts.confirmEndpoint || sibling(opts.endpoint, 'confirm');
  const welcomeUrl = opts.welcomeEndpoint || sibling(opts.endpoint, 'welcome');

  const host = element('div');
  host.style.cssText = 'all: initial';
  (opts.mount || document.body).appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });

  const dark = opts.theme === 'dark'
    || (opts.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  const palette = dark ? THEMES.dark : THEMES.light;

  const style = element('style');
  style.textContent = CSS;
  shadow.appendChild(style);

  const root = element('div', `root ${opts.position === 'left' ? 'left' : 'right'}`);
  root.style.setProperty('--accent', opts.accent);
  root.style.setProperty('--z', String(opts.zIndex));
  for (const [key, value] of Object.entries(palette)) root.style.setProperty(`--${key}`, value);
  shadow.appendChild(root);

  // -- panel ---------------------------------------------------------------
  const panel = element('div', 'panel');
  const head = element('div', 'head');
  const heading = element('div');
  heading.appendChild(element('div', 't', opts.title));
  if (opts.subtitle) heading.appendChild(element('div', 's', opts.subtitle));
  const closeBtn = element('button', null, '×');
  closeBtn.title = opts.strings.close;
  head.append(heading, closeBtn);

  const log = element('div', 'log');

  const form = element('form');
  const input = element('input');
  input.placeholder = opts.strings.placeholder;
  input.autocomplete = 'off';
  const sendBtn = element('button', null, opts.strings.send);
  sendBtn.type = 'submit';
  form.append(input, sendBtn);

  let micBtn = null;
  if (opts.voiceEndpoint) {
    micBtn = element('button', 'ghost', '🎤');
    micBtn.type = 'button';
    micBtn.title = opts.strings.mic;
    form.appendChild(micBtn);
  }

  panel.append(head, log, form);
  root.appendChild(panel);

  let launcherBtn = null;
  if (opts.launcher) {
    launcherBtn = element('button', 'launcher', opts.strings.launcher);
    root.appendChild(launcherBtn);
  }

  // -- rendering -----------------------------------------------------------
  function add(text, cls) {
    const node = element('div', `msg ${cls}`, text);
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  /** Links arrive twice: structured, and as a plain block glued to the text. */
  function stripLinkBlock(text) {
    const at = text.lastIndexOf(`\n\n${opts.strings.links}`);
    return at < 0 ? text : text.slice(0, at);
  }

  function addLinks(links) {
    if (!links.length) return;
    const box = element('div', 'links');
    for (const item of links) {
      const anchor = element('a', null, item.title || item.url);
      anchor.href = item.url;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      box.appendChild(anchor);
    }
    log.appendChild(box);
    log.scrollTop = log.scrollHeight;
  }

  /** `mode: confirm` actions come back parked and wait for an Ok/Cancel. */
  function addConfirm(result) {
    const card = element('div', 'card', result.confirm_prompt || result.message);
    const row = element('div', 'row');
    for (const [label, approved, cls] of [
      [opts.strings.ok, true, 'primary'],
      [opts.strings.cancel, false, ''],
    ]) {
      const button = element('button', cls, label);
      button.onclick = async () => {
        card.remove();
        try {
          const data = await post(confirmUrl, { action: result.action, approved });
          add(data.message || '', 'bot');
        } catch (err) {
          add(opts.strings.error, 'sys');
        }
      };
      row.appendChild(button);
    }
    card.appendChild(row);
    log.appendChild(card);
    log.scrollTop = log.scrollHeight;
  }

  function render(data) {
    const results = data.results || [];
    const links = results.flatMap((r) => r.links || []);
    const text = links.length ? stripLinkBlock(data.text || '') : (data.text || '');
    if (text.trim()) add(text, 'bot');
    addLinks(links);
    for (const result of results) {
      if (result.status === 'needs_confirm') addConfirm(result);
    }
  }

  // -- transport -----------------------------------------------------------
  async function auth() {
    if (!opts.getToken) return {};
    const token = await opts.getToken();
    return token ? { token } : {};
  }

  async function extraHeaders() {
    const value = typeof opts.headers === 'function' ? await opts.headers() : opts.headers;
    return value || {};
  }

  /**
   * This browser's local time as ISO-8601 *with its offset*: 2026-07-23T14:07:12+03:00.
   *
   * Sent with every request so the assistant can read dates out of a sentence —
   * «на 25 июля», «завтра в 11:15» mean nothing without knowing today, and the
   * server may well be in another timezone (or in UTC, which is already on the
   * next date for anyone east of London late in the evening).
   *
   * `Date#toISOString` is deliberately not used: it converts to UTC and drops
   * the offset, which is the one part that says where the person's day starts.
   */
  function localTime() {
    const now = new Date();
    const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
    const offset = -now.getTimezoneOffset();               // minutes east of UTC
    const sign = offset < 0 ? '-' : '+';
    return (
      `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
      `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}` +
      `${sign}${pad(offset / 60)}:${pad(offset % 60)}`
    );
  }

  async function post(url, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeout);
    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: opts.credentials,
        headers: { 'Content-Type': 'application/json', ...(await extraHeaders()) },
        body: JSON.stringify({ locale: opts.locale, ts: localTime(), ...(await auth()), ...body }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function ask(text) {
    add(text, 'me');
    const pending = add(opts.strings.thinking, 'sys');
    try {
      const data = await post(opts.endpoint, { text });
      pending.remove();
      render(data);
    } catch (err) {
      pending.textContent = opts.strings.error;
      pending.className = 'msg sys';
    }
  }

  form.onsubmit = (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    ask(text);
  };

  // -- voice (optional) ----------------------------------------------------
  let recorder = null;
  if (micBtn) {
    micBtn.onclick = async () => {
      if (recorder && recorder.state === 'recording') {
        recorder.stop();
        return;
      }
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        add(String(err), 'sys');
        return;
      }
      const chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (event) => chunks.push(event.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        micBtn.className = 'ghost';
        const pending = add(opts.strings.transcribing, 'sys');
        const body = new FormData();
        body.append('locale', opts.locale);
        body.append('ts', localTime());
        const credentials = await auth();
        if (credentials.token) body.append('token', credentials.token);
        body.append('audio', new Blob(chunks, { type: recorder.mimeType }), 'voice.webm');
        try {
          const response = await fetch(opts.voiceEndpoint, {
            method: 'POST', body, credentials: opts.credentials, headers: await extraHeaders(),
          });
          const data = await response.json();
          pending.remove();
          if (data.transcript) add(data.transcript, 'me');
          render(data);
        } catch (err) {
          pending.textContent = opts.strings.error;
        }
      };
      recorder.start();
      micBtn.className = 'ghost rec';
      add(opts.strings.recording, 'sys');
    };
  }

  // -- open / close --------------------------------------------------------
  let greeted = false;

  function open() {
    panel.classList.add('open');
    if (launcherBtn) launcherBtn.style.display = 'none';
    input.focus();
    if (opts.greetOnOpen && !greeted) {
      greeted = true;   // one attempt: a missing digest is not worth retrying
      post(welcomeUrl, {}).then(render).catch(() => {});
    }
  }

  function close() {
    panel.classList.remove('open');
    if (launcherBtn) launcherBtn.style.display = '';
  }

  closeBtn.onclick = close;
  if (launcherBtn) launcherBtn.onclick = open;
  if (opts.open) open();

  return {
    open,
    close,
    toggle: () => (panel.classList.contains('open') ? close() : open()),
    /** Ask a question programmatically, e.g. from a "Help" link on the page. */
    ask: (text) => { open(); return ask(text); },
    isOpen: () => panel.classList.contains('open'),
    destroy: () => host.remove(),
  };
}

// Configured straight from the tag: <script type="module" src="…" data-endpoint="…">
const tag = document.currentScript
  || document.querySelector('script[src*="onbo-widget"][data-endpoint]');
if (tag && tag.dataset.endpoint) {
  const {
    endpoint, title, subtitle, accent, position, theme, locale, voiceEndpoint, tokenEndpoint,
  } = tag.dataset;
  init({
    endpoint,
    ...(title && { title }),
    ...(subtitle && { subtitle }),
    ...(accent && { accent }),
    ...(position && { position }),
    ...(theme && { theme }),
    ...(locale && { locale }),
    ...(voiceEndpoint && { voiceEndpoint }),
    ...(tokenEndpoint && { tokenEndpoint }),
  });
}

if (typeof window !== 'undefined') window.OnboWidget = { init };

export default { init };
