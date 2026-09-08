import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ToolRegistry, toResult } from '../js/agent/registry.js';

function makeRegistry() {
  const reg = new ToolRegistry();
  reg.register({
    name: 'add',
    description: 'Add two numbers',
    inputSchema: { type: 'object', properties: { a: { type: 'number' }, b: { type: 'number' } }, required: ['a', 'b'], additionalProperties: false },
    annotations: { readOnlyHint: true },
    execute: ({ a, b }) => ({ sum: a + b }),
  });
  reg.register({ name: 'greet', description: 'Say hi', execute: () => 'hi' });
  reg.register({ name: 'boom', description: 'Throws', execute: () => { throw new Error('kaput'); } });
  reg.register({ name: 'nothing', description: 'Returns undefined', execute: () => {} });
  return reg;
}

test('list() exposes MCP tool descriptors', () => {
  const reg = makeRegistry();
  const add = reg.list().find((t) => t.name === 'add');
  assert.deepEqual(Object.keys(add), ['name', 'description', 'inputSchema', 'annotations']);
  assert.equal(reg.list().find((t) => t.name === 'greet').inputSchema.type, 'object');
});

test('call() validates, executes and wraps results', async () => {
  const reg = makeRegistry();
  const ok = await reg.call('add', { a: 1, b: 2 });
  assert.deepEqual(ok, { content: [{ type: 'text', text: '{"sum":3}' }], structuredContent: { sum: 3 } });
  assert.deepEqual(await reg.call('greet'), { content: [{ type: 'text', text: 'hi' }] });
  assert.deepEqual(await reg.call('nothing'), { content: [{ type: 'text', text: 'OK' }] });
});

test('bad arguments produce isError results with paths', async () => {
  const reg = makeRegistry();
  const r = await reg.call('add', { a: 1 });
  assert.equal(r.isError, true);
  assert.match(r.content[0].text, /Invalid arguments for add/);
  assert.match(r.content[0].text, /\$: missing required key "b"/);
  const r2 = await reg.call('add', { a: 1, b: 2, c: 3 });
  assert.match(r2.content[0].text, /\$\.c: unknown key "c"/);
  const r3 = await reg.call('add', [1, 2]);
  assert.match(r3.content[0].text, /must be a JSON object/);
});

test('unknown tools and thrown errors are reported, never thrown', async () => {
  const reg = makeRegistry();
  const r = await reg.call('nope', {});
  assert.equal(r.isError, true);
  assert.match(r.content[0].text, /Unknown tool "nope". Available tools: add, greet, boom, nothing/);
  const r2 = await reg.call('boom');
  assert.equal(r2.isError, true);
  assert.equal(r2.content[0].text, 'boom failed: kaput');
});

test('listeners see call and result events', async () => {
  const reg = makeRegistry();
  const events = [];
  const off = reg.on((e) => events.push(e));
  await reg.call('add', { a: 2, b: 2 });
  await reg.call('add', { a: 'x' });
  off();
  await reg.call('greet');
  assert.deepEqual(events.map((e) => [e.type, e.name, e.ok]), [
    ['call', 'add', undefined], ['result', 'add', true], ['result', 'add', false],
  ]);
  assert.equal(events[0].id, events[1].id);
  assert.ok(events[1].ms >= 0);
});

test('duplicate names and malformed tools are rejected at registration', () => {
  const reg = makeRegistry();
  assert.throws(() => reg.register({ name: 'add', execute() {} }), /already registered/);
  assert.throws(() => reg.register({ name: 'x' }), /needs an execute/);
});

test('toResult passes through ready-made results', () => {
  const r = { content: [{ type: 'text', text: 'x' }], isError: true };
  assert.equal(toResult(r), r);
});
