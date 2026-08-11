// Microphone/line-in recording via getUserMedia + Web Audio.
// Captures raw Float32 PCM at the context rate (no lossy MediaRecorder step).
// Uses an AudioWorklet when available, falling back to ScriptProcessorNode.
//
// With ?fakeinput=1 the input stream is synthesized in Web Audio
// (oscillator + amplitude LFO through a MediaStreamDestination), which
// exercises the exact same capture path without needing a real microphone —
// used for automated testing and demos.

const WORKLET_URL = new URL('./capture-worklet.js', import.meta.url);

export class Recorder {
  constructor() {
    this.ctx = null;
    this.stream = null;
    this.source = null;
    this.analyser = null;
    this.captureNode = null;
    this.chunks = [];
    this.recording = false;
    this.fake = false;
    this._fakeNodes = null;
  }

  get sampleRate() {
    return this.ctx ? this.ctx.sampleRate : 0;
  }

  get recordedSamples() {
    return this.chunks.reduce((n, c) => n + c.length, 0);
  }

  static async listInputDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === 'audioinput');
  }

  /** Open the input (deviceId, or 'fake' for the synthesized test input). */
  async init(deviceId) {
    await this.close();
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === 'suspended') await this.ctx.resume();

    if (deviceId === 'fake') {
      this.fake = true;
      this.stream = this._makeFakeStream();
    } else {
      this.fake = false;
      const constraints = {
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      };
      this.stream = await navigator.mediaDevices.getUserMedia(constraints);
    }

    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 2048;
    this.source.connect(this.analyser);
    return this;
  }

  _makeFakeStream() {
    const dest = this.ctx.createMediaStreamDestination();
    const osc = this.ctx.createOscillator();
    osc.type = 'sawtooth';
    osc.frequency.value = 220;
    const amp = this.ctx.createGain();
    amp.gain.value = 0.0;
    // 2 Hz amplitude LFO → audible rhythmic pulses on the meter/waveform
    const lfo = this.ctx.createOscillator();
    lfo.type = 'square';
    lfo.frequency.value = 2;
    const lfoGain = this.ctx.createGain();
    lfoGain.gain.value = 0.35;
    lfo.connect(lfoGain).connect(amp.gain);
    osc.connect(amp).connect(dest);
    osc.start();
    lfo.start();
    this._fakeNodes = [osc, lfo];
    return dest.stream;
  }

  /** Current input level, 0..1 (peak of the analyser window). */
  getLevel() {
    if (!this.analyser) return 0;
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) {
      const a = Math.abs(buf[i]);
      if (a > peak) peak = a;
    }
    return peak;
  }

  /** Time-domain snapshot for the live waveform display. */
  getWaveform() {
    if (!this.analyser) return new Float32Array(0);
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    return buf;
  }

  async startCapture() {
    if (!this.ctx || this.recording) return;
    this.chunks = [];
    this.recording = true;
    try {
      await this.ctx.audioWorklet.addModule(WORKLET_URL);
      this.captureNode = new AudioWorkletNode(this.ctx, 'capture-processor', {
        numberOfInputs: 1,
        numberOfOutputs: 0,
        channelCount: 1,
      });
      this.captureNode.port.onmessage = (e) => {
        if (this.recording) this.chunks.push(new Float32Array(e.data));
      };
      this.source.connect(this.captureNode);
    } catch {
      // Fallback for browsers without AudioWorklet
      this.captureNode = this.ctx.createScriptProcessor(4096, 1, 1);
      this.captureNode.onaudioprocess = (e) => {
        if (this.recording) {
          this.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        }
      };
      this.source.connect(this.captureNode);
      this.captureNode.connect(this.ctx.destination); // required to run
    }
  }

  /** Stop and return { samples, sampleRate }. */
  stopCapture() {
    this.recording = false;
    if (this.captureNode) {
      try {
        this.source.disconnect(this.captureNode);
        this.captureNode.disconnect();
      } catch { /* already disconnected */ }
      if (this.captureNode.port) this.captureNode.port.onmessage = null;
      this.captureNode = null;
    }
    const total = this.recordedSamples;
    const samples = new Float32Array(total);
    let offset = 0;
    for (const chunk of this.chunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }
    this.chunks = [];
    return { samples, sampleRate: this.sampleRate };
  }

  async close() {
    this.recording = false;
    if (this._fakeNodes) {
      for (const n of this._fakeNodes) {
        try { n.stop(); } catch { /* not started */ }
      }
      this._fakeNodes = null;
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
    if (this.ctx) {
      await this.ctx.close().catch(() => {});
      this.ctx = null;
    }
    this.source = null;
    this.analyser = null;
    this.captureNode = null;
  }
}
