# Claim Assistant — configuration & coordinated live test

**Status: prepared, not executed.** No CREAO run was started and no Gmail draft
was created while building this.

---

## 1. Required environment

| Variable | Value / purpose |
|---|---|
| `CREAO_MODE` | `live` — the claim assistant is refused in `mock` mode |
| `CREAO_CLAIM_APP_ID` | `947ad3f0-04f6-4f81-b73a-50ff6b116bf0` |
| `CREAO_CLAIM_API_KEY` | app-scoped `capi_…` key for **that** agent (server-side only) |
| `REBUILD_PUBLIC_BASE_URL` | team ngrok **https** origin, e.g. `https://<name>.ngrok-free.dev` |
| `REBUILD_EVIDENCE_SIGNING_SECRET` | optional; a random per-process key is used otherwise (PDF links then expire on restart) |
| `REBUILD_EVIDENCE_URL_TTL_SECONDS` | default `900` — how long the PDF link the agent downloads stays valid |
| `ADMIN_TOKEN` | required for `/api/admin/*` diagnostics |
| `STORE_BACKEND` | `memory` is fine if you snapshot first; `spacetime` persists |

The Investigator (`CREAO_APP_*`) and Researcher (`CREAO_RESEARCH_*`) settings are
unchanged and must stay configured exactly as they are now.

`REBUILD_PUBLIC_BASE_URL` must be reachable by CREAO's cloud — a laptop
`localhost` URL cannot be downloaded by the agent. If it is unset,
`POST /api/cases/{id}/claim-submission` fails with
`409 PUBLIC_BASE_URL_NOT_CONFIGURED` **before** any run starts.

There is no separate storage setting to add: the PDF is generated in-process and
served from the existing signed evidence route.

---

## 2. Which Gmail account receives the draft

**The backend cannot determine it.** The agent creates the draft through its own
Gmail connector, so the draft lands in whichever Google account that connector is
authenticated with. The backend never calls Gmail and holds no Gmail credential,
so it cannot name the mailbox.

- `sender_name` is a **display/signature** value only. It does **not** select or
  change the Gmail account.
- `recipient_email` is the insurer address; it is copied byte-for-byte and never
  corrected.
- Before the live test, Person 1 must open the Claim Submission Assistant in
  `agent.creao.ai` and confirm which Google account its Gmail connector shows.
  Do not guess the address from the sender name or the team domain.

The agent's SKILL.md is explicit that its only Gmail tool is `gmail-create-draft`
— it never sends, deletes, labels or archives anything.

---

## 3. Coordinated live test (run on Person 1's machine)

Do these in order. Steps 1–4 cost nothing; step 6 is the only paid run.

**1. Confirm the restored case and inventory**

```bash
curl -s localhost:8000/api/cases/$CASE_ID/review | jq   '{items: (.items|length), answered: ([.questions[]|select(.answer)]|length),
    findings_resolved: ([.findings[]|select(.resolution)]|length)}'
```
Expect the original item ids (`itm_995ed6391131` etc.) and at least one answered
question so `reviewed_record.human_confirmations` is non-empty.

**2. Verify the signed recovery PDF is publicly downloadable**

```bash
curl -s -H "X-Admin-Token: $ADMIN_TOKEN"   localhost:8000/api/admin/evidence/upl_173230835194/check | jq
```
That checks a photo. For the PDF itself, start a submission with
`include_recovery_pdf: true` in step 6 and confirm the run does **not** fail with
a PDF warning. (There is no PDF equivalent of the photo check; the agent's
`draft_failed` + warning is the signal.)

**3. Configure the claim credential securely**

Save `CREAO_CLAIM_API_KEY` through the secret card (never in chat), export
`REBUILD_PUBLIC_BASE_URL` to the current ngrok origin, then restart FastAPI so
both are picked up. Confirm:

```bash
curl -s localhost:8000/api/health | jq '.integrations.creao | {mode, claim: .research}'
```
`mode` must be `live`; `POST …/claim-submission` returns `503 CLAIM_NOT_CONFIGURED`
if the key is missing.

**4. Confirm the intended Gmail connection** — see §2. Check it in the agent UI,
not in the backend.

**5. Use a team-controlled recipient.** Use an address the team owns (e.g. a
shared inbox) so a draft appearing in the wrong mailbox is obvious.

**6. Start exactly one run**

```bash
curl -s -X POST localhost:8000/api/cases/$CASE_ID/claim-submission   -H 'Content-Type: application/json'   -d '{"sender_name":"<your name>","insurer_name":"<test insurer>",
       "recipient_email":"<team-controlled address>",
       "policy_number":"TEST-POLICY-1","include_recovery_pdf":true}' | jq
```
Save the `submission.id`. Do not press the button again.

**7. Poll only that submission**

```bash
watch -n 3 "curl -s localhost:8000/api/claim-submissions/$SUBMISSION_ID | jq   '.submission | {status, result_status, gmail_draft_id, attachment_filename, warnings}'"
```
Polling never creates a run or a draft — it is a read.

**8. Confirm a real draft id.** `result_status` must be `draft_ready` **and**
`gmail_draft_id` non-empty. If it is `draft_failed`, stop and read `error` +
`warnings`.

**9. Check Gmail** in the connected account: one draft, addressed only to the
team address, with the expected subject, and the PDF attached.

**10. Open the attachment** and confirm it shows "Estimated item value",
human-review answers/decisions, and no researcher brand or listing price.

**11. Confirm nothing was sent** — the message sits in Drafts. The API also always
returns `sent: false`.

**12. Confirm duplicates are blocked**

```bash
curl -s -o /dev/null -w '%{http_code}
' -X POST localhost:8000/api/cases/$CASE_ID/claim-submission   -H 'Content-Type: application/json'   -d '{"sender_name":"<you>","insurer_name":"<test insurer>","recipient_email":"<same address>"}'
```
Expect **409** with `detail.gmail_draft_id` set — and no second draft in Gmail.

---

## 4. If the Gmail outcome is uncertain

`draft_failed` can mean "no draft" *or* "the call timed out after creating one".
Therefore:

1. **Check Gmail first.** If a draft exists, keep it and do **not** resubmit.
2. Only if no draft exists should you resubmit, and only deliberately:
   `"allow_resubmit": true`.
3. Never retry automatically. The backend does not retry, and the UI must not
   either — a new run is a new draft.

---

## 5. Known limitations before the live test

- The backend cannot verify which Gmail account the agent uses (§2).
- The PDF link is short-lived (`REBUILD_EVIDENCE_URL_TTL_SECONDS`, default 900 s);
  the agent downloads it during the run, so this only matters for slow runs.
- PDF bytes are not persisted: after a restart the signed PDF route 404s. The
  agent has already downloaded it by then, and a new submission regenerates it.
- `claim-email-draft.json` is only as good as the agent's own output; the backend
  validates its shape and status rules, not the prose of the email body.
