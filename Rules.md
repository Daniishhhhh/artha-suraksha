=== FILE: Rules.md ===
```markdown
# Engineering, Security & Compliance Rules: Artha-Suraksha
> **CONFLICT HIERARCHY:** Product intent (PRD) -> Architecture -> Rules -> Design -> Active task (Task.md). If files disagree, the higher one wins.

## 1. Core Workflow Non-Negotiables (Testable)
* **R-01 (Mandatory Context Check):** Every engineer or AI agent MUST read all 5 root files (`PRD.md`, `Architecture.md`, `Rules.md`, `Design.md`, `Task.md`) before writing or modifying any code.
* **R-02 (No Unrelated Refactors):** Touch only the files listed in the active phase of `Task.md`. Do not rename files, alter folder paths, or refactor working modules outside the current task scope.
* **R-03 (Reuse Existing Components):** Import settings from `backend/config.py`, database helpers from `backend/database.py`, and shared types from `frontend/telemetry_dashboard.tsx` rather than duplicating logic.
* **R-04 (No Hard-Coded Secrets):** Never hard-code `X-API-Key-ID` values or URLs in source files[cite: 7]. Load all secrets via `backend/config.py` from `.env` and keep `.env.example` updated with variable names only.
* **R-05 (Input & AI Output Validation):** Validate all client WebSocket messages, SQL tool arguments, and Evon v3.3 JSON tool calls using Pydantic schemas before execution. If Evon outputs invalid tool JSON or drifts into unrequested English, catch and retry/fallback gracefully[cite: 10, 12].
* **R-06 (Explicit Error & Timeout Handling):** Every WebSocket stream (`prisma_controller.py`, `timbre_streamer.py`), SQLite query, and Batch STT poll must have explicit `try/except` blocks, connection timeout handlers, and clean socket teardown (`close_connection`).
* **R-07 (Test & Lint Gate):** Run `pytest` and linter checks (`ruff` / `tsc --noEmit`) before declaring any phase or task complete.
* **R-08 (Atomic Commits):** Commit in small, verifiable steps with clear prefix messages (`feat(phase-1): ...`, `fix(vad): ...`).
* **R-09 (Conflict Stop Rule):** If instructions in a prompt or code comment conflict with `PRD.md` or `Architecture.md`, STOP immediately and report the conflict rather than guessing.

## 2. Project-Specific Acoustic & Protocol Rules (Derived from PDFs)
* **R-10 (Strict 1,024-Byte PCM Contract):** `backend/media_gateway.py` MUST slice outbound STT audio into exact **1,024-byte** binary frames (512 samples of 16-bit mono linear PCM) dispatched every **32 ms** (16 kHz) or **64 ms** (8 kHz)[cite: 9]. Never burst buffered frames to Prisma v2.5[cite: 9].
* **R-11 (Raw WebSocket for Streaming STT):** Do NOT use the `gnani-vachana` Python SDK for real-time streaming STT because it strips custom VAD headers (`x-min-speech-ms`, `x-min-silence-ms`, `x-min-segment-ms`)[cite: 9]. Use raw `websockets` with TLS 1.3 connection pooling[cite: 9].
* **R-12 (Sub-30ms Barge-In Flush):** The instant `prisma_controller.py` receives `{"type": "processing"}` from Prisma v2.5, it MUST immediately emit `{"event": "clear_audio"}` to the frontend and abort active TTS synthesis without waiting for the finalized transcript[cite: 9].
* **R-13 (Zero-SSML Financial TTS):** Never wrap Indian currency (`₹1,24,567.50`), masked accounts (`A/C XX4521`), dates (`05/11/2026`), or rates (`2.5% p.a.`) in SSML tags when calling Timbre v2.5/v3.0[cite: 6]. Stream text clauses at punctuation boundaries (`।`, `.`, `,`, `?`)[cite: 14].

## 3. Contest Compliance, Privacy & Credit Firewall Rules
* **R-14 (100% Synthetic Data & Zero Real PII):** Never ingest, store, log, or output real phone numbers, real bank account numbers, `[Aadhaar Redacted]`, PAN, or actual customer call recordings[cite: 3]. Use only clearly labeled synthetic records (such as `A/C XX4521`)[cite: 3, 6].
* **R-15 (Mandatory Turn-0 AI Disclosure):** Every voice session MUST begin with an explicit spoken and visual disclosure that the caller is interacting with an AI assistant[cite: 5, 10].
* **R-16 (5,000-Credit & 3-Key Firewall):** Default `USE_MOCK_GNANI=true` during all frontend layout and state debugging so `0` programme credits are spent[cite: 1, 4]. Never use `GNANI_API_KEY_DEMO` (Key #3) outside the final demo recording and judge evaluation[cite: 1].
* **R-17 (Authentic Public Engagement):** Never use bots, paid promotion, or engagement swaps on the nominated LinkedIn or X posts (`#GnaniAI`, `#GreatIndianAIInternshipChallenge`)[cite: 1, 3].