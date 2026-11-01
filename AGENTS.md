# Repository instructions

Before making changes, read [README.md](README.md) and [docs/development.md](docs/development.md). The development guide is the detailed record of repository context, design decisions, workflow requirements, and task status. Keep it current as work proceeds; update the README summary when the status or user-facing scope changes. Put durable design decisions and operational details in tracked files under `docs/`, then link to them from the README or this file.

## Collaboration

- The requested lead role is GPT-6.1 Sol at medium reasoning, acting as reviewer, orchestrator, and owner of minor fixes. The requested implementation role is GPT-6.1 Luna at xhigh reasoning. The available dispatcher model ID is `gpt-6-luna`; do not claim a requested model alias is active unless the runtime confirms it.
- Preserve user changes already in the working tree. At initialization, `.gitignore` was modified and `Vagrantfile` was untracked; neither is part of this documentation task.
- Work in small, reviewable checkpoints. The user prefers frequent commits and pushes; use the `/c` workflow for commits. Codex attribution is permitted by the user. Do not stage or commit work unless the current task authorizes it.

## Implementation and operations

- The user resumed autonomous implementation after the tmux pause. Complete the keyboard change, guarded scripted build, signing, and verified F-Droid publication. Use Android application ID `com.hulloanson`; retain the existing Java namespace where practical.
- For future setup, download, build, signing, index-generation, and publishing operations, implement usable executable scripts. Do not leave broken or placeholder scripts.
- Those scripts must check cgroup and host available memory, filesystem free bytes, and free inodes before and during resource-intensive phases. Fail closed when limits cannot be established, use documented safety reserves and bounded workers/JVM settings, and stop spawned child processes when a reserve is breached. Do not start resource-intensive work until the guard design and reserve values are documented.
- Keep the app signing key and F-Droid repository signing key outside both tracked repositories, back them up, and reuse them for updates. Never regenerate keys for an update. Never put tokens, passwords, private key material, or credential-bearing URLs in tracked files, scripts, command logs, or documentation.
- Keep `repo_publish/` out of the main repository's tracked content. The future publishing setup must confirm a reachable HTTPS repository URL and repository signing fingerprint; cloning the repository alone does not establish hosting.
