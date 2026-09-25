"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PocDetail, StageCell } from "@/lib/types";
import { fmtCountdown, fmtDuration, fmtTime } from "@/lib/format";

export default function PipelineBoard({
  pocId,
  refreshSignal,
  pollMs,
}: {
  pocId: string;
  refreshSignal: number;
  pollMs: number;
}) {
  const [detail, setDetail] = useState<PocDetail | null>(null);
  const [error, setError] = useState("");
  const [lastPoll, setLastPoll] = useState<number>(0);
  const [paused, setPaused] = useState(false);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const pocRef = useRef(pocId);
  pocRef.current = pocId;

  const load = useCallback(async () => {
    const id = pocRef.current;
    if (!id) {
      setDetail(null);
      return;
    }
    try {
      const res = await fetch(`/api/pocs/${id}`, { cache: "no-store" });
      const data = await res.json();
      if (pocRef.current !== id) return; // selection changed mid-flight
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setDetail(data as PocDetail);
      setError("");
      setLastPoll(Date.now());
    } catch (err) {
      if (pocRef.current !== id) return;
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Reset + immediate load when the selected POC changes.
  useEffect(() => {
    setDetail(null);
    setError("");
    load();
  }, [pocId, load]);

  // Immediate refresh whenever the chat pane signals a completed turn.
  useEffect(() => {
    if (refreshSignal > 0) load();
  }, [refreshSignal, load]);

  // 10 s poll, paused while the tab is hidden.
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.hidden) {
        setPaused(true);
        return;
      }
      setPaused(false);
      load();
    }, pollMs);
    const onVis = () => {
      if (!document.hidden) {
        setPaused(false);
        load();
      } else {
        setPaused(true);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load, pollMs]);

  // 1 s ticker so running-run elapsed and the TTL countdown advance between polls.
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!pocId) {
    return <div className="empty">Select a POC to see its pipeline.</div>;
  }
  if (!detail) {
    return <div className="empty">{error ? <span className="err">{error}</span> : "Loading…"}</div>;
  }

  const { poc, stages, clarification, deployment, test, cloudResources } = detail;

  const stepMeta = (s: StageCell): string => {
    if (s.kind === "gate") {
      if (s.status === "succeeded") return `approved ${s.approvedVersion ?? ""} by ${s.approvedBy ?? ""}`.trim();
      return "awaiting approval";
    }
    if (s.status === "not_started") return "not started";
    const running = s.status === "running" || s.status === "waiting_user";
    const elapsed = running && s.started_at ? nowMs - Date.parse(s.started_at) : s.duration_ms ?? null;
    const parts: string[] = [];
    if (s.run_id) parts.push(s.run_id);
    parts.push(`${running ? "elapsed" : "took"} ${fmtDuration(elapsed)}`);
    if (s.error) parts.push(`✗ ${s.error.code}`);
    return parts.join(" · ");
  };

  return (
    <div className="board">
      <h2>{poc.title}</h2>
      <div className="sub">
        <span className="mono">{poc.poc_id}</span> ·{" "}
        <span className="pill status">{poc.status}</span> ·{" "}
        spec {poc.current_versions.spec ?? "—"} · code {poc.current_versions.code ?? "—"} ·{" "}
        updated {fmtTime(poc.updated_at)}
        <span style={{ marginLeft: 12 }}>
          <span className={`poll-dot ${paused ? "paused" : ""}`} /> {paused ? "poll paused (tab hidden)" : `polling every ${pollMs / 1000}s`}
          {lastPoll ? ` · last ${fmtTime(new Date(lastPoll).toISOString())}` : ""}
        </span>
        {error ? <span className="err"> · {error}</span> : null}
      </div>

      <div className="stepper">
        {stages.map((s) => (
          <div key={s.key} className={`step ${s.status}`}>
            <span className="dot" />
            <span className="name">{s.label}</span>
            <span className="meta mono">{stepMeta(s)}</span>
            <span className="kind">{s.kind}</span>
          </div>
        ))}
      </div>

      {clarification && (
        <div className="card warn">
          <h3>❓ Clarification needed{clarification.round ? ` (round ${clarification.round})` : ""}</h3>
          <ul>
            {clarification.questions.map((q, i) => (
              <li key={i}>{typeof q === "string" ? q : q.question ?? JSON.stringify(q)}</li>
            ))}
          </ul>
          <div style={{ color: "var(--muted)", fontSize: 12 }}>
            Answer in the chat pane; a new draft run will start.
          </div>
        </div>
      )}

      {deployment && (deployment.app || deployment.api || deployment.health) && (
        <div className="card">
          <h3>🔗 Deployment</h3>
          {deployment.app && (
            <div className="kv"><span className="k">App</span><a href={deployment.app} target="_blank" rel="noreferrer">{deployment.app}</a></div>
          )}
          {deployment.api && (
            <div className="kv"><span className="k">API</span><a href={deployment.api} target="_blank" rel="noreferrer">{deployment.api}</a></div>
          )}
          {deployment.health && (
            <div className="kv"><span className="k">Health</span><a href={deployment.health} target="_blank" rel="noreferrer">{deployment.health}</a></div>
          )}
          {deployment.instance_id && (
            <div className="kv"><span className="k">EC2</span><span className="mono">{deployment.instance_id}</span></div>
          )}
          {deployment.ttl_expires_at && (
            <div className="kv"><span className="k">TTL</span><span>{fmtCountdown(deployment.ttl_expires_at, nowMs)} <span style={{ color: "var(--muted)" }}>({fmtTime(deployment.ttl_expires_at)})</span></span></div>
          )}
        </div>
      )}

      {test && (
        <div className="card">
          <h3>🧪 Latest test report</h3>
          <div className="kv">
            <span className="k">Result</span>
            <span>
              {test.passed} passed · {test.failed} failed
              {test.skipped ? ` · ${test.skipped} skipped` : ""}
              {test.not_automatable ? ` · ${test.not_automatable} not automatable` : ""}
              {test.total ? ` · ${test.total} total` : ""}
            </span>
          </div>
          {test.failed > 0 && test.suspected_component && (
            <div className="kv"><span className="k">Suspected</span><span className="err">{test.suspected_component}</span></div>
          )}
        </div>
      )}

      <div className="card">
        <h3>
          ☁️ Cloud resources{" "}
          <span className={`badge ${cloudResources.activeCount > 0 ? "danger" : "zero"}`}>{cloudResources.activeCount}</span>
        </h3>
        {cloudResources.items.length === 0 ? (
          <div style={{ color: "var(--muted)" }}>No active resources.</div>
        ) : (
          <table className="tasks">
            <thead>
              <tr><th>Type</th><th>Resource</th><th>TTL</th></tr>
            </thead>
            <tbody>
              {cloudResources.items.map((r) => (
                <tr key={`${r.type}:${r.resource_id}`}>
                  <td>{r.type}</td>
                  <td className="mono">{r.resource_id}</td>
                  <td>{fmtCountdown(r.ttl_expires_at, nowMs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <details>
        <summary>Runs &amp; tasks ({detail.runs.length})</summary>
        {detail.runs.map((r) => (
          <div className="card" key={r.run_id} style={{ marginTop: 8 }}>
            <div className="kv">
              <span className="k">{r.stage}</span>
              <span className="mono">{r.run_id}</span>
            </div>
            <div className="kv">
              <span className="k">status</span>
              <span>
                {r.status} · {r.status === "running" && r.started_at ? `elapsed ${fmtDuration(nowMs - Date.parse(r.started_at))}` : `took ${fmtDuration(r.duration_ms)}`}
                {r.error ? <span className="err"> · {r.error.code}: {r.error.message}</span> : null}
              </span>
            </div>
            {detail.tasksByRun[r.run_id]?.length ? (
              <table className="tasks">
                <thead>
                  <tr><th>#</th><th>Agent</th><th>Tool</th><th>Mode</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {detail.tasksByRun[r.run_id].map((t) => (
                    <tr key={t.seq}>
                      <td>{t.seq}</td>
                      <td>{t.agent}</td>
                      <td className="mono">{t.tool}</td>
                      <td>{t.mode ?? "—"}</td>
                      <td>{t.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </div>
        ))}
      </details>
    </div>
  );
}
