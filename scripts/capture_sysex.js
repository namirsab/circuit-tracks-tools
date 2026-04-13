// Bidirectional WebMIDI SysEx capture for Novation Circuit Tracks
//
// Usage:
//   1. Open Novation Components (components.novationmusic.com) in Chrome
//   2. Connect your Circuit Tracks
//   3. Open DevTools console (Cmd+Option+J)
//   4. Paste this entire script and press Enter
//   5. Perform the action you want to capture (send/receive project, save patch, etc.)
//   6. Run dumpCapture() in the console to download the captured data as JSON
//
// The output JSON contains an array of messages, each with:
//   - dir: 'out' (host → device) or 'in' (device → host)
//   - idx: sequential message number
//   - len: byte count
//   - data: array of byte values

(async () => {
  const midi = await navigator.requestMIDIAccess({ sysex: true });
  const origSend = MIDIOutput.prototype.send;
  let msgCount = 0;
  const captured = [];

  // Capture outbound (Host → Device)
  MIDIOutput.prototype.send = function(data, ...args) {
    const arr = Array.from(data);
    if (arr[0] === 0xF0) {
      msgCount++;
      const hex = arr.map(b => b.toString(16).padStart(2, '0')).join(' ');
      const preview = arr.length > 60 ? hex.substring(0, 180) + '...' : hex;
      console.log(`[OUT #${msgCount}] ${arr.length} bytes: ${preview}`);
      captured.push({ dir: 'out', idx: msgCount, len: arr.length, data: arr });
    }
    return origSend.call(this, data, ...args);
  };

  // Capture inbound (Device → Host)
  for (const input of midi.inputs.values()) {
    input.addEventListener('midimessage', (e) => {
      const arr = Array.from(e.data);
      if (arr[0] === 0xF0) {
        msgCount++;
        const hex = arr.map(b => b.toString(16).padStart(2, '0')).join(' ');
        const preview = arr.length > 60 ? hex.substring(0, 180) + '...' : hex;
        console.log(`[IN  #${msgCount}] ${arr.length} bytes: ${preview}`);
        captured.push({ dir: 'in', idx: msgCount, len: arr.length, data: arr });
      }
    });
  }

  window.dumpCapture = () => {
    const blob = new Blob([JSON.stringify(captured, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'sysex_capture.json'; a.click();
    console.log(`Saved ${captured.length} SysEx messages`);
  };

  console.log('SysEx capture active (both directions). Trigger an action, then call dumpCapture().');
})();
