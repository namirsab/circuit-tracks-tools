// Agent bootstrap: builds the tool registry over the headless API and
// publishes it three ways — window.webtracks (console, tests, Claude in
// Chrome's JavaScript tool), document.modelContext (WebMCP) and, later, the
// Agent Link relay for remote MCP clients.
import { ToolRegistry } from './registry.js';
import { AgentApi } from './api.js';
import { createTools } from './tools.js';
import { exposeWebMCP } from './webmcp.js';

export const AGENT_VERSION = '0.1.0';
const LOG_LIMIT = 200;

export function initAgent(app) {
  const api = new AgentApi(app);
  const registry = new ToolRegistry();
  registry.registerAll(createTools(api));

  const log = [];
  registry.on((event) => {
    log.push({ ...event, at: Date.now() });
    if (log.length > LOG_LIMIT) log.shift();
    window.dispatchEvent(new CustomEvent('webtracks:tool', { detail: event }));
  });

  const webtracks = {
    version: AGENT_VERSION,
    api,
    tools: registry,
    log,
    list: () => registry.list(),
    call: (name, args = {}) => registry.call(name, args),
    webmcp: { supported: null },
  };
  window.webtracks = webtracks;

  exposeWebMCP(registry).then((status) => {
    webtracks.webmcp = status;
    if (status.supported && !status.error) {
      console.info(`[webtracks] ${status.count} agent tools published via ${status.api}`);
    } else if (status.error) {
      console.warn(`[webtracks] WebMCP registration failed: ${status.error}`);
    }
  });
  console.info(`[webtracks] agent tools ready: window.webtracks.call(name, args) — ${registry.names().length} tools`);
  return webtracks;
}
