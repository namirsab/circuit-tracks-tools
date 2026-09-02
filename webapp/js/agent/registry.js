// Transport-agnostic tool registry. Tools are MCP-shaped descriptors
// ({ name, description, inputSchema, annotations?, execute }) and call()
// returns an MCP CallToolResult ({ content, structuredContent?, isError? }),
// so the same registry backs WebMCP (document.modelContext), the Agent Link
// relay and the window.webtracks console API. Pure JS: testable in Node.
import { validate, formatErrors } from './schema.js';

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

export function textResult(text) {
  return { content: [{ type: 'text', text: String(text) }] };
}

export function errorResult(message) {
  return { isError: true, content: [{ type: 'text', text: String(message) }] };
}

// Normalise whatever a tool returned into a CallToolResult.
export function toResult(out) {
  if (out && typeof out === 'object' && Array.isArray(out.content)) return out;
  if (out === undefined || out === null) return textResult('OK');
  if (typeof out === 'string') return textResult(out);
  return { content: [{ type: 'text', text: JSON.stringify(out) }], structuredContent: out };
}

export class ToolRegistry {
  constructor() {
    this.tools = new Map();
    this.listeners = new Set();
    this.seq = 0;
  }

  register(tool) {
    if (!tool || typeof tool.name !== 'string' || !tool.name) throw new Error('Tool needs a name');
    if (typeof tool.execute !== 'function') throw new Error(`Tool ${tool.name} needs an execute()`);
    if (this.tools.has(tool.name)) throw new Error(`Tool ${tool.name} is already registered`);
    this.tools.set(tool.name, {
      name: tool.name,
      description: tool.description ?? '',
      inputSchema: tool.inputSchema ?? { type: 'object', properties: {} },
      annotations: tool.annotations,
      execute: tool.execute,
    });
    return this;
  }

  registerAll(tools) {
    for (const t of tools) this.register(t);
    return this;
  }

  has(name) { return this.tools.has(name); }

  get(name) { return this.tools.get(name); }

  names() { return [...this.tools.keys()]; }

  // Descriptors as an MCP tools/list result would carry them.
  list() {
    return [...this.tools.values()].map(({ name, description, inputSchema, annotations }) => (
      annotations ? { name, description, inputSchema, annotations } : { name, description, inputSchema }
    ));
  }

  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(event) {
    for (const fn of this.listeners) {
      try { fn(event); } catch (err) { console.warn('tool listener failed:', err); }
    }
  }

  async call(name, args = {}) {
    const id = ++this.seq;
    const started = now();
    const finish = (result) => {
      this.emit({ type: 'result', id, name, args, ms: now() - started, ok: !result.isError, result });
      return result;
    };
    const tool = this.tools.get(name);
    if (!tool) return finish(errorResult(`Unknown tool "${name}". Available tools: ${this.names().join(', ')}`));
    if (args == null) args = {};
    if (typeof args !== 'object' || Array.isArray(args)) {
      return finish(errorResult(`Arguments for ${name} must be a JSON object; got ${JSON.stringify(args)}`));
    }
    const errors = validate(tool.inputSchema, args);
    if (errors.length) {
      return finish(errorResult(`Invalid arguments for ${name}:\n${formatErrors(errors)}`));
    }
    this.emit({ type: 'call', id, name, args });
    try {
      return finish(toResult(await tool.execute(args)));
    } catch (err) {
      return finish(errorResult(`${name} failed: ${err?.message ?? err}`));
    }
  }
}
