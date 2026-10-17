# Product Requirements Document (PRD): Artha-Suraksha
> **CONFLICT HIERARCHY:** Product intent (PRD) -> Architecture -> Rules -> Design -> Active task (Task.md). If files disagree, the higher one wins.

## 1. Product Summary
**Artha-Suraksha** is a full-duplex, noise-resilient telephonic voice copilot and live acoustic telemetry workbench built on Gnani AI's sovereign model trio (**Prisma v2.5**, **Evon v3.3**, and **Timbre v2.5/v3.0**)[cite: 1, 5, 6, 7]. It enables rural and semi-urban citizens to check insurance claims, restructure micro-finance EMIs, and log grievances over noisy 8 kHz connections in regional languages and code-mixed Hinglish, while giving supervisors an automated post-call compliance audit pipeline[cite: 5, 8, 17, 20].

## 2. Problem Statement
Over 400 million rural and semi-urban citizens interact with Micro-Finance Institutions (MFIs), crop/health insurance schemes (PMFBY), and banking grievance cells over noisy 8 kHz cellular lines[cite: 17, 20]. Existing voice bots fail across three boundaries:
1. **Acoustic & VAD Fragility:** Static Voice Activity Detection (VAD) either severs utterances when callers pause mid-digit while reciting policy/account numbers or false-triggers on bazaar noise, pressure horns, and line hiss[cite: 9, 13, 20].
2. **Policy Grounding vs. Live Tool Drift:** Standard RAG pipelines lose cross-clause dependencies in long regulatory PDFs and suffer up to 91.7% English language drift when executing multi-step tool calls[cite: 5, 12, 20].
3. **Financial TTS Mangling:** Conventional TTS engines require brittle SSML/regex preprocessors and mispronounce Indian lakh/crore currency (`₹1,24,567.50`), masked accounts (`A/C XX4521`), and interest rates (`2.5% p.a.`)[cite: 1, 4, 6].

## 3. Target Users
| User Persona | Context & Environment | Core Need |
| :--- | :--- | :--- |
| **Rural / Semi-Urban Borrower or Policyholder** | Calling from hands-busy, high-noise environments (bazaars, farms, transit) over 8 kHz telephony in Hindi, Hinglish, or regional dialects[cite: 1, 17, 20]. | Check claim status, defer an EMI, or contest a rejection without repeating numbers or navigating rigid touch-tone IVR menus[cite: 17, 20]. |
| **Contact Center Compliance Supervisor** | Back-office auditor reviewing batch call recordings for RBI/IRDAI/DPDP compliance[cite: 5, 8, 11]. | Denoise multi-file recordings and generate structured JSON audits verifying AI disclosure and transaction accuracy[cite: 5, 8, 10]. |
| **Gnani AI Hackathon Judge / Evaluator** | Reviewing the live application and 60-second demo video on LinkedIn/X after November 10, 2026[cite: 1, 3]. | Visually verify noisy-audio resilience, mid-call word boosting, `<800 ms` P95 turnaround, and multi-model orchestration[cite: 1, 9, 13]. |

## 4. User Journeys (v1)
* **Journey 1: Live Noisy-Audio Grievance & EMI Restructuring (Caller + Judge)**
  1. User opens the **Caller & Judge Telemetry Console** (`Design.md` Screen 1), enables the **8 kHz PSTN & Bazaar Noise Mixer**, and starts a call[cite: 9, 13].
  2. System plays the Turn-0 greeting via Timbre TTS with mandatory AI disclosure[cite: 5, 10, 14].
  3. Caller asks in Hinglish/Hindi to check their account and restructure an upcoming EMI while reciting `A/C XX4521` with natural mid-digit pauses[cite: 3, 6, 9].
  4. System dynamically adjusts VAD silence tolerance (`800 ms`), boosts domain vocabulary (`EMI`, `PMFBY`, `KCC`), queries the synthetic SQLite database and 128K policy context via Evon v3.3, and reads back `₹1,24,567.50` due on `05/11/2026` at `2.5% p.a.` in natural lakh/crore cadence[cite: 6, 8, 9, 10].
  5. Caller interrupts mid-sentence (barge-in); TTS audio flushes within `<30 ms`.
  6. If confidence drops or caller requests an officer, system triggers `trigger_human_handoff` and displays the context packet.
* **Journey 2: Asynchronous Batch STT & Compliance Audit (Supervisor)**
  1. User switches to the **Supervisor Batch Audit Tab** (`Design.md` Screen 2) and submits a noisy synthetic call recording.
  2. System submits the file to Prisma Batch STT (`/stt/v3/batch/jobs` with `with_denoise=True`), polls to completion, and runs Evon v3.3 (`enable_thinking=True`) to render a structured JSON compliance card[cite: 5, 8, 10].

## 5. Scope: Must-Have (v1) vs. Later (v1.1+) & Non-Goals
| ID | Feature Name | Scope Tier | Mapped Architecture Component | Mapped Task Phase |
| :--- | :--- | :--- | :--- | :--- |
| **F-01** | **3-Key Config, Synthetic SQLite DB (`A/C XX4521`) & 0-Credit Mock Replay** | **Must-Have (v1)** | `backend/config.py`, `backend/database.py`, `backend/mock_replay.py` | Phase 1 |
| **F-02** | **TLS 1.3 Media Gateway & 1,024-Byte Monotonic PCM Frame Pacer** | **Must-Have (v1)** | `backend/media_gateway.py` | Phase 2 |
| **F-03** | **Prisma v2.5 Raw WSS Controller (VAD Presets, `x-bias-list`, `<30 ms` Barge-In)** | **Must-Have (v1)** | `backend/prisma_controller.py` | Phase 2 |
| **F-04** | **Evon v3.3 128K Policy Grounding, Dual-Mode Router & SQLite Tool Calling** | **Must-Have (v1)** | `backend/evon_orchestrator.py`, `backend/policy_context.md` | Phase 3 |
| **F-05** | **Timbre v2.5/v3.0 Clause Streamer & Turn-0 AI Disclosure** | **Must-Have (v1)** | `backend/timbre_streamer.py` | Phase 3 |
| **F-06** | **Web Audio 8 kHz Bazaar Noise Mixer & Live Telemetry Dashboard** | **Must-Have (v1)** | `frontend/audio_worklet.js`, `frontend/noise_mixer.ts`, `frontend/telemetry_dashboard.tsx` | Phase 4 |
| **F-07** | **Supervisor Batch STT (`with_denoise`) & Evon Thinking Compliance Audit** | **Must-Have (v1)** | `backend/prisma_controller.py` (batch leg), `frontend/telemetry_dashboard.tsx` | Phase 5 |
| **F-08** | **Vachana VC v1 (`vachana-vc-v1`) District Nodal Officer Voice Cloning** | **Later (v1.1)** | `backend/timbre_streamer.py` (`/api/v1/tts/voice-clone/embeddings`) | Deferred (Post-v1) |
| **F-09** | **ASR Text Substitution Map (`x-substitution-map` from Rank 2)** | **Later (v1.1)** | `backend/prisma_controller.py` | Deferred (Post-v1) |
| **F-10** | **Live PSTN SIP Trunking (Exotel / Plivo Integration)** | **Non-Goal** | Out of scope (simulated via client 8 kHz DSP filter) | Out of Scope |

## 6. Testable Acceptance Criteria per v1 Feature
* **F-01:** `database.py` returns synthetic record `A/C XX4521` with balance `₹1,24,567.50`, due date `05/11/2026`, and rate `2.5% p.a.`. Setting `USE_MOCK_GNANI=true` completes a full STT->LLM->TTS turn consuming `0` API credits.
* **F-02:** Every binary audio frame emitted to Prisma WSS is strictly `1,024 bytes` (`len(frame) == 1024`), paced at `32 ms` (16 kHz) or `64 ms` (8 kHz).
* **F-03:** Handshake injects `x-min-speech-ms: 250`, `x-min-silence-ms: 350` (or `800` in digit-capture mode), and `x-min-segment-ms: 120`. Mid-call `update_settings` sends `x-bias-list` with `x-bias-score: 1.5`. Receiving `{"type": "processing"}` dispatches `{"event": "clear_audio"}` to client in `<30 ms`.
* **F-04:** `evon_orchestrator.py` loads `policy_context.md` into context, sets `enable_thinking=False` (`temperature=0.6`, `top_p=0.95`) for live voice turns, executes all 4 deterministic database tools (`get_account_status`, `calculate_claim_payout`, `schedule_emi_deferral`, `trigger_human_handoff`), and stays in the user's Indic/code-mixed language without drifting into English[cite: 5, 10, 12, 13].
* **F-05:** Turn 0 speaks mandatory AI disclosure. Evon token deltas are buffered only until the first clause punctuation (`।`, `.`, `,`, `?`) before streaming to `wss://api.vachana.ai/api/v1/tts`. Financial strings (`₹1,24,567.50`, `A/C XX4521`) are sent without SSML.
* **F-06:** UI slider mixes 300–3400 Hz bandpass filtering and synthetic bazaar noise into mic stream; dashboard renders live P95 stage latencies and active VAD/bias states[cite: 9, 13].
* **F-07:** Uploading a recorded call file triggers `/stt/v3/batch/jobs` with `with_denoise=True`, polls every `10 s`, and passes the transcript to Evon v3.3 (`enable_thinking=True`) to render a valid JSON compliance audit[cite: 5, 8, 10].

## 7. Success Metrics
| Metric | Target Threshold | Source / Benchmark Basis |
| :--- | :--- | :--- |
| **End-to-End Voice Turnaround (P95)** | **`< 800 ms`** (`~622 ms` nominal) | Transport `<20 ms` + Prisma `~200 ms` + Evon TTFT `~302 ms` + Timbre TTFB `<100–150 ms`[cite: 6, 7, 13]. |
| **Barge-In Cutoff Latency** | **`< 30 ms`** from `{"type": "processing"}` | Halts client playback buffer immediately upon speech gate pass. |
| **API Credit Burn** | **`<= 5,000 credits`** across entire sprint | Enforced via 3-key isolation and `mock_replay.py`[cite: 1, 4]. |
| **Compliance & PII Safety** | **100% synthetic data** & **100% Turn-0 AI disclosure** | Mandatory contest and Evon v3.3 guardrails[cite: 3, 5, 10]. |

## 8. Constraints & Risks
* **Hard Deadline:** November 10, 2026, at 11:59 PM IST (no extensions)[cite: 1, 3].
* **Budget & Keys:** 5,000 programme credits total; maximum 3 free API keys on `app.gnani.ai`[cite: 1, 4].
* **Compliance & Privacy:** Zero real phone numbers, bank accounts, `[Aadhaar Redacted]`, PAN, or real customer recordings allowed. No paid promotion, bots, or engagement swaps on LinkedIn/X posts[cite: 1, 3].
* **Technical Risks:**
  1. *Browser WSS Header Limitation:* Browsers cannot send custom HTTP headers (`X-API-Key-ID`, VAD headers) on WebSocket handshakes; mitigated via `backend/media_gateway.py`.
  2. *Mid-Number Utterance Severing:* Callers pausing while reciting `A/C XX4521` split transcripts under `350 ms` VAD; mitigated via pre-warmed `800 ms` secondary socket and digit accumulator.
  3. *Evon v3.3 Compute Availability:* Mitigated via OpenAI-compatible adapter supporting hosted API, cloud vLLM/SGLang, and local quantized GGUF (`OQ-1`).

## 9. Open Questions
See Step 0 (`OQ-1` through `OQ-4`).