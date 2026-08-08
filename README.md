# AI Stack

AI Stack is a self-hosted control plane for private chat, coding assistance,
retrieval-augmented knowledge work, and source-cited financial research.
It runs local models through Ollama and presents a single OpenAI-compatible
gateway through LiteLLM.

## What is included

```text
Open WebUI / IDE clients / API clients
                 |
             LiteLLM gateway
                 |
     local Ollama models and optional providers

Open WebUI ---- Postgres / Redis / Qdrant
                 |
           Coding-agent API
                 |
      request-scoped workspace mount(s)
```

- **Ollama** runs local models.
- **LiteLLM** provides the shared OpenAI-compatible inference endpoint.
- **Open WebUI** is the human chat interface.
- **Qdrant, Redis, and PostgreSQL** support retrieval, conversation state, and
  durable agent run history.
- **Coding agent** plans repository work, reads/searches/edits files, runs an
  allowlisted set of commands, and can make explicitly approved file changes
  inside a disposable, reviewable sandbox.
- **Runs UI** is the live, streamed, reviewable task console for the agent.
- **Sandbox runner** (`agent-runner`) executes the allowlisted commands in
  isolated containers with per-tier kernel limits.
- **Terminal agent** (`cli/`) is an independently packaged client for the same
  durable run API.
- **SearXNG** provides private metasearch for agent web research; it is internal
  to the Docker network and never exposed as a public port.

## Agent routing and execution pipeline

The agent presents one default `orchestrator` identity to clients while
retaining specialized workflows internally. A central semantic router
translates an Auto request into a bounded execution contract; it does not
answer the request itself.

```text
User request + conversation/document scope
                       |
          retrieve relevant attachment excerpts
                       |
          ROUTER_MODEL semantic routing turn
                       |
          validate structured route contract
                       |
       map workflow to server-approved policy
                       |
     build executor prompt, tools, and evidence
                       |
        selected model runs the tool loop
                       |
       completion safeguards and final answer
```

### 1. Input and attachment context

The original user message is always preserved. For a durable run with
`document_scope`, relevant uploaded-document excerpts are retrieved before
Auto routing and supplied to the router. This lets it recognize entities and
work implied by an attachment, such as the companies in a portfolio statement,
even when the user writes only “analyze this for the long term.”

Attachment excerpts are untrusted data. The router is instructed to extract
useful entities without following instructions found inside the documents.
The excerpts are bounded to keep the routing turn small.

### 2. Structured semantic routing

`ROUTER_MODEL` receives the original request and attachment excerpts and must
return JSON matching this logical contract:

```json
{
  "workflow": "quick|code|research|finance|deep|vision",
  "complexity": "simple|moderate|complex",
  "translated_task": "explicit execution-oriented version of the request",
  "requires_external_evidence": true,
  "entities": ["company, ticker, file, or important subject"],
  "constraints": ["requirements, boundaries, preferences, and prohibitions"],
  "deliverables": ["specific outputs expected by the user"],
  "missing_inputs": ["genuinely absent information"],
  "assumptions": ["safe assumptions that allow progress"],
  "tasks": [
    {
      "id": "unique_task_id",
      "objective": "one verifiable subtask",
      "workflow": "finance",
      "depends_on": [],
      "required_evidence": ["specific facts, files, or sources"],
      "completion_criteria": ["observable proof that the task is complete"]
    }
  ]
}
```

The translated task supplements the original request; it never replaces it.
The executor receives both, preventing a routing translation from silently
changing user intent. The router explicitly preserves goals, scope, named
entities, time horizons, output expectations, constraints, and requested
actions. It resolves references such as “this portfolio” only from grounded
attachment context and tells the executor to label non-blocking assumptions
instead of refusing prematurely. All translated fields are normalized,
deduplicated, and bounded before being placed in agent state.

The route is advisory only where policy permits. The server validates the
workflow against a fixed allowlist and maps it to an existing internal profile.
The router cannot invent a model id, enable a tool, grant write access, expand
the workspace, or bypass sandbox approval.

Workflow classification uses a hybrid policy. The small model proposes a
workflow, while deterministic high-confidence rules override obvious Finance,
Code, Research, Vision, and Deep requests using the original message and
bounded grounded attachment text. This keeps nuanced semantic translation in
the model while preventing a weak classifier choice from sending a portfolio
to generic research or a repository review to the wrong tool policy. Per-task
workflow annotations are also constrained to combinations compatible with the
validated primary workflow.

### 3. Workflow and model policy

The validated workflow selects the executor prompt, model alias, tool policy,
completion limits, and evidence requirements:

| Workflow | Default model policy | Purpose and enforced behavior |
| --- | --- | --- |
| `quick` | `FAST_MODEL` | Simple, stable questions with minimal overhead |
| `code` | `DEFAULT_MODEL` | Repository inspection, tool use, tests, and reviewable edits |
| `research` | `RESEARCH_MODEL` | Current external research with source URLs |
| `finance` | `FINANCE_MODEL` | Current financial evidence, filings, news, and scenario analysis |
| `deep` | `reasoning` | Slower analysis of assumptions, alternatives, and trade-offs |
| `vision` | `vision` | Image-aware analysis |

Finance and Research always require external evidence, even when the request
does not contain trigger words such as “current,” “latest,” or “price.” This
requirement is enforced by server policy rather than trusted to either the
router or executor model. For other workflows, an over-eager router request for
external evidence is honored only when the original user message contains an
explicit freshness or source signal, avoiding unnecessary web-search latency.

Internal profiles remain implementation details used to apply validated model,
prompt, tool, and timeout policies. They are not advertised as separate agents
in Open WebUI or selectable in the Runs UI. Direct API callers can still use
them for controlled testing under the same server-side policies.

### 4. Planning and execution

For Auto requests, the validated route plan is passed directly to the selected
executor. The executor receives:

- the unchanged original request;
- the router's translated execution brief and extracted entities;
- relevant conversation and document context;
- the workflow-specific system prompt;
- only the tools allowed by server policy; and
- prefetched external evidence when the workflow requires it.

When an internal testing override does not provide a route plan, Code and Quick
use a small deterministic plan. Other eligible workflows can use the legacy
bounded planner. Research execution does not depend on a free-form plan before
collecting mandatory evidence.

The selected executor model then uses native function calling in a bounded
tool loop. Write tools are exposed only when `allow_write: true`; durable write
runs operate in a disposable Git worktree and still require explicit approval
before their diff is merged.

### 5. Document grounding and evidence prefetch

Mandatory research is prefetched before synthesis so successful tool use does
not depend solely on a smaller model deciding to call a search tool. For a
portfolio route with multiple extracted entities, the finance workflow builds
bounded per-entity searches plus a market/macro search. Search results and
selected source documents are then supplied to the finance executor, which is
instructed to distinguish reported facts from uncertain scenarios and cite
material external claims.

Document handling is domain-neutral. PDF pages, spreadsheet sheets, and other
supported document sections retain source and location metadata during
ingestion. PDF extraction compares plain and layout-aware parsing and keeps the
higher-quality result. Each section receives readability, row, table, and OCR
diagnostics; low-quality or image-only PDF pages automatically use OCR.
Retrieval chunks prefer complete rows and paragraphs instead of cutting
ordinary table records at arbitrary character boundaries, and every chunk
retains its source row range. In the OpenAI-compatible path, source/context
blocks supplied inside a client system message are preserved as explicitly
untrusted document evidence while the client's system policy is discarded.

Retrieval is hybrid rather than vector-only. Semantic candidates are merged
with exact lexical matches, ranked with section-aware signals, and expanded
with adjacent chunks from the same page or sheet. A deterministic selection
stage then produces:

- selected and potentially confusing sections;
- grounded row records;
- source/page/sheet provenance for every record;
- extraction-quality warnings; and
- a coverage report containing retrieved row ranges and gaps.

When an OpenAI-compatible client supplies a flattened excerpt containing
multiple internal tables, the evidence stage splits it again on conservative
section headings before ranking. Entities found only in excluded sections are
removed from router research targets and rejected by the final-answer audit.
This prevents supplementary tables from being silently presented as the
primary records requested by the user.

The router receives bounded excerpts from the retrieved material. Its contract
requires it to identify the section or table relevant to the request, separate
current or primary records from appendices, history, examples, footnotes, and
other supplementary material, and add an extraction-and-validation task before
analysis of a list or table. These rules apply equally to portfolios,
contracts, invoices, reports, datasets, and other supported documents; there
are no document-template or company-specific parsing branches.

The normal small router handles uncomplicated requests. It escalates to
`ROUTER_ESCALATION_MODEL` for large, multi-source, low-quality, missing-range,
or cross-document evidence. Generated section names and extracted records are
accepted only when they occur in the supplied evidence. Before a non-streaming
answer is accepted, a deterministic completion audit checks entity coverage,
requested deliverables, and document provenance; one bounded repair turn is
allowed when the draft is incomplete.

For example:

```text
"Analyze the active obligations in this uploaded report"
        |
retrieval preserves section headings and complete table rows
        |
router selects the relevant table and plans row validation
        |
the selected expert gathers any required external evidence
        |
the executor produces the requested grounded analysis
```

The pipeline provides research assistance, not trade execution or personalized
investment advice.

Embedding input is independently bounded with `OLLAMA_EMBED_MAX_CHARS`
(default `6000`) so long answers fit the local embedding model's physical
batch. Conversation-memory persistence is best effort: an embedding or vector
store failure is logged but cannot turn an already completed answer into an
HTTP 500 response.

### 6. Validation and fallback behavior

If the routing model fails, times out, produces incomplete JSON, chooses an
unknown workflow, or omits a usable translated task, the request is not lost.
A deterministic keyword fallback selects a conservative workflow and preserves
the original message as the execution task. Finance and Research evidence
requirements are reapplied after fallback.

Durable runs emit a `route_selected` event containing the chosen workflow,
executor model, evidence requirement, and whether the decision came from the
model or fallback. This makes routing behavior visible in the run event log
without exposing hidden model reasoning.

### 7. Router configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ROUTER_MODEL` | `quick` | LiteLLM model alias used for the routing-only turn |
| `ROUTER_ESCALATION_MODEL` | `DEFAULT_MODEL` | Stronger planner for complex or low-confidence document routes |
| `ROUTER_MAX_COMPLETION_TOKENS` | `1024` | Maximum router/planner response size |
| `ROUTER_TIMEOUT_SECONDS` | `120` | Timeout for the normal low-latency routing model |
| `ROUTER_ESCALATION_TIMEOUT_SECONDS` | `240` | Timeout for the stronger planner on complex evidence |
| `FAST_MODEL` | `quick` | Executor used by Quick |
| `DEFAULT_MODEL` | `qwen3-8b` in Compose | Executor used by Code |
| `RESEARCH_MODEL` | `quick` | Executor used by Research |
| `FINANCE_MODEL` | `coder` in Compose | Executor used by Finance |

Changing `ROUTER_MODEL` changes only request interpretation and routing. It
does not change the model chosen for execution unless the resulting workflow
changes. Keep the router deterministic (`temperature=0`), fast, and capable of
reliable JSON generation.

The default `quick` alias is Qwen3 4B Instruct in this stack. It is deliberately
small because routing is a narrow translation task; executor models retain the
expensive domain reasoning. Router quality should be evaluated primarily on
intent preservation, entity extraction, constraint/deliverable recall,
workflow accuracy, valid JSON rate, and downstream task completion—not on its
ability to answer domain questions.

### 8. Planner task graph

The router is also the query-level planner. It decomposes a translated request
into one to four useful tasks. Tasks may recommend different internal
workflows, declare dependencies, identify required evidence, and define
observable completion criteria. Multi-step plans should end with a synthesis
task rather than a generic “finish” step.

The server normalizes task ids and rejects the entire model-generated graph
when it contains duplicate ids, unknown dependencies, self-dependencies,
cycles, invalid workflows, empty objectives, or more than four tasks. A
deterministic single-task graph then preserves forward progress. This is
preferable to executing a partially corrupted plan.

The planner also distinguishes:

- `missing_inputs`: facts genuinely absent from the request or attachments;
- `assumptions`: safe, explicit defaults that allow useful work to continue;
- `constraints`: boundaries the executor must obey; and
- `deliverables`: outputs that completion must cover.

The validated graph is included in the expert execution brief and summarized
in the `route_selected` event with complexity, task count, and participating
workflow types. Its completion criteria are tracked against successful tool
observations. A write request cannot be reported complete without an observed
mutation, requested verification must have succeeded, and failed tool
categories must be recovered before the final answer is accepted.

The executor follows an explicit, bounded state graph:

```text
analyzing -> ready -> implementing -> verifying -> reviewing -> complete
                           ^              |
                           |              v
                           +---------- repairing
```

Every transition is emitted as a durable event and included in evaluation
replays. Invalid transitions are refused, repair and re-planning are bounded,
and terminal outcomes distinguish complete, partial, and rejected runs. After
repeated grounded failures, the executor can make one configurable escalation
to `AGENT_REASONING_MODEL`, passing a compact evidence handoff instead of the
entire tool transcript.

Before a write-run diff is exposed for approval, deterministic review checks
that its paths and implementation evidence match the active requirement and
that the final response does not claim unobserved mutations or passing tests.
Broad or security-sensitive accepted diffs receive an additional independent
review from `CHANGE_REVIEW_MODEL`; a model review may reject a change but can
never override a deterministic rejection.

At present, one primary expert model executes the complete graph in a single
tool loop, with bounded stronger-model escalation when recovery requires it.
Per-task workflow annotations establish the contract for multi-expert
dispatch: for complex or multi-workflow auto-routed requests the executor
launches several bounded expert analyses (architecture, implementation,
verification, risk) in parallel before the tool loop and merges their
structured JSON findings into the evidence ledger. Ordinary Code/Quick and
read-only runs stay on the single fast loop with no extra model calls.

Every tunable value is controlled from `.env` — the single source of truth
for the stack. Copy the tracked template (`.env.example`) and edit values
there; `docker-compose.yaml` forwards every variable into the containers with
defaults that match `agents/app/core/config.py`, so no other file needs
touching for routine tuning. The runtime model-loop defaults look like:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_REASONING_MODEL` | `qwen3-14b` | Stronger tool-capable model used after bounded execution failures |
| `AGENT_MODEL_ESCALATIONS` | `3` | Maximum stronger-model handoffs in one run |
| `CHANGE_REVIEW_MODEL` | `reasoning` | Independent reviewer for high-risk accepted diffs |
| `CHANGE_REVIEW_MODEL_ENABLED` | `true` | Enables risk-based independent diff review |
| `EXPERT_DISPATCH_ENABLED` | `true` | Enables parallel expert analyses for complex auto-routed requests |
| `EXPERT_DISPATCH_MODEL` | `qwen3-14b` | Model used for each bounded expert analysis |
| `MAX_PARALLEL_EXPERTS` | `4` | Maximum experts dispatched in one run |
| `EXPERT_MAX_COMPLETION_TOKENS` | `1024` | Per-expert output budget |
| `EXPERT_DISPATCH_TIMEOUT_SECONDS` | `600` | Per-expert completion timeout |
| `EXPERT_FINDINGS_CONTEXT_CHARS` | `2400` | Model-visible budget for merged findings in task context |

## Quick start

1. Copy and configure the environment file:

   ```bash
   cp .env.example .env
   ```

   Generate long random values for all credentials. The workspace is defined by
   three `.env` variables:
   - `WORKSPACE_PATH` — host directory mounted into the container at
     `/workspace`. Use the smallest directory that contains the repositories
     the agent may inspect or edit.
   - `WORKSPACE_DIR` — in-container allowed root (default `/workspace`); the
     agent never escapes this boundary and cannot reach sibling mounts.
   - `DEFAULT_WORKSPACE_DIR` — repository selected when the client does not
     explicitly choose a workspace (an in-container path under `WORKSPACE_DIR`).
   The Runs UI also lets you select a narrower repository, and clients
   automatically narrow to a valid path explicitly named in the prompt,
   preventing repository reviews from scanning unrelated sibling directories
   when a project path is supplied.
   If the agent must see more than one directory, mount each one as its own
   volume in `docker-compose.yaml` and list the in-container paths in
   `WORKSPACE_ROOTS` (comma-separated).

2. Build the two local images and start the stack:

   ```bash
   ./build.sh
   docker compose up -d
   ./download_models.sh
   ```

   The agent image must include `git` -- it is required both for the
   `run_command` tool's `git` operations and for sandboxed write-enabled runs.

3. Open Open WebUI at `http://localhost:3000`. The gateway, vector database,
   Ollama, pipelines service, and agent bind to `127.0.0.1` by default. Put an
   authenticated reverse proxy in front of them if remote access is required.

### Releasing agent changes

Use the targeted release script after changing the agent, tools, or CLI:

```bash
./scripts/release.sh
```

It validates Compose, builds the new image before downtime, runs the complete
agent/CLI suite, removes only the old `agents` and `agent-runner` containers,
recreates them, waits for API health, verifies both containers use the new
image, and removes the superseded image. Databases, Ollama, LiteLLM, Qdrant,
Redis, SearXNG, Open WebUI, and the Runs UI are not stopped. If startup or
health verification fails, the script retags and redeploys the previously
running agent image automatically.

Use `--no-cache` for a clean Docker build, `--skip-tests` only when tests were
already run against the exact checkout, `--keep-old-image` to retain the prior
image, or `--timeout SECONDS` on slower hosts. Run `./scripts/release.sh --help`
for the complete option list.

## Using the services

| Service | Local address | Purpose |
| --- | --- | --- |
| Open WebUI | `http://localhost:3000` | General chat and knowledge workflows |
| LiteLLM | `http://localhost:4000/v1` | Central OpenAI-compatible model API |
| Agent | `http://localhost:8000` | Repository-aware coding workflow |
| Runs UI | `http://localhost:3001` | Live, reviewable coding-task console |
| Ollama | `http://localhost:11434` | Local inference runtime |
| Qdrant | `http://localhost:6333` | Vector store |

Use LiteLLM from IDE tools that support an OpenAI-compatible endpoint only for
direct model chat. Those aliases do **not** invoke the planner, tools,
completion audit, durable run store, or review sandbox. For autonomous
repository work, point the client at the Agent's OpenAI-compatible
`http://localhost:8000/v1` endpoint and select `orchestrator`, or use the Runs
UI. LiteLLM's `quick` and `coder` aliases remain useful for non-agent chat;
`reasoning` and `vision` are deliberate heavyweight choices, and `embedding`
is only for embeddings.

When an IDE supplies its own tools to a direct LiteLLM model, the gateway adds
a continuation policy. For code-change requests that have started using IDE
tools, LiteLLM deterministically requires another tool call until a verification
command succeeds; the rule is capped at eight tool turns to prevent an infinite
loop. Shell state is treated as non-persistent, and missing coverage support
falls back to environment inspection or plain tests rather than unsolicited
installation. This improves compatibility but cannot provide the complete
server-side evidence and sandbox contract of `orchestrator`; use the Agent
endpoint or Runs UI when that guarantee is required.

The agent requires `Authorization: Bearer $AGENT_API_KEY` on every endpoint
except `/health` and the read-only `/models/available` catalog. Tool calls are made through the model's native function
calling rather than hand-written JSON, and the tools available to it are:
`tree`, `list_files`, `read_file`, `find_file`, `search_text`, `search_code`,
`project_summary`, `inspect_files`, `inspect_code`, `analyze_task_context`,
`inspect_test_environment`, `edit_file`, `write_file`, `apply_patch`,
`run_command`, `run_tests`, `git_status`, `git_diff`, `git_log`, `git_blame`,
`web_search`, `web_fetch`, and `workspace_root`. `edit_file`, `write_file`,
`apply_patch`, `run_command`, and `run_tests` are
only exposed when a request explicitly sets `allow_write: true`; it is `false`
by default.

### Single-response requests

`POST /chat` and `POST /execute` block until the agent finishes and return one
answer. If `allow_write` is set on these endpoints, edits are applied directly
to the request's workspace with no sandboxing or review step -- use them for
read-only analysis, or for writes you're comfortable applying immediately.

### Durable, streamed, reviewable runs

`POST /runs` (requires `POSTGRES_URL` to be configured) starts a run in the
background and returns a `run_id` immediately:

- `GET /runs/{run_id}` -- current status and, once finished, the answer.
- `GET /runs/{run_id}/events` -- a Server-Sent Events stream of every step,
  tool call, tool result, and (for write-enabled runs) the generated diff, as
  they happen.
- `POST /runs/{run_id}/approve` -- for a write-enabled run, applies the
  reviewed diff to the real repository.
- `POST /runs/{run_id}/discard` -- discards a write-enabled run's sandbox
  without merging it.

Write-enabled `/runs` requests execute inside a disposable Git worktree
(`SANDBOX_ROOT`) rather than the real repository. The run moves to
`awaiting_approval` with a diff attached once investigation and edits are
complete; nothing touches your actual files until you call `/approve`. This
is the safer path for write-enabled work -- prefer it over `allow_write` on
`/chat`/`/execute` when you want a chance to review changes first.

### Live Runs UI

The main React/TypeScript task UI is at `http://localhost:3001`. Enter
the agent API key for the current browser tab, submit a task, and it will show
planning, tool activity, streamed model output, and any reviewable diff. The
UI uses the same authenticated `/runs` API; it does not store the key in local
storage or send it to any third party.

### Terminal agent

`cli/` is an independently packaged terminal client for the same durable
`/runs` API.
The server remains the only planner and tool executor; the client streams
events, displays reviewable diffs, and sends explicit approve, discard, or
cancel actions. See the complete
[terminal-agent guide](cli/README.md) for command reference,
automation formats, workspace mapping, troubleshooting, and security details.

Current terminal capabilities include:

- an interactive shell plus one-shot commands and stdin-driven automation;
- persistent history, Tab completion, multiline prompts, and session status;
- read-only analysis and disposable, explicitly approved write sandboxes;
- foreground cancellation with a PostgreSQL-backed client lease, plus
  explicit detached runs;
- in-flight cancellation through planning, model streams, tool execution, and
  Git sandbox commands;
- stable private-runner job IDs, authenticated cancellation, owner leases, and
  process-group TERM/KILL escalation;
- durable event replay and bounded SSE reconnection without repeated output;
- run listing, inspection, resume, approve, discard, and cancellation;
- workspace-scoped continuation of the latest conversation;
- automatic host-to-container workspace mapping without a project-specific
  default directory;
- human-readable, JSON, and streaming JSON output with stable exit codes;
- a versioned OpenAPI protocol contract shared by the server, CLI, and Runs UI;
- replica-safe bounded lease sweeping with authenticated lifecycle metrics; and
- terminal-safe output with control-character sanitization, broken-pipe
  handling, and bounded SSE event buffers.

From the repository:

```bash
./cli/scripts/aistack doctor
./cli/scripts/aistack
./cli/scripts/aistack "review this repository and run its tests"
./cli/scripts/aistack run --allow-edits "fix the failing tests"
```

Install the launcher once to use it like other terminal agents:

```bash
./cli/scripts/install-aistack
aistack
```

The no-argument form opens an interactive shell. Use `/help` to see its
commands. Interactive change requests ask whether they may make sandboxed
edits, while read-only reviews start immediately;
`/resume RUN_ID` replays or continues monitoring a run, and `--continue`
reuses the latest conversation for the current workspace. CLI runs are
foreground-owned by default: interrupting or closing the client requests
cancellation, with a server-side lease covering abrupt client death. Use
`--detach` or `/detach` only when a run should survive terminal exit. An
edit-enabled run never applies its diff automatically: an interactive
terminal asks whether to approve, discard, or leave it pending. The same
actions are available non-interactively:

```bash
./cli/scripts/aistack list
./cli/scripts/aistack resume RUN_ID
./cli/scripts/aistack approve RUN_ID
./cli/scripts/aistack discard RUN_ID
./cli/scripts/aistack cancel RUN_ID
```

The wrapper reads `AGENT_API_KEY` from the repository `.env` as data without
executing that file. Override it with `AISTACK_API_KEY`, and override the
default local endpoint with `AISTACK_URL`. It maps the current host checkout
to an allowed in-container workspace; use `--workspace /workspace/repository`
or `--project NAME` when automatic mapping is ambiguous. The installer creates
`~/.local/bin/aistack` without overwriting an existing command. Remove only
that managed symlink with `./cli/scripts/install-aistack --uninstall`.
The old `scripts/aistack` and `scripts/install-aistack` paths remain as
compatibility shims.

For scripts and CI, `run --output json` emits the final run object and
`run --output stream-json` emits one durable event per line. Pending write
runs remain unapproved in non-interactive environments.

The Runs UI exposes one Central AI Agent. Every new task and follow-up uses the
server-side semantic routing pipeline; users do not choose a profile or
underlying model. Uploaded `.pdf`,
`.docx`, `.xlsx`, `.csv`, `.txt`, `.md`, and `.json` files are parsed
server-side, indexed in a conversation-specific retrieval scope, and returned
to the model with filename/page/sheet citations. Retrieval is reranked and
capped by `MEMORY_CONTEXT_TOKENS`; each file is limited by
`DOCUMENT_MAX_BYTES` (10 MB by default). Internally selected Research and
Finance workflows use current web evidence and do not inspect the mounted
repository unless the task asks for it. Source URLs found in an answer are
displayed below live output.

The Runs UI can save a workspace as a personal project. Projects and durable
run history are stored in PostgreSQL; selecting an old run replays its events,
answer, sources, and review diff. Conversation follow-ups retain the same
conversation id and document scope.

### Optional image generation

`vision` is for image analysis only. Image generation is not exposed as a
separate agent or Runs UI option. API clients may enable the standalone
`/images/generations` endpoint by running an Automatic1111/Forge-compatible
image server and setting its trusted local URL before recreating the agent
service:

```bash
IMAGE_GENERATION_URL=http://host.docker.internal:7860
docker compose up -d --force-recreate agents
```

The agent proxies only its administrator-configured URL to
`/sdapi/v1/txt2img`; callers cannot supply arbitrary backend URLs.

The UI proxies `/api` to the internal agent service, so browser SSE stays
same-origin while still sending the user-provided API key.

`/runs` is the API to use for a responsive coding UI: submitting the task does
not hold the HTTP request open, and its event stream reports planning, tool
calls/results, model output deltas, and a reviewable diff as they occur. The
OpenAI-compatible `/v1/chat/completions` endpoint also accepts `stream: true`
and buffers final prose until the completion audit accepts it, then forwards a
standard OpenAI SSE chunk. Tool activity is carried in SSE comments for
compatible clients to ignore safely.

### Keeping local runs fast and reliable

For the default CPU-oriented Code and Quick profiles, the agent uses a
deterministic local plan and skips vector-memory lookup/storage. This avoids
swapping Ollama from the chat model to the embedding model twice on every
routine coding question. Research, Finance, Deep, and custom profiles retain
scoped retrieval. Set `MEMORY_FOR_CODE_RUNS=true` only when repository RAG is
more valuable than that latency.

Model-generated answers are not written back into long-term retrieval by
default, preventing unsupported output from becoming future evidence. Set
`GENERATED_MEMORY_ENABLED=true` only when this feedback-loop tradeoff is
intentional; generated records are tagged and scoped to their workspace.

The executor compacts its transcript only when its estimated token usage
approaches `CONTEXT_COMPACT_THRESHOLD_TOKENS`, rather than after an arbitrary
number of tools. It stops after repeated empty searches or repeated failed
identical tool calls and synthesizes a partial answer from evidence already
collected. Directory scans and multi-file inspection are capped so a broad
workspace cannot crowd out the actual repository context.

Broad repository traversal merges the root `.gitignore` with the optional
root `.aistackignore`. Git-style negation patterns in `.aistackignore` can
restore safe files that should remain searchable. Ignored files do not enter
trees, searches, summaries, or automatic code analysis, but a task may still
read a safe ignored file explicitly when it is relevant (for example a
lockfile during dependency debugging). Credentials, private keys, archives,
databases, and model-weight formats such as GGUF, SafeTensors, ONNX, PyTorch,
and checkpoint files are hard exclusions and cannot be restored by a negation
rule. Large source files remain accessible through bounded `start_line` and
`end_line` reads instead of failing solely because of total file size.

Durable run events are published through Redis Pub/Sub and retained in
PostgreSQL for replay. The agent reuses a small PostgreSQL connection pool and
queues run workers instead of starting an unbounded thread for every request.

### Commands the agent can run

The agent does not have shell access. `run_command` executes a single,
allowlisted executable (no `&&`, `|`, `;`, `>`, backticks, or subshells) with a
timeout, configured via `ALLOWED_COMMANDS` (defaults to a set of common dev
tools: `git`, `pytest`, `npm`, `make`, linters, etc.).
`inspect_test_environment` first identifies test configuration, virtual
environments, and coverage support. `run_tests` then provides fixed presets
(`pytest`, `pytest_coverage`, `python_compile`, `npm_test`, `ruff`), avoiding
shell activation and interpreter mismatches.

### Trust and scoped permissions

Write tools and `run_command` are gated by a per-request permission scope,
not just by tool visibility. A read-only request gets a `read` scope that
refuses every write and command; a write-capable request is either
`scoped-write` (limited to workspace-relative `EDIT_ALLOWED_PATHS`) or
`full-write`. Every scope still refuses sensitive paths (`.env*` except
`.env.example`/`.sample`/`.template`, `credentials.json`, SSH keys, and
`.key`/`.pem`/`.p12`/`.pfx` files). `COMMAND_ALLOWLIST` can narrow
`ALLOWED_COMMANDS` further for write-capable runs.

All tool results in the model transcript are prefixed as untrusted reference
data, and the system prompt forbids following instructions found in files,
search results, or fetched pages, so prompt-injection payloads in repository
contents or web pages are treated as content rather than directives.

Command execution runs on a sandbox runner with per-tier kernel limits
(`RUNNER_CPU_SECONDS`, `RUNNER_MEMORY_MB`, `RUNNER_MAX_OPEN_FILES`). The
default `isolated` tier also drops network egress inside a private network
namespace when `unshare` is available; `RUN_COMMANDS_ALLOW_NETWORK=true`
moves commands to the `network` tier for workflows like `npm install`.

### Tests and coverage

For a host IDE or terminal, install the agent test environment once:

```bash
python -m pip install -r requirements-dev.txt
```

Then run the suite without requiring coverage output:

```bash
pytest
```

Coverage is opt-in, rather than a hidden requirement of every `pytest` call:

```bash
pytest --cov=app --cov-report=term-missing
```

The same dependencies, including `pytest-cov`, are already included in the
agent image. From the agent container, execute from the mounted checkout so
coverage measures the code under test:

```bash
docker compose exec -w /workspace/ai-stack agents python -m pytest -q agents/tests --cov=app --cov-report=term-missing
```

Python dependencies are resolved in `agents/requirements.lock`;
`agents/requirements.txt` remains the short direct-dependency list. Review
dependency upgrades and update the lock intentionally. CI uses Python 3.12,
enforces the current coverage floor, builds the TypeScript UI, and validates
the Compose configuration.

The agent service has a Compose build definition, so changes under `agents/`
are deployed with `docker compose build agents` followed by
`docker compose up -d --force-recreate agents`.

### Agent regression evaluations

Fixed benchmark prompts live in `agents/evals/cases.json`. Their schema is
validated in the normal test suite. Run them against a live local stack when
changing models, prompts, tools, or routing:

```bash
python agents/evals/run_evals.py --base http://127.0.0.1:8000 --key "$AGENT_API_KEY"
```

The checks catch known regressions; compare answers, tool traces, citations,
and latency before adopting a new model or prompt.

Evaluate the central router/planner separately inside an environment that has
the agent's LiteLLM settings:

```bash
cd agents
PYTHONPATH=. python evals/run_router_evals.py
```

The router suite checks workflow policy, intent/constraint preservation,
attachment grounding, external-evidence decisions, minimum useful task
decomposition, JSON validity, deterministic fallback avoidance, and resistance
to instructions embedded in retrieved documents. Keep adding cases when a
real request exposes a new routing failure.

### Agent in Open WebUI

For a new Open WebUI data directory, the Compose configuration seeds two
OpenAI-compatible connections: LiteLLM for direct model chats and the
centralized agent. The agent connection advertises only
**agent.orchestrator**. Its router chooses the internal workflow and execution
model; Code, Research, Finance, Quick, Deep, and Vision are not separate
front-facing agents. Open WebUI does not connect directly to Ollama; LiteLLM is
the single direct-model gateway, preventing the same Ollama model from
appearing once as local and again as an OpenAI-compatible external model.

Existing Open WebUI installations retain connection settings in their data
directory. If old `agent.auto`, `agent.code`, `agent.finance`, or other profile
entries remain visible, edit or recreate the agent connection so its model list
contains only `orchestrator`.

Open WebUI also supports adding the agent manually at **Admin Settings →
Connections → OpenAI → Add New Connection** using URL
`http://agents:8000/v1`, the `AGENT_API_KEY`, and the prefix `agent`.
Keep this as an administrator-managed connection: it stores the key server-side.
The compatibility endpoint accepts `stream: true` and sends SSE heartbeats
and model-token deltas. For actual live agent progress (planning, tool
calls/results, output, and diff review), use the non-blocking `POST /runs` +
`GET /runs/{id}/events` workflow directly or through the Runs UI.

Open WebUI and Continue may include long code excerpts, tool definitions, and
conversation history. Before the agent adds its own prompt and tools, the
compatibility endpoint compacts that client-provided text to
`OPENAI_INPUT_MAX_CHARS` (default `16000`). The agent retains its own system
policy and preserves the newest user request plus short recent history,
omitting client system/tool instructions and older/oversized context first.
Raise it only when using a model with a larger verified context window.

### IDE agent ownership

Continue Agent mode already owns its planning and tool-execution loop. Select
the direct `coder` model for that mode so Continue remains the only
orchestrator. The LiteLLM direct-client guard prevents known premature stops
and safely falls back to plain pytest when optional coverage tooling is
unavailable.

Use `agent.orchestrator` through the Runs UI or the agent `/runs` API when the
server should own planning, tool recovery, verification, sandboxing, and
approval. Do not point Continue Agent mode at `agent.orchestrator`: nesting the
IDE agent around the server agent duplicates orchestration, increases latency,
and makes tool and permission ownership ambiguous.

### Web research

When `WEB_SEARCH_ENABLED=true`, the agent can decide to call `web_search` for a
user request requiring current public information. Queries go to the internal
SearXNG service, which contacts public search engines. Search results are
untrusted text; the agent is instructed not to follow instructions found in
them and to return the source URLs it relied on. Disable it with
`WEB_SEARCH_ENABLED=false` if no query text may leave your network.

For finance and document research, `web_fetch` can retrieve public HTML and
PDF sources returned by search. Its download cap defaults to 8 MB so typical
annual reports work; change `WEB_FETCH_MAX_BYTES` in `.env` (for example,
`WEB_FETCH_MAX_BYTES=12000000`) before recreating `agents` if you need to
support larger filings. `MAX_TOOL_OUTPUT_CHARS` remains a separate cap on the
amount of retrieved text sent to the local model.

## Security model

- `AGENT_API_KEY` is mandatory. The only exception is explicit local
  development mode with `ALLOW_INSECURE_NO_AUTH=true`. Comparison against the
  provided key is constant-time.
- Workspace paths are validated against `WORKSPACE_DIR` and any additional
  `WORKSPACE_ROOTS` on every request; one request cannot alter another
  request's workspace selection, and a request cannot escape the configured
  roots via `..` or symlinks.
- The agent does not have general shell access. `run_command` only executes a
  single command from an administrator-configured allowlist, with shell
  metacharacters (chaining, redirection, subshells) rejected outright.
- Write-enabled `/runs` requests execute inside a disposable Git worktree, not
  the real repository, and require an explicit `/approve` call before changes
  reach your actual files. `/chat` and `/execute` do not sandbox writes --
  reserve `allow_write` on those for changes you're fine applying immediately.
- Run output is coalesced into short batches before PostgreSQL persistence and
  Redis Pub/Sub publication. PostgreSQL remains the replay source after a
  browser reconnects; Redis only provides low-latency delivery to connected
  clients.
- `POST /runs/{run_id}/cancel` propagates through planning, closeable model
  streams, tool execution, and sandbox Git operations. Runner and Git commands
  execute in dedicated process groups with graceful TERM and bounded KILL
  escalation. Private-runner owner leases also stop commands after an API
  restart or network partition.
- Foreground lease expiry uses PostgreSQL-clock, bounded
  `FOR UPDATE SKIP LOCKED` batches. `/metrics/runs` exposes renewal/sweep
  failures, lease age, expired-run backlog, and observed cancellation delay.
- A run records the repository HEAD at sandbox creation. Approval refuses a
  stale diff if HEAD changed, avoiding an accidental apply onto a different
  revision.
- The agent container has no Docker socket, all Linux capabilities are dropped,
  and `no-new-privileges` is enabled.
- Do not expose the default ports directly to a LAN or the internet. Use a
  reverse proxy with TLS, SSO/MFA, rate limits, and audit logs.
- Treat uploaded documents, retrieved text, and repository instructions as
  untrusted. They must not authorize tool use or secret access.
- Back up PostgreSQL, Qdrant, Redis, Open WebUI data, and Ollama model storage.
  PostgreSQL now also holds durable run/event history for `/runs`.
- For any deployment beyond a single trusted user, front the stack with an
  identity provider, issue per-user LiteLLM keys and quotas, centralize logs
  and metrics, and pin container image digests after validating a release.

## Project layout

```text
agents/app/
  main.py          stable FastAPI/OpenAI-compatible entry point
  api/             routes, request/response schemas, authentication dependencies
  core/            configuration and shared exceptions
  agent/           planning, completion contracts, state, tool loop, orchestration
  llm/             LiteLLM gateway client and model discovery
  runs/            PostgreSQL run store, live events, Git sandboxes
  tools/           registry, schemas, filesystem, constrained commands, web search
  memory/          Redis conversations and Qdrant vector memory
contracts/         canonical OpenAPI contracts shared by server, CLI, and Runs UI
cli/               independently packaged terminal client, tests, launchers, and guide
scripts/aistack    compatibility shim for the former launcher location
runs-ui/           main React/TypeScript coding-task application
```

Keep HTTP routes, agent behavior, persistence, sandboxing, tools, memory, and
frontends separate. The CLI communicates with the service only through the
Runs HTTP/SSE API and must not import `agents/app`. New capabilities should be
added to the matching package rather than extending `main.py` with business
logic.

Shared wire contracts stay under root-level `contracts/` because no one client
owns them. Server, CLI, and Runs UI tests must all validate the same versioned
artifact. A future repository split should distribute that artifact through a
release pipeline instead of moving the canonical schema into either consumer.

## Model selection

The Runs UI submits every new task and follow-up as `orchestrator`; it does not
offer profile or model selection. The default identity first uses
`ROUTER_MODEL` for a short,
JSON-only semantic routing turn. The router sees the request and relevant
uploaded-document excerpts, then selects a validated internal workflow,
translates the task, records required evidence, and chooses the workflow's
server-approved model policy. Invalid router output falls back to deterministic
routing; it cannot invent model ids or tools.

In CPU mode, Quick and Research use `quick` (Qwen3 4B), while Code and Finance use `coder`
(Qwen3 8B) for more reliable evidence synthesis. `reasoning` and `vision` are
slower manual choices. Never choose
`embedding` for an agent run; it exists only for retrieval.

Finance and Research workflows always require external evidence, regardless of
whether the original request contains words such as "current" or "latest".
For portfolio requests, extracted entities are turned into per-holding searches
before final synthesis. Internal profile overrides remain API-level testing
controls and are not advertised in either frontend.

When Auto selects Research it uses `RESEARCH_MODEL` (default `quick`); when it
selects Finance it uses `FINANCE_MODEL` (default `coder`). Set
`RESEARCH_MODEL=reasoning` if you prefer deeper, slower web synthesis, or
change `FINANCE_MODEL` independently for financial workloads.

### CPU model management

The Compose stack permits up to three resident models so the router, executor,
and embedding model do not constantly evict one another. Inference remains
serialized by default (`MAX_CONCURRENT_LLM_CALLS=1`) to protect CPU latency,
while bounded runs may overlap tool and retrieval I/O. Models unload after 10
minutes (`OLLAMA_KEEP_ALIVE`) and run with a 32768-token context window
(`OLLAMA_CONTEXT_LENGTH`, matching the `num_ctx` each model advertises through
LiteLLM). Lower `OLLAMA_MAX_LOADED_MODELS` on memory-constrained hosts. Models
are never deleted automatically.
Use:

```bash
./scripts/manage-models.sh list
./scripts/manage-models.sh active
./scripts/manage-models.sh enable quick
./scripts/manage-models.sh disable reasoning
```

`enable` downloads a model if required; `disable` only unloads it from RAM.

`agent.orchestrator` in Open WebUI is the single routed agent identity. Use
normal LiteLLM models in Open WebUI only when direct, non-agent chat is desired.
The Runs UI provides uploads, live routing/tool events, and reviewable
repository work without exposing internal model selection.

## Documents, RAG, and financial research

There are three retrieval paths. The Runs UI parses uploaded `.pdf`, `.docx`,
`.xlsx`, `.csv`, `.txt`, `.md`, and `.json` files server-side into a
conversation-scoped retrieval scope with filename/page/sheet citations (see
"Live Runs UI" above). Open WebUI keeps its own document workflow for its own
chats. The orchestrator `/ingest` endpoint accepts API-supplied text and stores
embeddings in Qdrant. An upload in one front end is not automatically visible
to another.

Finance and Research workflows always require current external evidence
(`web_search` through the internal SearXNG service, `web_fetch` for pages and
filings), whether or not the request says "current". SearXNG engines can fail
or be suspended: treat an empty or partial result set as normal, preserve the
returned source URLs, and never let search-result text authorize tool use.

Do not use a language model as the source of market prices or financial facts,
and never act on search snippets or model output as if they were authoritative
prices, filings, tax advice, or trade instructions. Research remains separate
from brokerage or banking execution; any future action must require a user
confirmation and an auditable approval record.
