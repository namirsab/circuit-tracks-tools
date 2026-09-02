import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { ToolRegistry } from '../js/agent/registry.js';
import { exposeWebMCP, getModelContext } from '../js/agent/webmcp.js';

const registry = new ToolRegistry().register({
  name: 'echo', description: 'Echo', inputSchema: { type: 'object', properties: { x: { type: 'integer' } }, required: ['x'] },
  annotations: { readOnlyHint: true }, execute: ({ x }) => ({ x }),
});

afterEach(() => { delete globalThis.document; delete globalThis.navigator; });

test('reports missing support without touching anything', async () => {
  assert.equal(getModelContext(), null);
  assert.deepEqual(await exposeWebMCP(registry), { supported: false, reason: 'no modelContext API in this browser' });
});

test('prefers document.modelContext.provideContext and pipes execute through the registry', async () => {
  const seen = {};
  globalThis.document = { modelContext: { provideContext: async (ctx) => { seen.tools = ctx.tools; } } };
  globalThis.navigator = { modelContext: { registerTool: () => { throw new Error('should not be used'); } } };
  const status = await exposeWebMCP(registry);
  assert.deepEqual(status, { supported: true, api: 'document.modelContext', count: 1 });
  const [tool] = seen.tools;
  assert.deepEqual(Object.keys(tool), ['name', 'description', 'inputSchema', 'annotations', 'execute']);
  assert.deepEqual(await tool.execute({ x: 4 }), { content: [{ type: 'text', text: '{"x":4}' }], structuredContent: { x: 4 } });
  const bad = await tool.execute({ x: 'no' });
  assert.equal(bad.isError, true);
});

test('falls back to navigator.modelContext.registerTool and reports failures', async () => {
  const registered = [];
  globalThis.navigator = { modelContext: { registerTool: async (t) => { registered.push(t.name); } } };
  assert.deepEqual(await exposeWebMCP(registry), { supported: true, api: 'navigator.modelContext', count: 1 });
  assert.deepEqual(registered, ['echo']);
  globalThis.navigator = { modelContext: { registerTool: async () => { throw new Error('denied'); } } };
  const failed = await exposeWebMCP(registry);
  assert.equal(failed.error, 'denied');
  assert.equal(failed.count, 0);
});
