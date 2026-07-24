---
name: guild-integrations
description: Build custom integrations that connect external APIs to Guild agents. Activated when user mentions custom integrations, OpenAPI specs, connecting APIs via CLI, guild integration commands, or building service tools for agents.
---

# Guild Integrations

Connect external APIs to Guild agents. Integrations are defined by an OpenAPI spec, managed entirely through the CLI, and require no code deployment — tools are generated automatically.

## When to Use This

Activate when user:

- Mentions `guild integration` commands
- Wants to connect an external API to agents
- Has an OpenAPI spec they want to use
- Asks about building custom service tools
- Mentions integration versions, operations, or publishing
- Wants to configure auth (API key or OAuth) for an integration

## Lifecycle Overview

```
Create integration → Create version → Add operations → Build → Publish → Connect → Use in agent
```

Each step is a CLI command. The integration exists in Guild's backend — no local files or npm packages to manage.

## CLI Commands

### Create an Integration

```bash
guild integration create <name> \
  --description "What this integration does" \
  --base-url "https://api.example.com" \
  --auth-scheme api-key \
  --header-template "Authorization: Bearer {token}"
```

**Auth schemes (pick one):**

**API Key** (simpler, most common):

```bash
guild integration create my-service \
  --auth-scheme api-key \
  --header-template "X-API-Key: {token}"
```

The `{token}` placeholder is replaced with the user's stored credential at runtime. Common header templates:

- `Authorization: Bearer {token}` — Bearer token
- `X-API-Key: {token}` — Custom header
- `Authorization: Token {token}` — Token prefix

**OAuth 2.0:**

```bash
guild integration create my-service \
  --auth-scheme oauth \
  --install-url "https://provider.com/oauth/authorize" \
  --token-url "https://provider.com/oauth/token" \
  --client-id "your-client-id" \
  --client-secret "your-client-secret" \
  --scopes "read,write"
```

**Other options:**

- `--owner <org-id>` — Assign to an organization (default: your user account)
- `--public` — Make visible to all users

### Create a Version

```bash
guild integration version create <integration-id-or-name>
```

Creates a new draft version. All operation changes happen on a draft before building.

### Add Operations from OpenAPI

```bash
guild integration operation create <integration-id-or-name> <version-id> \
  --openapi spec.yaml
```

The OpenAPI spec defines what API endpoints become available as agent tools. Each operation in the spec becomes one tool.

### Build a Version

```bash
guild integration version build <integration-id-or-name> \
  --version-number 1.0.0
```

Assigns a semver version number and triggers validation. The CLI polls until validation completes. Exit code 0 means passed, 1 means failed.

Use `--no-wait` to return immediately without polling.

### Publish a Version

```bash
guild integration version publish <integration-id-or-name> \
  --version 1.0.0
```

Makes the version available to agents. Only versions that passed validation can be published.

### Connect Credentials

```bash
guild integration connect <integration-id-or-name>
```

Configures credentials (API key or OAuth tokens) so agents can make authenticated requests.

### Test a Version

```bash
guild integration version test <integration-id-or-name> \
  --version 1.0.0
```

### List and Inspect

```bash
guild integration list                           # List all integrations
guild integration get <id-or-name>               # Get integration details
guild integration version list <id-or-name>      # List versions
guild integration operation list <id-or-name>    # List operations (tools)
guild integration operation list <id-or-name> --json  # Full schemas in JSON
```

## OpenAPI Spec Requirements

The parser accepts OpenAPI 3.0 YAML specs. Each qualifying HTTP operation becomes a tool.

### Supported HTTP Methods

GET, POST, PUT, PATCH, DELETE.

### What Gets Extracted

For each operation:

| OpenAPI field                       | Maps to                          |
| ----------------------------------- | -------------------------------- |
| `operationId`                       | Tool name (snake_case)           |
| `summary`                           | Tool description (max 255 chars) |
| Path parameters                     | `input_path_type` schema         |
| Query parameters                    | `input_query_type` schema        |
| Request body (`application/json`)   | `input_body_type` schema         |
| Response body (200/201/204/default) | `output_body_type` schema        |
| `components/schemas`                | Reusable type references         |

### What Gets Skipped

- Operations without `application/json` request body (if body is required)
- Operations without a parseable response schema
- Non-JSON content types (file uploads, XML, etc.)

### Best Practices

- Always set `operationId` — otherwise one is synthesized from method+path
- Define response schemas in `components/schemas` and reference with `$ref` — bare `type: object` produces untyped output
- Keep `summary` under 255 characters
- Use `description` for longer explanations

### Example Spec

```yaml
openapi: 3.0.3
info:
  title: Acme API
  version: 1.0.0
servers:
  - url: https://api.acme.com/v1

paths:
  /tickets:
    get:
      operationId: list_tickets
      summary: List all tickets
      parameters:
        - name: status
          in: query
          schema:
            type: string
            enum: [open, closed, pending]
      responses:
        '200':
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/TicketListResponse'

  /tickets/{ticket_id}:
    get:
      operationId: get_ticket
      summary: Get a ticket by ID
      parameters:
        - name: ticket_id
          in: path
          required: true
          schema:
            type: integer
      responses:
        '200':
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Ticket'

components:
  schemas:
    Ticket:
      type: object
      properties:
        id:
          type: integer
        subject:
          type: string
        status:
          type: string
          enum: [open, closed, pending]
    TicketListResponse:
      type: object
      properties:
        tickets:
          type: array
          items:
            $ref: '#/components/schemas/Ticket'
        total:
          type: integer
```

## Using Integrations in an Agent

After publishing, the integration's tools are available as an npm package:

```typescript
import { AcmeTools } from '@guildai-services/owner~acme';
```

Where `owner` is the integration owner's username or org, and `acme` is the integration name.

> Skills are different from integrations. A skill such as `acme~brand-voice` does not produce a package like `@guildai-services/acme~brand-voice`. To let an agent use skills, import `skillsTools` from `@guildai/agents-sdk` once. Keep skills knowledge-only; if the behavior needs API access, executable code, credentials, or a dedicated tool set, build an integration or agent instead.

### Selecting Specific Tools

Use `pick()` to select only the tools your agent needs:

```typescript
import { pick } from '@guildai/agents-sdk';
import { AcmeTools } from '@guildai-services/owner~acme';

const tools = pick(AcmeTools, ['acme_list_tickets', 'acme_get_ticket']);
```

Tool names follow the pattern `{integration}_{operation_id}`.

### Requesting Credentials

Agents request credentials at runtime via the SDK. The runtime injects stored credentials server-side — agents never see raw API keys or tokens.

### Agent package.json

Add the integration as a dependency:

```json
{
  "dependencies": {
    "@guildai-services/owner~acme": "*"
  }
}
```

## Webhooks

Mark operations as webhook-triggered by adding `x-guild-hook: true` in the OpenAPI spec:

```yaml
paths:
  /tickets/webhook:
    post:
      operationId: receive_ticket_event
      x-guild-hook: true
      summary: Receive ticket webhook events
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/TicketEvent'
      responses:
        '200':
          content:
            application/json:
              schema:
                type: object
```

Webhook operations deliver responses asynchronously via callback instead of synchronous HTTP response.

## Troubleshooting

### Validation Failed

After `guild integration version build`, check the validation status:

```bash
guild integration version get <id-or-name> --version 1.0.0 --json
```

Common causes:

- Invalid OpenAPI spec syntax
- Missing required schema references (`$ref` pointing to undefined schemas)
- Operations with no parseable request/response content types

### Operations Not Appearing

Run `guild integration operation list <id-or-name> --json` to see what was parsed. Endpoints are skipped when:

- No `application/json` content type is available
- Response schema can't be extracted
- The operation has no `operationId` and path synthesis failed

### Auth Failures at Runtime

- **API Key:** Verify `guild integration connect` was run and the token is valid
- **OAuth:** Check that `install_url`, `token_url`, `client_id`, and `client_secret` are correct
- Re-run `guild integration connect` to refresh credentials

### Package Not Found in Agent

- Verify the version is published: `guild integration version list <id-or-name>`
- Check the import path matches: `@guildai-services/{owner}~{name}`
- Ensure the agent's `package.json` includes the dependency
