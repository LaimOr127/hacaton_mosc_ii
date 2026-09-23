import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  PublicApiError,
  apiBaseUrl,
  apiRequest,
  uploadToPresignedUrl,
  type Finding,
  type Inspection,
  type InspectionStart,
  type PageResult,
  type Project,
  type ReviewDecision,
  type UploadStage,
  type UploadTicket,
} from "./api";

type UploadState = {
  file?: File;
  progress: number;
  status: "idle" | "uploading" | "confirmed" | "failed";
  error?: string;
};

const stages: UploadStage[] = ["PROJECT", "WORKING", "AS_BUILT"];

function publicError(error: unknown) {
  if (error instanceof PublicApiError) {
    return {
      text: error.message,
      detail: error.correlationId ? `Correlation ID: ${error.correlationId}` : undefined,
    };
  }
  return { text: "Unexpected frontend error.", detail: undefined };
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projectName, setProjectName] = useState("");
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [apiError, setApiError] = useState<{ text: string; detail?: string } | null>(null);
  const [uploads, setUploads] = useState<Record<UploadStage, UploadState>>({
    PROJECT: { progress: 0, status: "idle" },
    WORKING: { progress: 0, status: "idle" },
    AS_BUILT: { progress: 0, status: "idle" },
  });
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingsPage, setFindingsPage] = useState({ page: 1, page_size: 20, total: 0 });
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [filters, setFilters] = useState({ severity: "", status: "", finding_type: "" });
  const pollingAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    void loadProjects();
    return () => pollingAbort.current?.abort();
  }, []);

  async function loadProjects() {
    setLoadingProjects(true);
    setApiError(null);
    try {
      const data = await apiRequest<Project[]>("/projects");
      setProjects(data);
    } catch (error) {
      setApiError(publicError(error));
    } finally {
      setLoadingProjects(false);
    }
  }

  function selectProject(project: Project) {
    pollingAbort.current?.abort();
    setSelectedProject(project);
    setInspection(null);
    setFindings([]);
    setSelectedFinding(null);
    setUploads({
      PROJECT: { progress: 0, status: "idle" },
      WORKING: { progress: 0, status: "idle" },
      AS_BUILT: { progress: 0, status: "idle" },
    });
  }

  async function createProject(event: FormEvent) {
    event.preventDefault();
    setApiError(null);
    setNotice(null);
    try {
      const project = await apiRequest<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: projectName.trim() }),
      });
      setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
      selectProject(project);
      setProjectName("");
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  async function uploadStage(stage: UploadStage) {
    if (!selectedProject || !uploads[stage].file) return;
    const file = uploads[stage].file;
    setUploads((current) => ({
      ...current,
      [stage]: { ...current[stage], status: "uploading", progress: 0, error: undefined },
    }));
    setApiError(null);
    try {
      const ticket = await apiRequest<UploadTicket>(`/projects/${selectedProject.id}/documents/uploads`, {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || "application/pdf",
          size_bytes: file.size,
          stage,
        }),
      });
      await uploadToPresignedUrl(ticket, file, (progress) => {
        setUploads((current) => ({ ...current, [stage]: { ...current[stage], progress } }));
      });
      await apiRequest(`/documents/${ticket.document_id}/confirm`, { method: "POST", body: JSON.stringify({}) });
      setUploads((current) => ({ ...current, [stage]: { ...current[stage], status: "confirmed", progress: 100 } }));
    } catch (error) {
      const message = publicError(error).text;
      setUploads((current) => ({ ...current, [stage]: { ...current[stage], status: "failed", error: message } }));
      setApiError(publicError(error));
    }
  }

  async function startInspection() {
    if (!selectedProject) return;
    setApiError(null);
    setNotice(null);
    pollingAbort.current?.abort();
    try {
      const started = await apiRequest<InspectionStart>(`/projects/${selectedProject.id}/inspections`, {
        method: "POST",
        body: JSON.stringify({}),
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      await pollInspection(started.inspection_id);
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  async function pollInspection(inspectionId: string) {
    const controller = new AbortController();
    pollingAbort.current = controller;
    let delay = 1000;
    try {
      while (!controller.signal.aborted) {
        const current = await apiRequest<Inspection>(`/inspections/${inspectionId}`, undefined, controller.signal);
        setInspection(current);
        if (["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"].includes(current.status)) {
          if (current.status !== "FAILED") await loadFindings(current.id);
          return;
        }
        await cancellableDelay(delay, controller.signal);
        delay = Math.min(delay * 1.5, 8000);
      }
    } catch (error) {
      if (!controller.signal.aborted) setApiError(publicError(error));
    } finally {
      if (pollingAbort.current === controller) pollingAbort.current = null;
    }
  }

  async function loadFindings(inspectionId: string) {
    try {
      const query = new URLSearchParams({ page: "1", page_size: "20" });
      for (const [key, value] of Object.entries(filters)) {
        if (value) query.set(key, value);
      }
      const data = await apiRequest<PageResult<Finding>>(`/inspections/${inspectionId}/findings?${query}`);
      setFindings(data.items);
      setFindingsPage({ page: data.page, page_size: data.page_size, total: data.total });
      if (data.items[0]) await loadFindingDetail(data.items[0].id);
      else setSelectedFinding(null);
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  async function loadFindingDetail(findingId: string) {
    try {
      const detail = await apiRequest<Finding>(`/findings/${findingId}`);
      setSelectedFinding(detail);
      setFindings((current) => current.map((finding) => (finding.id === detail.id ? { ...finding, ...detail } : finding)));
      setReviewComment("");
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  async function reviewFinding(decision: ReviewDecision) {
    if (!selectedFinding) return;
    try {
      await apiRequest<unknown>(`/findings/${selectedFinding.id}/reviews`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ decision, comment: reviewComment.trim() || undefined }),
      });
      await loadFindingDetail(selectedFinding.id);
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  async function exportInspection() {
    if (!inspection) return;
    try {
      const report = await apiRequest<unknown>(`/inspections/${inspection.id}/export`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `inspection-${inspection.id}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  const filteredFindings = useMemo(
    () =>
      findings.filter(
        (finding) =>
          (!filters.severity || finding.severity === filters.severity) &&
          (!filters.status || finding.status === filters.status) &&
          (!filters.finding_type || finding.finding_type === filters.finding_type),
      ),
    [findings, filters],
  );

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <p className="eyebrow">Construction Inspection</p>
          <h1>PDF inspection cockpit</h1>
        </div>
        <span className="api-pill">API: {apiBaseUrl()}</span>
      </header>

      {apiError && (
        <section className="alert" role="alert">
          <strong>Dependency state:</strong> {apiError.text}
          {apiError.detail && <span>{apiError.detail}</span>}
        </section>
      )}
      {notice && <section className="notice">{notice}</section>}

      <section className="grid">
        <aside className="panel">
          <h2>Projects</h2>
          <form className="stack" onSubmit={createProject}>
            <label>
              Project name
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} required minLength={2} />
            </label>
            <button type="submit">Create project</button>
          </form>
          <button className="ghost" type="button" onClick={loadProjects}>
            Refresh
          </button>
          {loadingProjects && <p className="muted">Loading projects...</p>}
          {!loadingProjects && projects.length === 0 && <p className="muted">No projects from public API yet.</p>}
          <div className="list">
            {projects.map((project) => (
              <button key={project.id} className="list-item" type="button" onClick={() => selectProject(project)}>
                <strong>{project.name}</strong>
                <span>{project.id}</span>
              </button>
            ))}
          </div>
        </aside>

        <section className="panel wide">
          <h2>{selectedProject ? selectedProject.name : "Project detail"}</h2>
          {!selectedProject && <p className="muted">Create or select a project to unlock upload and inspection controls.</p>}
          {selectedProject && (
            <>
              <p className="muted">{selectedProject.id}</p>
              <div className="uploads">
                {stages.map((stage) => (
                  <div className="upload-row" key={stage}>
                    <label>
                      {stage} PDF
                      <input
                        type="file"
                        accept="application/pdf,.pdf"
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          setUploads((current) => ({ ...current, [stage]: { file, progress: 0, status: "idle" } }));
                        }}
                      />
                    </label>
                    <progress value={uploads[stage].progress} max={100} aria-label={`${stage} upload progress`} />
                    <button type="button" disabled={!uploads[stage].file || uploads[stage].status === "uploading"} onClick={() => uploadStage(stage)}>
                      Upload
                    </button>
                    <span className={uploads[stage].status === "failed" ? "bad" : "muted"}>{uploads[stage].error || uploads[stage].status}</span>
                  </div>
                ))}
              </div>
              <div className="actions">
                <button type="button" onClick={startInspection}>
                  Start inspection
                </button>
                <button
                  type="button"
                  className="ghost"
                  disabled={!inspection}
                  onClick={exportInspection}
                >
                  Export
                </button>
              </div>
            </>
          )}
        </section>
      </section>

      <section className="grid">
        <section className="panel">
          <h2>Progress</h2>
          {!inspection && <p className="muted">No inspection started.</p>}
          {inspection && (
            <div className="stack">
              <strong>{inspection.status}</strong>
              <progress value={inspection.progress || 0} max={100} aria-label="Inspection progress" />
              <span className="muted">{inspection.current_stage || "Waiting for stage"}</span>
              <pre>{JSON.stringify(inspection.stats || {}, null, 2)}</pre>
            </div>
          )}
        </section>

        <section className="panel wide">
          <h2>Findings</h2>
          <div className="filters">
            {(["severity", "status", "finding_type"] as const).map((field) => (
              <label key={field}>
                {field}
                <input value={filters[field]} onChange={(event) => setFilters((current) => ({ ...current, [field]: event.target.value }))} />
              </label>
            ))}
            <button type="button" className="ghost" disabled={!inspection} onClick={() => inspection && loadFindings(inspection.id)}>
              Apply
            </button>
          </div>
          <p className="muted">
            Page {findingsPage.page}, {filteredFindings.length} of {findingsPage.total}
          </p>
          {filteredFindings.length === 0 && <p className="muted">No findings returned by API.</p>}
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Status</th>
                <th>Type</th>
                <th>Finding</th>
              </tr>
            </thead>
            <tbody>
              {filteredFindings.map((finding) => (
                <tr key={finding.id} onClick={() => loadFindingDetail(finding.id)}>
                  <td>{finding.severity}</td>
                  <td>{finding.status}</td>
                  <td>{finding.finding_type}</td>
                  <td>{finding.title || finding.description || finding.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </section>

      <section className="panel">
        <h2>Finding detail</h2>
        {!selectedFinding && <p className="muted">Select a finding after inspection completes.</p>}
        {selectedFinding && (
          <>
            <dl className="finding-meta">
              <div>
                <dt>Expected</dt>
                <dd>{formatValue(selectedFinding.expected_value)}</dd>
              </div>
              <div>
                <dt>Actual</dt>
                <dd>{formatValue(selectedFinding.actual_value)}</dd>
              </div>
              <div>
                <dt>Entity</dt>
                <dd>{selectedFinding.entity_type || "n/a"}</dd>
              </div>
              <div>
                <dt>Field</dt>
                <dd>{selectedFinding.field_name || "n/a"}</dd>
              </div>
            </dl>
            <div className="evidence">
              {(["EXPECTED", "ACTUAL"] as const).map((side) => (
                <article key={side}>
                  <h3>{side === "EXPECTED" ? "Expected evidence" : "Actual evidence"}</h3>
                  {renderEvidence(selectedFinding, side)}
                </article>
              ))}
            </div>
            <label className="review-comment">
              Review comment
              <textarea value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} />
            </label>
            <div className="actions">
              <button type="button" onClick={() => reviewFinding("CONFIRMED")}>
                Confirm
              </button>
              <button type="button" onClick={() => reviewFinding("REJECTED")}>
                Reject
              </button>
              <button type="button" onClick={() => reviewFinding("NEEDS_REVIEW")}>
                Needs review
              </button>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

function cancellableDelay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Polling cancelled", "AbortError"));
      },
      { once: true },
    );
  });
}

function formatValue(value: unknown) {
  if (value === undefined || value === null || value === "") return "n/a";
  return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value);
}

function renderEvidence(finding: Finding, side: "EXPECTED" | "ACTUAL") {
  const evidence = finding.evidence?.find((item) => item.side === side);
  if (!evidence) return <p className="muted">No {side.toLowerCase()} evidence in API response.</p>;
  return <EvidenceCard key={evidence.document_id} evidence={evidence} side={side} />;
}

function EvidenceCard({ evidence, side }: { evidence: NonNullable<Finding["evidence"]>[number]; side: string }) {
  const [url, setUrl] = useState(evidence.pdf_url || evidence.file_url || evidence.url);
  const [error, setError] = useState<string | null>(null);

  async function openPdf() {
    if (!evidence.download_url) return;
    try {
      const result = await apiRequest<{ url: string }>(evidence.download_url.replace(/^\/api\/v1/, ""));
      setUrl(result.url);
      setError(null);
    } catch (cause) {
      setError(publicError(cause).text);
    }
  }

  return (
    <div className="evidence-body">
      <p>
        <strong>{evidence.document_stage || "Document"}</strong>
        {evidence.document_name ? `: ${evidence.document_name}` : ""}
      </p>
      <p>Page: {evidence.page_number || "n/a"}</p>
      <p>{evidence.quote || "No quote returned."}</p>
      <pre>{formatValue(evidence.bbox)}</pre>
      {evidence.download_url && <button type="button" className="ghost" onClick={openPdf}>Open PDF evidence</button>}
      {error && <p role="alert" className="bad">{error}</p>}
      {url && (
        <>
          <a href={url} target="_blank" rel="noreferrer">
            Open PDF
          </a>
          <iframe title={`${side} PDF evidence`} src={url} />
        </>
      )}
    </div>
  );
}
