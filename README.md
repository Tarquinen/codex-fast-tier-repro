# Codex Fast Service Tier Throughput Reproduction

This repository provides a minimal, zero-dependency reproduction script demonstrating how OpenAI's ChatGPT Codex backend (`https://chatgpt.com/backend-api/codex/responses`) gates **Fast Mode (`service_tier: "priority"`)** throughput behind specific client identification headers.

Related Issue: [anomalyco/opencode#39864](https://github.com/anomalyco/opencode/issues/39864)

---

## Summary of Findings

When sending requests to `https://chatgpt.com/backend-api/codex/responses` with `service_tier: "priority"`:

- **Codex CLI / TUI Headers** (`originator: "codex-tui"` + `User-Agent: codex-tui/*`):
  - Throughput: **~85+ tok/s** (Fast Tier worker pool)
  - TTFT: **~1.3s**
- **OpenCode Headers** (`originator: "opencode"` + `User-Agent: opencode/*`):
  - Throughput: **~56 tok/s** (Standard Tier fallback pool)
  - TTFT: **~5.5s**

The backend accepts `service_tier: "priority"` without returning an error in both cases, but silently routes requests with non-Codex client headers to standard-speed instances.

---

## What Was Ruled Out (Ablation Findings)

Through systematic single-variable ablation testing against live endpoints, the following differences between OpenCode and Codex were **ruled out** as causes of the throughput drop:

| Tested Element | OpenCode Format | Codex Format | Impact on Speed |
| :--- | :--- | :--- | :--- |
| **System Prompts** | Top-level `instructions` string (tested up to 29 KB) | Developer messages embedded in `input` array | **None** (Full 85 tok/s with Codex headers) |
| **Tool Definitions** | Top-level `tools` array | Embedded tool declarations | **None** (Full 85 tok/s with Codex headers) |
| **Reasoning Config** | `{"effort": "high", "summary": "auto"}` | `{"effort": "medium", "context": "all_turns"}` | **None** (Full 85 tok/s with Codex headers) |
| **Session Cache Key** | `prompt_cache_key: "ses_..."` | UUID format | **None** (Full 85 tok/s with Codex headers) |
| **Client Metadata** | Omitted | Sent in body (`client_metadata`) | **None** (Omitting does not drop speed) |
| **Turn / Window Headers** | Omitted | `x-codex-turn-metadata`, `x-codex-window-id` | **None** (Omitting does not drop speed) |
| **Routing Hint Header** | Omitted | `x-codex-routing-hint: model=...;tier=...` | **None** (Optional, does not gate routing) |
| **Cloudflare Cookies** | Omitted | Sent | **None** (Omitting does not drop speed) |

### The Actual Gating Mechanism
The **only** requirements to unlock the fast worker pool (~85+ tok/s) are:
1. `originator: "codex-tui"`
2. `User-Agent` prefix matching `codex-tui/` (e.g. `codex-tui/0.149.1 (OpenCode)`)

---

## Prerequisites

- Python 3.8+
- `curl` installed and available in `PATH`
- A ChatGPT Pro / Team / Enterprise OAuth access token (from Codex CLI or OpenCode)

---

## Quick Start

### 1. Configure Credentials

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Set your ChatGPT OAuth access token and Account ID in `.env`:

```bash
# Your Bearer JWT token (starts with eyJ...)
OPENAI_ACCESS_TOKEN="eyJhbGciOiJSUzI1NiIs..."

# Your ChatGPT Account ID (UUID)
CHATGPT_ACCOUNT_ID="00000000-0000-0000-0000-000000000000"
```

> **Tip:** You can obtain these from:
> - Codex CLI auth store: `~/.codex/auth.json`
> - OpenCode auth store: `~/.local/share/opencode/auth.json`
> - Or using `codex` / `mitmproxy` / browser network inspector.

### 2. Run the Benchmark

```bash
python3 benchmark.py
```

---

## What the Benchmark Tests

The benchmark issues requests asking for integers `1` to `300` (~603 output tokens), testing 4 conditions:

1. **Standard Baseline**: Codex headers without `service_tier` (~56 tok/s).
2. **OpenCode Fast Shape**: `originator: "opencode"` + `User-Agent: opencode/...` + `service_tier: "priority"`.
3. **Codex Fast Shape**: `originator: "codex-tui"` + `User-Agent: codex-tui/...` + `service_tier: "priority"`.
4. **Header Isolation**: `originator: "codex-tui"` only vs `User-Agent: codex-tui/...` only.

---

## Expected Output

```text
========================================================================================
Test Scenario                                      | TTFT   | Stream Time | Stream TPS | Speedup
========================================================================================
1. Standard Baseline (no service_tier)             | 1.45s  | 10.65s      | 56.6 tok/s | 1.00x
2. OpenCode Fast (originator: opencode)            | 5.76s  | 10.59s      | 57.0 tok/s | 1.01x
3. Codex Fast (originator & UA: codex-tui)         | 1.38s  |  7.11s      | 84.8 tok/s | 1.50x
========================================================================================
```
