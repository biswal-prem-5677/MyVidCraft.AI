"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Project = { id: string; name: string; niche: string };
type Run = { id: string; status: string; stage: string; progress: number; error?: string | null };
type Artifact = { id: string; type: string; path: string; created_at: string; metadata: Record<string, unknown> };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function safeJsonParse(input: string): unknown {
  try {
    return JSON.parse(input);
  } catch {
    return {};
  }
}

export default function HomePage() {
  const [projectName, setProjectName] = useState("Zentcast Phase1");
  const [niche, setNiche] = useState("AI productivity");
  const [mode, setMode] = useState("teaching");
  const [brandProfileText, setBrandProfileText] = useState(
    JSON.stringify({ tone: "confident", catchphrase: "Let’s make this practical." }, null, 2)
  );

  const [project, setProject] = useState<Project | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifactContents, setArtifactContents] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);

  const progressPct = useMemo(() => Math.round(((run?.progress ?? 0) * 100 + Number.EPSILON) * 1) / 1, [run?.progress]);

  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) }
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  }

  async function createProjectAndRun() {
    setBusy(true);
    setEvents([]);
    setArtifacts([]);
    setArtifactContents({});
    setRun(null);
    try {
      const p = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: projectName,
          niche,
          brand_profile: safeJsonParse(brandProfileText),
          defaults: {}
        })
      });
      setProject(p);

      const r = await api<Run>(`/projects/${p.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ mode, platforms: ["youtube", "podcast"], language: "en", config: {} })
      });
      setRun(r);
      connectWs(r.id);
    } finally {
      setBusy(false);
    }
  }

  function connectWs(runId: string) {
    wsRef.current?.close();
    const wsUrl = API_BASE.replace(/^http/, "ws") + `/ws/runs/${runId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      setEvents((prev) => [ev, ...prev].slice(0, 80));
      if (ev?.type === "run.completed" || ev?.type === "run.failed") {
        refreshRun(runId);
        refreshArtifacts(runId);
      }
      if (ev?.type === "stage.started" || ev?.type === "stage.completed" || ev?.type === "artifact.created") {
        refreshRun(runId);
        refreshArtifacts(runId);
      }
    };
    ws.onerror = () => {
      // no-op; user can still refresh artifacts via button
    };
  }

  async function refreshRun(runId: string) {
    try {
      const r = await api<Run>(`/runs/${runId}`);
      setRun(r);
    } catch {}
  }

  async function refreshArtifacts(runId: string) {
    try {
      const list = await api<Artifact[]>(`/runs/${runId}/artifacts`);
      setArtifacts(list);
    } catch {}
  }

  async function loadArtifactJson(a: Artifact) {
    try {
      const payload = await api<any>(`/artifacts/${a.id}/json`);
      setArtifactContents((prev) => ({ ...prev, [a.id]: payload }));
    } catch (e: any) {
      setArtifactContents((prev) => ({ ...prev, [a.id]: { error: String(e?.message ?? e), path: a.path } }));
    }
  }

  useEffect(() => {
    return () => wsRef.current?.close();
  }, []);

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="title">zentcast-ai — Phase 1</div>
          <div className="subtitle">Content Brain → StrategyPack + multi-agent Dialogue JSON (async runs + artifacts)</div>
        </div>
        <div className="pill mono">
          API <strong>{API_BASE}</strong>
        </div>
      </div>

      <div className="grid">
        <div className="card">
          <h3>Run builder</h3>
          <div className="row">
            <div>
              <div className="label">Project name</div>
              <input className="input" value={projectName} onChange={(e) => setProjectName(e.target.value)} />
            </div>
            <div>
              <div className="label">Niche</div>
              <input className="input" value={niche} onChange={(e) => setNiche(e.target.value)} />
            </div>
            <div className="row2">
              <div>
                <div className="label">Mode</div>
                <select className="select" value={mode} onChange={(e) => setMode(e.target.value)}>
                  <option value="teaching">teaching</option>
                  <option value="storytelling">storytelling</option>
                  <option value="debate">debate</option>
                </select>
              </div>
              <div>
                <div className="label">Quick actions</div>
                <button className="btn2" disabled={!run?.id} onClick={() => run?.id && refreshArtifacts(run.id)}>
                  Refresh artifacts
                </button>
              </div>
            </div>
            <div>
              <div className="label">Brand profile (JSON)</div>
              <textarea className="textarea mono" value={brandProfileText} onChange={(e) => setBrandProfileText(e.target.value)} />
            </div>
            <button className="btn" disabled={busy} onClick={createProjectAndRun}>
              {busy ? "Starting…" : "Create project + start run"}
            </button>
          </div>
        </div>

        <div className="card">
          <h3>Status</h3>
          <div className="progressWrap">
            <div className="pill">
              <span className="mono">project</span> <strong className="mono">{project?.id ?? "—"}</strong>
            </div>
            <div className="pill">
              <span className="mono">run</span> <strong className="mono">{run?.id ?? "—"}</strong>
            </div>
            <div className="pill">
              <span className="mono">stage</span> <strong className="mono">{run?.stage ?? "—"}</strong>
              <span style={{ marginLeft: "auto" }} className="mono">
                {run?.status ?? "—"} · {progressPct}%
              </span>
            </div>
            <div className="bar">
              <div style={{ width: `${progressPct}%` }} />
            </div>
            {run?.error ? <pre className="pre mono">{run.error}</pre> : null}
          </div>
        </div>
      </div>

      <div style={{ height: 12 }} />

      <div className="grid">
        <div className="card">
          <h3>Artifacts</h3>
          {artifacts.length === 0 ? (
            <div className="subtitle">No artifacts yet. Start a run.</div>
          ) : (
            <div className="row">
              {artifacts.map((a) => (
                <div key={a.id} className="card" style={{ padding: 12 }}>
                  <div className="pill">
                    <span className="mono">{a.type}</span>
                    <strong className="mono">{a.id.slice(0, 8)}</strong>
                    <span style={{ marginLeft: "auto" }} className="mono">
                      {new Date(a.created_at).toLocaleString()}
                    </span>
                  </div>
                  <div style={{ height: 8 }} />
                  <div className="subtitle mono">{a.path}</div>
                  <div style={{ height: 10 }} />
                  <button className="btn2" onClick={() => loadArtifactJson(a)}>
                    Show details
                  </button>
                  <div style={{ height: 10 }} />
                  <pre className="pre mono">{JSON.stringify(artifactContents[a.id] ?? {}, null, 2)}</pre>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <h3>Live events (websocket)</h3>
          {events.length === 0 ? (
            <div className="subtitle">No events yet.</div>
          ) : (
            <pre className="pre mono">{events.map((e) => JSON.stringify(e)).join("\n")}</pre>
          )}
        </div>
      </div>
    </div>
  );
}

