// Sidebar "AI agent" panel: connect/disconnect the Agent Link, show the MCP
// URL, a rolling log of tool calls, and an undo button for agent changes.
const STATE_LABEL = { off: 'OFF', connecting: '…', connected: 'LIVE', reconnecting: 'RETRY', error: 'ERROR' };

function summarize(args) {
  const s = JSON.stringify(args ?? {});
  return s === '{}' ? '' : s.length > 70 ? `${s.slice(0, 67)}…` : s;
}

export function bindAgentPanel(webtracks, app) {
  const $ = (id) => document.getElementById(id);
  const els = {
    state: $('agent-state'), connect: $('btn-agent-connect'), connected: $('agent-connected'),
    url: $('agent-url'), copy: $('btn-agent-copy'), disconnect: $('btn-agent-disconnect'),
    relay: $('agent-relay-url'), log: $('agent-log'), undo: $('btn-agent-undo'), error: $('agent-error'),
  };
  if (!els.connect) return;
  const { link } = webtracks;

  els.relay.value = link.url;
  els.relay.addEventListener('change', () => { link.setUrl(els.relay.value); els.relay.value = link.url; });
  els.connect.addEventListener('click', () => {
    app.engine.resume(); // the click doubles as the audio-unlock gesture
    link.connect();
  });
  els.disconnect.addEventListener('click', () => link.disconnect());
  els.copy.addEventListener('click', async () => {
    if (!link.mcpUrl) return;
    try {
      await navigator.clipboard.writeText(link.mcpUrl);
      els.copy.textContent = 'Copied';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(els.url);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      els.copy.textContent = 'Select + ⌘C';
    }
    setTimeout(() => { els.copy.textContent = 'Copy'; }, 1500);
  });
  els.undo.addEventListener('click', () => webtracks.call('undo'));

  const render = () => {
    const st = link.state;
    els.state.textContent = STATE_LABEL[st] ?? st;
    els.state.className = `fx-state agent-${st}`;
    els.connect.hidden = st !== 'off' && st !== 'error';
    els.connected.hidden = st === 'off' || st === 'error';
    els.url.textContent = link.mcpUrl ?? (st === 'connecting' ? 'Connecting to relay…' : 'Reconnecting…');
    els.copy.disabled = !link.mcpUrl;
    els.error.textContent = link.error ?? '';
    els.error.hidden = !link.error;
  };
  link.addEventListener('change', render);
  render();

  window.addEventListener('webtracks:tool', (e) => {
    const ev = e.detail;
    if (ev.type === 'call') app.lcdMsg(`AI: ${ev.name}`);
    if (ev.type !== 'result') return;
    const li = document.createElement('li');
    li.className = ev.ok ? 'ok' : 'err';
    const name = document.createElement('b');
    name.textContent = ev.name;
    const args = document.createElement('span');
    args.textContent = summarize(ev.args);
    li.append(name, ' ', args);
    els.log.prepend(li);
    while (els.log.children.length > 8) els.log.lastChild.remove();
    els.undo.hidden = webtracks.api.history.length === 0;
  });
}
