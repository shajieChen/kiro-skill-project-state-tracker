# prompts/

Two subfolders:

- `prompts/landing/` — prompts that perform implementation work.
- `prompts/test/` — prompts that verify, regress, or accept implementation work.

These are kept separate because they have **different ready gates**:

- A LandingPrompt can only be `ready` when its TestPrompt counterpart is also `ready` (plus dependency Plan approved, no high blockers, etc.).
- A TestPrompt can be `ready` on its own once it's complete; it does not require the LandingPrompt to be approved first.

See the README in each subfolder for specifics.
