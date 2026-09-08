import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { ToolRegistry } from '../js/agent/registry.js';
import { AgentLink, DEFAULT_LINK_URL } from '../js/agent/link.js';

// Minimal in-memory WebSocket and storage doubles.
class FakeSocket extends EventTarget {
  static instances = [];
  constructor(url) {
    super();
    this.url = url;
    this.sent = [];
    this.readyState = 0;
    FakeSocket.instances.push(this);
  }
  static get OPEN() { return 1; }
  send(data) { this.sent.push(JSON.parse(data)); }
  close(code = 1000, reason = '') { this.readyState = 3; this.dispatchEvent(Object.assign(new Event('close'), { code, reason })); }
  // test helpers
  open() { this.readyState = 1; this.dispatchEvent(new Event('open')); }
  receive(obj) { this.dispatchEvent(Object.assign(new Event('message'), { data: JSON.stringify(obj) })); }
}
class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}

const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  FakeSocket.instances = [];
  globalThis.WebSocket = FakeSocket;
  globalThis.localStorage = new MemoryStorage();
  globalThis.sessionStorage = new MemoryStorage();
});
afterEach(() => { delete globalThis.WebSocket; delete globalThis.localStorage; delete globalThis.sessionStorage; });

function makeLink() {
  const registry = new ToolRegistry().register({
    name: 'set_bpm', description: 'Tempo', inputSchema: { type: 'object', properties: { bpm: { type: 'number' } }, required: ['bpm'] },
    execute: ({ bpm }) => `Tempo ${bpm}`,
  });
  return new AgentLink(registry, { clientVersion: '0.1.0' });
}

test('connects, says hello, and exposes the MCP URL from the relay', async () => {
  const link = makeLink();
  const states = [];
  link.addEventListener('change', (e) => states.push(e.detail.state));
  assert.equal(link.url, DEFAULT_LINK_URL);
  link.connect();
  const ws = FakeSocket.instances[0];
  assert.equal(ws.url, DEFAULT_LINK_URL);
  ws.open();
  assert.deepEqual(ws.sent[0], { type: 'hello', client: 'web-tracks/0.1.0', tools: 1 });
  ws.receive({ type: 'hello', session: 'abc12345', secret: 's'.repeat(24), mcp_url: 'https://relay/mcp/abc12345/sss' });
  assert.equal(link.state, 'connected');
  assert.equal(link.mcpUrl, 'https://relay/mcp/abc12345/sss');
  assert.deepEqual(states, ['connecting', 'connected']);
  assert.match(sessionStorage.getItem('ct-agent-link-session'), /abc12345/);
});

test('answers tools/list and tools/call through the registry, errors included', async () => {
  const link = makeLink();
  link.connect();
  const ws = FakeSocket.instances[0];
  ws.open();
  ws.receive({ id: 1, method: 'tools/list', params: {} });
  await tick();
  assert.equal(ws.sent[1].id, 1);
  assert.equal(ws.sent[1].result.tools[0].name, 'set_bpm');
  ws.receive({ id: 2, method: 'tools/call', params: { name: 'set_bpm', arguments: { bpm: 120 } } });
  await tick();
  assert.deepEqual(ws.sent[2], { id: 2, result: { content: [{ type: 'text', text: 'Tempo 120' }] } });
  ws.receive({ id: 3, method: 'tools/call', params: { name: 'set_bpm', arguments: { bpm: 'x' } } });
  await tick();
  assert.equal(ws.sent[3].result.isError, true);
  ws.receive({ id: 4, method: 'resources/list', params: {} });
  await tick();
  assert.match(ws.sent[4].error.message, /Unknown method/);
  assert.equal(link.calls, 2);
});

test('resumes the stored session on reconnect and stops when disconnected', async () => {
  const link = makeLink();
  link.connect();
  const first = FakeSocket.instances[0];
  first.open();
  first.receive({ type: 'hello', session: 'abc12345', secret: 's'.repeat(24), mcp_url: 'https://relay/mcp/abc12345/sss' });
  first.close(1006, '');
  assert.equal(link.state, 'reconnecting');
  await new Promise((r) => setTimeout(r, 1100)); // first retry after 1 s
  const second = FakeSocket.instances[1];
  assert.ok(second, 'reconnected');
  second.open();
  assert.deepEqual(second.sent[0].resume, { session: 'abc12345', secret: 's'.repeat(24) });
  link.disconnect();
  assert.equal(link.state, 'off');
  assert.equal(link.mcpUrl, null);
  await new Promise((r) => setTimeout(r, 1100));
  assert.equal(FakeSocket.instances.length, 2, 'no reconnect after disconnect');
});

test('setUrl persists overrides and forgets the default', () => {
  const link = makeLink();
  link.setUrl(' ws://localhost:8770/ws ');
  assert.equal(link.url, 'ws://localhost:8770/ws');
  assert.equal(localStorage.getItem('ct-agent-link-url'), 'ws://localhost:8770/ws');
  link.setUrl('');
  assert.equal(link.url, DEFAULT_LINK_URL);
  assert.equal(localStorage.getItem('ct-agent-link-url'), null);
});
