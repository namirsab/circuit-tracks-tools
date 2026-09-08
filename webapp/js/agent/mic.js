// Browser microphone capture for record_melody: getUserMedia + a
// ScriptProcessorNode tap, feeding raw PCM to transcribe.js. Deliberately
// thin and untested here (no DOM/Web Audio in Node) — see transcribe.js for
// the tested algorithm; this module only gets audio samples into memory.
//
// getUserMedia does not strictly require a user gesture, but requesting it
// out of the blue (e.g. a remote agent call arriving over the relay while
// the user isn't looking) is a bad experience and fails outright if the
// browser has not yet decided the permission. enableMicrophone() primes the
// OS/browser permission from a real click in the sidebar (see panel.js);
// recordSeconds() then works whether the call comes from a local click or a
// remote MCP tool call, without a fresh prompt.

let permissionPrimed = false;

/** One-time mic permission grant, called from a user gesture. */
export async function enableMicrophone() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
  stream.getTracks().forEach((t) => t.stop());
  permissionPrimed = true;
  return true;
}

export function microphonePrimed() {
  return permissionPrimed;
}

/**
 * Record `seconds` of mono PCM through `ctx` (the app's AudioContext, so the
 * capture shares its clock/sample rate). Resolves with
 * `{ audio: Float32Array, sampleRate }`.
 */
export async function recordSeconds(ctx, seconds) {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch (err) {
    throw new Error(`Microphone unavailable: ${err.message}. Click "Enable microphone" in the AI agent panel and retry.`);
  }
  permissionPrimed = true;

  try {
    return await new Promise((resolve, reject) => {
      const source = ctx.createMediaStreamSource(stream);
      const bufferSize = 4096;
      const processor = ctx.createScriptProcessor(bufferSize, 1, 1);
      const sink = ctx.createGain();
      sink.gain.value = 0; // ScriptProcessorNode only runs while connected to a destination
      const chunks = [];
      let collected = 0;
      const target = Math.ceil(seconds * ctx.sampleRate);
      let done = false;

      const finish = (err, result) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        processor.onaudioprocess = null;
        processor.disconnect();
        source.disconnect();
        sink.disconnect();
        if (err) reject(err); else resolve(result);
      };

      processor.onaudioprocess = (e) => {
        const data = e.inputBuffer.getChannelData(0);
        chunks.push(Float32Array.from(data));
        collected += data.length;
        if (collected >= target) {
          const out = new Float32Array(collected);
          let offset = 0;
          for (const c of chunks) { out.set(c, offset); offset += c.length; }
          finish(null, { audio: out.subarray(0, target), sampleRate: ctx.sampleRate });
        }
      };
      source.connect(processor);
      processor.connect(sink);
      sink.connect(ctx.destination);
      const timer = setTimeout(() => finish(new Error('Recording timed out')), (seconds + 5) * 1000);
    });
  } finally {
    stream.getTracks().forEach((t) => t.stop());
  }
}
