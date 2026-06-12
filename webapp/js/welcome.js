// First-run welcome overlay: quick-start guide + disclaimer. Shown once
// (tracked in localStorage), reopenable any time from the footer About link.
const WELCOMED_KEY = 'webtracks.welcomed';

export function buildWelcome(root) {
  root.innerHTML = `
    <div class="welcome-panel">
      <button class="ko-close" id="welcome-close" title="Close">×</button>
      <div class="welcome-head">
        <span class="logo-bars"><i></i><i></i><i></i><i></i></span>
        <span class="logo-text">web<b>tracks</b></span>
      </div>
      <p class="welcome-tagline">A groovebox in your browser — 8 tracks, a 4×8 pad grid,
        and a starter pack of generated sounds. Everything runs locally; nothing is uploaded.</p>
      <ul class="welcome-list">
        <li><b>Play</b> — hit <kbd>Space</kbd> or the green ▶ button.</li>
        <li><b>Try a demo</b> — press <b>Projects</b> and pick a coloured slot.
          While playing, the switch waits for the pattern to end (Shift+pad switches instantly).</li>
        <li><b>Pick a track</b> — Synth 1/2, MIDI 1/2, Drum 1–4 buttons. Pads then play
          notes or program steps; <b>Note</b>, <b>Velocity</b>, <b>Gate</b> and
          <b>Pattern Settings</b> change what the grid edits.</li>
        <li><b>Shape the sound</b> — drag knobs up/down. The 8 macro knobs tweak the
          active track; <b>Preset</b> picks one of 128 patches.</li>
        <li><b>Save &amp; share</b> — <b>Save</b> stores to the current project slot;
          the sidebar exports projects (.ncs), patches (.syx) and whole packs.
          Drop a <b>.circuittrackspack</b> anywhere to load one.</li>
        <li><b>Keyboard</b> — press <kbd>/</kbd> for the full key mapping.</li>
      </ul>
      <button id="welcome-go" class="welcome-go">Start jamming</button>
      <p class="welcome-disclaimer">Web Tracks is an unofficial, non-commercial fan project
        by <a href="https://namirsab.dev" target="_blank" rel="noopener">namirsab.dev</a>.
        It is not affiliated with, endorsed, or sponsored by Novation or Focusrite plc.
        Circuit Tracks is a trademark of its owner; the file format was independently
        reverse-engineered, and all bundled sounds, patches and projects are generated from code.</p>
    </div>`;
}

// Wires open/close behaviour; shows the overlay automatically on first visit.
export function bindWelcome(root) {
  const setVisible = (v) => {
    root.classList.toggle('visible', v);
    if (!v) {
      try { localStorage.setItem(WELCOMED_KEY, '1'); } catch { /* private mode */ }
    }
  };
  root.querySelector('#welcome-close').addEventListener('click', () => setVisible(false));
  root.querySelector('#welcome-go').addEventListener('click', () => setVisible(false));
  root.addEventListener('click', (e) => {
    if (e.target === root) setVisible(false); // backdrop click
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && root.classList.contains('visible')) setVisible(false);
  });
  document.getElementById('about-link')?.addEventListener('click', (e) => {
    e.preventDefault();
    setVisible(true);
  });

  let welcomed = false;
  try { welcomed = !!localStorage.getItem(WELCOMED_KEY); } catch { /* private mode */ }
  if (!welcomed) setVisible(true);
}
