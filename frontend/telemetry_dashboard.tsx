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
type BatchStage = "CREATED" | "STARTED" | "POLLING" | "COMPLETED" | "EVON_THINKING_AUDIT";

const BATCH_LANGUAGES = ["hi-IN", "en-IN", "kn-IN"] as const;

interface ComplianceReport {
  job_id: string;
  denoised: boolean;
  detected_languages: string[];
  verbatim_transcript: string;
  ai_disclosure_verified: boolean;
  synthetic_pii_compliant: boolean;
  extracted_entities: Record<string, unknown>;
  emi_or_claim_mutations: Record<string, unknown>[];
  handoff_triggered: boolean;
  sentiment: string;
  enable_thinking_used: boolean;
  reasoning_trace: string;
}

interface BatchResponse {
  job_id: string;
  status: string;
  history?: string[];
  batch_result?: Record<string, unknown>;
  compliance_report?: ComplianceReport;
  report?: ComplianceReport;
  error?: string;
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function fileAsBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return window.btoa(binary);
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

function BatchAuditPanel() {
  const [batchFile, setBatchFile] = useState<File | null>(null);
  const [sampleFile, setSampleFile] = useState("synthetic_noisy_call_xx4521.wav");
  const [withDenoise, setWithDenoise] = useState(true);
  const [batchState, setBatchState] = useState<ViewState>("empty");
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchJob, setBatchJob] = useState<BatchResponse | null>(null);
  const [pollLabel, setPollLabel] = useState("Ready for mock replay");
  const abortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | null>(null);

  const clearBatchPolling = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearBatchPolling, [clearBatchPolling]);

  const runBatchAudit = useCallback(async (retryFile?: File | null) => {
    clearBatchPolling();
    const file = retryFile === undefined ? batchFile : retryFile;
    const controller = new AbortController();
    abortRef.current = controller;
    setBatchState("loading");
    setBatchError(null);
    setBatchJob(null);
    setPollLabel("Job Created · Starting Prisma batch lifecycle");
    try {
      const payload: Record<string, unknown> = {
        file_name: file?.name || sampleFile,
        with_denoise: withDenoise,
        languages: [...BATCH_LANGUAGES],
        poll_interval_s: 0.01,
      };
      if (file) payload.audio_base64 = await fileAsBase64(file);
      const response = await fetch("/api/batch/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const raw: unknown = await response.json();
      if (!isRecord(raw)) throw new Error("Batch audit returned an invalid JSON object");
      if (!response.ok || raw.status === "ERROR") {
        const detail = typeof raw.detail === "string" ? raw.detail : "Batch audit failed";
        throw new Error(`${detail} (HTTP ${response.status})`);
      }
      if (typeof raw.job_id !== "string") throw new Error("Batch audit did not return a job ID");
      setPollLabel("Polling (/stt/v3/batch/jobs every 10s)");
      let finalJob = raw as unknown as BatchResponse;
      // The primary endpoint completes the local mock in milliseconds.  Keep
      // the GET fallback for a live/asynchronous gateway response so the same
      // view can render intermediate CREATED/STARTED/POLLING states.
      for (let attempt = 0; finalJob.status !== "COMPLETED" && attempt < 30; attempt += 1) {
        if (finalJob.status === "ERROR") throw new Error(finalJob.error || "Batch audit failed");
        const jobResponse = await fetch(`/api/batch/jobs/${encodeURIComponent(raw.job_id)}`, {
          signal: controller.signal,
        });
        const jobRaw: unknown = await jobResponse.json();
        if (!jobResponse.ok || !isRecord(jobRaw)) throw new Error(`Batch polling failed (HTTP ${jobResponse.status})`);
        finalJob = jobRaw as unknown as BatchResponse;
        if (finalJob.status !== "COMPLETED") {
          await new Promise<void>((resolve) => {
            pollTimerRef.current = window.setTimeout(resolve, 10_000);
          });
        }
      }
      if (finalJob.status !== "COMPLETED") throw new Error("Batch polling timed out");
      setBatchJob(finalJob);
      setPollLabel("Evon Thinking Audit... · enable_thinking=True");
      setBatchState("success");
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setBatchError(caught instanceof Error ? caught.message : "Batch audit failed");
      setBatchState("error");
      setPollLabel("Error / Fallback");
    }
  }, [batchFile, clearBatchPolling, sampleFile, withDenoise]);

  const history = batchJob?.history || [];
  const report = batchJob?.compliance_report || batchJob?.report;
  const transcript = report?.verbatim_transcript || String(batchJob?.batch_result?.verbatim_transcript || "");
  const languages = report?.detected_languages || BATCH_LANGUAGES;
  const stageComplete = (stage: BatchStage) => history.includes(stage);
  const statusTone = (passed: boolean) => (passed ? "emerald" : "rose") as "emerald" | "rose";

  return (
    <div className="space-y-4">
      <Panel title="Screen 2: Supervisor Batch Audit Tab" eyebrow="Phase 5 · denoised batch STT + compliance artifact">
        <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-3">
            <label className="grid gap-2 text-sm text-slate-300">
              Upload WAV recording
              <input
                type="file"
                accept=".wav,audio/wav"
                className="rounded-lg border border-slate-700 bg-slate-800 p-2 text-xs text-slate-300"
                onChange={(event) => setBatchFile(event.target.files?.[0] || null)}
              />
            </label>
            <label className="grid gap-2 text-sm text-slate-300">
              Or select synthetic fixture
              <select className="rounded-lg border border-slate-700 bg-slate-800 p-2 text-slate-100" value={sampleFile} onChange={(event) => setSampleFile(event.target.value)}>
                <option value="synthetic_noisy_call_xx4521.wav">8 kHz noisy code-switched call · A/C XX4521</option>
                <option value="pmfby_clause4b_dispute_8khz.wav">PMFBY Clause 4B dispute · 8 kHz synthetic replay</option>
              </select>
            </label>
            <label className="flex items-center justify-between rounded-lg bg-slate-800 p-3 text-sm text-slate-200">
              <span>Denoise before batch STT <small className="block text-xs text-slate-500">with_denoise=True is mandatory</small></span>
              <input type="checkbox" checked={withDenoise} onChange={(event) => setWithDenoise(event.target.checked)} aria-label="Toggle batch denoise" />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500">Fallback languages</span>
              {BATCH_LANGUAGES.map((language) => <Badge key={language} tone="blue">{language}</Badge>)}
            </div>
            <button className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50" onClick={() => void runBatchAudit()} disabled={batchState === "loading"}>
              {batchState === "loading" ? "Running Audit…" : "Run Batch STT & Evon Thinking Audit"}
            </button>
            {batchState === "error" && <button className="ml-2 rounded-lg border border-rose-400 px-4 py-2 text-sm text-rose-200" onClick={() => void runBatchAudit()}>Retry</button>}
          </div>
          <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-3">
            <p className="mb-3 text-xs uppercase tracking-[0.2em] text-slate-500">Lifecycle · {pollLabel}</p>
            <div className="grid gap-2 text-xs">
              {(["CREATED", "STARTED", "POLLING", "EVON_THINKING_AUDIT", "COMPLETED"] as BatchStage[]).map((stage) => (
                <div key={stage} className={`flex items-center justify-between rounded-lg p-3 ${stageComplete(stage) ? "bg-emerald-500/15 text-emerald-200" : "bg-slate-800 text-slate-500"}`}>
                  <span>{stage === "EVON_THINKING_AUDIT" ? "Evon Thinking Audit" : stage}</span>
                  <span>{stageComplete(stage) ? "PASS" : "PENDING"}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </Panel>

      {batchState === "empty" && <div className="rounded-xl border border-dashed border-slate-700 bg-slate-950/40 p-8 text-center"><p className="mb-2 text-sm font-semibold text-slate-200">Empty state: No Batch Job Running</p><p className="text-sm text-slate-400">Select a synthetic noisy call or upload a WAV to begin a zero-credit audit.</p></div>}
      {batchState === "loading" && <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200" aria-live="polite">Loading / Streaming: {pollLabel}</div>}
      {batchState === "error" && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200" role="alert">Error / Fallback: {batchError || "Batch audit failed"}</div>}

      {batchState === "success" && report && (
        <div className="grid gap-4 xl:grid-cols-2" aria-live="polite">
          <Panel title="Denoised / Native Transcript" eyebrow={`${languages.join(" · ")} · ${batchJob?.job_id || report.job_id}`}>
            <p className="mb-3 text-xs text-slate-500">Verbatim batch transcript</p>
            <p className="rounded-lg bg-slate-950 p-4 text-sm leading-6 text-slate-200">{transcript}</p>
            <div className="mt-3 flex flex-wrap gap-2">{languages.map((language) => <Badge key={language} tone="blue">{language}</Badge>)}</div>
          </Panel>
          <Panel title="Evon Compliance Report" eyebrow="enable_thinking=True · zero-credit mock artifact">
            <div className="flex flex-wrap gap-2">
              <Badge tone={statusTone(report.ai_disclosure_verified)}>AI Disclosure: {report.ai_disclosure_verified ? "PASS" : "FAIL"}</Badge>
              <Badge tone={statusTone(report.synthetic_pii_compliant)}>PII Check: {report.synthetic_pii_compliant ? "PASS" : "FAIL"}</Badge>
              <Badge tone={statusTone(report.denoised)}>Denoise: {report.denoised ? "PASS" : "FAIL"}</Badge>
              <Badge tone={statusTone(report.enable_thinking_used)}>Evon Thinking: {report.enable_thinking_used ? "PASS" : "FAIL"}</Badge>
              <Badge tone={report.handoff_triggered ? "rose" : "slate"}>Human Handoff: {report.handoff_triggered ? "TRIGGERED" : "NOT TRIGGERED"}</Badge>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg bg-slate-950 p-3">
                <p className="mb-2 text-xs uppercase tracking-[0.16em] text-slate-500">Extracted financial entities</p>
                <div className="space-y-1 text-xs text-slate-300">
                  {[
                    ["Account", report.extracted_entities.account_id],
                    ["Balance", report.extracted_entities.balance],
                    ["Due date", report.extracted_entities.due_date],
                    ["Interest", report.extracted_entities.interest_rate],
                    ["Policy", report.extracted_entities.policy_id],
                  ].map(([label, value]) => <p key={label}><span className="text-slate-500">{label}:</span> {String(value || "not detected")}</p>)}
                </div>
              </div>
              <div className="rounded-lg bg-slate-950 p-3">
                <p className="mb-2 text-xs uppercase tracking-[0.16em] text-slate-500">EMI / claim mutation log</p>
                {report.emi_or_claim_mutations.length === 0 ? <p className="text-xs text-slate-500">No mutation request detected.</p> : <ul className="space-y-1 text-xs text-slate-300">{report.emi_or_claim_mutations.map((mutation, index) => <li key={`${String(mutation.type || "mutation")}-${index}`}>• {String(mutation.type || "mutation")} · {String(mutation.tool || "audit evidence")}</li>)}</ul>}
              </div>
            </div>
            <pre aria-live="polite" className="mt-4 max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-emerald-300">{JSON.stringify(report, null, 2)}</pre>
          </Panel>
        </div>
      )}
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
          ) : <BatchAuditPanel />}

          {error && <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200" role="alert">Error / Fallback: {error}</div>}
          {!error && viewState === "empty" && <div className="rounded-xl border border-slate-700 bg-slate-900 p-4 text-sm text-slate-500">Empty state: Ready to Connect — Select Mock Replay (0 Credits) with {SYNTHETIC_ACCOUNT} seeded.</div>}
          {viewState === "loading" && <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200" aria-live="polite">Loading / Streaming: opening the local media gateway…</div>}
        </section>
      </div>
    </main>
  );
}
