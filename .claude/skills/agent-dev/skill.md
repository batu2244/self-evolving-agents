---
name: guild-agent-development
description: Local agent development using the Guild CLI. Activated when user mentions creating agents, guild agent commands, saving/publishing agents, or agent development workflow. Handles proper CLI workflow and prevents direct git operations.
---

# Guild Agent Development

Build agents for Guild using the CLI.

The Guild CLI is self-documenting:

- You may type `--help` after any command to learn more about how to use it
- You can type `guild --help` to learn about common options available across all commands.
- If you discover a discrepancy between these instructions and the Guild CLI's internal documentation, assume that the Guild CLI's version is correct.

When using the CLI, prefer to explicitly provide arguments as defaults can sometimes be unintuitive

**Always use the Guild CLI for agent operations - unless specified below, never use raw git commands.**

## MCP vs CLI

If Guild MCP tools are available (check for tools prefixed with `guild_`), use them for **read operations**: searching agents, listing workspaces, reading contexts, checking sessions, viewing credentials. MCP tools are faster and don't require shell execution.

Use the **CLI** (via Bash) for **local development operations**: `guild agent init`, `guild agent save`, `guild agent test`, `guild agent pull`, `guild agent clone`. These involve the local filesystem and git, which MCP can't do.

## When to Use This

Activate when user:

- Mentions "guild agent" commands
- Wants to create, save, or publish an agent
- Is working in an agent directory
- Mentions agent development workflow
- Asks about agent versioning or publishing
- Wants to build a new agent

## Quick Reference

### Project Setup

```bash
# Install Guild CLI skills for coding assistants
guild setup

# Also create a CLAUDE.md template
guild setup --claude-md
```

### Creating a New Agent

```bash
# Create and initialize a new agent (interactive - prompts for name and template)
guild agent init

# Create with specific name and template. You must specify an owner account.
guild agent init --owner account-name --name my-agent --template LLM --category development
guild agent init --owner account-name --name my-agent --template AUTO_MANAGED_STATE --category development
guild agent init --owner account-name --name my-agent --template BLANK --category development

# Initialize with fork of existing agent
guild agent init --fork owner~agent-name
```

IMPORTANT! When creating a new agent, it's important to understand who will be the owner of the agent; typically this is likely to be an organization to which the user belongs, or the user's own private account. If unsure, ask for clarification!

IMPORTANT! By default `guild agent init` will create an agent in the current working directory. Use the `--directory` option to specify an alternate location.

### Modifying an Existing Agent

```bash
# Clone to work on existing agent
guild agent clone owner~agent-name
```

IMPORTANT! By default `guild agent clone` will create an agent in the `<agent-name>` directory. Use the `--directory` option to specify an alternate location.

### Syncing and Saving

Git owns the working tree, Guild owns the remote. Use normal git commands to stage and commit, then `guild agent save` to push and create a version.

```bash
# Pull remote changes (e.g., edits from other collaborators)
guild agent pull

# Commit with git, then push via Guild (creates draft)
git add . && git commit -m "Description of changes"
guild agent save

# Or commit modified tracked files + push in one step
# (-A commits tracked changes only; git add new files first to include them)
guild agent save -A --message "Description of changes"

# Save and wait for validation
guild agent save --message "Fix bug" --wait

# Save, validate, and publish
guild agent save -A --message "Release v1.0" --wait --publish
```

### Building

An agent is just an `npm` TypeScript project.

```bash
# Install the agent's dependencies
npm install

# Build the agent's code
npm run build
```

IMPORTANT! You must be logged in to guild to `npm install` dependencies; run `guild auth login` if you get a "not authorized" error when running `npm install`.

TIP. Once you've identified the integrations upon which your agent will depend, update `package.json` and run `npm install` _before_ attempting to write any code that uses the integration. You're much less likely to make a poor assumption about what tools exist or how you must use them.

IMPORTANT! Always make sure that your agent builds correctly before testing.

### Testing

An agent must be tested using the `guild` tool: this will upload the agent to the server runtime environment where the agent will operate.

```bash
# Interactive test session
guild agent test

# Ephemeral test (no persistent storage)
guild agent test --ephemeral

# Bundle locally, then test (faster — skips server-side build)
npm run bundle
guild agent test --bundle agent.js.gz

# Test with structured JSON input (non-interactive)
npm run bundle
guild agent test --bundle agent.js.gz --mode json <<-EOF
{ "type": "text", "text": "this is my agent input" }
EOF

# Test with specific input
guild agent chat "Hello, can you help me?"
```

IMPORTANT! Most integrations require that you provide credentials to connect them appropriately. Choose a workspace (e.g. with `guild workspace list`) that has appropriate credentials installed for each integration that your agent uses. If you cannot find one, instruct the user of your conundrum and ask for advice; specifically, tell them "I cannot test the agent without access to a workspace that has access to all of the integrations that this agent requires. I can't seem to find one. If I've overlooked a workspace with the correct credentials, please let me know which one to use. Otherwise, please configure a workspace appropriately and let me know its name when it is ready."

### Chatting with Agents

```bash
# Chat with any published agent by name
guild workspace chat --agent owner~agent-name

# Chat with a specific agent in a specific workspace
guild workspace chat --agent owner~agent-name --workspace owner~workspace-name

# One-shot mode (send prompt, get response, exit)
guild workspace chat --agent owner~agent-name --once "What can you do?"
```

To chat with the agent you are developing locally, use `guild agent chat` from within the agent directory.

## Guild CLI Is the ONLY Tool for Agent Operations

**ALL agent work — creating, saving, testing, debugging, investigating — goes through Guild CLI.**

### For Creating and Modifying

- ✅ `guild agent init`, `guild agent clone`
- ✅ `git add`, `git commit` (manage your own working tree)
- ✅ `guild agent save` (push commits and create a version)
- ✅ `guild agent save -A --message "desc"` (commit modified tracked files + push; new files need `git add` first)
- ✅ `guild agent pull` (sync remote changes into local directory)
- ✅ `guild agent test`, `guild agent chat`
- ❌ NEVER use `git push` directly (a pre-push hook blocks this — use `guild agent save`)
- ❌ NEVER use `gh repo` for agent operations
- ❌ NEVER manually create `package.json`, `tsconfig.json`, or `guild.json`

### For Investigating and Debugging

- ✅ `guild agent clone <id>` to get agent source locally
- ✅ `guild agent versions <id>` to check version history
- ✅ `guild agent capabilities <id>` to view resolved tools
- ✅ `guild agent code <id>` to view source
- ✅ `guild agent get <id>` to view agent info
- ✅ `guild agent grep <pattern>` to search across all agent code
- ✅ Read local clones created by `guild agent clone`
- ❌ NEVER use `git clone`, `gh repo`, or direct API calls for agent source — always use Guild CLI

### If Guild CLI Can't Do Something

**STOP and tell the user:**

1. What you need to do
2. Why Guild CLI can't do it
3. Why you think `gh`/`git` is needed
4. Let the user decide — never reach for `gh`/`git` on your own

---

## SDK Reference

### Imports

The SDK core comes from `@guildai/agents-sdk`. Service tools are in separate `@guildai-services/*` packages.

You can inspect this package locally after you `npm install` the agent's dependencies.

You can review [online documentation for the API](https://docs.guild.ai/guide/sdk-introduction): this includes guides and references.

### Guild Skills

Guild skills are account-scoped Markdown instructions, not integration packages. Do not add a dependency such as `@guildai-services/acme~brand-voice`, and do not import a per-skill `SkillsTools` export. Instead, opt the agent into the skills runtime once:

```typescript
import { llmAgent, skillsTools, userInterfaceTools } from '@guildai/agents-sdk';

export default llmAgent({
  description: 'Uses account skills when they are relevant.',
  tools: {
    ...skillsTools,
    ...userInterfaceTools,
  },
  systemPrompt: `
    You help users with account-specific writing and operations.
    When the task may benefit from account knowledge, inspect the skill catalog
    and activate the relevant skill before applying its instructions.
  `,
});
```

The runtime exposes two tools:

- `skills_search` opens/searches the skill catalog and returns metadata-only headers such as `acme~brand-voice`.
- `skills_activate` loads one selected skill body into the conversation.

For the current MVP, skills are knowledge-only. They do not grant tools, run scripts, import files/assets, or configure credentials. If something needs API access, executable code, credentials, or a dedicated tool set, build an integration or agent instead of a skill.

### Common Integrations

An _integration_ provides the agent the tools it needs to interact with the world. An integration's API is made available via a separate `@guildai-services/*` package. To use an integration, you add it as a dependency in `package.json` and then `import` it in the agent's TypeScript code. Here are some examples:

| Service        | Package                                    | Export               | Tool Name Prefix  |
| -------------- | ------------------------------------------ | -------------------- | ----------------- |
| Azure DevOps   | `@guildai-services/guildai~azure-devops`   | `azureDevOpsTools`   | `azure_devops_`   |
| Bitbucket      | `@guildai-services/guildai~bitbucket`      | `bitbucketTools`     | `bitbucket_`      |
| Cypress        | `@guildai-services/guildai~cypress`        | `cypressTools`       | `cypress_`        |
| GitHub         | `@guildai-services/guildai~github`         | `gitHubTools`        | `github_`         |
| Google Compute | `@guildai-services/guildai~google-compute` | `googleComputeTools` | `google_compute_` |
| Google Logging | `@guildai-services/guildai~google-logging` | `googleLoggingTools` | `google_logging_` |
| Guild          | `@guildai/agents-sdk`                      | `guildTools`         | `guild_`          |
| Jira           | `@guildai-services/guildai~jira`           | `jiraTools`          | `jira_`           |
| Linear         | `@guildai-services/guildai~linear`         | `linearTools`        | `linear_`         |
| NewRelic       | `@guildai-services/guildai~newrelic`       | `newrelicTools`      | `newrelic_`       |
| Pipedream      | `@guildai-services/guildai~pipedream`      | `pipedreamTools`     | `pipedream_`      |
| Slack          | `@guildai-services/guildai~slack`          | `slackTools`         | `slack_`          |
| TestRail       | `@guildai-services/guildai~testrail`       | `testrailTools`      | `testrail_`       |
| User Interface | `@guildai/agents-sdk`                      | `userInterfaceTools` | `ui_`             |
| Zendesk        | `@guildai-services/guildai~zendesk`        | `zendeskTools`       | `zendesk_`        |

### MCP Integrations

In addition to the first-party integrations above, agents can use _MCP integrations_ — third-party integrations packaged under any `@guildai-services/<owner>~<name>` scope, not just `guildai`. MCP integrations follow a slightly different naming convention from first-party integrations:

| Convention  | First-party example                | MCP example                         |
| ----------- | ---------------------------------- | ----------------------------------- |
| Package     | `@guildai-services/guildai~github` | `@guildai-services/attio~attio-mcp` |
| Export      | `gitHubTools` (camelCase)          | `AttioMcpTools` (PascalCase)        |
| Tool prefix | `github_`                          | `attio_mcp_`                        |

**How the names are derived:**

1. Take the integration name: `attio-mcp`
2. Replace hyphens with underscores → `attio_mcp` (this is also the tool prefix, e.g. `attio_mcp_search_records`)
3. Capitalise each `_`-separated word → `AttioMcp`
4. Append `Tools` → **`AttioMcpTools`**

**To add an MCP integration:**

```bash
npm install --save @guildai-services/attio~attio-mcp
```

**Example usage:**

```typescript
'use agent';

import { agent, pick } from '@guildai/agents-sdk';
import { AttioMcpTools } from '@guildai-services/attio~attio-mcp'; // PascalCase, not camelCase

const tools = {
  ...pick(AttioMcpTools, ['attio_mcp_search_records', 'attio_mcp_get_record']),
};

type Tools = typeof tools;

async function run(input: Input, task: Task<Tools>): Promise<Output> {
  const records = await task.tools.attio_mcp_search_records({ query: 'Acme' });
  // ...
}
```

You can use the CLI to search for a full list of integrations using `guild integration list`.

### Tool Access via `task.tools.*`

To make use of an integration, you must:

1. Import the integration's tools
2. Include the relevant tools in the agent's tool set. This will automatically create functions you can call on the `task.tools` object.
3. Invoke a tool using the `task.tools.<toolName>(args)`.

```typescript
// GitHub
const pr = await task.tools.github_pulls_get({ owner, repo, pull_number: 123 });
const results = await task.tools.github_search_issues_and_pull_requests({
  q: 'is:pr is:open repo:owner/name',
});

// Slack
await task.tools.slack_chat_post_message({ channel: 'C1234567890', text: 'Hello!' });

// Jira
const issues = await task.tools.jira_search_and_reconsile_issues_using_jql({
  jql: 'project = MYPROJ AND status = Open',
});

// User interface
const response = await task.tools.ui_prompt({
  type: 'text',
  text: 'What repo?',
});
await task.tools.ui_notify(progressLogNotifyEvent('Processing...'));

// Guild
const me = await task.tools.guild_get_me({});
await task.tools.guild_credentials_request({ service: 'GITHUB' });
```

### Task Properties

| Property         | Description                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------- |
| `task.sessionId` | Session ID for correlating operations                                                       |
| `task.tools`     | Primary API for calling all tools                                                           |
| `task.llm`       | LLM service — call `task.llm.generateText({ messages, system, tools })` for AI model access |
| `task.console`   | Debug logging (`task.console.debug(...)`, `.info(...)`, `.warn(...)`, `.error(...)`)        |
| `task.save()`    | Persist agent state (self-managed state agents only)                                        |
| `task.restore()` | Retrieve previously saved state (self-managed state agents only)                            |
| `task.guild`     | **Deprecated** — use `task.tools.guild_*` instead                                           |
| `task.ui`        | **Deprecated** — use `task.tools.ui_*` instead                                              |
| `task.env`       | **Deprecated** — do not use                                                                 |

---

## Agent Patterns

Three patterns, ordered by simplicity:

1. LLM agent - just a prompt and tools
2. Coded agent - automatic state management
3. Coded agent - explicit state management

Considerations:

- Maintenance. An LLM agent will be easiest for a human to understand and maintain. A coded agent requires expert knowledge, and one with explicit state management results in a state machine implementation that will be difficult even for an expert programmer to maintain.
- Cost. An LLM agent requires inference to operate and so incurs a high per-invocation cost. If a task is sufficiently simple, a coded agent may be preferable and will certainly be cheaper to operate.
- Latency. An LLM agent requires inference to operate and so can incur a non-trivial latency.
- Control. An LLM agent is ultimately stochastic. If precise, fine-grained control is required, it may be easier to achieve with a coded agent than through natural language instructions.
- Reasoning and decision making. An LLM agent allows for nuanced judgment calls; a coded agent requires strict rules.

TIP. A coded agent can make use of the `task.llm.generateText` call to use an LLM as a subroutine: this may be a reasonable trade-off to make.

A detailed description and example of each is provided below.

### 1. LLM Agent (`llmAgent()`) — Simplest

For conversational/prompt-driven agents where the LLM IS the logic.

```typescript
import { llmAgent, pick } from '@guildai/agents-sdk';
import { gitHubTools } from '@guildai-services/guildai~github';

export default llmAgent({
  description: 'Helps users with GitHub questions',
  tools: {
    ...pick(gitHubTools, ['github_issues_list_for_repo', 'github_issues_get']),
  },
  systemPrompt: `
    You are a helpful assistant that answers questions about GitHub repositories.
    Use the GitHub tools to look up information when asked.
  `,
  mode: 'one-shot', // "one-shot" (default) or "multi-turn"
  useWorkspaceAgents: false,
});
```

**Structured inputs with `inputSchema` and `inputTemplate`:**

By default, `llmAgent` accepts `{ type: "text", text: string }` and sends `text` as the first user message. Override this for structured inputs:

```typescript
import { llmAgent, pick } from '@guildai/agents-sdk';
import { gitHubTools } from '@guildai-services/guildai~github';
import { z } from 'zod';

export default llmAgent({
  description: 'Analyzes a GitHub repository',
  inputSchema: z.object({
    repo: z.string().describe('Repository in owner/repo format'),
    branch: z.string().default('main'),
  }),
  inputTemplate: 'Analyze repo {{repo}} on branch {{branch}}',
  tools: {
    ...pick(gitHubTools, ['github_repos_get', 'github_pulls_list']),
  },
  systemPrompt: 'You analyze GitHub repositories.',
  mode: 'one-shot', // "one-shot" (default) or "multi-turn"
  useWorkspaceAgents: false,
});
```

- `inputSchema` — Zod schema for the agent's input (replaces the default `{ type: "text", text: string }`)
- `inputTemplate` — Mustache-style template that renders the input as the initial LLM user message (default: `"{{text}}"`)

**`mode`: `one-shot` versus `multi-turn`**

An LLM agent whose `mode` is `one-shot` will run for a single turn and then return its output as a result. The "single turn" may include rounds of tool calls, thinking, etc. but once its computation is complete it will terminate and control will be restored to the agent that invoked it. This appropriate for most agents.

TIP: use this agent whenever you need fully autonomous operation without user interaction.

An LLM agent whose `mode` is `multi-turn` does not automatically return control to its caller and will instead proceed interactively -- i.e., prompting the user -- until a specific termination criteria is defined. You must specify this exact termination criteria and instruct the agent to call the `__submit__` tool once that criteria is achieved. This will end the interactive loop and restore control to the caller.

WARNING: this is **rarely** the mode that an agent should operate in: it is **only** useful for agents that are guaranteed to be invoked interactively. USE WITH CAUTION!

**`useWorkspaceAgents`**

When `true`, the agent dynamically discovers and can call other agents installed in the workspace (like Guild's built-in assistant does). Defaults to `true`, but most agents should explicitly set this to `false` unless they specifically need dynamic agent discovery. For deterministic orchestration, delegate to a specific agent by importing its published `/tool` sub-package (see Agent-to-Agent Delegation below).

IMPORTANT! Understand whether or not your agent will operate in an interactive environment; i.e., one where a user will be able to directly chat with the agent. (An agent that is activated from a trigger -- i.e., a webhook or a timer -- is considered **non-interactive** since no user is present.) If the agent is non-interactive, **NEVER** make use of the `ui_prompt` tool: this tool attempts to solicit a response from an interactive user who will not be available.

**Tools**

- Provide an LLM agent with the minimum set of tools that it needs to accomplish its task.
  - Many models have strict limits on the number of tools that are allowed.
  - Unnecessary tools waste context space and risk confusion.
- Use the `pick` utility to select multiple tools from an integration in a succinct way.
- NEVER spread an entire toolset (e.g. `...gitHubTools`) in an LLM agent: this is bad form, and you always want to look good.

### 2. Code Agent with Automatic State Management

An agent that is implemented as a simple TypeScript `run` function. Ideal for most situations that require coding.

- Implement a single `run()` function that accepts the input and returns the output: the function is implemented in a natural procedural style that is easy to understand and maintain.
- Requires the `"use agent"` directive at top of the file: this triggers a compilation step that converts the TypeScript code into a resumable state machine that can be suspended at for long-running tasks.
- Use `task.tools.*` for all integration tool calls.

```typescript
'use agent';

import {
  type Task,
  agent,
  guildTools,
  pick,
  userInterfaceTools,
} from '@guildai/agents-sdk';
import { gitHubTools } from '@guildai-services/guildai~github';
import { z } from 'zod';

const inputSchema = z.object({
  type: z.literal('text'),
  text: z.string().describe('Repository in owner/repo format'),
});

type Input = z.infer<typeof inputSchema>;

const outputSchema = z.object({
  type: z.literal('text'),
  text: z.string().describe('Summary of open PRs'),
});

type Output = z.infer<typeof outputSchema>;

const tools = {
  ...pick(gitHubTools, ['github_search_issues_and_pull_requests']),
  ...userInterfaceTools,
};

type Tools = typeof tools;

async function run(input: Input, task: Task<Tools>): Promise<Output> {
  const repo = input.text.trim();

  const results = await task.tools.github_search_issues_and_pull_requests({
    q: `is:pr is:open repo:${repo}`,
    per_page: 20,
  });

  if (!results.items?.length) {
    return { type: 'text', text: `No open PRs found in ${repo}` };
  }

  const summary = results.items
    .map((pr) => `- #${pr.number}: ${pr.title} (by ${pr.user?.login})`)
    .join('\n');

  return { type: 'text', text: `## Open PRs in ${repo}\n\n${summary}` };
}

export default agent({
  description: 'Lists open PRs in a GitHub repository',
  inputSchema,
  outputSchema,
  tools,
  run,
});
```

### 3. Coded agent with Self-Managed State

For explicit state control.

- Implement the `start()` and `onToolResults()` functions: these return `AgentResult<Output, Tools>`
  - `return ask(prompt)` — sends a `ui_prompt` tool call to get user input
  - `return output(value)` — wraps your output as `{ type: "output", output: value }`
  - `return callTools([...])` — requests the runtime to execute tool calls
- `task.save(state)` / `task.restore()` persist state between invocations: you must explicitly specify the schema of the state.
- May be necessary for more elaborate situations; notably, invoking multiple agents or tools in parallel.
- Generally results in a much more complicated program that can be difficult to understand and maintain; avoid if possible.
- No `"use agent"` directive since there is no compilation step.

```typescript
import {
  agent,
  ask,
  output,
  callTools,
  userInterfaceTools,
  type Task,
  type AgentResult,
  type TypedToolResult,
  type TypedToolError,
} from '@guildai/agents-sdk';
import { z } from 'zod';

const inputSchema = z.object({
  type: z.literal('text'),
  text: z.string().describe("The user's input"),
});

const outputSchema = z.object({
  type: z.literal('text'),
  text: z.string().describe("The agent's response"),
});

const stateSchema = z.object({
  count: z.number(),
});

type Input = z.infer<typeof inputSchema>;
type Output = z.infer<typeof outputSchema>;
type State = z.infer<typeof stateSchema>;
const tools = { ...userInterfaceTools };
type Tools = typeof tools;

async function start(
  input: Input,
  task: Task<Tools, State>
): Promise<AgentResult<Output, Tools>> {
  await task.save({ count: 1 });
  return ask(`Got: ${input.text}`);
}

async function onToolResults(
  results: Array<TypedToolResult<Tools> | TypedToolError<Tools>>,
  task: Task<Tools, State>
): Promise<AgentResult<Output, Tools>> {
  const state = await task.restore();
  const result = results[0];
  if (result.type === 'tool-result' && result.output.text === 'done') {
    return output({ type: 'text', text: `Final count: ${state!.count}` });
  }
  await task.save({ count: state!.count + 1 });
  return ask(`Count: ${state!.count + 1}`);
}

export default agent({
  description: 'Tracks conversation state explicitly',
  inputSchema,
  outputSchema,
  stateSchema,
  tools,
  start,
  onToolResults,
});
```

---

## Compiler Limitations for `"use agent"`

Code agents with automatic state management (pattern #2 above) use the
`"use agent"` directive. This triggers a Babel compiler
(`@guildai/babel-plugin-agent-compiler`) that translates each `async` function
into a serializable state machine — the agent's entire execution state can be
captured at any `await`, persisted to storage, and resumed hours or days later.

The compiler supports most JavaScript, but some constructs either fail at build
time or — more dangerously — compile cleanly and then silently produce wrong
behavior across an `await`. This section enumerates both.

These limitations apply **only to code inside a `"use agent"` function body**.
`llmAgent` agents and self-managed-state agents are not compiled and have none
of these restrictions.

### Fails at build time

The compiler throws `NotImplemented` and `npm run build` fails. Fix the source.

#### Async generators

```typescript
// ❌ NotImplemented: async generator function
async function* stream() { ... }
```

There is no workaround in compiled code — restructure to a regular async
function that returns a batch, or accumulate results imperatively.

#### Labeled `break` / `continue`

```typescript
// ❌ NotImplemented: break to label / continue to label
outer: for (...) {
  for (...) { break outer }
}
```

Refactor to a boolean flag, an early `return`, or extract the inner loop to a
helper that signals via its return value.

#### Two nested functions sharing a name

Non-async nested functions are hoisted to closure level by name. Two
declarations of the same name in sibling scopes collide silently — the second
shadows the first.

```typescript
// ❌ Second `bar` overwrites the first
function outer() {
  { function bar() { ... }; bar() }
  { function bar() { ... }; bar() }
}
```

Give each helper a unique name.

### Compiles cleanly, fails at runtime

These compile cleanly. If the agent never suspends (no `task.save()` /
`ui_prompt` / external tool that takes time), they may even appear to work in
testing. But once the state machine is serialized at the `await` and later
resumed, behavior is wrong. **The compiler does not warn you.**

Rule of thumb: anything stored in a local variable that crosses an `await`
must be JSON-serializable via `@guildai/s11n`. `s11n` natively handles
primitives, plain objects, arrays, `Map`, `Set`, `Date`, plain `Error`, cycles
and shared references. Everything else is suspect.

#### `Promise` objects and `Promise.all` / `.any` / `.race`

The compiler tracks `await` expressions individually. Raw `Promise` values and
the static composition methods on `Promise` cannot survive serialization at
the `await` that consumes them.

```typescript
// ❌ Won't survive a suspend at the await
const p = fetchSomething();
await delay();
return await p;

// ❌ Same problem
const [a, b] = await Promise.all([fetchA(), fetchB()]);
const winner = await Promise.race([f1(), f2()]);
```

**Workaround:** await each promise sequentially. The compiler is built around
single `await` expressions, not composition.

```typescript
// ✅
const a = await fetchA();
const b = await fetchB();
```

This serializes between the two awaits, so each call is independent.

If the calls are independent tool calls and you want them to run **in
parallel** rather than one after another, use `task.gather` /
`task.gatherSettled` instead — see [Parallel tool calls with
`task.gather`](#parallel-tool-calls-with-taskgather) below. These are the
compiler-supported replacement for `Promise.all` / `Promise.allSettled` when
every input is a tool call.

#### `for await ... of` and async iterators

```typescript
// ❌ The `await` is silently dropped
for await (const item of asyncIterable) { ... }
```

The compiler emits a plain `for-of` loop, so each `item` is the unresolved
Promise rather than its value, and any iterator-protocol `await`s are skipped.
The build does not warn.

If the data source can be enumerated synchronously, use `for-of` and `await`
each item explicitly. If the source is genuinely streaming, you cannot consume
it from a compiled agent — fetch the data in a non-compiled helper or pre-load
into an array.

#### Externally-produced function values across `await`

Inline arrow / function expressions written directly in your source are
hoisted into a `$fns` array and survive serialization. **Function values that
arrive from outside the compiled source do not** — the compiler has no body to
hoist.

```typescript
// ❌ Function received as a parameter
async function run(callback: () => void) {
  'use agent';
  await delay();
  callback(); // undefined after restore
}

// ❌ Function returned from a non-compiled call
const f = makeAdder(x);
await delay();
return f(1); // undefined after restore

// ❌ Imported / module-level function stored in a frame slot
import { uncompiled } from './helpers';
const h = { fn: uncompiled };
await delay();
return h.fn(x); // undefined after restore

// ❌ Functions produced by .map / Object.assign / spread
const fns = items.map((i) => () => process(i));
await delay();
return fns[0](); // undefined after restore
```

**Workarounds:**

1. Wrap module-level or imported functions in an inline arrow. The arrow is a
   literal the compiler can hoist; the body resolves the external name at call
   time:
   ```typescript
   const f = (n: number) => uncompiled(n); // ✅
   const h = { fn: (n: number) => uncompiled(n) }; // ✅
   ```
2. Inline factory logic at the call site rather than going through a factory
   that returns a function value:
   ```typescript
   const f = (n: number) => x + n; // ✅ instead of makeAdder(x)
   ```
3. Persist the data, not the functions. Cross the `await` with the inputs and
   construct functions just-in-time on the synchronous side.
4. Replace a callback parameter with a tagged-dispatch enum. Wrapping a
   parameter callback in an inline arrow does **not** help — the parameter
   itself is in a frame slot:
   ```typescript
   async function run(strategy: 'upper' | 'lower', text: string) {
     'use agent';
     await delay();
     return strategy === 'upper' ? text.toUpperCase() : text.toLowerCase();
   }
   ```
5. If you cannot eliminate a callback, call it before any `await` and store
   only its result.

#### MemberExpression call of a compiled async in an object or array

Async arrow and async function expressions compile into the state machine as
**call descriptors** stored in a closure-level `$fns` array — they invoke
correctly when called via a plain identifier callee. Calling one via member
access (`obj.fn()`, `arr[i]()`) goes through a different code path that the
compiler does not yet rewrite, so JavaScript invokes the descriptor's throw
stub directly: `compiled async function called from outside the state machine`.

```typescript
// ❌ Runtime throw
const handlers = {
  onClick: async (x: number) => {
    await delay();
    return x + 1;
  },
};
return await handlers.onClick(5);

// ❌ Same shape
const fns = [async (x: number) => x + 1];
return await fns[0](5);

// ✅ Extract to a local Identifier first
const fn = handlers.onClick;
return await fn(5);
```

#### `new` on a compiled async function

Calling `new` on a compiled async expression dispatches through a code path
the compiler does not rewrite, so JavaScript invokes the descriptor's throw
stub. This is unusual code — just don't.

#### Descriptor leaks to non-compiled JavaScript

A compiled async expression that escapes into non-compiled JS — passed as a
callback to `.map`, `setTimeout`, `Promise.all`, etc. — gets invoked as a
plain function and throws. The error message includes the source location of
the original async expression so leaks are diagnosable.

```typescript
// ❌ .map invokes the descriptor directly → throws
const results = items.map(async (x) => task.tools.http_get({ url: x }))

// ❌ setTimeout schedules the descriptor as a plain callback
setTimeout(async () => { ... }, 1000)

// ❌ Promise.all itself is also unsupported, and the IIFE descriptor leak
//    is what fails first when each entry is invoked
await Promise.all([asyncFn(), otherAsync()])
```

**Workaround:** invoke async work sequentially with explicit `await`s in the
compiled function. If you need fan-out, build an array of inputs across the
loop and process them with sequential awaits.

#### Other non-serializable values across `await`

The following are not serializable; storing them in a local that crosses an
`await` will produce wrong behavior after restore:

- `Promise` (see above)
- `RegExp`
- `WeakMap`, `WeakSet`
- Class instances (`new Foo(...)` for any user-defined class)
- Arbitrary external functions (see above)

Keep these inside a single step. If you must cross an `await`, store the data
needed to reconstruct them (the regex source string, the constructor args)
and rebuild on the other side.

### Module and dependency limits

#### No imports from local modules

The compiler only processes the file containing the agent. Async helpers in
sibling `.ts` files are not compiled into the state machine and will not
survive serialization.

```typescript
// ❌ Async helper imported from another file
import { fetchAndProcess } from './helpers';
// fetchAndProcess will run, but if it suspends, its frame is lost
```

Keep all code that crosses `await` in the same file as the agent. Pure-sync
helpers can live elsewhere as long as the values they return are serializable.

#### CJS / native modules cannot be used

Agent code runs in an ESM-only sandbox. Adding a CommonJS package to
`dependencies` will fail at runtime. Verify each dependency is ESM-compatible
before adding it (`"type": "module"` in its `package.json`, or shipped as
`.mjs`).

#### No source maps

The compiled state machine has no source-map relationship to your TypeScript
source. Runtime stack traces point into the generated `switch ($step)
{ ... }`. When debugging, reproduce in a small standalone test and read the
generated code if you must — `npx babel agent.js --plugins
@guildai/babel-plugin-agent-compiler` will print the transformed output.

### Keep heavy work in synchronous helpers, not async ones

The `"use agent"` directive makes the compiler translate **every asynchronous
function in the agent's module** — the `run` body and any `async` function it
nests or calls within the same file — into state-machine steps. It does
**not** translate **synchronous** functions (those are hoisted and left as
ordinary JavaScript), and it never touches code in other files. So the lever
for heavy _pure_ computation is simple: **make it a synchronous function.** The
async/sync distinction is what matters here, not where the function lives.

A synchronous call runs to completion within a single step of its caller,
which has two payoffs:

1. **Nothing extra is serialized.** A sync helper isn't compiled at all — it's
   hoisted to the closure as an ordinary function and runs inside its caller's
   current step, so its intermediates — a 5,000-element array, a large `Map` —
   are plain JS locals that never enter `$frames` and so are never serialized.
   (What _is_ captured at each `await` is the live frame stack of the compiled
   code.) Mark that same helper `async` and it becomes part of the state
   machine: its locals move into frame slots and serialize at every suspension
   like everything else.

2. **It runs as plain JS, and it can't blow the step budget.** Compiled code
   is native JavaScript, but the compiler lowers every loop into numbered
   state-machine steps and each iteration costs one metered step against a
   budget (1,000 per refill). A genuine `await` resets the meter, but a tight
   compiled loop never yields — so a few thousand iterations of pure arithmetic
   exhaust the budget repeatedly, and after a fixed number of refills without
   yielding (10 by default, i.e. ~10,000 steps) the runtime **stops the agent**
   with "Agent ran too many synchronous steps without yielding." A sync helper
   sidesteps this entirely: it runs as one ordinary native loop, off the meter.

Keep `run()` a thin **orchestrator** — tool calls, `task.gather`, control flow
— and push date parsing, bucketing, aggregation, statistics, and large-array
assembly into **synchronous** helpers (module scope is the natural home):

```typescript
// ✅ run() awaits I/O; the heavy loop is a plain sync function
const calls = [];
for (const w of workflows) calls.push(listRuns(w.id, 1)); // compiled: 1 step/item
const pages = await task.gather(calls);
const { skipped, regressed } = analyzeRuns(pages, cfg); // sync → not compiled

// ❌ marking the same helper async pulls it into the state machine — its big
//    intermediates now serialize at every await, for no benefit
async function analyzeRuns(pages, cfg) {
  /* parse dates, bucket, median... */
}
```

Note that `run()`'s own loops are compiled too: the `for` above spends one step
per workflow against the budget. That's fine for tens or hundreds of items, but
if the orchestrator itself iterates over thousands, move that loop into a sync
helper as well — the budget doesn't care whether a loop is "setup" or "work."

The sync helpers are also independently unit-testable, since they never touch
`task` — and per the local-modules rule above, a pure-sync helper may even
live in another file, as long as the value it returns is serializable.

(This applies only to compiled `"use agent"` agents. Self-managed-state agents
— the `start` / `onToolResults` pattern — have no directive and aren't compiled
at all.)

---

## Parallel Tool Calls with `task.gather`

`Promise.all` does not survive serialization (see [the limitation
above](#promise-objects-and-promiseall--any--race)). For automatic-state agents
(`"use agent"`) the supported way to fan out **independent tool calls
concurrently** is `task.gather` and `task.gatherSettled`. The runtime allocates
every subtask atomically, dispatches them as a single batch, suspends the state
machine while they run, and assembles the results in source order on resume.

### `task.gather` — fail fast

Analogous to `Promise.all`: resolves to an array of results in source order, or
rejects on the first failure.

```typescript
'use agent';

import { type Task, agent, pick } from '@guildai/agents-sdk';
import { gitHubTools } from '@guildai-services/guildai~github';
import { z } from 'zod';

const tools = {
  ...pick(gitHubTools, ['github_pulls_list', 'github_issues_list_for_repo']),
};
type Tools = typeof tools;

async function run(input: Input, task: Task<Tools>): Promise<Output> {
  const [pulls, issues] = await task.gather([
    task.tools.github_pulls_list({ owner, repo, state: 'open' }),
    task.tools.github_issues_list_for_repo({ owner, repo, state: 'open' }),
  ]);

  return {
    type: 'text',
    text: `${pulls.length} open PRs, ${issues.length} open issues`,
  };
}
```

Results are fully typed: `task.gather([a, b])` returns a tuple whose elements
are the awaited return types of `a` and `b`, in order.

### `task.gatherSettled` — collect every outcome

Analogous to `Promise.allSettled`: never rejects. Each entry is a
`PromiseSettledResult` describing whether that call fulfilled or rejected, so
one failing call doesn't sink the rest.

```typescript
const results = await task.gatherSettled([
  task.tools.github_repos_get({ owner, repo: 'a' }),
  task.tools.github_repos_get({ owner, repo: 'b' }),
]);

for (const result of results) {
  if (result.status === 'fulfilled') {
    task.console.info(`got ${result.value.full_name}`);
  } else {
    task.console.warn(`failed: ${result.reason}`);
  }
}
```

### Rules and limits

- **Inputs must be tool-call expressions** — `task.tools.X(...)`, sub-agent
  tool calls, or hook tools. The `ToolCallPromise` brand on the parameter type
  rejects arbitrary promises (`fetch(...)`, timers, library code) at compile
  time. This is the deliberate boundary that makes `gather` work where
  `Promise.all` can't: every input is a dispatchable tool call the runtime can
  serialize, not an opaque promise.
- **Use only from a compiled (`"use agent"`) agent body.** Self-managed-state
  agents already fan out in parallel by returning `callTools([...])` with more
  than one entry — they don't need `gather`.

---

## Agent-to-Agent Delegation

Every published agent exposes a `/tool` sub-package that allows it to be used just like any other callable tool. Import it and add it to your tools object — the runtime handles dispatch.

### Using a published agent as a tool (preferred)

Add the agent as a dependency in your `package.json`:

```json
"@guildai/waterson~subagent": "^1.0.0"
```

Then import from its `/tool` sub-package:

```typescript
import subagentTool from '@guildai/waterson~subagent/tool';

// In an llmAgent:
export default llmAgent({
  description: 'My agent',
  tools: { subagent: subagentTool },
  systemPrompt: '...',
});

// In an automatic state agent:
const result = await task.tools.subagent({ type: 'text', text: 'do the thing' });

// In a self-managed state agent:
return callTools([
  {
    type: 'tool-call',
    toolName: 'subagent',
    toolCallId: 'subagent-1',
    input: { type: 'text', text: 'do the thing' },
  },
]);
```

The `/tool` sub-package is auto-generated at build time — every published agent has one. The tool inherits the agent's `inputSchema` and `outputSchema`, so callers get full type safety.

To call another agent, publish it and import its `/tool` sub-package as shown above — that is the supported, type-safe path. Don't hand-wire delegation with the lower-level `guildAgentTool()`; always prefer the `/tool` import.

### Using the Coding Agent

The coding agent runs instructions inside a dedicated (but isolated) virtual machine. The agent has full access to the computer: use it when your agent needs to read/write files, run shell commands, or work with a cloned repository.

```typescript
import { ExperimentalCodingTools as codingTools } from '@guildai-services/guildai~experimental-coding';
import { type Task, agent } from '@guildai/agents-sdk';
import {
  CONTAINER_IMAGE,
  codingAgentToolsFrom,
} from '@guildai/guildai~sys-experimental-coding';
import codingAgentTool from '@guildai/guildai~sys-experimental-coding/tool';

const tools = { ...codingTools, communicate: codingAgentTool };
type Tools = typeof tools;

async function run(input: Input, task: Task<Tools>): Promise<Output> {
  const { container_id } = await task.tools.experimental_coding_create({
    image: CONTAINER_IMAGE,
  });
  try {
    const { text } = await task.tools.communicate({
      container_id,
      message: 'What files are in the current directory?',
    });
    return { type: 'text', text };
  } finally {
    await task.tools.experimental_coding_delete({ container_id });
  }
}
```

**Container lifecycle** — You own the container. Create it before you need it, and always clean up in a `finally` block so you don't leave containers running if your agent throws.

**System prompts** — Pass a `system_prompt` to `communicate` to shape how the coding agent behaves. Keep it in a separate `.md` file (see External Prompts below).

```typescript
import systemPrompt from './system-prompt.md';

const { text } = await task.tools.communicate({
  container_id,
  system_prompt: systemPrompt,
  message,
});
```

**Giving the coding agent tools** — The container is sandboxed and can't reach external services. Pass tools explicitly using `codingAgentToolsFrom`:

```typescript
import { pick } from '@guildai/agents-sdk';
import { gitHubTools } from '@guildai-services/guildai~github';
import { codingAgentToolsFrom } from '@guildai/guildai~sys-experimental-coding';

const { text } = await task.tools.communicate({
  container_id,
  message,
  tools: codingAgentToolsFrom({
    ...pick(gitHubTools, [
      'github_repos_download_zipball_archive',
      'github_pulls_create',
    ]),
  }),
});
```

Only pass the tools the coding agent actually needs for the task at hand.

**Multi-turn conversations** — By default each `communicate` call starts a fresh session. To continue a conversation, capture the `session_id` from the first response and pass it back:

```typescript
// Step 1: set up the environment
const { text: setupResult, session_id } = await task.tools.communicate({
  container_id,
  message: 'Clone the repo and set up the workspace',
  tools: codingAgentToolsFrom({
    ...pick(gitHubTools, ['github_repos_download_zipball_archive']),
  }),
});

// Step 2: do the actual work, continuing the same session
const { text } = await task.tools.communicate({
  container_id,
  session_id,
  message: 'Now implement the feature described above',
  tools: codingAgentToolsFrom({
    ...pick(gitHubTools, ['github_pulls_create']),
  }),
});
```

Using `session_id` preserves the container's working directory, environment variables, and conversation history between calls.

**GitHub tools for common container tasks:**

The GitHub API provides primitives that must be composed in the coding container for common operations. If you need the agent to perform any of the following tasks, be sure to include the tools upon which the task depends. Use the table below as a reference.

| Task                | Required Tools                                                                                                                                         |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Clone a repository  | `github_repos_download_zipball_archive`                                                                                                                |
| Create a branch     | `github_git_get_ref`, `github_git_create_ref`                                                                                                          |
| Create a commit     | `github_git_get_ref`, `github_git_get_commit`, `github_git_create_blob`, `github_git_create_tree`, `github_git_create_commit`, `github_git_update_ref` |
| Create a PR         | `github_pulls_create`                                                                                                                                  |
| Create an issue     | `github_issues_create`                                                                                                                                 |
| Comment on issue/PR | `github_issues_create_comment`                                                                                                                         |
| Compute a PR diff   | `github_pulls_list_files`                                                                                                                              |

---

## Advanced Topics

### Example: Using an LLM from within a coded agent

For agents that need an LLM tool-calling loop with fine-grained control over which tool calls the LLM handles vs which get delegated to the runtime:

```typescript
import {
  agent,
  callTools,
  output,
  delegatedCallsOf,
  asToolResultContent,
  userInterfaceTools,
  type ModelMessage,
  type Task,
  type AgentResult,
  type TypedToolResult,
  type TypedToolError,
} from '@guildai/agents-sdk';
import { gitHubTools } from '@guildai-services/guildai~github';
import { slackTools } from '@guildai-services/guildai~slack';
import { z } from 'zod';

const tools = { ...gitHubTools, ...slackTools, ...userInterfaceTools };
type Tools = typeof tools;

// Separate tools the LLM can execute directly from those needing delegation
const llmTools = { ...gitHubTools }; // LLM gets execute access to these
const agentTools = { ...slackTools, ...userInterfaceTools }; // These get delegated

async function start(input, task: Task<Tools>) {
  const messages: ModelMessage[] = [{ role: 'user', content: input.text }];

  const result = await task.llm.generateText({
    system: 'You are a helpful assistant.',
    messages,
    tools: llmTools, // Only give LLM the tools it can execute
  });

  // Check for delegated (unexecuted) tool calls
  const delegated = delegatedCallsOf<Tools>(result.content);
  if (delegated.length > 0) {
    // Save conversation state for onToolResults
    await task.save({ messages: [...messages, ...result.response.messages] });
    return callTools(delegated);
  }

  return output({ type: 'text', text: result.text });
}

async function onToolResults(
  results: Array<TypedToolResult<Tools> | TypedToolError<Tools>>,
  task: Task<Tools>
) {
  const state = await task.restore();
  // Convert results back into LLM message format
  state.messages.push({
    role: 'tool',
    content: asToolResultContent(results),
  });

  // Continue the conversation
  // ...
}
```

**Key utilities:**

- `task.llm.generateText({ messages, system, tools })` — call the LLM with automatic authentication and provider selection. The runtime handles model selection and credential injection.
- `delegatedCallsOf<Tools>(content)` — extracts unexecuted tool calls from `generateText` results that need runtime delegation
- `asToolResultContent(results)` — converts `TypedToolResult[]` into LLM message format for conversation history

### Example: Slack-Specific Patterns

When posting to Slack, convert markdown to Slack's mrkdwn format. Use an inline converter (`slackify-markdown` is CJS and breaks in the ESM agent runtime):

```typescript
// Simple markdown-to-Slack-mrkdwn converter (inline — do NOT use slackify-markdown)
function slackifyMarkdown(md: string): string {
  return md
    .replace(/\*\*(.+?)\*\*/g, '*$1*') // bold: **text** → *text*
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '_$1_') // italic: *text* → _text_
    .replace(/~~(.+?)~~/g, '~$1~') // strikethrough
    .replace(/^### (.+)$/gm, '*$1*') // h3 → bold
    .replace(/^## (.+)$/gm, '*$1*') // h2 → bold
    .replace(/^# (.+)$/gm, '*$1*') // h1 → bold
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<$2|$1>') // links
    .replace(/^> (.+)$/gm, '> $1') // blockquotes (same syntax)
    .replace(/`([^`]+)`/g, '`$1`'); // inline code (same syntax)
}

// In your agent:
const responseText = '## Summary\n- Item 1\n- Item 2';
const slackText = slackifyMarkdown(responseText);

await task.tools.slack_chat_post_message({
  channel: 'C1234567890',
  text: slackText,
});
```

---

## Anti-Hallucination Guide

**Only use methods and patterns that actually exist.**

### Discovering Available Tools

**NEVER attempt to guess a tool's name.** Use the Guild CLI to discover what tools exist for each integration:

```bash
# List all available integrations
guild integration list

# List operations for a specific integration
guild integration operation list guildai~github

# Get full schemas (input/output) in JSON
guild integration operation list guildai~github --json
```

Tool names follow the pattern `{service_prefix}_{operation_name}` (e.g., `github_pulls_get`).

- Check the service packages table above for common service prefixes.
- For an integration not listed above (e.g., `@guildai-services/some-owner~some-integration`), the service prefix will be the _integration name_ (e.g., `some-integration`) with any hyphens replaced by underscores (e.g., `some_integration`).

Use `pick()` to select specific tools, or `omit()` to exclude specific tools:

```typescript
// Include only specific tools
const tools = {
  ...pick(gitHubTools, ['github_repos_get', 'github_pulls_list']),
};

// Include all tools except specific ones
const tools = {
  ...omit(gitHubTools, ['github_repos_delete', 'github_repos_update']),
};
```

### Adding an integration for your agent

To add an integration for you agent, use `npm install`:

```bash
# Add an integration
npm install --save @guildai-services/some-owner~some-integration

# Add an agent as a tool
npm install --save @guildai/some-owner~some-agent
```

The integration or agent should be added as a dependency (i.e., _not_ a `devDependency`)

Once you've done that, import the agent into your agent as you would any other package:

```typescript
// An integration. This object will be a ToolSet from which you should select the tools you need.
import { SomeIntegration } from '@guildai-services/some-owner~some-integration';

// An agent... note that it's imported from the `/tool` sub-package as the default export
import SomeAgent from '@guildai/some-onwer~some-agent/tool';

// Include the relevant tools in your agent's tool object.
const tools = {
  // The integration tools.
  ...pick(SomeIntegration, ['some_integration_tool_one', 'some_integration_tool_two']),

  // The agent tool
  someAgentTool: SomeAgent,
};
```

**For a coded agent...**

Once you've done the above, you can use the TypeScript LSP to determine the exact parameter and return type for each tool. OR... you can inspect the TypeScript types for the package directly in the `node_modules` subdirectory (this is often an expedient way to understand the type details).

IMPORTANT! Always let TypeScript help you:

- Let the TypeScript compiler infer the type of the `tools` object: DO NOT provide an explicit type annotation as it is likely incorrect!
- NEVER cast to `any`: failure to resolve correct types likely means that something else is going wrong so keep investigating.
- Adding a tool to the agent's `tools` creates the correct signature on the `task` object:

```typescript
// Assume `tools` is defined as above, and that `Input` and `Output` are the `z.infer'd` types
type Tools = typeof tools;

async function run(input: Input, task: Task<Tools>): Promise<Output> {
  // These should *not* resolve to `any` type, but should instead be strongly typed if all your imports have been done correctly.
  const { foo, bar } = await task.tools.some_integration_tool_one({
    baz: 'bop',
    bip: 12,
  });
}
```

### DO NOT USE (Common Mistakes)

```typescript
// ❌ WRONG: identifier is deprecated
export default agent({ identifier: "my-agent", ... })

// ❌ WRONG: service tools are NOT in @guildai/agents-sdk
import { gitHubTools } from "@guildai/agents-sdk"
import { slackTools } from "@guildai/agents-sdk"

// ❌ WRONG: these direct service accessors don't exist
const pr = await task.github.search_issues(...)
await task.slack.post_message(...)
const issue = await task.jira.get_issue(...)

// ❌ WRONG: these methods don't exist
task.github.pulls_list()
task.github.repos_get()
task.github.pulls_create()

// ❌ WRONG: parameter name
github_search_issues_and_pull_requests({ query: "..." })  // Use { q: "..." }

// ❌ WRONG: task.ui_prompt() is not a method on task
await task.ui_prompt("What repo?")

// ❌ WRONG: importing service tools from internal packages directly
import { gitHubTools } from "@guildai-services/guildai~github/src/service"

// ❌ WRONG: missing "use agent" directive on coded agents
import { agent } from "@guildai/agents-sdk"
// (no "use agent" at top)
export default agent({ run: async (input, task) => { ... } })

// ❌ WRONG: MCP integrations export PascalCase, not camelCase
import { attioMcpTools } from "@guildai-services/attio~attio-mcp"
```

### CORRECT Patterns

```typescript
// ✅ No identifier needed
export default agent({ description: "My agent", ... })

// ✅ Service tools from @guildai-services/* packages
import { gitHubTools } from "@guildai-services/guildai~github"
import { slackTools } from "@guildai-services/guildai~slack"

// ✅ Platform tools from @guildai/agents-sdk
import { guildTools, userInterfaceTools } from "@guildai/agents-sdk"

// ✅ Use task.tools.* for all tool calls
const pr = await task.tools.github_pulls_get({ owner, repo, pull_number })
const results = await task.tools.github_search_issues_and_pull_requests({ q: "is:pr repo:owner/name" })
await task.tools.slack_chat_post_message({ channel, text })
const response = await task.tools.ui_prompt({ type: "text", text: "What repo?" })

// ✅ "use agent" directive for coded agents
"use agent"
import { agent } from "@guildai/agents-sdk"
export default agent({ run: async (input, task) => { ... } })

// ✅ MCP integrations export PascalCase, not camelCase
import { AttioMcpTools } from "@guildai-services/attio~attio-mcp" // MCP integrations export PascalCase, not camelCase
```

---

## External Prompts

Don't embed long prompts as strings in your code — import them as `.md` files instead.

```typescript
import { llmAgent } from '@guildai/agents-sdk';
import systemPrompt from './system-prompt.md';

export default llmAgent({
  description: 'My agent',
  systemPrompt,
  tools: {
    /* ... */
  },
});
```

Then write your prompt in a standalone `system-prompt.md`:

```markdown
<!-- system-prompt.md -->

You are a helpful assistant that...

[...detailed instructions...]

Good luck!
```

Your editor treats `.md` files as first-class citizens: syntax highlighting, preview, spell-check. Long prompt strings buried in TypeScript get none of that, and they make the surrounding logic harder to read.

The `--loader:.md=text` flag in the bundle script handles importing `.md` files at build time.

---

## package.json

Agents that use `"use agent"` (automatic state agents) need the Babel compiler to transform `await task.tools.*` calls into continuations. New agent templates include this by default. The `bundle` script must be separate from and run _after_ the `build` step — don't merge them.

```json
{
  "name": "guild-agent-{name}",
  "version": "1.0.0",
  "author": "Guild.ai",
  "type": "module",
  "scripts": {
    "build": "npm run build:compile && npm run build:transform && npm run build:copy",
    "build:compile": "tsc --build",
    "build:transform": "babel ./dist/agent.js --out-dir ./dist --out-file-extension .compiled.js --plugins @guildai/babel-plugin-agent-compiler",
    "build:copy": "cp *.md dist/",
    "bundle": "npm run build && esbuild dist/agent.compiled.js --bundle --loader:.md=text --platform=node --format=esm --external:zod --external:@guildai/agents-sdk | gzip | base64 > agent.js.gz"
  },
  "dependencies": {},
  "devDependencies": {
    "@babel/cli": "^7.28.3",
    "@guildai/babel-plugin-agent-compiler": "*",
    "esbuild": "^0.25.0",
    "typescript": "^5.0.0"
  }
}
```

For simple `llmAgent()` agents that don't use `"use agent"`, you can skip the Babel step:

```json
{
  "scripts": {
    "build": "tsc",
    "bundle": "npm run build && esbuild dist/agent.js --bundle --loader:.md=text --platform=node --format=esm --external:zod --external:@guildai/agents-sdk | gzip | base64 > agent.js.gz"
  },
  "devDependencies": {
    "esbuild": "^0.25.0",
    "typescript": "^5.0.0"
  }
}
```

**CRITICAL:**

- DO NOT modify the `@guildai/agents-sdk` and `zod` dependencies provided in the agent's template.
- You may add third-party ESM-compatible packages your agent uses to `dependencies`.
- DO NOT include CJS packages: an agent that includes a CJS module will fail at runtime.
- `devDependencies` is for build tools only (`esbuild` for bundling, `typescript` for compilation).

## Versioning

- Use semver: `1.0.0` → `1.0.1` (patch), `1.1.0` (minor), `2.0.0` (breaking)
- Use `--bump [patch|minor|major]` with `guild agent save` to auto-bump `package.json` version
- Or bump manually in `package.json` before saving

## File Structure

After `guild agent init`:

```
my-agent/
├── .git/              # Git repo (remote is Guild server)
├── .gitignore         # Includes guild.json
├── agent.ts           # Your agent code (default location; can also be in src/)
├── package.json       # Dependencies
├── tsconfig.json      # TypeScript config
└── guild.json         # Agent ID (gitignored, local only)
```

## Version Lifecycle

1. **Draft** - After `guild agent save` (no `--publish`)
2. **Validating** - After `--publish`, running validation
3. **Published** - Validation passed, available for use
4. **Failed** - Validation failed, check errors

## CLI Commands

Below is a quick reference of common CLI commands.

- Use `guild help` to discover the full set of commands
- Use `guild <command> [...<subcommand>] --help` with any command for full documentation on its usage
- Prefer to explicitly provide all command arguments rather than relying on defaults

```bash
guild setup                                        # Install coding assistant skills
guild setup --claude-md                            # Also create CLAUDE.md template
guild agent init                                   # Create and initialize a new agent
guild agent init --name <name> --template LLM      # Create with specific name and template
guild agent init --fork <agent-id>                 # Fork existing agent
guild agent pull                                   # Pull remote changes
guild agent save                                   # Push commits and create a draft version
guild agent save -A --message "description"        # Commit modified tracked files + push (new files need git add)
guild agent save --message "v1.0" --wait --publish # Save + validate + publish
guild agent save --bump minor --message "v1.1"     # Auto-bump version before saving
guild agent test                                   # Interactive test
guild agent test --ephemeral                       # Ephemeral test
guild agent test --bundle agent.js.gz              # Test with pre-built bundle
guild agent test --no-cache                        # Force fresh build (skip cache)
guild agent chat "Hello"                           # Test with input
guild agent get [agent-id]                         # View agent info
guild agent capabilities [agent-id]                # Show resolved tools for an agent
guild agent list                                   # List agents
guild agent list --search "github" --published     # Search published agents
guild agent search <query>                         # Search published agents
guild agent versions [agent-id]                    # Version history
guild agent clone <agent-id>                       # Clone existing agent
guild agent fork [identifier]                      # Fork an agent (latest published version, or identifier:version)
guild agent publish                                # Publish a version
guild agent unpublish                              # Remove from catalog
guild agent update [identifier]                    # Update agent metadata
guild agent workspaces [agent-id]                  # List workspaces using an agent
guild agent categories                             # List agent categories and their allowed tags
guild agent tags list|add|remove|set               # Manage agent tags
guild agent init ... --tags code-review,testing    # Optional tags at creation (must be allowed by the category)
guild agent fork ... --tags code-review            # Optional tags on fork (default: source tags when category inherited)
guild agent revalidate                             # Re-run validation
guild agent code [agent-id]                        # View agent source
guild agent grep <pattern>                         # Search agent code files for a regex pattern
guild agent grep <pattern> --published             # Search only published agents
guild agent owners                                 # List accounts that can own agents
guild workspace select                             # Set default workspace (writes to guild.json if in agent dir)
```

### Environment Variable Overrides

```bash
GUILD_WORKSPACE_ID=<id> guild agent test           # Override workspace for this command
```

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

### guild.json Accidentally Tracked

If `guild.json` is tracked in git (it shouldn't be):

```bash
echo "guild.json" >> .gitignore
git rm --cached guild.json
git add .gitignore   # -A only commits tracked files; add new/updated .gitignore explicitly
guild agent save -A --message "fix: Add guild.json to gitignore"
```
