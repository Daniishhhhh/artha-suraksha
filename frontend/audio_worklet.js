"use strict";

// Prisma's browser-facing audio contract: 512 mono samples × 2 bytes = 1,024 bytes.
const FRAME_SAMPLES = 512;
const FRAME_BYTES = 1024;

class ArthaPcmWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.captureFrame = new Int16Array(FRAME_SAMPLES);
    this.captureOffset = 0;
    this.playbackQueue = [];
    this.playbackOffset = 0;
    this.port.onmessage = (event) => this.handleMessage(event.data);
  }

  handleMessage(message) {
    if (!message || typeof message.type !== "string") return;

    if (message.type === "clear_audio") {
      // Clearing one JavaScript array is synchronous and completes in this
      // render quantum, comfortably below the 30 ms barge-in budget.
      this.playbackQueue.length = 0;
      this.playbackOffset = 0;
      this.port.postMessage({
        type: "barge_in_flushed",
        flushed_at_ms: currentTime * 1000,
      });
      return;
    }

    if (message.type === "enqueue_tts_pcm") {
      const source = message.buffer;
      if (!(source instanceof ArrayBuffer) || source.byteLength === 0) return;
      if (source.byteLength % 2 !== 0) {
        this.port.postMessage({
          type: "playback_contract_error",
          message: "TTS PCM must contain complete 16-bit samples",
        });
        return;
      }
      this.playbackQueue.push(new Int16Array(source));
    }
  }

  postCaptureFrame() {
    const frame = new Int16Array(FRAME_SAMPLES);
    frame.set(this.captureFrame);
    this.port.postMessage(
      {
        type: "pcm_frame",
        bytes: FRAME_BYTES,
        samples: FRAME_SAMPLES,
        buffer: frame.buffer,
      },
      [frame.buffer],
    );
    this.captureOffset = 0;
  }

  appendInputSample(sample) {
    const clipped = Math.max(-1, Math.min(1, sample));
    this.captureFrame[this.captureOffset] =
      clipped < 0 ? Math.round(clipped * 0x8000) : Math.round(clipped * 0x7fff);
    this.captureOffset += 1;
    if (this.captureOffset === FRAME_SAMPLES) this.postCaptureFrame();
  }

  renderPlayback(outputChannel) {
    outputChannel.fill(0);
    let outputOffset = 0;
    while (outputOffset < outputChannel.length && this.playbackQueue.length > 0) {
      const current = this.playbackQueue[0];
      const available = current.length - this.playbackOffset;
      const requested = outputChannel.length - outputOffset;
      const count = Math.min(available, requested);
      outputChannel.set(
        current.subarray(this.playbackOffset, this.playbackOffset + count),
        outputOffset,
      );
      outputOffset += count;
      this.playbackOffset += count;
      if (this.playbackOffset >= current.length) {
        this.playbackQueue.shift();
        this.playbackOffset = 0;
      }
    }
  }

  process(inputs, outputs) {
    const inputChannels = inputs[0] || [];
    const monoInput = inputChannels[0];
    if (monoInput) {
      for (let index = 0; index < monoInput.length; index += 1) {
        this.appendInputSample(monoInput[index]);
      }
    }

    const outputChannels = outputs[0] || [];
    if (outputChannels[0]) this.renderPlayback(outputChannels[0]);
    for (let channel = 1; channel < outputChannels.length; channel += 1) {
      outputChannels[channel].set(outputChannels[0] || new Float32Array(outputChannels[channel].length));
    }
    return true;
  }
}

registerProcessor("artha-pcm-processor", ArthaPcmWorkletProcessor);
