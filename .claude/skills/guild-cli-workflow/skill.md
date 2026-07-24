---
name: guild-cli-workflow
description: Agent development using the Guild CLI. Activated when user mentions guild agent commands, saving/publishing agents, clone/pull workflow, or agent testing. Covers CLI commands and common workflows.
---

# Guild CLI Agent Development Workflow

For local agent development using the Guild CLI. This workflow manages agent code via the Guild git server.

## MCP vs CLI

If Guild MCP tools are available (check for tools prefixed with `guild_`), use them for **read operations**: searching agents, listing workspaces, reading contexts, checking sessions, viewing credentials. MCP tools are faster and don't require shell execution.

Use the **CLI** (via Bash) for **local development operations**: `guild agent init`, `guild agent save`, `guild agent test`, `guild agent pull`, `guild agent clone`. These involve the local filesystem and git, which MCP can't do.

## CRITICAL: Always Use the Guild CLI

**NEVER manually create agent files or use raw git commands.**

```bash
# Create a new agent
guild agent init --name my-agent --template LLM --category development

# Clone an existing agent (full name or short name)
guild agent clone guildai~dev-assistant
guild agent clone dev-assistant  # resolves to your-username~dev-assistant
cd dev-assistant

# Save changes (pushes commits to Guild server)
git add . && git commit -m "Description of changes"
guild agent save

# Or commit modified tracked files + push in one step
# (new files are NOT added; git add them first if you want them included)
guild agent save -A --message "Description of changes"

# Save and publish
guild agent save --message "Description" --wait --publish
```

## What the CLI Handles

- Proper `.gitignore` (includes `guild.json`)
- Correct file structure
- Git remote configuration to Guild server
- Version management and validation
- Publishing workflow

## NEVER Do These Things

- ❌ Manually create `package.json`, `tsconfig.json`, or `guild.json`
- ❌ Run `git push` directly (use `guild agent save` — a pre-push hook blocks direct pushes)
- ❌ Run `git pull` directly (use `guild agent pull`)
- ❌ Edit `guild.json` (it's generated and gitignored)

## Common Commands

### Project Setup

```bash
# Install Guild CLI skills for coding assistants (Claude Code, Codex, etc.)
guild setup

# Also create a CLAUDE.md template
guild setup --claude-md

# Install Codex skill files
guild setup --codex

# Also create an AGENTS.md template for Codex
guild setup --codex --agents-md
```

### Creating Agents

```bash
# Create and initialize a new agent (interactive — prompts for name and template)
guild agent init
guild agent init --name my-agent --template LLM --category development
guild agent init --name my-agent --template AUTO_MANAGED_STATE --category development
guild agent init --name my-agent --template BLANK --category development

# Fork an existing agent
guild agent init --fork owner~agent-name

# Clone to work on an existing agent (full name, short name, or UUID)
guild agent clone owner~agent-name
guild agent clone agent-name  # auto-resolves owner
```

### Working with Existing Agents

```bash
# Clone to work on an agent
guild agent clone guildai~dev-assistant
cd dev-assistant

# Pull remote changes (from collaborators or web edits)
# Also syncs unpublished changes made via the web editor
guild agent pull

# Check current version status
guild agent versions --limit 1

# Get latest code
guild agent code

# Search across all agent code files
guild agent grep "pattern"
guild agent grep "pattern" --published
```

### Saving Changes

Git owns the working tree, Guild owns the remote. Use normal git commands to stage and commit, then `guild agent save` to push and create a version.

```bash
# Commit with git, then push via Guild (creates draft)
git add . && git commit -m "WIP: still testing"
guild agent save

# Or commit modified tracked files + push in one step
# (-A commits tracked changes only; new files need an explicit git add)
guild agent save -A --message "WIP: still testing"

# Save and wait for validation
guild agent save --message "Fix bug" --wait

# Save, validate, and publish
guild agent save -A --message "Release v1.0" --wait --publish
```

### Publishing

```bash
# Publish latest validated version
guild agent publish

# Check publication status
guild agent versions --limit 1
```

### Testing

```bash
# Interactive test session
guild agent test

# Test uncommitted changes without saving
guild agent test --ephemeral

# Test with specific input
guild agent chat "Hello, can you help me?"
```

### Chatting with Agents

```bash
# Chat with The Smith
guild chat

# Chat with any published agent by name
guild workspace chat --agent owner~agent-name

# Chat with a specific agent in a specific workspace
guild workspace chat --agent owner~agent-name --workspace owner~workspace-name

# One-shot mode (send prompt, get response, exit)
guild workspace chat --agent owner~agent-name --once "What can you do?"
```

To chat with the agent you are developing locally, use `guild agent chat` from within the agent directory.

## File Structure

After `guild agent init`, you get:

```
my-agent/
├── .git/              # Git repo (remote is Guild server)
├── .gitignore         # Includes guild.json
├── agent.ts           # Your agent code (default; can also be in src/)
├── package.json       # Dependencies
├── tsconfig.json      # TypeScript config
└── guild.json         # Agent ID (gitignored, local only)
```

## Version Lifecycle

1. **Draft** - After `guild agent save` (no `--publish`)
2. **Validating** - After `--publish`, running validation
3. **Published** - Validation passed, available for use
4. **Failed** - Validation failed, check errors

## Troubleshooting

### "No changes to save"

Working tree is clean and there are no unpushed commits. Make a code change, commit it, then run `guild agent save` again.

### "guild.json not found"

You're not in an agent directory. Either:

- `cd` into the agent directory
- Run `guild agent init` to create one

### Validation Failed

Check the error with `guild agent versions --limit 1`. Common issues:

- TypeScript compilation errors
- Missing dependencies
- Invalid schema
