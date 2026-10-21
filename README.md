# DevPulse

A self-hosted MCP server that connects Alexa+ to your GitHub CI pipelines, pull requests, and test results. Ask what broke. Get a real answer — by voice, in under 5 seconds.

Built on [MCP spec 2025-11-25](https://spec.modelcontextprotocol.io/specification/2025-11-25/) Streamable HTTP transport. Uses AWS Bedrock to convert raw GitHub API data into spoken responses tuned for Alexa+.

---

## What it does

DevPulse exposes five tools to Alexa+:

| Tool | Ask Alexa... |
|---|---|
| `get_test_results` | "What failed in my last test run on acme/backend?" |
| `get_ci_status` | "What's the CI status for my backend repo?" |
| `get_pr_summary` | "What pull requests are waiting for review?" |
| `get_repo_activity` | "What happened in acme/backend this week?" |
| `trace_breakage` | "Who broke main?" |

The last tool — `trace_breakage` — is DevPulse's most distinctive feature. When CI goes red, it finds the exact transition from green to red, lists every commit that landed between the last passing run and the first failing run, cross-references changed files against what failed, and asks Bedrock to reason about the most likely culprit. It answers the question every engineer asks — "who broke this and why?" — without opening a single tab.

---

## Requirements

- Python 3.12 or later
- A GitHub personal access token with `repo` and `actions` read scopes
  — create one at https://github.com/settings/tokens
- AWS credentials with `bedrock:InvokeModel` permission on the Bedrock model below
  — the IAM policy needed is shown in the Configuration section
- A publicly reachable HTTPS URL for Alexa+ to call your server
  — use [ngrok](https://ngrok.com) for local development

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/devpulse
cd devpulse
```

### 2. Install Python dependencies

```bash
cd server
pip install -r requirements.txt
```

### 3. Configure credentials

```bash
cp .env.example .env
```

Open `.env` and fill in the four required values:

```
GITHUB_TOKEN=ghp_your_token_here
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_REGION=us-east-1
```

The optional values (`BEDROCK_MODEL_ID`, `HOST`, `PORT`, `LOG_LEVEL`) have working defaults and do not need to be changed for a basic setup.

### 4. Start the server

```bash
python main.py
```

The server starts on `http://0.0.0.0:8000`. You should see:

```
DevPulse MCP server starting on 0.0.0.0:8000
```

### 5. Verify the MCP handshake

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-11-25",
      "clientInfo": { "name": "curl-test", "version": "1.0" }
    }
  }'
```

Expected response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "serverInfo": { "name": "devpulse", "version": "1.0.0" },
    "capabilities": { "tools": {} }
  }
}
```

### 6. List available tools

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

---

## Docker

```bash
# Build the image
docker build -t devpulse .

# Run with your .env file
docker run --env-file .env -p 8000:8000 devpulse
```

---

## Connecting to Alexa+

1. Deploy DevPulse to a publicly reachable HTTPS endpoint.
   For local testing, run `ngrok http 8000` and copy the `https://` URL it gives you.

2. In the [Alexa Developer Console](https://developer.amazon.com/alexa), create a new integration.
   Choose the MCP option and paste your server URL followed by `/mcp` as the endpoint.

3. DevPulse handles the MCP `initialize` handshake and tool registration automatically.
   Alexa+ will discover all five tools from the `tools/list` response.

4. Test with: *"Alexa, ask DevPulse who broke main on acme/backend."*

---

## AWS IAM policy

The minimum IAM policy to attach to your AWS credentials:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-3-5-haiku-20241022-v1:0"
    }
  ]
}
```

Replace the region and model ID if you change `BEDROCK_MODEL_ID` in your `.env`.

---

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | Yes | — | GitHub personal access token |
| `AWS_ACCESS_KEY_ID` | Yes | — | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Yes | — | AWS secret key |
| `AWS_REGION` | No | `us-east-1` | AWS region for Bedrock |
| `BEDROCK_MODEL_ID` | No | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | Bedrock model to use |
| `HOST` | No | `0.0.0.0` | Server bind address |
| `PORT` | No | `8000` | Server port |
| `LOG_LEVEL` | No | `info` | Logging level: debug, info, warning, error |

---

## Running without AWS credentials

If `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` are not set, DevPulse logs a warning on startup and falls back to returning the first three lines of the raw GitHub API data. This is useful for local development — all tools still work, but responses are not voice-optimized.

---

## Project structure

```
devpulse/
├── server/
│   ├── main.py              # FastAPI app + HTTP entry point
│   ├── mcp_server.py        # MCP protocol dispatch layer
│   ├── config.py            # Environment variable configuration
│   ├── requirements.txt     # Python dependencies
│   └── tools/
│       ├── __init__.py
│       ├── github_tools.py  # GitHub API: CI, PRs, tests, activity
│       ├── bedrock_ai.py    # AWS Bedrock voice summarization
│       └── trace_breakage.py  # "Who broke main?" commit tracer
├── .env.example             # Credential template
├── .gitignore
├── Dockerfile
├── LICENSE                  # MIT
└── README.md
```

---

## MCP protocol details

DevPulse implements [MCP 2025-11-25](https://spec.modelcontextprotocol.io/specification/2025-11-25/) Streamable HTTP transport.

- **Endpoint**: `POST /mcp`
- **Protocol**: JSON-RPC 2.0 (single messages and batch arrays)
- **Response formats**: `application/json` or `text/event-stream` (SSE), chosen by the `Accept` header
- **Session management**: `Mcp-Session-Id` response header
- **Supported methods**: `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`

---

## Mini challenges (hackathon)

This project qualifies for:

- **AWS Builder**: Uses `bedrock:InvokeModel` via the `boto3` client in `server/tools/bedrock_ai.py`
- **Open Source**: Licensed MIT — see `LICENSE`

---

## License

MIT — see [LICENSE](LICENSE).
