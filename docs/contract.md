# Rebuild — Agent Contract (Rebuild Evidence Investigator)

Owner: Person 2 (agent). Agent v1.2.2 on CREAO.
Agent ID: `9819d9a5-89e0-4d62-a284-91bac67f8cd1`
Example output: `demo/sample-result.json` · Correction test: `demo/correction-test-result.json`

## Input (CREAO form fields)

| Field | Type | Notes |
|---|---|---|
| `case_description` | text | required |
| `room_name` | text | required |
| `room_photos` | files | required |
| `receipt_files` | files | |
| `evidence_manifest` | JSON text | `[{evidence_id, filename, description}]`. Backend assigns `ev_###` IDs **before** calling the agent |
| `confirmed_items` | JSON text | `[{target_type: "item"\|"finding"\|"question", target_id, field, value, confirmed_by}]` |
| `previous_result` | JSON **file upload** | last result; omit on first run |

## Output (top-level, all always present)

`case_summary`, `evidence`, `items`, `findings`, `questions`, `limitations`, `receipt_extracts`

### evidence[]
`evidence_id`, `filename`, `evidence_type` (photo|receipt|other), `readable`, `description`

### items[]
`item_id` (`itm_###`), `name`, `room`, `location_in_image`, `visible_observations`, `supporting_evidence_ids[]`, `brand`, `model`, `purchase_date`, `purchase_price` (string), `currency`, `proposed_receipt_evidence_id`, `review_status` (needs_review|confirmed), `user_confirmed_fields[]`

### findings[]
`finding_id` (`fnd_###`), `type` (possible_duplicate|missing_information|conflicting_information|receipt_match), `item_ids[]`, `evidence_ids[]`, `explanation`, `status` (needs_review|resolved), `resolution_reason` (null unless resolved)

### questions[]
`question_id` (`q_###`), `item_id`, `question`, `reason`, `status` (open|resolved), `resolution_reason`

### receipt_extracts[]
`evidence_id`, `source_filename`, `vendor_as_printed`, `receipt_number`, `is_template`, `date_as_printed`, `normalized_date`, `date_interpretation`, `currency`, `placeholder_text_detected[]`, `line_items[{description_as_printed, quantity, unit_price, line_amount}]`, `subtotal_as_printed`, `tax_as_printed`, `discount_as_printed`, `total_as_printed`, `arithmetic_check`

## Rules
1. Backend assigns stable evidence IDs before calling the agent; the agent never changes them.
2. Every item and finding cites supporting evidence IDs.
3. Missing values are `null`, never guessed. Money values are strings exactly as printed.
4. Agent proposals and human corrections are stored separately; `user_confirmed_fields` marks which fields came from a human.
5. Follow-up runs keep every existing item/finding/question ID with its original meaning. IDs are never reassigned or reused; closed records stay with `status: "resolved"` and a `resolution_reason`.
6. Confirmed facts are copied exactly and never contradicted; disagreements go in `limitations`.
7. Receipt prices are never assigned to an item without human confirmation. Matches need specific details (brand, model, item #, color, material, size), not generic words like "sofa".
8. Unrelated receipts are not reported as conflicting.
9. `date_as_printed` keeps the original text; ambiguous dates get `normalized_date: null`.
10. The same object in multiple evidence files (including crops) is one item plus a `possible_duplicate` finding.
