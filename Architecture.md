# System Architecture: Artha-Suraksha
> **CONFLICT HIERARCHY:** Product intent (PRD) -> Architecture -> Rules -> Design -> Active task (Task.md). If files disagree, the higher one wins.

## 1. Technology Stack & Justification
| Layer | Technology Choice | Justification (Grounded in PDFs) |
| :--- | :--- | :--- |
| **Frontend Client & DSP** | React / Next.js, TypeScript, Tailwind CSS, Web Audio API (`AudioWorklet`) | Provides non-blocking 16-bit mono PCM capture, real-time 8 kHz bandpass/bazaar noise mixing, `<30 ms` audio buffer flushing, and live P95 telemetry visualization[cite: 9, 13]. |
| **Backend Media Gateway** | Python 3.11+, FastAPI, `websockets`, `aiohttp`, `asyncio` | Bypasses the `gnani-vachana` SDK limitation (which lacks VAD header support) and browser WSS header restrictions, maintaining persistent server-to-server TLS 1.3 pools and monotonic 1,024-byte frame pacing. |
| **Speech-to-Text (ASR)** | **Gnani Prisma v2.5** (`wss://api.vachana.ai/stt/v3/stream` & `/stt/v3/batch/jobs`) | Trained on 14M+ hours of telephonic audio (`~200 ms` P95), supporting custom VAD gates, mid-call `x-bias-list` word boosting, and batch denoising (`with_denoise`)[cite: 1, 7, 8, 9]. |
| **Sovereign Reasoning Engine** | **Gnani Evon v3.3** (`gnani/gnani-evon-v3.3-30B-A3B` via vLLM/SGLang or GGUF) | 30B total / ~3.5B active MoE (`302 ms` TTFT) with 128K context, 38.6% lower Indic token fertility, 15.3% low English drift during tool calls, and `enable_thinking` toggle[cite: 5, 10, 11, 12]. |
| **Text-to-Speech (TTS)** | **Gnani Timbre v2.5 / v3.0** (`wss://api.vachana.ai/api/v1/tts`) | Sub-100 ms (v2.5) / sub-150 ms (v3.0) streaming synthesis with native zero-SSML Indian lakh/crore numbering (`₹1,24,567.50`) and financial shorthand (`A/C XX4521`)[cite: 6, 13, 14]. |
| **Persistence Layer** | SQLite (`aiosqlite`) + Markdown Policy Corpus (`policy_context.md`) | Lightweight, zero-latency local storage for the 50 synthetic borrower records and full regulatory text loaded directly into Evon's 128K context window[cite: 3, 5, 10]. |

## 2. System Diagram (Mermaid)
```mermaid
flowchart LR
    subgraph Client["Frontend Layer (React + Web Audio API)"]
        Mic["Caller Mic (16-bit PCM)"] --> Mixer["8kHz PSTN & Bazaar Noise Mixer\n(frontend/audio_worklet.js & noise_mixer.ts)"]
        Mixer -- "WSS Binary Stream" --> Gateway
        Flush["Barge-In Flush Controller"] -- "Clears Playback (<30ms)" --> Speaker["Audio Output + AI Disclosure Banner"]
        Telemetry["Judge Telemetry UI\n(frontend/telemetry_dashboard.tsx)"]
    end

    subgraph Backend["Security, Pacing & Orchestration Layer (FastAPI / Python 3.11+)"]
        Gateway["TLS 1.3 Media Gateway\n(backend/media_gateway.py)"] --> Pacer["1,024-Byte Ring-Buffer Pacer\n(32ms @ 16kHz / 64ms @ 8kHz)"]
        Mock["0-Credit Mock Interceptor\n(backend/mock_replay.py)"] -.-> Pacer
        Pacer --> PrismaCtrl["Prisma v2.5 Controller\n(backend/prisma_controller.py)"]
        PrismaCtrl -- "{'type': 'processing'} (<30ms)" --> Flush
        PrismaCtrl -- "Final Transcript JSON" --> EvonOrch["Evon v3.3 Orchestrator\n(backend/evon_orchestrator.py)"]
        EvonOrch <--> DB[("Synthetic SQLite DB\n(backend/database.py)\n50 Records: A/C XX4521")]
        EvonOrch <--> Policy["128K Policy Corpus\n(backend/policy_context.md)"]
        EvonOrch -- "Clause-Chunked Deltas" --> TimbreStr["Timbre Streamer\n(backend/timbre_streamer.py)"]
        TimbreStr -- "Synthesized PCM + Metrics" --> Gateway
        Gateway -- "WSS Audio + Telemetry JSON" --> Speaker
        Gateway -- "P95 Waterfall & VAD State" --> Telemetry
    end

    subgraph Gnani["Gnani AI Sovereign Cloud / Local Weights"]
        PrismaCtrl <== "TLS 1.3 WSS (Fast 350ms / Digit 800ms VAD)\n+ update_settings (x-bias-list)" ==> PrismaAPI["Prisma v2.5 WSS & Batch STT\nwss://api.vachana.ai/stt/v3/stream\n/stt/v3/batch/jobs"]
        EvonOrch <== "OpenAI-Compatible Chat & Tools\n(enable_thinking=False/True)" ==> EvonModel["Evon v3.3 (30B-A3B MoE)\ngnani/gnani-evon-v3.3-30B-A3B"]
        TimbreStr <== "TLS 1.3 WSS Clause Stream" ==> TimbreAPI["Timbre v2.5 / v3.0 TTS\nwss://api.vachana.ai/api/v1/tts"]
    end