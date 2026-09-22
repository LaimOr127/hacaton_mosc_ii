import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  PublicApiError,
  apiBaseUrl,
  apiRequest,
  uploadToPresignedUrl,
  type Finding,
  type Inspection,
  type InspectionStart,
  type Project,
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
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null);
  const [filters, setFilters] = useState({ severity: "", status: "", type: "" });
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
      setSelectedProject(project);
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
    pollingAbort.current?.abort();
    const controller = new AbortController();
    pollingAbort.current = controller;
    let delay = 1000;
    while (!controller.signal.aborted) {
      const current = await apiRequest<Inspection>(`/inspections/${inspectionId}`, undefined, controller.signal);
      setInspection(current);
      if (["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"].includes(current.status)) {
        if (current.status !== "FAILED") await loadFindings(current.id);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 1.5, 8000);
    }
  }

  async function loadFindings(inspectionId: string) {
    try {
      const data = await apiRequest<Finding[]>(`/inspections/${inspectionId}/findings`);
      setFindings(data);
      setSelectedFinding(data[0] || null);
    } catch (error) {
      setApiError(publicError(error));
    }
  }

  async function reviewFinding(status: "CONFIRMED" | "REJECTED" | "NEEDS_REVIEW") {
    if (!selectedFinding) return;
    try {
      const updated = await apiRequest<Finding>(`/findings/${selectedFinding.id}/reviews`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ status }),
      });
      setSelectedFinding(updated);
      setFindings((current) => current.map((finding) => (finding.id === updated.id ? updated : finding)));
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
          (!filters.type || finding.type === filters.type),
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
              <button key={project.id} className="list-item" type="button" onClick={() => setSelectedProject(project)}>
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
                  onClick={() => inspection && window.open(`${apiBaseUrl()}/inspections/${inspection.id}/export`, "_blank", "noopener,noreferrer")}
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
            {(["severity", "status", "type"] as const).map((field) => (
              <label key={field}>
                {field}
                <input value={filters[field]} onChange={(event) => setFilters((current) => ({ ...current, [field]: event.target.value }))} />
              </label>
            ))}
          </div>
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
                <tr key={finding.id} onClick={() => setSelectedFinding(finding)}>
                  <td>{finding.severity}</td>
                  <td>{finding.status}</td>
                  <td>{finding.type}</td>
                  <td>{finding.title || finding.message || finding.id}</td>
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
            <div className="evidence">
              <article>
                <h3>Source evidence</h3>
                <p>{selectedFinding.source_text || "No source evidence URL/text in API response."}</p>
              </article>
              <article>
                <h3>Target evidence</h3>
                <p>{selectedFinding.target_text || "No target evidence URL/text in API response."}</p>
              </article>
            </div>
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
