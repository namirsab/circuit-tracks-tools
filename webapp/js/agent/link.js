// Agent Link client: connects this tab to a relay (see ../../../link/) so
// remote MCP clients get a URL for the tool registry. The relay forwards
// tools/list and tools/call over the socket; results come from the same
// registry that backs WebMCP and window.webtracks. The session id and secret
// are kept in sessionStorage so a reload keeps the same MCP URL.
export const DEFAULT_LINK_URL = 'wss://link.webtracks.namirsab.dev/ws';
const URL_KEY = 'ct-agent-link-url';
const SESSION_KEY = 'ct-agent-link-session';
const RETRY_MS = [1000, 2000, 4000, 8000, 15000];

// Storage access is wrapped: private mode throws, and Node (tests) has none.
const storage = {
  get(name, key) { try { return globalThis[name]?.getItem(key) ?? null; } catch { return null; } },
  set(name, key, value) {
    try {
      const store = globalThis[name];
      if (!store) return;
      if (value == null) store.removeItem(key); else store.setItem(key, value);
    } catch { /* private mode */ }
  },
};

export class AgentLink extends EventTarget {
  constructor(registry, { url = null, clientVersion = '' } = {}) {
    super();
    this.registry = registry;
    this.clientVersion = clientVersion;
    this.url = url ?? storage.get('localStorage', URL_KEY) ?? DEFAULT_LINK_URL;
    this.state = 'off'; // off | connecting | connected | reconnecting | error
    this.error = null;
    this.mcpUrl = null;
    this.session = null;
    this.ws = null;
    this.wanted = false;
    this.attempt = 0;
    this.timer = null;
    this.calls = 0;
  }

  setUrl(url) {
    const clean = String(url ?? '').trim();
    this.url = clean || DEFAULT_LINK_URL;
    storage.set('localStorage', URL_KEY, this.url === DEFAULT_LINK_URL ? null : this.url);
    if (this.wanted) this.reconnectNow();
  }

  connect() {
    this.wanted = true;
    this.attempt = 0;
    this.open();
  }

  disconnect() {
    this.wanted = false;
    clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) { try { ws.close(1000, 'disconnected by user'); } catch { /* already closed */ } }
    this.setState('off', { mcpUrl: null, error: null });
  }

  reconnectNow() {
    clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    if (ws) { try { ws.close(); } catch { /* ignore */ } }
    this.open();
  }

  open() {
    let ws;
    try {
      ws = new WebSocket(this.url);
    } catch (err) {
      this.setState('error', { error: `Bad relay URL: ${err.message}` });
      return;
    }
    this.ws = ws;
    this.setState(this.attempt ? 'reconnecting' : 'connecting', { error: null });
    ws.addEventListener('open', () => {
      const resume = this.storedSession();
      ws.send(JSON.stringify({
        type: 'hello',
        client: `web-tracks${this.clientVersion ? `/${this.clientVersion}` : ''}`,
        tools: this.registry.names().length,
        ...(resume ? { resume } : {}),
      }));
    });
    ws.addEventListener('message', (e) => this.onMessage(ws, e.data));
    ws.addEventListener('close', (e) => {
      if (this.ws !== ws) return; // superseded
      this.ws = null;
      if (!this.wanted) { this.setState('off', { mcpUrl: null }); return; }
      const delay = RETRY_MS[Math.min(this.attempt, RETRY_MS.length - 1)];
      this.attempt += 1;
      this.setState('reconnecting', { error: e.reason || (this.attempt > 2 ? 'Relay unreachable, retrying' : null) });
      this.timer = setTimeout(() => this.open(), delay);
    });
  }

  storedSession() {
    try {
      const raw = storage.get('sessionStorage', SESSION_KEY);
      const s = raw ? JSON.parse(raw) : null;
      return s?.session && s?.secret && s.url === this.url ? { session: s.session, secret: s.secret } : null;
    } catch { return null; }
  }

  async onMessage(ws, data) {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }
    if (msg.type === 'hello') {
      this.attempt = 0;
      this.session = msg.session;
      storage.set('sessionStorage', SESSION_KEY, JSON.stringify({ session: msg.session, secret: msg.secret, url: this.url }));
      this.setState('connected', { mcpUrl: msg.mcp_url, error: null });
      return;
    }
    if (msg.type === 'error') { this.setState('error', { error: msg.message }); return; }
    if (msg.type === 'ping') { ws.send(JSON.stringify({ type: 'pong' })); return; }
    if (msg.method === undefined || msg.id === undefined) return;
    let reply;
    try {
      if (msg.method === 'tools/list') {
        reply = { id: msg.id, result: { tools: this.registry.list() } };
      } else if (msg.method === 'tools/call') {
        this.calls += 1;
        const { name, arguments: args } = msg.params ?? {};
        reply = { id: msg.id, result: await this.registry.call(name, args ?? {}) };
      } else {
        reply = { id: msg.id, error: { message: `Unknown method ${msg.method}` } };
      }
    } catch (err) {
      reply = { id: msg.id, error: { message: err?.message ?? String(err) } };
    }
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(reply));
  }

  setState(state, extra = {}) {
    this.state = state;
    Object.assign(this, extra);
    this.dispatchEvent(new CustomEvent('change', { detail: { state, mcpUrl: this.mcpUrl, error: this.error } }));
  }
}
