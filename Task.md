# Mission & Phased Implementation Plan: Artha-Suraksha
> **CONFLICT HIERARCHY:** Product intent (PRD) -> Architecture -> Rules -> Design -> Active task (Task.md). If files disagree, the higher one wins.

## 1. Mission Objective & Scope
* **Objective:** Build, verify, and package **Artha-Suraksha** (v1) across 6 modular layers so it passes all 6 Gnani AI enterprise procurement checks and is ready for the 60-second public demo before **November 10, 2026, 11:59 PM IST**[cite: 1, 13].
* **In Scope (v1):** Features `F-01` through `F-07` (`PRD.md` Section 5).
* **Out of Scope (v1):** Live `vachana-vc-v1` voice cloning (`F-08`), `x-substitution-map` (`F-09`), and live telecom SIP trunking (`F-10`).

## 2. Phased Implementation Roadmap & Model Assignments
> *Available Models:* **Claude Opus 4.8**, **GPT "Astra"**, **Gemini Pro (via agy)**.

### Phase 1: Environment Foundation, 3-Key Firewall, Synthetic DB & 128K Policy Corpus — **[COMPLETE]**
* **Goal:** Establish the zero-credit development harness, 3-key configuration, 50-record synthetic SQLite database (`A/C XX4521`), and the multi-page regulatory policy corpus in a single session[cite: 1, 3, 6, 10].
* **Deliverables:** `.env.example`, `requirements.txt`, `backend/config.py`, `backend/database.py`, `backend/mock_replay.py`, `backend/policy_context.md`, and `tests/test_phase1_foundation.py`.
* **Dependencies:** None (root foundation).
* **Acceptance Criteria:**
  1. `pytest tests/test_phase1_foundation.py` passes 100%.
  2. `database.py` seeds 50 synthetic records including `A/C XX4521` (`₹1,24,567.50`, due `05/11/2026`, `2.5% p.a.`) and executes all 4 async tool functions (`get_account_status`, `calculate_claim_payout`, `schedule_emi_deferral`, `trigger_human_handoff`)[cite: 3, 5, 6, 13].
  3. `mock_replay.py` intercepts STT/TTS calls when `USE_MOCK_GNANI=true` consuming `0` API credits[cite: 1, 4].
  4. `policy_context.md` contains vernacular + English RBI/IRDAI/PMFBY clauses ready for Evon v3.3's 128K context window[cite: 5, 10].
* **Estimated Effort:** 1 Session (~2–3 hours).
* **RECOMMENDED MODEL:** **GPT "Astra"** — *Unmatched long-context capacity to generate the comprehensive multi-clause vernacular `policy_context.md` alongside consistent cross-file schemas in `config.py`, `database.py`, and `mock_replay.py` in one pass.*

### Phase 2: TLS 1.3 Media Gateway, 1,024-Byte Frame Pacer & Prisma v2.5 Acoustic Controller — **[COMPLETE]**
* **Goal:** Build the asynchronous WebSocket proxy, monotonic 1,024-byte PCM ring-buffer pacer, dual-socket VAD manager (`350 ms` fast turn vs. `800 ms` digit capture), mid-call `x-bias-list` updater, and `<30 ms` barge-in trigger[cite: 8, 9].
* **Deliverables:** `backend/media_gateway.py`, `backend/prisma_controller.py`, and `tests/test_phase2_acoustic.py`.
* **Dependencies:** Phase 1 (`config.py`, `mock_replay.py`).
* **Acceptance Criteria:**
  1. Pacer slices arbitrary input chunks into exact `1,024-byte` frames dispatched at `32 ms` (16 kHz) or `64 ms` (8 kHz)[cite: 9].
  2. Raw WSS handshake injects custom VAD headers (`x-min-speech-ms: 250`, `x-min-silence-ms: 350`/`800`, `x-min-segment-ms: 120`) and sends mid-session `update_settings` with `x-bias-list`[cite: 8, 9].
  3. Simulated `{"type": "processing"}` event emits `{"event": "clear_audio"}` in `<30 ms`[cite: 9].
* **Estimated Effort:** 1–2 Sessions (~4 hours).
* **RECOMMENDED MODEL:** **Claude Opus 4.8** — *Best-in-class systems engineering and concurrency reasoning for deterministic `asyncio` ring-buffer pacing, dual-WebSocket state machines, and race-condition-free barge-in cancellation.*

### Phase 3: Evon v3.3 Sovereign Logic Engine & Timbre v2.5/v3.0 Clause Streamer — **[COMPLETE]**
* **Goal:** Wire the end-to-end voice reasoning and synthesis loop connecting Prisma transcripts to Evon v3.3 (`enable_thinking=False/True` + SQLite tools) and streaming punctuation-chunked clauses into Timbre WSS with Turn-0 AI disclosure[cite: 5, 6, 10, 14].
* **Deliverables:** `backend/evon_orchestrator.py`, `backend/timbre_streamer.py`, full pipeline wiring in `backend/media_gateway.py`, and `tests/test_phase3_orchestration.py`.
* **Dependencies:** Phase 1 & Phase 2.
* **Acceptance Criteria:**
  1. Turn 0 automatically synthesizes mandatory AI disclosure via Timbre[cite: 5, 10].
  2. Live turns invoke Evon v3.3 with `enable_thinking=False` (`temp=0.6`, `top_p=0.95`), execute SQLite tool calls, and stream clauses at `।`, `.`, `,`, `?` boundaries into `wss://api.vachana.ai/api/v1/tts`[cite: 10, 14].
  3. Financial strings (`₹1,24,567.50`, `A/C XX4521`, `05/11/2026`, `2.5% p.a.`) pass to Timbre with zero SSML[cite: 6].
* **Estimated Effort:** 1–2 Sessions (~4 hours).
* **RECOMMENDED MODEL:** **Claude Opus 4.8** (with **GPT "Astra"** verifying 128K context prompts) — *Opus excels at streaming async generators, tool-call loop edge cases, and sub-800 ms latency coordination.*

### Phase 4: Web Audio 8 kHz Bazaar Noise Mixer & Live Judge Telemetry UI — **[COMPLETE]**
* **Goal:** Build the split-screen React/Tailwind frontend featuring the `AudioWorklet` PCM capture, 300–3400 Hz 8 kHz bandpass filter, synthetic bazaar/traffic noise SNR slider, `<30 ms` playback flush, and live P95 latency waterfall[cite: 9, 13].
* **Deliverables:** `frontend/audio_worklet.js`, `frontend/noise_mixer.ts`, `frontend/telemetry_dashboard.tsx` (Screen 1).
* **Dependencies:** Phase 2 & Phase 3 backend WebSocket contracts.
* **Acceptance Criteria:**
  1. Toggling the 8 kHz & Bazaar Noise Mixer visibly/audibly degrades the input stream while VAD and `x-bias-list` preserve transcription[cite: 9, 13].
  2. P95 Latency Waterfall updates after every turn (`<20 ms` / `~200 ms` / `~302 ms` / `<100–150 ms` / `~622 ms` total)[cite: 6, 7, 13].
  3. Client audio flushes immediately on receiving `{"event": "clear_audio"}`[cite: 9].
* **Estimated Effort:** 1–2 Sessions (~4 hours).
* **RECOMMENDED MODEL:** **Gemini Pro (via agy)** — *Fastest and strongest at full-stack TypeScript, Web Audio API DSP (`AudioWorklet`), and polished Tailwind CSS telemetry dashboards.*

### Phase 5: Supervisor Batch STT (`with_denoise`) Audit Pipeline & 60s Demo Verification — **[ACTIVE]**
* **Goal:** Implement the `/stt/v3/batch/jobs` denoised polling pipeline + Evon `enable_thinking=True` compliance JSON generator (Screen 2), run the 6-check enterprise benchmark, and record the 60-second LinkedIn/X demo using `GNANI_API_KEY_DEMO`[cite: 1, 5, 8, 10, 13].
* **Deliverables:** Batch STT methods in `backend/prisma_controller.py`, Screen 2 in `frontend/telemetry_dashboard.tsx`, `tests/benchmark_6_checks.py`, and final 60s demo artifact.
* **Dependencies:** Phases 1–4.
* **Acceptance Criteria:**
  1. Batch job creates, starts, and polls `/stt/v3/batch/jobs` (`with_denoise=True`) every `10 s` and outputs a validated Evon (`enable_thinking=True`) compliance JSON artifact[cite: 5, 8, 10].
  2. All 6 Gnani enterprise procurement checks pass end-to-end[cite: 13].
* **Estimated Effort:** 1 Session (~3 hours).
* **RECOMMENDED MODEL:** **GPT "Astra"** — *Ideal for full-repository cross-file auditing, running the 6-check enterprise verification suite, and validating compliance JSON schemas.*

## 3. Master Progress Checklist
- [x] **Phase 1 [COMPLETE]:** `.env.example`, `requirements.txt`, `backend/config.py`, `backend/database.py`, `backend/mock_replay.py`, `backend/policy_context.md`, and `tests/test_phase1_foundation.py`.
- [x] **Phase 2 [COMPLETE]:** `backend/media_gateway.py` (1,024-byte pacer) and `backend/prisma_controller.py` (raw WSS VAD, `x-bias-list`, `<30 ms` barge-in).
- [x] **Phase 3 [COMPLETE]:** `backend/evon_orchestrator.py` (128K context + tools) and `backend/timbre_streamer.py` (Turn-0 disclosure + clause streaming).
- [x] **Phase 4 [COMPLETE]:** `frontend/audio_worklet.js`, `frontend/noise_mixer.ts`, and `frontend/telemetry_dashboard.tsx` (Screen 1).
- [ ] **Phase 5 [ACTIVE]:** Batch STT (`with_denoise=True`) + Evon thinking compliance tab (Screen 2), 6-check benchmark, and 60s demo submission before Nov 10, 2026[cite: 1, 8, 13].

## 4. Notes
* Keep `USE_MOCK_GNANI=true` enabled during Phases 1–4 UI tests so your 5,000 programme credits remain intact for acoustic tuning and the Phase 5 final demo[cite: 1, 4].

### Phase 1 Session Execution Plan — 2026-10-17
1. Create the secret-free environment template and compatible Python dependency manifest.
2. Implement immutable VAD/bias settings and the three-profile API-key firewall with test-safe DEMO protection.
3. Build and seed the validated 50-record synthetic SQLite store, then implement and exercise all four deterministic tools.
4. Add the zero-credit JSON/PCM replay streams with strict 1,024-byte frame validation and telemetry.
5. Author the English/Hindi/Hinglish policy corpus and verify its required regulatory/tool clauses.
6. Add focused Phase 1 tests, run the exact Ruff and pytest checks, and fix only Phase 1 failures.

### Phase 2 Session Execution Plan — 2026-10-17
1. Implement the Prisma raw WebSocket/mock adapter with exact VAD headers, dual-mode handover, bias updates, digit accumulation, and receive-loop barge-in telemetry.
2. Implement the FastAPI `/health` and `/ws/call` gateway with monotonic 1,024-byte pacing, validated control events, transcript callbacks, and clean task teardown.
3. Add focused pacer, cadence, VAD, lexicon, barge-in, and WebSocket integration tests without live network calls.
4. Run both Phase 1 and Phase 2 suites plus Ruff, then review the diff for Phase 2-only scope.

### Phase 3 Session Execution Plan — 2026-10-17
1. Implement the zero-SSML Timbre clause chunker, Turn-0 disclosure, mock/live streaming adapters, and barge-in cancellation checks.
2. Implement Evon 128K policy loading, dual thinking-mode routing, validated SQLite tool calls, language/format guards, and deterministic mock responses.
3. Wire transcript-to-Evon-to-Timbre callbacks into the existing media gateway without creating Phase 4 or Phase 5 modules.
4. Add closed-loop mock WebSocket tests, run all Phase 1–3 checks, and commit only the verified Phase 3 changes.

### Phase 3 Closeout — 2026-10-17
- Verified zero-credit mock STT → Evon/SQLite → Timbre closed-loop behavior, Turn-0 disclosure, protected financial formatting, and barge-in cancellation.

### Phase 4 Session Execution Plan — 2026-10-17
1. Implement the AudioWorklet 16-bit mono PCM capture, exact 1,024-byte framing, TTS playback queue, and single-quantum barge-in flush.
2. Implement the dependency-free TypeScript PSTN bandpass/noise mixer and browser WebSocket bridge bound to `/ws/call`.
3. Build the responsive Screen 1 telemetry workbench with the Phase 5 audit-tab shell, accessibility states, and synthetic quick tests.
4. Add static frontend/security and backend-contract tests, run all Phase 1–4 checks, and stage only Phase 4 deliverables.

### Phase 4 Closeout — 2026-10-17
- Verified the browser PCM framing, local gateway protocol, zero-secret frontend audit, Screen 1 design tokens, accessibility states, and Phase 5 placeholder shell.

### Phase 5 Session Execution Plan — 2026-10-17
1. Add deterministic mock/live Prisma batch job lifecycle methods with denoise enforcement, three-language metadata, and nominal polling telemetry.
2. Add the validated Evon `ComplianceAuditReport` and `enable_thinking=True` post-call audit with disclosure, synthetic-PII, entity, mutation, and handoff checks.
3. Expose batch audit REST endpoints and wire the Screen 2 supervisor workflow without implementing deferred features.
4. Add the six-check benchmark, run all Phase 1–5 checks plus Node/Ruff verification, and commit only Phase 5 changes.

### Phase 5 Verification Closeout — 2026-10-17
- Implemented the mock/live Prisma batch lifecycle with `with_denoise=True`, three-language fallback metadata, nominal 10-second polling telemetry, native-script/code-mixed synthetic transcript replay, and TLS 1.3 live request isolation.
- Implemented the validated Evon `ComplianceAuditReport` with deterministic Turn-0 disclosure and synthetic-PII gates, extracted financial entities, mutation evidence, handoff state, and `enable_thinking=True` request telemetry.
- Wired JSON, multipart, and raw-file batch REST flows, in-memory job status lookup, the six-check scorecard, and the Screen 2 supervisor audit workflow with retry/error states.
- Verified offline with `ruff check backend/ tests/`, `node --check frontend/audio_worklet.js`, and the complete Phase 1–5 pytest command: 34 tests passed, 0 live API credits spent.
- Phase 5 remains `[ACTIVE]` only for the separately scheduled 60-second live demo recording; automated implementation and verification gates are complete in mock mode.

> Phase 1 closeout note: all five foundation documents are now present for Phase 2 review. The PRD and rules confirm the 1,024-byte pacing, raw WebSocket, synthetic-data, and barge-in constraints; no cross-file conflict was found for this phase.
