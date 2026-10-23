# Design guidelines

All tokens (color, type, space, radius, motion) are defined once in
`web/app/globals.css`; component styles in `web/components/copilot.module.css` use only
those tokens.

## Direction

An analyst's ledger: warm paper background, ink text, IBM Plex Sans and Plex Mono, and a
single ledger-green accent. The UI exists to make evidence inspectable, so visual weight
follows trust:

- **Accent means verified.** The green accent marks only verified evidence, the primary
  action and focus. Do not use it for decoration or status.
- **Numbers use tabular figures** (`.num`) so columns of figures align.
- **Evidence is never hidden behind the answer.** The evidence panel shows the exact SQL,
  the rows returned and the passages retrieved for the selected answer.
- **Unverified citations are marked.** A cited id that the agent did not retrieve keeps
  an "unverified" label instead of a link.

## Interface conventions

- Light and dark themes come from `prefers-color-scheme`; every token has a dark value.
- Motion collapses to zero under `prefers-reduced-motion`.
- Coarse pointers get 44px or larger controls.
- Everything works from the keyboard: run cards are buttons with `aria-pressed`, the
  evidence tabs follow the ARIA tabs pattern with arrow keys.
- Enter submits a question except during IME composition (Vietnamese, CJK input).
- Questions are shareable as `/?q=<question>` links that run on load.
- Model output is rendered as markdown without raw HTML.
