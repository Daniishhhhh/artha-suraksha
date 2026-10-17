/**
 * Dependency-free browser DSP and gateway bridge for Artha-Suraksha.
 *
 * This module intentionally contains no credentials and no cloud endpoint.
 * The browser speaks only to the local FastAPI /ws/call gateway.
 */

export type NoiseProfile = "bazaar" | "traffic" | "pstn_hiss" | "clean";
export type VadMode = "FAST_TURN" | "DIGIT_CAPTURE";
export type SampleRate = 8000 | 16000;

export interface BackendEvent {
  event: string;
  [key: string]: unknown;
}

export interface VoiceSessionClientOptions {
  gatewayUrl?: string;
  sampleRate?: SampleRate;
  vadMode?: VadMode;
  onEvent?: (event: BackendEvent) => void;
  onFrame?: (frameCount: number) => void;
  worklet?: AudioWorkletNode;
}

const FRAME_BYTES = 1024;
const DEFAULT_BIAS_WORDS = ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"];

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function createSeededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (1664525 * state + 1013904223) >>> 0;
    return state / 0xffffffff;
  };
}

/** Web Audio source, PSTN filter, procedural noise, and RMS meter. */
export class NoiseMixerEngine {
  readonly context: AudioContext;
  readonly inputGain: GainNode;
  readonly highpass: BiquadFilterNode;
  readonly lowpass: BiquadFilterNode;
  readonly noiseGain: GainNode;
  readonly analyser: AnalyserNode;
  readonly output: GainNode;
  sampleRate: SampleRate;
  pstn8kHzEnabled = false;
  noiseSnrDb = 20;
  noiseProfile: NoiseProfile = "clean";
  private noiseSource: AudioBufferSourceNode | null = null;
  private streamSource: MediaStreamAudioSourceNode | null = null;

  constructor(context: AudioContext, sampleRate: SampleRate = 16000) {
    this.context = context;
    this.sampleRate = sampleRate;
    this.inputGain = context.createGain();
    this.highpass = context.createBiquadFilter();
    this.lowpass = context.createBiquadFilter();
    this.noiseGain = context.createGain();
    this.analyser = context.createAnalyser();
    this.output = context.createGain();

    // 300–3400 Hz is the synthetic 8 kHz PSTN bandpass contract.
    this.highpass.type = "highpass";
    this.highpass.frequency.value = 300;
    this.lowpass.type = "lowpass";
    this.lowpass.frequency.value = 3400;
    this.analyser.fftSize = 1024;
    this.updateNoiseGain();
    this.rebuildGraph();
  }

  private rebuildGraph(): void {
    this.inputGain.disconnect();
    this.highpass.disconnect();
    this.lowpass.disconnect();
    this.noiseGain.disconnect();
    this.analyser.disconnect();
    this.inputGain.connect(this.pstn8kHzEnabled ? this.highpass : this.output);
    if (this.pstn8kHzEnabled) {
      this.highpass.connect(this.lowpass);
      this.lowpass.connect(this.output);
    }
    this.output.connect(this.analyser);
    this.analyser.connect(this.context.destination);
    this.noiseGain.connect(this.output);
  }

  connectStream(stream: MediaStream): void {
    this.streamSource?.disconnect();
    this.streamSource = this.context.createMediaStreamSource(stream);
    this.streamSource.connect(this.inputGain);
  }

  setSampleRate(sampleRate: SampleRate): void {
    this.sampleRate = sampleRate;
    this.setNoiseProfile(this.noiseProfile);
  }

  setPstn8kHzEnabled(enabled: boolean): void {
    this.pstn8kHzEnabled = enabled;
    this.rebuildGraph();
  }

  setNoiseSnrDb(snrDb: number): void {
    this.noiseSnrDb = clamp(snrDb, 0, 20);
    this.updateNoiseGain();
  }

  setNoiseProfile(profile: NoiseProfile): void {
    this.noiseProfile = profile;
    this.updateNoiseGain();
    if (profile !== "clean" && this.noiseSource === null) this.startNoise();
    if (profile === "clean") this.stopNoise();
  }

  private updateNoiseGain(): void {
    const noiseAmount = this.noiseProfile === "clean" ? 0 : 10 ** (-this.noiseSnrDb / 20);
    this.noiseGain.gain.setTargetAtTime(
      noiseAmount,
      this.context.currentTime,
      0.01,
    );
  }

  private createProceduralNoiseBuffer(): AudioBuffer {
    const durationSeconds = 2;
    const length = Math.max(1, Math.floor(this.context.sampleRate * durationSeconds));
    const buffer = this.context.createBuffer(1, length, this.context.sampleRate);
    const samples = buffer.getChannelData(0);
    const random = createSeededRandom(0xa27a);
    let brown = 0;
    for (let index = 0; index < length; index += 1) {
      const white = random() * 2 - 1;
      const time = index / this.context.sampleRate;
      const chatter = Math.sin(2 * Math.PI * (90 + 18 * Math.sin(time)) * time) * 0.08;
      const horn = this.noiseProfile === "traffic"
        ? (Math.sin(2 * Math.PI * 440 * time) + Math.sin(2 * Math.PI * 554 * time)) * 0.18
        : 0;
      const hiss = this.noiseProfile === "pstn_hiss" ? white * 0.35 : white * 0.08;
      brown = (brown + white * 0.04) * 0.995;
      const bazaar = this.noiseProfile === "bazaar" ? brown + chatter : 0;
      samples[index] = clamp(bazaar + horn + hiss, -1, 1);
    }
    return buffer;
  }

  startNoise(): void {
    this.stopNoise();
    if (this.noiseProfile === "clean") return;
    this.noiseSource = this.context.createBufferSource();
    this.noiseSource.buffer = this.createProceduralNoiseBuffer();
    this.noiseSource.loop = true;
    this.noiseSource.connect(this.noiseGain);
    this.noiseSource.start();
  }

  stopNoise(): void {
    if (!this.noiseSource) return;
    try {
      this.noiseSource.stop();
    } catch {
      // A source may already have ended during a browser context shutdown.
    }
    this.noiseSource.disconnect();
    this.noiseSource = null;
  }

  getInputRmsDb(): number {
    const values = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(values);
    let sum = 0;
    for (const value of values) sum += value * value;
    const rms = Math.sqrt(sum / Math.max(1, values.length));
    return 20 * Math.log10(Math.max(rms, 0.00001));
  }

  dispose(): void {
    this.stopNoise();
    this.streamSource?.disconnect();
    this.inputGain.disconnect();
    this.output.disconnect();
  }
}

/** Browser-side WebSocket contract for the Phase 2/3 FastAPI gateway. */
export class VoiceSessionClient {
  readonly gatewayUrl: string;
  sampleRate: SampleRate;
  vadMode: VadMode;
  biasWords = [...DEFAULT_BIAS_WORDS];
  private socket: WebSocket | null = null;
  private worklet: AudioWorkletNode | null = null;
  private frameCount = 0;
  private readonly onEvent?: (event: BackendEvent) => void;
  private readonly onFrame?: (frameCount: number) => void;

  constructor(options: VoiceSessionClientOptions = {}) {
    this.gatewayUrl = options.gatewayUrl || "ws://localhost:8000/ws/call";
    this.sampleRate = options.sampleRate || 16000;
    this.vadMode = options.vadMode || "FAST_TURN";
    this.onEvent = options.onEvent;
    this.onFrame = options.onFrame;
    if (options.worklet) this.bindWorklet(options.worklet);
  }

  async connect(): Promise<void> {
    const url = new URL(this.gatewayUrl, window.location.href);
    url.searchParams.set("sample_rate", String(this.sampleRate));
    url.searchParams.set("vad_mode", this.vadMode);
    this.socket = new WebSocket(url.toString());
    this.socket.binaryType = "arraybuffer";
    await new Promise<void>((resolve, reject) => {
      if (!this.socket) return reject(new Error("WebSocket was not created"));
      this.socket.onopen = () => resolve();
      this.socket.onerror = () => reject(new Error("Media gateway WebSocket failed"));
      this.socket.onmessage = (event) => this.handleMessage(event.data);
      this.socket.onclose = () => {
        this.onEvent?.({ event: "connection_closed" });
      };
    });
  }

  bindWorklet(worklet: AudioWorkletNode): void {
    this.worklet = worklet;
    worklet.port.onmessage = (event: MessageEvent) => {
      if (event.data?.type === "pcm_frame") {
        this.sendPcmFrame(event.data.buffer);
      } else if (event.data?.type === "barge_in_flushed") {
        this.onEvent?.({ event: "barge_in_flushed", ...event.data });
      }
    };
  }

  sendPcmFrame(frame: ArrayBuffer): void {
    if (frame.byteLength !== FRAME_BYTES) {
      throw new RangeError(`PCM frame must be exactly ${FRAME_BYTES} bytes`);
    }
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(frame);
    this.frameCount += 1;
    this.onFrame?.(this.frameCount);
  }

  private handleMessage(data: string | ArrayBuffer): void {
    if (typeof data !== "string") return;
    let event: BackendEvent;
    try {
      event = JSON.parse(data) as BackendEvent;
    } catch {
      this.onEvent?.({ event: "error", error: "Invalid gateway JSON" });
      return;
    }
    if (event.event === "clear_audio") {
      this.worklet?.port.postMessage({ type: "clear_audio" });
    }
    if (event.event === "audio" && typeof event.audio_base64 === "string") {
      const binary = atob(event.audio_base64);
      const pcm = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        pcm[index] = binary.charCodeAt(index);
      }
      this.worklet?.port.postMessage(
        { type: "enqueue_tts_pcm", buffer: pcm.buffer },
        [pcm.buffer],
      );
    }
    this.onEvent?.(event);
  }

  setVadMode(mode: VadMode): void {
    this.vadMode = mode;
    this.sendJson({ event: "set_vad_mode", mode });
  }

  updateBiasWords(words: string[], biasScore = 1.5): void {
    this.biasWords = [...words];
    this.sendJson({ event: "update_bias", words, bias_score: biasScore });
  }

  private sendJson(payload: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(payload));
  }

  /** Send deterministic PCM frames for UI quick tests without a microphone. */
  sendSyntheticUtterance(_label: string, frameTotal = 1): void {
    const frame = new ArrayBuffer(FRAME_BYTES);
    for (let index = 0; index < frameTotal; index += 1) this.sendPcmFrame(frame.slice(0));
  }

  disconnect(): void {
    this.socket?.close(1000, "caller ended synthetic session");
    this.socket = null;
  }
}
