# rebuild frontend

A React, TypeScript, and Vite recovery workspace with photo inventory cards.

## Run locally

From the repository root, using a current Node.js LTS release:

```sh
git switch frontend-redesign
cd frontend
npm ci
npm run dev
```

Open the local URL printed by Vite (normally http://localhost:5173).

## Checks

```sh
npm run build
npm run lint
```

## Current workflow

- Create a room draft in Overview and select JPG, PNG, or PDF evidence.
- Create an inventory item by naming it and explicitly choosing its photo.
- Expand a photo card, then choose Review item for its details, evidence, and review window.
- Explore the separate sample inventory to try field confirmation, questions, findings, and undo.

Files and edits remain in memory for the current page session and clear on refresh. Automatic image analysis, server uploads, and persistent saving are not connected in this frontend branch. Sample source descriptions are included; original sample photos are not.

Disclosure, Dialog, and Transition Panel are adapted from [Motion Primitives](https://motion-primitives.com) with reduced-motion support. Attribution and the upstream MIT license are in `src/components/core/MOTION-PRIMITIVES-LICENSE`.
