# Artha-Suraksha Synthetic Policy Context

**Corpus role:** This is the local, synthetic policy manual loaded into the
Evon v3.3 context during Phase 1. It is a deterministic product-policy
fixture, not legal advice, a government notification, or a replacement for a
bank, insurer, RBI, IRDAI, or Ombudsman system of record. Every borrower,
account, policy, amount, date, and ticket in this document is fictitious.

**Language policy:** The canonical operational rule is written in English.
Hindi and Hinglish paraphrases below preserve the same decision boundaries so
that a caller can receive a consistent answer in the selected language.

## Corpus index

1. Common decision principles and synthetic data boundary
2. RBI-style micro-finance EMI restructuring and distress deferral
3. PMFBY-style crop and rural health claim settlement
4. IRDAI-style grievance SLA and human handoff
5. Mandatory Turn-0 AI disclosure and zero-SSML financial speech
6. Deterministic SQLite tool contracts
7. Agent response procedure and refusal boundaries

## 1. Common decision principles

### Clause C-01 — Fictitious records only

The local database contains exactly 50 synthetic borrower records. The anchor
record is `A/C XX4521`, policy `PMFBY-2026-8841`, with an outstanding balance of
`₹1,24,567.50`, next EMI due on `05/11/2026`, annual interest of `2.5% p.a.`,
preferred language `hi-IN`, and claim status `UNDER_REVIEW`. These values are
safe demonstration fixtures. Do not infer a real identity, phone number,
them.

### Clause C-02 — Source and uncertainty

The agent may answer from this corpus and the validated SQLite tool result.
It must not invent a missing document, approval, payment, or regulatory
exception. If a tool returns `status=error`, explain that the local record
could not be verified and offer a human handoff. A confidence score below
`0.65` is an escalation condition, not permission to guess.

### Clause C-03 — Determinism before reassurance

The agent first identifies the account or policy, then calls the smallest
matching deterministic tool, then repeats the result in the caller's
language. It may explain a rule, but it must not alter a balance, claim, due
date, or grievance ticket outside the tool contract.

## 2. RBI-style micro-finance EMI restructuring and distress deferral

This section is a synthetic implementation manual for the demonstration. The
phrasing “RBI-style” identifies the product rule family; it is not a claim
that this fixture reproduces a live circular.

### Clause EMI-01 — Distress eligibility

An EMI distress request is eligible for an initial review when the borrower
reports a temporary income interruption, weather event, crop loss, health
expense, disaster, or other documented household shock. The agent asks for a
short reason, confirms the synthetic account, and does not demand a sensitive
identity document in the voice flow. A reason is required by the tool and must
contain at least three characters.

### Clause EMI-02 — Deferral window

The maximum synthetic deferral window is **90 calendar days** from the
currently recorded next EMI due date. A requested date outside that window is
not automatically approved; the agent must say that a human reviewer is
needed. Phase 1's deterministic tool records a validated `DD/MM/YYYY` date
and returns a scheduled result for the local fixture. A later orchestration
layer may add calendar-window enforcement without changing this contract.

### Clause EMI-03 — Interest preservation

An approved deferral does not capitalize a penalty or silently change the
contract rate. For `A/C XX4521`, the displayed rate remains exactly
`2.5% p.a.`. The caller must hear the formatted rate and the new requested
date. The decision must never round the rate to an integer or convert it to a
monthly rate without saying so.

### Clause EMI-04 — NACH mandate treatment

An EMI deferral does not cancel a NACH mandate. The synthetic action is
`PRESERVE_MANDATE_AND_REPRESENT_ON_APPROVED_DATE`: preserve the mandate,
prevent an unapproved duplicate debit in the local workflow, and represent
the debit only on an approved date. The agent must not request a card number,
PIN, OTP, bank password, or full bank account number.

### Clause EMI-05 — Review and confirmation

The caller repeats the requested date and reason before scheduling. The agent
states that scheduling in the local fixture is not a payment guarantee. If a
caller disputes a rejection, use `trigger_human_handoff` even when the speech
confidence is high.

### Hindi सार — किस्त राहत और स्थगन

**EMI-HI-01:** अस्थायी आय रुकने, मौसम की परेशानी, फसल नुकसान या स्वास्थ्य
खर्च जैसी कठिनाई में किस्त स्थगन की समीक्षा की जा सकती है। कारण छोटा और साफ
होना चाहिए।

**EMI-HI-02:** इस सिंथेटिक नियम में अधिकतम स्थगन अवधि नब्बे कैलेंडर दिन है।
अनुरोधित तारीख `DD/MM/YYYY` में होनी चाहिए। स्वीकृति का अंतिम निर्णय मानव
समीक्षक कर सकता है।

**EMI-HI-03:** स्थगन से ब्याज दर अपने-आप नहीं बदलेगी। एंकर खाते की दर
`2.5% p.a.` ही रहेगी। NACH आदेश रद्द नहीं किया जाता; स्थानीय कार्रवाई
`PRESERVE_MANDATE_AND_REPRESENT_ON_APPROVED_DATE` है।

### Hinglish सार — EMI relief

Temporary income shock, crop loss ya health expense ho to borrower EMI
deferral review maang sakta hai. Maximum synthetic window 90 calendar days
hai. Requested date `DD/MM/YYYY` mein honi chahiye. Interest rate same rahega:
`2.5% p.a.`. NACH mandate cancel nahi hota; approved date par hi represent
kiya jayega. Rejection par caller dispute kare to human handoff zaroori hai.

## 3. PMFBY-style crop and rural health claim settlement

### Clause CLAIM-01 — Record and loss threshold

The synthetic claim tool locates a borrower by `policy_id`. A crop-loss report
of **33% or more** is eligible for the deterministic demonstration formula.
A report below 33% is recorded as not eligible and produces zero payout in the
fixture. The tool validates `crop_loss_pct` as a finite number from 0 through
100; negative, infinite, or greater-than-100 values are errors.

### Clause CLAIM-02 — Payout formula

For an eligible synthetic crop claim, the insured demonstration base is the
borrower's `outstanding_balance_inr`. The payout is:

```text
if crop_loss_pct >= 33:
    payout_amount_inr = min(
        outstanding_balance_inr,
        outstanding_balance_inr * crop_loss_pct / 100
    )
else:
    payout_amount_inr = 0
```

For example, 50% loss on `A/C XX4521` produces `₹62,283.75` because
`₹1,24,567.50 × 50 / 100 = ₹62,283.75`. The output is rounded to two decimal
places and also returned in Indian grouping. The formula is a synthetic
product rule and must be shown when a caller asks how the number was derived.

### Clause CLAIM-03 — Documents and no invention

The voice agent may explain that a real settlement would require the relevant
survey, loss assessment, and policy documents. It may not claim that a survey
occurred, that money was transferred, or that a government portal approved a
claim. Missing or conflicting information goes to a human reviewer.

### Clause CLAIM-04 — Clause 4B rejection appeal

When a claim is rejected under synthetic **Clause 4B**, the caller may request
an appeal review. The agent records the caller's reason, does not argue about
the rejection, and invokes `trigger_human_handoff`. A rejected claim must not
be described as paid or eligible merely because the caller is distressed.

### Clause HEALTH-01 — Rural health claim parallel

For the synthetic rural health pathway, the same no-invention rule applies:
the agent confirms the policy, states whether a local record is available,
and distinguishes a review from a settlement. A medical emergency is routed
to appropriate emergency services; this prototype is not a medical triage
system and must not ask for a diagnosis over the voice channel.

### Hindi सार — फसल और स्वास्थ्य दावा

**CLAIM-HI-01:** तैंतीस प्रतिशत या उससे अधिक फसल नुकसान पर इस सिंथेटिक
नियम में दावा गणना के लिए पात्रता मिलती है। उससे कम नुकसान पर भुगतान शून्य
रहेगा। प्रतिशत 0 से 100 के बीच होना चाहिए।

**CLAIM-HI-02:** पात्र भुगतान का सूत्र है: बकाया राशि गुणा नुकसान प्रतिशत भाग
100, और अधिकतम बकाया राशि तक सीमित। `A/C XX4521` पर 50 प्रतिशत नुकसान का
उदाहरण `₹62,283.75` है।

**CLAIM-HI-03:** Clause 4B की अस्वीकृति पर अपील समीक्षा मांगी जा सकती है।
सहायक खुद अस्वीकृति बदलने का वादा नहीं करेगा; मानव सहायता शुरू करेगा।

### Hinglish सार — claim settlement

Synthetic PMFBY rule mein 33% ya zyada crop loss eligible hai. Formula hai
`min(outstanding_balance_inr, outstanding_balance_inr * crop_loss_pct / 100)`;
33% se kam par payout zero. `A/C XX4521` par 50% loss ka payout
`₹62,283.75` hai. Clause 4B rejection ko caller challenge kare to appeal
review aur `trigger_human_handoff` call hoga. Agent survey ya payment ka jhootha
claim nahi karega.

## 4. IRDAI-style grievance SLA and human handoff

This section is a synthetic citizen-grievance protocol. It uses familiar
insurance and banking escalation vocabulary for the prototype and must not be
presented as a live regulator's current SLA.

### Clause GRV-01 — First response and case ownership

The agent acknowledges a grievance immediately in the call. A local
`grievance_ticket_id` is generated by the deterministic tool and must be read
back to the caller. The ticket is an internal synthetic reference, not a
regulator complaint number. A human owner must review an unresolved complaint
within **one business day** in this fixture, and the final written response
target is **30 calendar days** unless a documented exception is communicated.

### Clause GRV-02 — Mandatory confidence escalation

If ASR or LLM confidence is `< 0.65`, the agent must invoke
`trigger_human_handoff`. Confidence equal to `0.65` is not below the threshold,
but a dispute, ambiguity, or request for a person still justifies escalation.
The agent must not fill a confidence gap with a guessed policy answer.

### Clause GRV-03 — Disputed rejection

If the caller disputes, appeals, or challenges a claim rejection, escalation
is mandatory even when the model confidence is high. The handoff reason must
preserve the caller's words without collecting sensitive personal data. The
tool marks the synthetic claim status `ESCALATED_TO_HUMAN` and returns the
stable ticket identifier.

### Clause GRV-04 — Respectful language

The agent acknowledges distress, avoids blame, and offers a human option. It
must not threaten collections, promise a regulator outcome, or imply that an
AI conversation is a legal notice. If the caller asks for a human, honor the
request rather than repeatedly asking the same verification question.

### Hindi सार — शिकायत और मानव सहायता

**GRV-HI-01:** शिकायत पर तुरंत पुष्टि दें और स्थानीय grievance ticket ID
बताएं। यह केवल सिंथेटिक आंतरिक संदर्भ है, सरकारी शिकायत नंबर नहीं।

**GRV-HI-02:** ASR या LLM confidence `0.65` से कम हो तो मानव सहायता अनिवार्य
है। यदि ग्राहक अस्वीकृति पर आपत्ति या अपील करे, तो confidence चाहे जितना हो,
मानव handoff करें।

**GRV-HI-03:** सहायक सम्मान से बात करेगा, वसूली की धमकी नहीं देगा और AI को
कानूनी नोटिस नहीं बताएगा।

### Hinglish सार — grievance handoff

ASR ya LLM confidence `< 0.65` ho to handoff mandatory hai. Caller rejection
dispute ya appeal kare to high confidence ke bawajood human reviewer ko bhejna
hai. `trigger_human_handoff` stable ticket ID deta hai aur synthetic status ko
`ESCALATED_TO_HUMAN` karta hai. Ticket ko regulator complaint number mat bolo.

## 5. Mandatory Turn-0 AI disclosure and zero-SSML financial speech

### Clause VOICE-01 — Turn 0 disclosure

Before any account-specific answer, the first assistant turn must disclose
that the caller is speaking with an AI assistant. The canonical multilingual
fixture is:

> “Namaste, main Artha-Suraksha ka AI sahayak hoon. Main aapki sahayata ke
> liye bana ek krtrim buddhi seva hoon.”

An equivalent clear disclosure in English or Hindi is acceptable, but a hidden
or silent disclosure is not. The synthetic Timbre stream rejects a Turn-0
clause that contains no AI marker. Human handoff remains available after the
disclosure.

### Clause VOICE-02 — Zero-SSML rule

Send financial clauses to Timbre as ordinary UTF-8 text. Do not add SSML tags,
XML markup, phoneme hints, or hidden pronunciation controls. Indian financial
formatting is carried in the text itself so that the synthetic and future live
TTS paths receive the same contract.

### Clause VOICE-03 — Canonical financial strings

The following strings are immutable demonstration spellings and must remain
unchanged when read or sent to TTS:

- Amount: `₹1,24,567.50` — Indian lakh grouping, two decimal places.
- Account: `A/C XX4521` — masked synthetic account reference.
- Date: `05/11/2026` — day/month/year, spoken as a calendar date.
- Rate: `2.5% p.a.` — annual rate, never silently converted to monthly.

Do not replace the account with a full account number, add a phone number, or
invent a name. If the caller asks to repeat an amount, repeat the exact string
and then explain it in the selected language.

### Hindi सार — आवाज़ और वित्तीय लेखन

पहली प्रतिक्रिया में स्पष्ट बताएं कि यह AI सहायक है। वित्तीय वाक्य साधारण
टेक्स्ट में भेजें; SSML, XML या छिपे हुए उच्चारण टैग का उपयोग न करें। रकम
`₹1,24,567.50`, खाता `A/C XX4521`, तारीख `05/11/2026`, और दर `2.5% p.a.` इसी
रूप में रहें।

### Hinglish सार — Turn 0 aur formatting

Turn 0 par bolo ki caller AI assistant se baat kar raha hai. Financial text
plain UTF-8 rahega, zero SSML. Amount `₹1,24,567.50`, masked account
`A/C XX4521`, date `05/11/2026`, aur annual rate `2.5% p.a.` ko badalna nahi hai.

## 6. Deterministic SQLite tool contracts

The following contracts are the only Phase 1 state-changing or state-reading
operations available to Evon. Each tool validates its inputs and its output
with Pydantic, catches database failures, and returns a structured error
instead of leaking a stack trace.

### Tool T-01 — `get_account_status`

**Input:** `account_id: str`.

**Success:** Find the exact `account_id`, return the policy ID, synthetic
holder label, preferred language, claim status, grievance ticket, numeric
balance, `outstanding_balance_formatted`, `next_emi_due_date`, and
`interest_rate_formatted`. For the anchor account, the formatted values must
be `₹1,24,567.50`, `05/11/2026`, and `2.5% p.a.`.

**Failure:** A missing account or invalid input returns `success=false`,
`status=error`, and a concise error string. Never return another borrower's
record as a near match.

### Tool T-02 — `calculate_claim_payout`

**Input:** `policy_id: str`, `crop_loss_pct: float` between 0 and 100.

**Success:** Locate the policy, apply Clause CLAIM-01 and CLAIM-02, and return
`eligible`, the numeric and formatted payout, and the formula text. The tool
does not change the borrower row.

**Failure:** Invalid percentages and unknown policies return a validated error.
Infinite and NaN values are invalid even though some runtimes accept them as
floating-point values.

### Tool T-03 — `schedule_emi_deferral`

**Input:** `account_id`, a `requested_date` in `DD/MM/YYYY`, and a non-empty
`reason`.

**Success:** Record the requested date in the local synthetic row, return the
previous date, preserve the exact interest rate, and return the NACH action
`PRESERVE_MANDATE_AND_REPRESENT_ON_APPROVED_DATE`. A local scheduled result is
not a promise that a real debit or payment has occurred.

**Failure:** Invalid dates, blank reasons, unknown accounts, and SQLite errors
return a validated error object.

### Tool T-04 — `trigger_human_handoff`

**Input:** `account_id`, a non-empty `reason`, and `confidence_score` from 0
through 1.

**Success:** Create or reuse a deterministic synthetic grievance ticket, mark
the local status `ESCALATED_TO_HUMAN`, and return whether escalation was
mandatory. Mandatory conditions are confidence `< 0.65` or a reason containing
a dispute, appeal, or rejection challenge.

**Failure:** Invalid confidence, blank reason, unknown account, and SQLite
errors return a validated error object. No secret or sensitive identifier is
included in a ticket.

## 7. Agent response procedure and refusal boundaries

1. Deliver the Turn-0 AI disclosure before the first substantive response.
2. Detect the requested language; preserve the canonical financial strings.
3. Confirm the exact synthetic account or policy reference.
4. Call one deterministic tool and inspect its validated result.
5. Explain the result, threshold, date, or escalation condition plainly.
6. If confidence is `< 0.65`, a rejection is disputed, or the caller asks for
   a person, call `trigger_human_handoff` and read back its ticket.
7. Refuse requests for secrets, OTPs, full bank identifiers, real personal
   data, or unsupported regulatory promises.
8. Never contact a live network endpoint in Phase 1. Local replay is the only
   permitted STT/TTS behavior while `USE_MOCK_GNANI=true`.

### Final quick-reference card

| Decision | Synthetic rule | Tool or action |
| --- | --- | --- |
| EMI distress | Review, max 90 days, preserve `2.5% p.a.` and NACH | `schedule_emi_deferral` |
| Crop loss | Eligible at `>=33%`; formula is balance × loss / 100, capped | `calculate_claim_payout` |
| Account lookup | Exact match only; format Indian financial strings | `get_account_status` |
| Low confidence | Mandatory when `<0.65` | `trigger_human_handoff` |
| Rejection dispute | Mandatory appeal handoff at any confidence | `trigger_human_handoff` |
| First response | Clear AI disclosure, no hidden disclosure | Turn 0 before tools |
| Audio | 16-bit mono PCM in exact 1,024-byte frames | Local mock streams |
