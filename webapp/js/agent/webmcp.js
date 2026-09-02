// WebMCP adapter: publishes the registry through the browser's model context
// API so in-browser agents (and bridge extensions) can discover and call the
// tools. The API moved from navigator.modelContext to document.modelContext in
// July 2026; Chrome keeps the old name as a deprecated alias, so both are
// checked. Chrome serves it during the origin trial (149-156) and behind
// chrome://flags otherwise.

export function getModelContext() {
  if (typeof document !== 'undefined' && document.modelContext) return document.modelContext;
  if (typeof navigator !== 'undefined' && navigator.modelContext) return navigator.modelContext;
  return null;
}

export async function exposeWebMCP(registry) {
  const mc = getModelContext();
  if (!mc) return { supported: false, reason: 'no modelContext API in this browser' };
  const tools = registry.list().map((t) => ({
    name: t.name,
    description: t.description,
    inputSchema: t.inputSchema,
    ...(t.annotations ? { annotations: t.annotations } : {}),
    execute: (args) => registry.call(t.name, args ?? {}),
  }));
  const api = typeof document !== 'undefined' && document.modelContext ? 'document.modelContext' : 'navigator.modelContext';
  try {
    if (typeof mc.provideContext === 'function') {
      // provideContext replaces the page's whole tool set, which is what we want.
      await mc.provideContext({ tools });
    } else if (typeof mc.registerTool === 'function') {
      for (const t of tools) await mc.registerTool(t);
    } else {
      return { supported: false, reason: `${api} has neither provideContext nor registerTool` };
    }
    return { supported: true, api, count: tools.length };
  } catch (err) {
    return { supported: true, api, count: 0, error: err?.message ?? String(err) };
  }
}
