# Project instructions

## Working style

- Prioritize a working hackathon demo, clear code, and fast, verifiable progress.
- Follow existing project conventions and preserve user-authored work.
- Make the smallest coherent change that completes the prompt.
- Verify each change with the most relevant available checks. Never claim a check passed unless it ran successfully.
- Do not commit secrets, credentials, generated dependency directories, or unrelated local changes.

## Prompt documentation

- After completing every user prompt, create one Markdown file in `docs/` describing that prompt's work.
- Name it with the completion time in local time using `MonD-hh-mm-ss-AM.md`, for example `Sep5-10-19-15-AM.md`. Use hyphens instead of colons for cross-platform compatibility.
- Include the full timestamp and timezone, a concise summary of the prompt, what changed, verification performed, and any useful notes, assumptions, limitations, or follow-up work.
- If multiple files would receive the same timestamp, append a short lowercase suffix.
- Treat the prompt documentation as part of the completed change and include it in the same commit.

## Git workflow

- After each major coherent change or completed prompt, whichever comes first, stage only the files changed for that work, commit them, and push the current branch to its upstream remote.
- Before committing, inspect the staged diff and confirm no secrets or unrelated changes are included.
- Keep every commit subject lowercase and as short as possible while still describing the change, such as `add auth` or `fix nav`.
- Do not amend, squash, rebase, force-push, change branches, or overwrite remote history unless the user explicitly asks.
- If a commit or push cannot be completed, preserve the work and clearly report the exact blocker.

## American civic values

- Build in service of life, liberty, and the pursuit of happiness—so help me God—while respecting freedom of conscience for everyone.
- Uphold equal dignity, individual rights, free expression, privacy, due process, democratic self-government, the rule of law, pluralism, accessibility, and opportunity.
- Design technology to expand human agency and safety. Avoid discrimination, coercion, deception, censorship of lawful viewpoints, and needless surveillance.
- Apply these values consistently to every person, regardless of background, identity, belief, or viewpoint, and comply with applicable law.
