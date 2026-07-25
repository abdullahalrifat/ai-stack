import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, Run, RunEvent, streamEvents } from "./api";

type Profile = "auto" | "code" | "research" | "finance" | "quick" | "deep" | "vision" | "image" | "custom";
type ImageResult = { url: string; created: number; prompt: string };

const terminal = new Set(["completed", "awaiting_approval", "failed", "discarded", "cancelled"]);
// The API replaces this list as soon as an Agent API key is entered. Keeping
// the local stack's chat-capable aliases here avoids a misleading one-option
// Custom selector before the authenticated request can be made.
const defaultModels = ["quick", "qwen3-8b", "qwen3-14b", "coder", "reasoning", "vision"];
const profileInfo: Record<Profile, { title: string; description: string; model: string }> = {
  auto: { title: "Auto", description: "Routes routine work to the fast local model.", model: "quick" },
  code: { title: "Code", description: "Repository tools, tests, and reviewable edits.", model: "coder" },
  research: { title: "Research", description: "Current web evidence with sources.", model: "quick" },
  finance: { title: "Finance", description: "Current market research and cautious scenarios.", model: "coder" },
  quick: { title: "Quick chat", description: "Fast summaries and simple questions.", model: "quick" },
  deep: { title: "Deep analysis", description: "Slower investigation and tradeoffs.", model: "reasoning" },
  vision: { title: "Image analysis", description: "Image-aware prompt analysis.", model: "vision" },
  image: { title: "Generate image", description: "Optional local image backend.", model: "image backend" },
  custom: { title: "Custom model", description: "Choose a model explicitly.", model: "qwen3-8b" },
};

function pickProfile(task: string): Profile {
  const text = task.toLowerCase();
  if (/\b(generate|create) (an |a )?image\b/.test(text)) return "image";
  if (/\b(stock|portfolio|dse|nasdaq|closing price|share price|financial)\b/.test(text)) return "finance";
  if (/\b(latest|current|today|news|search the web|look up)\b/.test(text)) return "research";
  if (/\b(image|screenshot|photo)\b/.test(text)) return "vision";
  if (/\b(fix|bug|code|test|repository|refactor|implement)\b/.test(text)) return "code";
  return "quick";
}

function sourceUrls(text: string): string[] {
  return [...new Set(text.match(/https?:\/\/[^\s)\]}>,]+/g) || [])];
}

async function attachmentContext(files: File[]): Promise<string> {
  const accepted = files.filter((file) => /\.(txt|md|csv|json|py|ts|tsx|js|jsx|yaml|yml)$/i.test(file.name));
  const parts = await Promise.all(accepted.slice(0, 5).map(async (file) => {
    const body = (await file.text()).slice(0, 50_000);
    return `\n\n<attached-file name="${file.name}">\n${body}\n</attached-file>`;
  }));
  return parts.join("");
}

export function App() {
  const [key, setKey] = useState(""); const [task, setTask] = useState(""); const [workspace, setWorkspace] = useState("/workspace");
  const [profile, setProfile] = useState<Profile>("auto"); const [customModel, setCustomModel] = useState("qwen3-8b"); const [models, setModels] = useState<string[]>(defaultModels);
  const [write, setWrite] = useState(false); const [files, setFiles] = useState<File[]>([]); const [runs, setRuns] = useState<Run[]>([]); const [active, setActive] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]); const [answer, setAnswer] = useState(""); const [diff, setDiff] = useState(""); const [error, setError] = useState(""); const [imageAvailable, setImageAvailable] = useState(false); const [images, setImages] = useState<ImageResult[]>([]); const [conversationId, setConversationId] = useState<string | null>(null); const [followUp, setFollowUp] = useState(""); const [showHistory, setShowHistory] = useState(false); const cursor = useRef(0);
  const effectiveProfile = profile === "auto" ? pickProfile(task) : profile;
  const recommendedModel = effectiveProfile === "custom" ? customModel : profileInfo[effectiveProfile].model;
  const sources = useMemo(() => sourceUrls(answer), [answer]);

  const loadRuns = async () => { if (!key) return; try { setRuns((await api<{ runs: Run[] }>(key, "/runs")).runs); } catch (e) { setError(String(e)); } };
  useEffect(() => { void loadRuns(); }, [key]);
  useEffect(() => { api<{ models: string[] }>(key, "/models/available").then((data) => { const available = data.models.filter((id) => id !== "embedding"); setModels(available); setCustomModel((current) => available.includes(current) ? current : available[0] || "quick"); }).catch((e) => setError(String(e))); }, [key]);
  useEffect(() => { if (!key) return; api<{ available: boolean }>(key, "/images/status").then((data) => setImageAvailable(data.available)).catch(() => setImageAvailable(false)); }, [key]);

  const consume = (event: RunEvent) => { setEvents((old) => [...old, event]); if (event.event_type === "output_delta") setAnswer((old) => old + String(event.payload.content || "")); if (event.event_type === "diff_ready") setDiff(String(event.payload.diff || "")); if (event.event_type === "stream_closed") setActive((old) => old ? { ...old, status: event.status || old.status } : old); };
  const follow = async (run: Run) => {
    cursor.current = 0; setEvents([]); setAnswer(""); setDiff(""); setActive(run); setConversationId(run.conversation_id || null);
    let retries = 0;
    try {
      // Always open the stream once. Completed runs have durable historical
      // events that must be replayed when selected from the Recent runs list.
      do {
        try {
          cursor.current = await streamEvents(key, run.id, cursor.current, consume);
          const current = await api<Run>(key, `/runs/${run.id}`);
          run = current; setActive(current); retries = 0;
        } catch (e) {
          retries += 1;
          if (retries > 5) throw e;
          const waitMs = Math.min(1_000 * 2 ** (retries - 1), 10_000);
          setError(`Live connection interrupted; retrying in ${Math.round(waitMs / 1_000)}s (${retries}/5)…`);
          await new Promise((resolve) => window.setTimeout(resolve, waitMs));
        }
      } while (!terminal.has(run.status));
      setError(""); await loadRuns();
    } catch (e) { setError(`Could not reconnect to this run: ${String(e)}`); }
  };
  const start = async (event: FormEvent) => { event.preventDefault(); setError(""); if (!task.trim()) return; try {
    if (effectiveProfile === "image") { const data = await api<{ created: number; data: { url: string }[] }>(key, "/images/generations", { method: "POST", body: JSON.stringify({ prompt: task }) }); setImages((old) => [...data.data.map((item) => ({ url: item.url, created: data.created, prompt: task })), ...old]); return; }
    const context = await attachmentContext(files); const selected = effectiveProfile === "custom" ? customModel : effectiveProfile; const session = conversationId || crypto.randomUUID(); setConversationId(session);
    const result = await api<{ run_id: string; status: string }>(key, "/runs", { method: "POST", body: JSON.stringify({ task: `${task}${context}`, workspace, model: selected, conversation_id: session, allow_write: effectiveProfile === "code" && write }) });
    await follow({ id: result.run_id, status: result.status, task, model: selected, conversation_id: session, requested_workspace: workspace, allow_write: effectiveProfile === "code" && write, created_at: new Date().toISOString() });
  } catch (e) { setError(String(e)); } };
  const continueConversation = async (event: FormEvent) => { event.preventDefault(); if (!active || !followUp.trim()) return; setError(""); try {
    const session = active.conversation_id || conversationId || crypto.randomUUID(); setConversationId(session);
    const result = await api<{ run_id: string; status: string }>(key, "/runs", { method: "POST", body: JSON.stringify({ task: followUp, workspace: active.requested_workspace, model: active.model, conversation_id: session, allow_write: false }) });
    setFollowUp(""); await follow({ id: result.run_id, status: result.status, task: followUp, model: active.model, conversation_id: session, requested_workspace: active.requested_workspace, allow_write: false, created_at: new Date().toISOString() });
  } catch (e) { setError(String(e)); } };
  const newConversation = () => { setConversationId(null); setActive(null); setEvents([]); setAnswer(""); setDiff(""); setFollowUp(""); setError(""); };
  const action = async (name: "approve" | "discard" | "cancel") => { if (!active) return; try { await api(key, `/runs/${active.id}/${name}`, { method: "POST" }); await follow({ ...active, status: name === "cancel" ? "cancelling" : active.status }); } catch (e) { setError(String(e)); } };

  return <main><header><div><h1>{showHistory ? "Run history" : "AI Task Router"}</h1><p>{showHistory ? "Open a prior run when you need its answer or conversation." : "Choose intent, review live work, and keep models in their lane."}</p></div><div className="header-actions"><span className="badge">{profileInfo[effectiveProfile].title} → {recommendedModel}</span><button type="button" onClick={() => setShowHistory((current) => !current)}>{showHistory ? "Back to chat" : `History (${runs.length})`}</button><button type="button" onClick={newConversation}>New conversation</button></div></header>
    <section className="profiles">{(Object.keys(profileInfo) as Profile[]).map((id) => id === "custom" ? <div className={`profile custom ${profile === id ? "selected" : ""}`} key={id}><button type="button" className="profile-select" onClick={() => setProfile("custom")}><b>{profileInfo[id].title}</b><span>{profileInfo[id].description}</span></button><select aria-label="Custom model" value={customModel} onChange={(event) => { setCustomModel(event.target.value); setProfile("custom"); }}>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></div> : <button type="button" className={`profile ${profile === id ? "selected" : ""}`} key={id} onClick={() => setProfile(id)}><b>{profileInfo[id].title}</b><span>{profileInfo[id].description}</span></button>)}</section>
    {showHistory ? <section className="card history"><h2>Recent runs</h2>{runs.length === 0 ? <p className="hint">Enter your API key to load run history.</p> : runs.map((run) => <button className="run" type="button" key={run.id} onClick={() => { setShowHistory(false); void follow(run); }}><b>{run.status} · {run.model}</b><span>{run.task}</span></button>)}</section> : <><section className="grid"><form onSubmit={start} className="card"><label>Agent API key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} /></label><label>Task<textarea value={task} placeholder="Ask for code, research, analysis, or an image" onChange={(e) => setTask(e.target.value)} /></label><div className="two"><label>Workspace<input value={workspace} disabled={effectiveProfile !== "code"} onChange={(e) => setWorkspace(e.target.value)} /></label><div className="model-choice"><b>Selected model</b><span>{models.includes(recommendedModel) ? recommendedModel : models[0] || "Loading…"}</span><small>Chosen by the {profileInfo[effectiveProfile].title} profile.</small></div></div><label>Text attachments <input type="file" multiple accept=".txt,.md,.csv,.json,.py,.ts,.tsx,.js,.jsx,.yaml,.yml" onChange={(e) => setFiles(Array.from(e.target.files || []))} /></label>{files.length > 0 && <p className="hint">Attached: {files.map((file) => file.name).join(", ")}</p>}{effectiveProfile === "code" && <label className="check"><input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} /> Make changes in a reviewable Git sandbox</label>}{effectiveProfile === "image" && !imageAvailable && <p className="notice">Image generation is not configured. Set <code>IMAGE_GENERATION_URL</code> to an Automatic1111/Forge-compatible local API.</p>}<button disabled={!key || (effectiveProfile === "image" && !imageAvailable)}>{effectiveProfile === "image" ? "Generate image" : "Start routed task"}</button>{error && <pre className="error">{error}</pre>}</form></section>
    <section className="card"><h2>{active ? `Conversation run ${active.id}` : "Live activity"}</h2><div className="actions">{active && ["queued", "running", "cancelling"].includes(active.status) && <button type="button" onClick={() => void action("cancel")}>Cancel</button>}{active?.status === "awaiting_approval" && <><button type="button" onClick={() => void action("approve")}>Approve</button><button type="button" className="danger" onClick={() => void action("discard")}>Discard</button></>}</div><pre className="answer">{answer || "Start a task to see streamed model output, sources, and tool activity."}</pre>{sources.length > 0 && <div className="sources"><h3>Sources</h3>{sources.map((url) => <a href={url} key={url} target="_blank" rel="noreferrer">{url}</a>)}</div>}<div className="events">{events.map((event, i) => <div key={`${event.id || i}-${event.event_type}`}><b>{event.event_type}</b> {JSON.stringify(event.payload)}</div>)}</div>{diff && <><h3>Review diff</h3><pre className="diff">{diff}</pre></>} {active && <form className="follow-up" onSubmit={continueConversation}><label>Follow up in this conversation<textarea value={followUp} placeholder="Ask for clarification, challenge an assumption, or request a deeper analysis" onChange={(event) => setFollowUp(event.target.value)} /></label><button disabled={!key || !followUp.trim() || !terminal.has(active.status)}>Send follow-up</button></form>}</section>
    <section className="card"><h2>Generated images</h2>{images.length === 0 ? <p className="hint">Generated images from the configured local backend appear here for this browser session.</p> : <div className="gallery">{images.map((image, index) => <figure key={`${image.created}-${index}`}><img src={image.url} alt={image.prompt} /><figcaption>{image.prompt}</figcaption></figure>)}</div>}</section></>}
  </main>;
}
