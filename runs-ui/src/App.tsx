import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, Run, RunEvent, streamEvents, uploadDocuments } from "./api";

type Project = { id: string; name: string; workspace: string };

const terminal = new Set([
  "completed",
  "awaiting_approval",
  "failed",
  "discarded",
  "cancelled",
]);
function sourceUrls(text: string): string[] {
  return [...new Set(text.match(/https?:\/\/[^\s)\]}>,]+/g) || [])];
}

export function App() {
  const [key, setKey] = useState("");
  const [task, setTask] = useState("");
  const [workspace, setWorkspace] = useState("/workspace");
  const [workspaceOptions, setWorkspaceOptions] = useState<string[]>([
    "/workspace",
  ]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [write, setWrite] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [active, setActive] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [answer, setAnswer] = useState("");
  const [diff, setDiff] = useState("");
  const [error, setError] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const cursor = useRef(0);
  const sources = useMemo(() => sourceUrls(answer), [answer]);

  const loadRuns = async () => {
    if (!key) return;
    try {
      setRuns((await api<{ runs: Run[] }>(key, "/runs")).runs);
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    void loadRuns();
  }, [key]);
  useEffect(() => {
    if (!key) return;
    api<{ projects: Project[] }>(key, "/projects")
      .then((data) => setProjects(data.projects))
      .catch((e) => setError(String(e)));
  }, [key]);
  useEffect(() => {
    if (!key) return;
    api<{ workspaces: string[] }>(key, "/workspace/choices")
      .then((data) => setWorkspaceOptions(data.workspaces))
      .catch((e) => setError(String(e)));
  }, [key]);
  useEffect(() => {
    if (!key) return;
    api<{ workspace: string }>(key, "/workspace/default")
      .then((data) => setWorkspace(data.workspace))
      .catch((e) => setError(String(e)));
  }, [key]);

  const consume = (event: RunEvent) => {
    setEvents((old) => [...old, event]);
    if (event.event_type === "output_delta")
      setAnswer((old) => old + String(event.payload.content || ""));
    if (event.event_type === "diff_ready")
      setDiff(String(event.payload.diff || ""));
    if (event.event_type === "stream_closed")
      setActive((old) =>
        old ? { ...old, status: event.status || old.status } : old,
      );
  };
  const follow = async (run: Run) => {
    cursor.current = 0;
    setEvents([]);
    setAnswer("");
    setDiff("");
    setActive(run);
    setConversationId(run.conversation_id || null);
    let retries = 0;
    try {
      // Always open the stream once. Completed runs have durable historical
      // events that must be replayed when selected from the Recent runs list.
      do {
        try {
          cursor.current = await streamEvents(
            key,
            run.id,
            cursor.current,
            consume,
          );
          const current = await api<Run>(key, `/runs/${run.id}`);
          run = current;
          setActive(current);
          retries = 0;
        } catch (e) {
          retries += 1;
          if (retries > 5) throw e;
          const waitMs = Math.min(1_000 * 2 ** (retries - 1), 10_000);
          setError(
            `Live connection interrupted; retrying in ${Math.round(waitMs / 1_000)}s (${retries}/5)…`,
          );
          await new Promise((resolve) => window.setTimeout(resolve, waitMs));
        }
      } while (!terminal.has(run.status));
      setError("");
      await loadRuns();
    } catch (e) {
      setError(`Could not reconnect to this run: ${String(e)}`);
    }
  };
  const start = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (!task.trim()) return;
    try {
      const selected = "coding-agent";
      const session = conversationId || crypto.randomUUID();
      setConversationId(session);
      if (files.length > 0) await uploadDocuments(key, files.slice(0, 10), session);
      const attachmentNote = files.length
        ? `\n\nUse the uploaded documents in retrieval scope ${session}. Cite their document name and location when relying on them.`
        : "";
      const result = await api<{ run_id: string; status: string }>(
        key,
        "/runs",
        {
          method: "POST",
          body: JSON.stringify({
            task: `${task}${attachmentNote}`,
            workspace,
            model: selected,
            conversation_id: session,
            document_scope: files.length ? session : null,
            project_id: projectId || null,
            allow_write: write,
          }),
        },
      );
      await follow({
        id: result.run_id,
        status: result.status,
        task,
        model: selected,
        conversation_id: session,
        requested_workspace: workspace,
        allow_write: write,
        created_at: new Date().toISOString(),
      });
    } catch (e) {
      setError(String(e));
    }
  };
  const createProject = async () => {
    if (!projectName.trim()) return;
    try {
      const created = await api<Project>(key, "/projects", {
        method: "POST",
        body: JSON.stringify({ name: projectName, workspace }),
      });
      setProjects((current) => [created, ...current]);
      setProjectId(created.id);
      setProjectName("");
    } catch (e) {
      setError(String(e));
    }
  };
  const continueConversation = async (event: FormEvent) => {
    event.preventDefault();
    if (!active || !followUp.trim()) return;
    setError("");
    try {
      const session =
        active.conversation_id || conversationId || crypto.randomUUID();
      setConversationId(session);
      // Runs created before conversation IDs existed cannot be recovered from
      // durable history. Seed their first follow-up with the prior exchange.
      const taskWithLegacyContext = active.conversation_id
        ? followUp
        : `Earlier user request:\n${active.task}\n\nEarlier agent answer:\n${active.answer || answer}\n\nFollow-up request:\n${followUp}`;
      const result = await api<{ run_id: string; status: string }>(
        key,
        "/runs",
        {
          method: "POST",
          body: JSON.stringify({
            task: taskWithLegacyContext,
            workspace: active.requested_workspace,
            model: "coding-agent",
            conversation_id: session,
            document_scope: active.document_scope || null,
            project_id: active.project_id || projectId || null,
            allow_write: false,
          }),
        },
      );
      setFollowUp("");
      await follow({
        id: result.run_id,
        status: result.status,
        task: followUp,
        model: "coding-agent",
        conversation_id: session,
        project_id: active.project_id || projectId || null,
        requested_workspace: active.requested_workspace,
        allow_write: false,
        created_at: new Date().toISOString(),
      });
    } catch (e) {
      setError(String(e));
    }
  };
  const newConversation = () => {
    setConversationId(null);
    setActive(null);
    setEvents([]);
    setAnswer("");
    setDiff("");
    setFollowUp("");
    setError("");
  };
  const action = async (name: "approve" | "discard" | "cancel") => {
    if (!active) return;
    try {
      await api(key, `/runs/${active.id}/${name}`, { method: "POST" });
      await follow({
        ...active,
        status: name === "cancel" ? "cancelling" : active.status,
      });
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <main>
      <header>
        <div>
          <h1>{showHistory ? "Run history" : "Central AI Agent"}</h1>
          <p>
            {showHistory
              ? "Open a prior run when you need its answer or conversation."
              : "Describe the task once; the central router selects the workflow and model."}
          </p>
        </div>
        <div className="header-actions">
          <span className="badge">
            Central router agent
          </span>
          <button
            type="button"
            onClick={() => setShowHistory((current) => !current)}
          >
            {showHistory ? "Back to chat" : `History (${runs.length})`}
          </button>
          <button type="button" onClick={newConversation}>
            New conversation
          </button>
        </div>
      </header>
      {showHistory ? (
        <section className="card history">
          <h2>Recent runs</h2>
          {runs.length === 0 ? (
            <p className="hint">Enter your API key to load run history.</p>
          ) : (
            runs.map((run) => (
              <button
                className="run"
                type="button"
                key={run.id}
                onClick={() => {
                  setShowHistory(false);
                  void follow(run);
                }}
              >
                <b>
                  {run.status} · {run.model}
                </b>
                <span>{run.task}</span>
              </button>
            ))
          )}
        </section>
      ) : (
        <>
          <section className="grid">
            <form onSubmit={start} className="card">
              <label>
                Agent API key
                <input
                  type="password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </label>
              <label>
                Task
                <textarea
                  value={task}
                  placeholder="Ask for code, research, analysis, or an image"
                  onChange={(e) => setTask(e.target.value)}
                />
              </label>
              <div className="two">
                <label>
                  Workspace
                  <input
                    value={workspace}
                    list="workspace-options"
                    onChange={(e) => setWorkspace(e.target.value)}
                  />
                  <datalist id="workspace-options">
                    {workspaceOptions.map((option) => (
                      <option key={option} value={option} />
                    ))}
                  </datalist>
                </label>
                <div className="model-choice">
                  <b>Execution routing</b>
                  <span>Automatic</span>
                  <small>
                    The central agent selects the internal workflow and model.
                  </small>
                </div>
              </div>
              <div className="two project-row">
                <label>
                  Saved project
                  <select
                    value={projectId}
                    onChange={(event) => {
                      const selected = projects.find((item) => item.id === event.target.value);
                      setProjectId(event.target.value);
                      if (selected) setWorkspace(selected.workspace);
                    }}
                  >
                    <option value="">No project</option>
                    {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </label>
                <label>
                  Save current workspace as project
                  <div className="inline-input">
                    <input value={projectName} placeholder="Project name" onChange={(event) => setProjectName(event.target.value)} />
                    <button type="button" onClick={() => void createProject()} disabled={!key || !projectName.trim()}>Save</button>
                  </div>
                </label>
              </div>
              <label>
                Documents (PDF, DOCX, XLSX, CSV, text){" "}
                <input
                  type="file"
                  multiple
                  accept=".txt,.md,.csv,.json,.pdf,.docx,.xlsx"
                  onChange={(e) => setFiles(Array.from(e.target.files || []))}
                />
              </label>
              {files.length > 0 && (
                <p className="hint">
                  Attached: {files.map((file) => file.name).join(", ")}
                </p>
              )}
              <label className="check">
                <input
                  type="checkbox"
                  checked={write}
                  onChange={(e) => setWrite(e.target.checked)}
                />{" "}
                Allow reviewable changes when the router selects a code workflow
              </label>
              <button disabled={!key}>
                Start routed task
              </button>
              {error && <pre className="error">{error}</pre>}
            </form>
          </section>
          <section className="card">
            <h2>
              {active ? `Conversation run ${active.id}` : "Live activity"}
            </h2>
            <div className="actions">
              {active &&
                ["queued", "running", "cancelling"].includes(active.status) && (
                  <button type="button" onClick={() => void action("cancel")}>
                    Cancel
                  </button>
                )}
              {active?.status === "awaiting_approval" && (
                <>
                  <button type="button" onClick={() => void action("approve")}>
                    Approve
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void action("discard")}
                  >
                    Discard
                  </button>
                </>
              )}
            </div>
            <pre className="answer">
              {answer ||
                "Start a task to see streamed model output, sources, and tool activity."}
            </pre>
            {sources.length > 0 && (
              <div className="sources">
                <h3>Sources</h3>
                {sources.map((url) => (
                  <a href={url} key={url} target="_blank" rel="noreferrer">
                    {url}
                  </a>
                ))}
              </div>
            )}
            <div className="events">
              {events.map((event, i) => (
                <div key={`${event.id || i}-${event.event_type}`}>
                  <b>{event.event_type}</b> {JSON.stringify(event.payload)}
                </div>
              ))}
            </div>
            {diff && (
              <>
                <h3>Review diff</h3>
                <pre className="diff">{diff}</pre>
              </>
            )}{" "}
            {active && (
              <form className="follow-up" onSubmit={continueConversation}>
                <label>
                  Follow up in this conversation
                  <textarea
                    value={followUp}
                    placeholder="Ask for clarification, challenge an assumption, or request a deeper analysis"
                    onChange={(event) => setFollowUp(event.target.value)}
                  />
                </label>
                <button
                  disabled={
                    !key || !followUp.trim() || !terminal.has(active.status)
                  }
                >
                  Send follow-up
                </button>
              </form>
            )}
          </section>
        </>
      )}
    </main>
  );
}
