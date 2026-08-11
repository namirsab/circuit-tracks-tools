// AudioWorklet processor that forwards mono input frames to the main thread.
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel && channel.length) {
      // Copy — the engine reuses the underlying buffer between calls
      this.port.postMessage(new Float32Array(channel).buffer, []);
    }
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
