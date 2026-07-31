"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Project = { id: string; name: string; niche: string };
type Run = { id: string; status: string; stage: string; progress: number; error?: string | null };
type Artifact = { id: string; type: string; path: string; created_at: string; metadata: Record<string, unknown> };
type Voice = { id: string; name: string };
type Health = { video_engine: string; video_ready: boolean; video_detail: string };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const TERMINAL_STATUSES = ["completed", "failed", "cancelled"];

function safeJsonParse(input: string): unknown {
  try {
    return JSON.parse(input);
  } catch {
    return {};
  }
}

export default function HomePage() {
  const [projectName, setProjectName] = useState("MyVidCraft Phase 3");
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
  const [revoicing, setRevoicing] = useState(false);
  const [recutting, setRecutting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Voice casting: speakers come from the TTS manifest of the last render, so the
  // override selects only appear once there is audio to re-render.
  const [voices, setVoices] = useState<Voice[]>([]);
  const [castVoices, setCastVoices] = useState<Record<string, string>>({});
  const [voiceOverrides, setVoiceOverrides] = useState<Record<string, string>>({});

  // Compositor look. These ride on the re-cut request rather than being stored
  // client-side state of the last render — the run's config is the record.
  const [showCaptions, setShowCaptions] = useState(true);
  const [showBrollSlots, setShowBrollSlots] = useState(true);
  const [health, setHealth] = useState<Health | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  const progressPct = useMemo(() => Math.round(((run?.progress ?? 0) * 100 + Number.EPSILON) * 1) / 1, [run?.progress]);
  const isTerminal = useMemo(() => TERMINAL_STATUSES.includes(run?.status ?? ""), [run?.status]);
  const canRevoice = useMemo(
    () => Boolean(run?.id) && isTerminal && Object.keys(castVoices).length > 0,
    [run?.id, isTerminal, castVoices]
  );

  const byType = useMemo(() => {
    const map: Record<string, Artifact[]> = {};
    for (const a of artifacts) (map[a.type] ??= []).push(a);
    return map;
  }, [artifacts]);

  const videoArtifact = byType["episode_video_mp4"]?.[0] ?? null;
  const storyboardArtifact = byType["video_storyboard"]?.[0] ?? null;
  const captionsArtifact = byType["episode_captions_srt"]?.[0] ?? null;
  // A re-cut needs the timeline the audio stage wrote, which is exactly what the
  // API checks for; gating on it here keeps the button from 400ing.
  const canRecut = Boolean(run?.id) && isTerminal && Boolean(byType["episode_timeline"]?.length);

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
    setNotice(null);
    setEvents([]);
    setArtifacts([]);
    setArtifactContents({});
    setCastVoices({});
    setVoiceOverrides({});
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
    } catch (e: any) {
      setNotice(`Could not start run: ${e?.message ?? e}`);
    } finally {
      setBusy(false);
    }
  }

  async function regenerateVoice() {
    if (!run?.id) return;
    setRevoicing(true);
    setNotice(null);
    try {
      // Only send genuine changes; an empty map means "recast automatically".
      const overrides = Object.fromEntries(
        Object.entries(voiceOverrides).filter(([speaker, id]) => id && id !== castVoices[speaker])
      );
      const r = await api<Run>(`/runs/${run.id}/regenerate-voice`, {
        method: "POST",
        body: JSON.stringify({ voice_overrides: overrides })
      });
      setRun(r);
      setEvents([]);
      setArtifactContents({});
      connectWs(r.id);
    } catch (e: any) {
      setNotice(`Could not regenerate voice: ${e?.message ?? e}`);
    } finally {
      setRevoicing(false);
    }
  }

  async function regenerateVideo() {
    if (!run?.id) return;
    setRecutting(true);
    setNotice(null);
    try {
      const r = await api<Run>(`/runs/${run.id}/regenerate-video`, {
        method: "POST",
        body: JSON.stringify({ captions: showCaptions, show_broll_slots: showBrollSlots })
      });
      setRun(r);
      setEvents([]);
      connectWs(r.id);
    } catch (e: any) {
      setNotice(`Could not re-cut the video: ${e?.message ?? e}`);
    } finally {
      setRecutting(false);
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
      await loadCast(list);
    } catch {}
  }

  async function loadCast(list: Artifact[]) {
    const manifest = list.find((a) => a.type === "turn_audio_manifest");
    if (!manifest) return;
    try {
      const payload = await api<any>(`/artifacts/${manifest.id}/json`);
      const cast: Record<string, string> = {};
      for (const [speaker, v] of Object.entries<any>(payload?.voices ?? {})) {
        if (v?.voice_id) cast[speaker] = v.voice_id;
      }
      setCastVoices(cast);
      // Seed the selects with what was actually used, without clobbering a
      // choice the user has already made for the next re-render.
      setVoiceOverrides((prev) => ({ ...cast, ...prev }));
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
    // Installed system voices and renderer readiness are properties of the
    // machine, not of a run: a missing FFmpeg should be visible before the first
    // render finishes without one.
    api<Voice[]>("/voices")
      .then(setVoices)
      .catch(() => setVoices([]));
    api<Health>("/health")
      .then(setHealth)
      .catch(() => setHealth(null));
    return () => wsRef.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="title">MyVidCraft.ai — Phase 3</div>
          <div className="subtitle">
            Content Brain → Dialogue JSON → per-turn TTS → mastered audio → cut episode video (async runs + artifacts)
          </div>
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
            {notice ? <pre className="pre mono">{notice}</pre> : null}
          </div>
        </div>
      </div>

      <div style={{ height: 12 }} />

      <div className="card">
        <h3>Voice cast</h3>
        {Object.keys(castVoices).length === 0 ? (
          <div className="subtitle">
            No audio rendered yet. Once a run finishes you can recast speakers and re-render the audio without
            regenerating the script.
          </div>
        ) : (
          <div className="row">
            {Object.keys(castVoices).map((speaker) => (
              <div key={speaker}>
                <div className="label">{speaker}</div>
                <select
                  className="select"
                  value={voiceOverrides[speaker] ?? castVoices[speaker] ?? ""}
                  onChange={(e) => setVoiceOverrides((prev) => ({ ...prev, [speaker]: e.target.value }))}
                >
                  {voices.length === 0 ? <option value="">(system default)</option> : null}
                  {voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </div>
            ))}
            <button className="btn" disabled={!canRevoice || revoicing} onClick={regenerateVoice}>
              {revoicing ? "Re-rendering…" : "Regenerate voice only"}
            </button>
            <div className="subtitle">
              Keeps the strategy, episode plan and dialogue exactly as they are; replaces every per-turn WAV and the
              mastered episode.
            </div>
          </div>
        )}
      </div>

      <div style={{ height: 12 }} />

      <div className="card">
        <h3>Episode video</h3>
        {!storyboardArtifact ? (
          <div className="subtitle">
            No video cut yet. A finished run produces the episode video, burned captions and a storyboard.
          </div>
        ) : (
          <div className="row">
            {videoArtifact ? (
              <video
                controls
                preload="metadata"
                style={{ width: "100%", borderRadius: 12, border: "1px solid var(--border)", background: "#000" }}
                src={`${API_BASE}/artifacts/${videoArtifact.id}/file`}
              />
            ) : (
              <pre className="pre mono">
                {`No episode.mp4 for this run.\n\n` +
                  (health && !health.video_ready
                    ? `${health.video_engine}: ${health.video_detail}`
                    : "The compositor could not mux. See the storyboard notes below.")}
              </pre>
            )}

            <div className="row2">
              {videoArtifact ? (
                <a className="btn2" style={{ textAlign: "center", textDecoration: "none" }}
                   href={`${API_BASE}/artifacts/${videoArtifact.id}/file`} download>
                  Download mp4
                </a>
              ) : null}
              {captionsArtifact ? (
                <a className="btn2" style={{ textAlign: "center", textDecoration: "none" }}
                   href={`${API_BASE}/artifacts/${captionsArtifact.id}/file`} download>
                  Download captions (.srt)
                </a>
              ) : null}
            </div>

            <div className="label">Re-cut the look</div>
            <div className="row2">
              <label className="pill" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={showCaptions} onChange={(e) => setShowCaptions(e.target.checked)} />
                Burned captions
              </label>
              <label className="pill" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={showBrollSlots} onChange={(e) => setShowBrollSlots(e.target.checked)} />
                B-roll slots
              </label>
            </div>
            <button className="btn" disabled={!canRecut || recutting} onClick={regenerateVideo}>
              {recutting ? "Re-cutting…" : "Re-cut video only"}
            </button>
            <div className="subtitle">
              The cheapest re-render in the pipeline: the script and every WAV are reused, and only the compositor runs
              again.
            </div>

            <button className="btn2" onClick={() => loadArtifactJson(storyboardArtifact)}>
              Show storyboard
            </button>
            {artifactContents[storyboardArtifact.id] ? (
              <pre className="pre mono">{JSON.stringify(artifactContents[storyboardArtifact.id], null, 2)}</pre>
            ) : null}
          </div>
        )}
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
                  {a.path.endsWith(".wav") || a.path.endsWith(".mp3") ? (
                    <>
                      <audio
                        controls
                        style={{ width: "100%" }}
                        src={`${API_BASE}/artifacts/${a.id}/file`}
                      />
                      <div style={{ height: 10 }} />
                    </>
                  ) : null}
                  {/* Only JSON artifacts can be inspected inline; everything else
                      is a media file and is offered as a download instead. */}
                  {a.path.endsWith(".json") ? (
                    <>
                      <button className="btn2" onClick={() => loadArtifactJson(a)}>
                        Show details
                      </button>
                      {artifactContents[a.id] ? (
                        <>
                          <div style={{ height: 10 }} />
                          <pre className="pre mono">{JSON.stringify(artifactContents[a.id], null, 2)}</pre>
                        </>
                      ) : null}
                    </>
                  ) : (
                    <a className="btn2" style={{ display: "block", textAlign: "center", textDecoration: "none" }}
                       href={`${API_BASE}/artifacts/${a.id}/file`} download>
                      Download
                    </a>
                  )}
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

