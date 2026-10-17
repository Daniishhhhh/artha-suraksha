# Visual & UX Design Specification: Artha-Suraksha
> **CONFLICT HIERARCHY:** Product intent (PRD) -> Architecture -> Rules -> Design -> Active task (Task.md). If files disagree, the higher one wins.

## 1. Design Tokens (Dark-Mode Acoustic Telemetry Workbench)
> *Note: Only specified in the PDFs as a "React / Next.js + Tailwind CSS" live judge telemetry dashboard and caller console; exact tokens below standardize implementation across frontend components.*

* **Color Palette (Tailwind Slate/Emerald/Amber/Rose):**
  * **Backgrounds:** App Canvas `#090D16` (`bg-slate-950`), Panel Surface `#0F172A` (`bg-slate-900`), Elevated Card `#1E293B` (`bg-slate-800/80`), Border `#334155` (`border-slate-700`).
  * **Text:** Primary `#F8FAFC` (`text-slate-50`), Secondary `#94A3B8` (`text-slate-400`), Muted/Monospace Labels `#64748B` (`text-slate-500`).
  * **Semantic Status:** Sovereign Primary `#3B82F6` (`blue-500`), Latency Target Met (`<800 ms`) `#10B981` (`emerald-500`), VAD / Noise Warning `#F59E0B` (`amber-500`), Barge-In / Handoff Alert `#F43F5E` (`rose-500`).
* **Typography:**
  * **UI & Indic Transcripts:** `Inter` / `Noto Sans Devanagari` (ensures crisp native-script rendering for Hindi, Kannada, Tamil, Marathi, Punjabi)[cite: 10].
  * **Telemetry, Timers & JSON Logs:** `JetBrains Mono` / `monospace` (`tabular-nums` mandatory so live millisecond counters never jitter).
* **Spacing & Radii:** `4px` base grid; `p-4` (`16px`) panel padding; `gap-4` (`16px`) grid gaps; `rounded-xl` (`12px`) panel corners; `rounded-full` status pills.

## 2. Layout & Responsive Rules
* **Desktop (`>= 1024px` — Primary Demo Recording View):** 12-column split-screen viewport (`h-screen overflow-hidden`) designed specifically for the unedited 60-second LinkedIn/X split-screen video[cite: 1, 3]:
  * **Left Column (4 Cols):** **Caller Terminal & 8 kHz Noise Simulator** (Journey 1).
  * **Right Column (8 Cols):** Top tab bar switching between **Screen 1: Live Acoustic & Latency Telemetry** (Journey 1) and **Screen 2: Supervisor Batch STT & Compliance Audit** (Journey 2)[cite: 8, 9, 13].
* **Tablet / Mobile (`< 1024px`):** Stacks vertically (`flex-col overflow-y-auto`) with Caller Terminal sticky at the top.

## 3. Component List & Traceability to PRD Journeys
| Component | Screen / Location | PRD Journey & Feature Trace | Key Visual Elements |
| :--- | :--- | :--- | :--- |
| **AI Disclosure Banner** | Top Header (Global) | Journey 1 (`F-05`, `R-15`) | Persistent emerald/blue badge: *"AI Assistant Disclosure: Active on Turn 0 | 100% Synthetic Data Mode (A/C XX4521)"*[cite: 3, 5, 6, 10]. |
| **Caller Terminal & Noise Mixer** | Screen 1 (Left Panel) | Journey 1 (`F-06`) | Start/End Call button, live mic waveform, **8 kHz PSTN Bandpass Toggle (300–3400 Hz)**, **Bazaar/Traffic Noise SNR Slider (0 dB to +20 dB)**, and Barge-In flash indicator[cite: 9, 13]. |
| **P95 Latency Waterfall Card** | Screen 1 (Right Top) | Journey 1 (`F-02`–`F-06`) | Horizontal stacked progress bars for Stage 1 Transport (`<20 ms`), Stage 2 Prisma STT (`~200 ms`), Stage 3 Evon TTFT (`~302 ms`), Stage 4 Timbre TTFB (`<100–150 ms`), and Total Turnaround (`~622 ms` vs. `800 ms` limit line)[cite: 6, 7, 13]. |
| **VAD & Lexicon State Inspector** | Screen 1 (Right Middle) | Journey 1 (`F-03`, `F-06`) | Live badges showing active socket mode (`Fast-Turn 350ms` vs. `Digit-Capture 800ms`), `x-min-speech-ms: 250`, and active `x-bias-list` word pills (`EMI`, `PMFBY`, `KCC`, `IRDAI`, `किस्त`)[cite: 8, 9]. |
| **Live Transcript & Tool Feed** | Screen 1 (Right Bottom) | Journey 1 (`F-04`, `F-05`) | Dual stream showing native-script/Hinglish caller transcript, Evon tool call JSON (`schedule_emi_deferral`), Timbre zero-SSML financial text (`₹1,24,567.50`), and Human Handoff context drawer[cite: 5, 6, 13]. |
| **Supervisor Batch Audit View** | Screen 2 (Tab 2) | Journey 2 (`F-07`) | Synthetic noisy `.wav` selector/uploader, `with_denoise=True` toggle, 10-second polling progress bar, and formatted JSON compliance report from Evon (`enable_thinking=True`)[cite: 5, 8, 10]. |

## 4. Screen States (Loading, Empty, Error, Success)
| Screen / Panel | Empty State | Loading / Active Streaming State | Error / Fallback State | Success / Completed State |
| :--- | :--- | :--- | :--- | :--- |
| **Screen 1: Live Caller & Telemetry Console** | *"Ready to Connect — Select Mock Replay (0 Credits) or Live Gnani WSS (`A/C XX4521` seeded)."* | Pulsing mic ring, live 1,024-byte frame counter ticking every 32ms/64ms, streaming partial transcript & stage timers[cite: 9]. | Amber/Rose banner if WSS drops or confidence `< 0.65`; automatically opens **Human Agent Handoff Context Card**[cite: 13]. | Green `<800 ms` P95 badge, updated SQLite EMI schedule displayed, and zero-overlap barge-in count logged[cite: 9]. |
| **Screen 2: Supervisor Batch Audit Tab** | *"No Batch Job Running — Upload or select a synthetic noisy call recording to audit."* | Progress stepper: `Job Created` -> `Started` -> `Polling (/stt/v3/batch/jobs every 10s)` -> `Evon Thinking Audit...`[cite: 5, 8, 10]. | Explicit error card showing HTTP status / timeout with one-click retry without losing uploaded file reference. | Side-by-side denoised native transcript and green/red compliance badges (`AI Disclosure: PASS`, `PII Check: PASS`)[cite: 3, 5, 8]. |

## 5. Accessibility Basics
* Minimum `4.5:1` contrast ratio on all text and latency badges; status indicators always pair color with text/icons (`PASS`, `WARN`, `BARGE-IN`).
* Full keyboard navigation (`Space` to toggle mute/barge-in test, `Tab` between Caller Console and Supervisor Audit Tab) and `aria-live="polite"` on transcript and compliance output containers.