import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  NoiseMixerEngine,
  VoiceSessionClient,
  type BackendEvent,
  type NoiseProfile,
  type SampleRate,
  type VadMode,
} from "./noise_mixer";

const SYNTHETIC_ACCOUNT = "A/C XX4521";
const FINANCIAL_AMOUNT = "₹1,24,567.50";
const FINANCIAL_DATE = "05/11/2026";
const FINANCIAL_RATE = "2.5% p.a.";
const BIAS_WORDS = ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"];

// Design.md Screen 1 tokens are kept explicit so a host app can map them to
// Tailwind configuration without changing the dashboard component.
export const DESIGN_TOKENS = {
  canvas: "#090D16",
  panel: "#0F172A",
  card: "#1E293B",
  border: "#334155",
  textPrimary: "#F8FAFC",
  textSecondary: "#94A3B8",
  primary: "#3B82F6",
  success: "#10B981",
  warning: "#F59E0B",
  alert: "#F43F5E",
} as const;

type Screen = "live" | "audit";
type ViewState = "empty" | "loading" | "error" | "success";

interface WaterfallState {
  transport_ms: number;
  prisma_stt_ms: number;
  evon_ttft_ms: number;
  timbre_ttfb_ms: number;
  total_turnaround_ms: number;
}

interface TranscriptEntry {
  text: string;
  language?: string;
  confidence?: number;
  timestamp: string;
}

interface HandoffPacket {
  account: string;
  reason: string;
  confidence: number;
  transcript: string[];
}

const INITIAL_WATERFALL: WaterfallState = {
  transport_ms: 12,
  prisma_stt_ms: 200,
  evon_ttft_ms: 302,
  timbre_ttfb_ms: 80,
  total_turnaround_ms: 594,
};

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function Badge({
  children,
  tone = "blue",
}: {
  children: React.ReactNode;
  tone?: "blue" | "emerald" | "amber" | "rose" | "slate";
}) {
  const tones = {
    blue: "border-blue-500/40 bg-blue-500/15 text-blue-300",
    emerald: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
    amber: "border-amber-500/40 bg-amber-500/15 text-amber-300",
    rose: "border-rose-500/40 bg-rose-500/15 text-rose-300",
    slate: "border-slate-700 bg-slate-800 text-slate-300",
  };
  return (
    <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${tones[tone]}`}>
      {children}
    </span>
  );
}

function Panel({
  title,
  eyebrow,
  children,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-700 bg-slate-900 p-4 shadow-xl ${className}`}>
      {eyebrow && <p className="mb-1 text-xs uppercase tracking-[0.2em] text-slate-500">{eyebrow}</p>}
      <h2 className="mb-4 text-sm font-semibold text-slate-50">{title}</h2>
      {children}
    </section>
  );
}

function MetricBar({
  label,
  value,
  target,
  color = "bg-emerald-500",
}: {
  label: string;
  value: number;
  target: string;
  color?: string;
}) {
  const width = Math.min(100, Math.max(4, (value / 800) * 100));
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-slate-300">{label}</span>
        <span className="font-mono tabular-nums text-slate-400">{value.toFixed(0)} ms · {target}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

function EmptyAuditShell() {
  return (
    <div className="rounded-xl border border-dashed border-slate-700 bg-slate-950/40 p-8 text-center">
      <p className="mb-2 text-sm font-semibold text-slate-200">No Batch Job Running</p>
      <p className="mx-auto max-w-md text-sm text-slate-400">
        Upload or select a synthetic noisy call recording to audit. Phase 5 will wire
        `/stt/v3/batch/jobs` with `with_denoise=True` and Evon `enable_thinking=True`.
      </p>
      <div className="mt-6 grid gap-2 text-left text-xs text-slate-500 sm:grid-cols-4">
        <span className="rounded-lg bg-slate-800 p-3">Job Created</span>
        <span className="rounded-lg bg-slate-800 p-3">Started</span>
        <span className="rounded-lg bg-slate-800 p-3">Polling every 10s</span>
        <span className="rounded-lg bg-slate-800 p-3">Evon Thinking Audit</span>
      </div>
    </div>
  );
}

export default function TelemetryDashboard() {
  const [screen, setScreen] = useState<Screen>("live");
  const [sampleRate, setSampleRate] = useState<SampleRate>(16000);
  const [vadMode, setVadMode] = useState<VadMode>("FAST_TURN");
  const [pstnEnabled, setPstnEnabled] = useState(false);
  const [noiseSnrDb, setNoiseSnrDb] = useState(20);
  const [noiseProfile, setNoiseProfile] = useState<NoiseProfile>("clean");
  const [connected, setConnected] = useState(false);
  const [viewState, setViewState] = useState<ViewState>("empty");
  const [turn0Verified, setTurn0Verified] = useState(false);
  const [bargeInMs, setBargeInMs] = useState<number | null>(null);
  const [frameCount, setFrameCount] = useState(0);
  const [rmsDb, setRmsDb] = useState(-60);
  const [waterfall, setWaterfall] = useState(INITIAL_WATERFALL);
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>([]);
  const [toolCalls, setToolCalls] = useState<Record<string, unknown>[]>([]);
  const [handoff, setHandoff] = useState<HandoffPacket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const clientRef = useRef<VoiceSessionClient | null>(null);
  const mixerRef = useRef<NoiseMixerEngine | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const handleBackendEvent = useCallback((event: BackendEvent) => {
    if (event.event === "ai_disclosure") {
      setTurn0Verified(event.verified === true);
      setViewState("success");
    } else if (event.event === "clear_audio") {
      setBargeInMs(numberValue(event.latency_ms, 0));
    } else if (event.event === "transcript") {
      const text = typeof event.text === "string" ? event.text : "";
      setTranscripts((current) => [
        ...current.slice(-7),
        { text, language: typeof event.language === "string" ? event.language : undefined, confidence: numberValue(event.confidence, 0.98), timestamp: new Date().toLocaleTimeString() },
      ]);
    } else if (event.event === "telemetry") {
      setWaterfall((current) => ({
        transport_ms: numberValue(event.transport_ms, current.transport_ms),
        prisma_stt_ms: numberValue(event.prisma_stt_ms, current.prisma_stt_ms),
        evon_ttft_ms: numberValue(event.evon_ttft_ms, current.evon_ttft_ms),
        timbre_ttfb_ms: numberValue(event.timbre_ttfb_ms, current.timbre_ttfb_ms),
        total_turnaround_ms: numberValue(event.total_turnaround_ms, current.total_turnaround_ms),
      }));
      if (Array.isArray(event.tool_calls)) setToolCalls(event.tool_calls as Record<string, unknown>[]);
    } else if (event.event === "error") {
      setError(typeof event.error === "string" ? event.error : "Gateway error");
      setViewState("error");
    } else if (event.event === "handoff") {
      setHandoff({
        account: SYNTHETIC_ACCOUNT,
        reason: typeof event.reason === "string" ? event.reason : "Human handoff requested",
        confidence: numberValue(event.confidence_score, 0.64),
        transcript: transcripts.map((entry) => entry.text),
      });
    }
  }, [transcripts]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (mixerRef.current && connected) setRmsDb(mixerRef.current.getInputRmsDb());
    }, 250);
    return () => window.clearInterval(interval);
  }, [connected]);

  const teardown = useCallback(() => {
    clientRef.current?.disconnect();
    mixerRef.current?.dispose();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    audioContextRef.current?.close();
    clientRef.current = null;
    mixerRef.current = null;
    streamRef.current = null;
    audioContextRef.current = null;
    setConnected(false);
    setViewState("empty");
  }, []);

  const startCall = useCallback(async () => {
    setError(null);
    setViewState("loading");
    try {
      const context = new AudioContext();
      audioContextRef.current = context;
      await context.audioWorklet.addModule("/frontend/audio_worklet.js");
      const worklet = new AudioWorkletNode(context, "artha-pcm-processor", { outputChannelCount: [1] });
      const mixer = new NoiseMixerEngine(context, sampleRate);
      mixer.setPstn8kHzEnabled(pstnEnabled);
      mixer.setNoiseSnrDb(noiseSnrDb);
      mixer.setNoiseProfile(noiseProfile);
      mixerRef.current = mixer;
      if (navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        mixer.connectStream(stream);
        mixer.output.connect(worklet);
      }
      const client = new VoiceSessionClient({
        sampleRate,
        vadMode,
        worklet,
        onEvent: handleBackendEvent,
        onFrame: setFrameCount,
      });
      await client.connect();
      clientRef.current = client;
      setConnected(true);
      setViewState("success");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to start synthetic call");
      setViewState("error");
      teardown();
    }
  }, [handleBackendEvent, noiseProfile, noiseSnrDb, pstnEnabled, sampleRate, teardown, vadMode]);

  const updateSampleRate = (next: SampleRate) => {
    setSampleRate(next);
    if (connected) teardown();
  };

  const updateVadMode = (next: VadMode) => {
    setVadMode(next);
    clientRef.current?.setVadMode(next);
  };

  const updateNoiseProfile = (next: NoiseProfile) => {
    setNoiseProfile(next);
    mixerRef.current?.setNoiseProfile(next);
  };

  const updateNoiseSnr = (next: number) => {
    setNoiseSnrDb(next);
    mixerRef.current?.setNoiseSnrDb(next);
  };

  const sessionMode = connected ? "Mock Replay — 0 Credits" : "Ready for Mock Replay — 0 Credits";
  const latencyPass = waterfall.total_turnaround_ms < 800;
  const cadence = sampleRate === 16000 ? "32 ms" : "64 ms";
  const stateLabel = useMemo(
    () => ({ empty: "READY", loading: "STREAMING", error: "FALLBACK", success: "PASS" }[viewState]),
    [viewState],
  );

  return (
    <main className="min-h-screen bg-[#090D16] p-4 font-sans text-[#F8FAFC] sm:p-6">
      <header className="mb-4 flex flex-col gap-3 rounded-xl border border-slate-700 bg-slate-900 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.22em] text-blue-300">Artha-Suraksha · F-06</p>
          <h1 className="mt-1 text-xl font-bold">Caller &amp; Judge Telemetry Console</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone="emerald">AI Assistant Disclosure: Active on Turn 0 | 100% Synthetic Data Mode (A/C XX4521)</Badge>
          <Badge tone={turn0Verified ? "emerald" : "amber"}>Turn-0: {turn0Verified ? "PASS" : "PENDING"}</Badge>
          <Badge tone="slate">{sessionMode}</Badge>
        </div>
      </header>

      <div className="grid min-h-[calc(100vh-9rem)] grid-cols-1 gap-4 lg:grid-cols-12">
        <aside className="space-y-4 lg:col-span-4">
          <Panel title="Caller Terminal & 8 kHz Noise Simulator" eyebrow="Journey 1 · live input">
            <div className="flex items-center justify-between">
              <Badge tone={connected ? "emerald" : "slate"}>{stateLabel}</Badge>
              {connected ? (
                <button className="rounded-lg bg-rose-500 px-3 py-2 text-xs font-bold text-white" onClick={teardown} aria-label="End synthetic call">End Call</button>
              ) : (
                <button className="rounded-lg bg-blue-500 px-3 py-2 text-xs font-bold text-white" onClick={startCall} aria-label="Start synthetic call">Start Call</button>
              )}
            </div>
            <div className="mt-4 grid gap-3 text-sm">
              <label className="grid gap-1 text-slate-400">Sample Rate
                <select className="rounded-lg border border-slate-700 bg-slate-800 p-2 text-slate-100" value={sampleRate} onChange={(event) => updateSampleRate(Number(event.target.value) as SampleRate)}>
                  <option value={16000}>16 kHz Wideband</option>
                  <option value={8000}>8 kHz PSTN Telephonic</option>
                </select>
              </label>
              <label className="flex items-center justify-between rounded-lg bg-slate-800 p-3 text-slate-200">
                <span>8 kHz PSTN Bandpass <small className="block text-xs text-slate-500">300–3400 Hz</small></span>
                <input type="checkbox" checked={pstnEnabled} onChange={(event) => { setPstnEnabled(event.target.checked); mixerRef.current?.setPstn8kHzEnabled(event.target.checked); }} aria-label="Toggle 8 kHz PSTN bandpass" />
              </label>
              <label className="grid gap-2 text-slate-400">Bazaar / Traffic Noise SNR: <span className="font-mono tabular-nums text-slate-100">{noiseSnrDb} dB</span>
                <input type="range" min="0" max="20" value={noiseSnrDb} onChange={(event) => updateNoiseSnr(Number(event.target.value))} aria-label="Noise SNR from 0 dB to 20 dB" />
              </label>
              <div className="flex flex-wrap gap-2">
                {(["clean", "bazaar", "traffic", "pstn_hiss"] as NoiseProfile[]).map((profile) => (
                  <button key={profile} className={`rounded-full border px-2 py-1 text-xs ${noiseProfile === profile ? "border-amber-400 bg-amber-500/20 text-amber-200" : "border-slate-700 text-slate-400"}`} onClick={() => updateNoiseProfile(profile)}>
                    {profile === "pstn_hiss" ? "PSTN Line Hiss" : profile === "bazaar" ? "Bazaar Chatter" : profile === "traffic" ? "Traffic Horns" : "Clean Studio"}
                  </button>
                ))}
              </div>
            </div>
          </Panel>

          <Panel title="Synthetic Quick Tests" eyebrow="0-credit replay controls">
            <div className="grid gap-2">
              {[
                "Check A/C XX4521 Balance",
                "Defer EMI due 05/11/2026",
                "Trigger Barge-In Interrupt",
                "Simulate Low-Confidence Handoff",
              ].map((label) => (
                <button key={label} className="rounded-lg border border-slate-700 bg-slate-800 p-3 text-left text-xs text-slate-300 transition hover:border-blue-400" onClick={() => clientRef.current?.sendSyntheticUtterance(label, 1)}>
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-slate-800 p-3"><span className="block text-slate-500">PCM frames</span><strong className="font-mono tabular-nums text-slate-100">{frameCount}</strong><span className="text-slate-500"> · {cadence}</span></div>
              <div className="rounded-lg bg-slate-800 p-3"><span className="block text-slate-500">Input RMS</span><strong className="font-mono tabular-nums text-slate-100">{rmsDb.toFixed(1)} dB</strong></div>
            </div>
            <div className="mt-3">{bargeInMs !== null && <Badge tone="rose">BARGE-IN · {bargeInMs.toFixed(1)} ms Audio Flush</Badge>}</div>
          </Panel>
        </aside>

        <section className="space-y-4 lg:col-span-8">
          <div className="flex items-center gap-2 border-b border-slate-700 pb-2" role="tablist" aria-label="Telemetry screens">
            <button role="tab" aria-selected={screen === "live"} className={`rounded-lg px-3 py-2 text-sm ${screen === "live" ? "bg-blue-500 text-white" : "text-slate-400"}`} onClick={() => setScreen("live")}>Screen 1: Live Acoustic &amp; Latency</button>
            <button role="tab" aria-selected={screen === "audit"} className={`rounded-lg px-3 py-2 text-sm ${screen === "audit" ? "bg-blue-500 text-white" : "text-slate-400"}`} onClick={() => setScreen("audit")}>Screen 2: Supervisor Batch Audit Tab</button>
          </div>

          {screen === "live" ? (
            <>
              <Panel title="P95 Latency Waterfall" eyebrow="Target: ~622 ms vs 800 ms human threshold">
                <div className="grid gap-3" aria-live="polite">
                  <MetricBar label="Stage 1 · Media Transport" value={waterfall.transport_ms} target="<20 ms" color="bg-blue-500" />
                  <MetricBar label="Stage 2 · Prisma v2.5 STT" value={waterfall.prisma_stt_ms} target="~200 ms" color="bg-blue-500" />
                  <MetricBar label="Stage 3 · Evon v3.3 TTFT" value={waterfall.evon_ttft_ms} target="~302 ms" color="bg-amber-500" />
                  <MetricBar label="Stage 4 · Timbre v2.5/v3.0 TTFB" value={waterfall.timbre_ttfb_ms} target="<100–150 ms" color="bg-emerald-500" />
                  <MetricBar label="Cumulative Turnaround" value={waterfall.total_turnaround_ms} target="~622 ms / 800 ms" color={latencyPass ? "bg-emerald-500" : "bg-rose-500"} />
                </div>
                <div className="mt-4"><Badge tone={latencyPass ? "emerald" : "rose"}>{latencyPass ? "PASS (<800ms)" : "WARN (>800ms)"}</Badge></div>
              </Panel>

              <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="VAD & Lexicon State Inspector" eyebrow="F-03 acoustic state">
                  <div className="flex flex-wrap gap-2"><Badge tone={vadMode === "FAST_TURN" ? "blue" : "amber"}>{vadMode === "FAST_TURN" ? "Fast-Turn 350ms silence" : "Digit-Capture 800ms silence"}</Badge><Badge tone="slate">x-min-speech-ms: 250</Badge><Badge tone="slate">x-min-segment-ms: 120</Badge><Badge tone="slate">confidence &gt;= 0.75</Badge></div>
                  <div className="mt-4 flex flex-wrap gap-2">{BIAS_WORDS.map((word) => <Badge key={word} tone="blue">{word} · score: 1.5</Badge>)}</div>
                  <div className="mt-4 flex gap-2"><button className="rounded-lg bg-blue-500 px-3 py-2 text-xs" onClick={() => updateVadMode("FAST_TURN")}>Fast-Turn</button><button className="rounded-lg bg-amber-500 px-3 py-2 text-xs text-slate-950" onClick={() => updateVadMode("DIGIT_CAPTURE")}>Digit-Capture</button></div>
                </Panel>

                <Panel title="Human Agent Handoff Context" eyebrow="R-12 · R-15 · confidence guardrail">
                  {handoff ? <div className="space-y-2 text-sm" aria-live="polite"><Badge tone="rose">HUMAN HANDOFF OPEN</Badge><p className="text-slate-300">Account: <strong>{handoff.account}</strong></p><p className="text-slate-400">Reason: {handoff.reason}</p><p className="font-mono tabular-nums text-slate-400">Confidence: {handoff.confidence.toFixed(2)} · threshold &lt;0.65</p></div> : <p className="text-sm text-slate-500">Drawer opens automatically when confidence &lt;0.65 or `trigger_human_handoff` fires.</p>}
                </Panel>
              </div>

              <Panel title="Live Transcript, Evon Tool Call & Zero-SSML Financial Feed" eyebrow="Indic/code-mixed response stream">
                <div className="grid gap-4 xl:grid-cols-2">
                  <div className="max-h-56 space-y-2 overflow-auto rounded-lg bg-slate-950 p-3" aria-live="polite">
                    {transcripts.length === 0 ? <p className="text-sm text-slate-500">Ready for native-script/Hinglish transcript…</p> : transcripts.map((entry, index) => <div key={`${entry.timestamp}-${index}`} className="border-b border-slate-800 pb-2 text-sm"><p className="text-slate-200">{entry.text}</p><p className="text-xs text-slate-500">{entry.language || "hi-IN"} · confidence {entry.confidence?.toFixed(2)} · {entry.timestamp}</p></div>)}
                  </div>
                  <div className="space-y-3" aria-live="polite">
                    <Badge tone="blue">Evon v3.3 · enable_thinking=False</Badge>
                    <pre className="max-h-28 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-emerald-300">{JSON.stringify(toolCalls.length ? toolCalls : { tool: "get_account_status", account_id: SYNTHETIC_ACCOUNT }, null, 2)}</pre>
                    <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 text-sm text-emerald-100">Zero-SSML financial output: {FINANCIAL_AMOUNT} · {SYNTHETIC_ACCOUNT} · {FINANCIAL_DATE} · {FINANCIAL_RATE}</div>
                  </div>
                </div>
              </Panel>
            </>
          ) : (
            <Panel title="Screen 2: Supervisor Batch Audit Tab" eyebrow="Phase 5 shell — intentionally not wired in Phase 4">
              <EmptyAuditShell />
            </Panel>
          )}

          {error && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200" role="alert">Error / Fallback: {error}</div>}
          {!error && viewState === "empty" && <div className="rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm text-slate-500">Empty state: Ready to Connect — Select Mock Replay (0 Credits) with {SYNTHETIC_ACCOUNT} seeded.</div>}
          {viewState === "loading" && <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200" aria-live="polite">Loading / Streaming: opening the local media gateway…</div>}
        </section>
      </div>
    </main>
  );
}
