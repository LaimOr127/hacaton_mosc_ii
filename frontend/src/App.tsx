import { useRef, useState } from "react";
import {
  PublicApiError,
  apiRequest,
  uploadToPresignedUrl,
  type Finding,
  type Inspection,
  type InspectionStart,
  type PageResult,
  type Project,
  type UploadStage,
  type UploadTicket,
} from "./api";

type DocumentSlot = { id: UploadStage; title: string };
const documents: DocumentSlot[] = [
  { id: "PROJECT", title: "Проектная документация" },
  { id: "WORKING", title: "Рабочая документация" },
  { id: "AS_BUILT", title: "Исполнительная документация" },
];
type Files = Record<UploadStage, File | null>;
const emptyFiles = (): Files => ({ PROJECT: null, WORKING: null, AS_BUILT: null });
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Не удалось связаться с сервисом. Повторите попытку.";

function FileUpload({ slot, file, onChange }: { slot: DocumentSlot; file: File | null; onChange: (file: File) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  function choose(file?: File) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) return window.alert("Можно выбрать только PDF-файл.");
    onChange(file);
  }
  return <div className="document-row">
    <div className="document-label">{slot.title}</div><div className="label-divider" />
    <button type="button" className={`file-dropzone ${file ? "has-file" : ""} ${dragging ? "is-dragging" : ""}`} onClick={() => inputRef.current?.click()}
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files[0]); }}>
      <span className="file-caption">{file?.name || "Выбрать PDF файл"}</span>{file && <span className="file-meta">PDF</span>}
    </button>
    <input ref={inputRef} className="hidden-input" type="file" accept="application/pdf,.pdf" onChange={(event) => choose(event.target.files?.[0])} />
  </div>;
}

export default function App() {
  const [files, setFiles] = useState<Files>(emptyFiles);
  const [menuOpen, setMenuOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ inspection: Inspection; findings: Finding[] } | null>(null);
  const allFilesSelected = documents.every(({ id }) => files[id]);

  async function upload(project: Project, stage: UploadStage, file: File, index: number) {
    const ticket = await apiRequest<UploadTicket>(`/projects/${project.id}/documents/uploads`, { method: "POST", body: JSON.stringify({ filename: file.name, content_type: file.type || "application/pdf", size_bytes: file.size, stage }) });
    await uploadToPresignedUrl(ticket, file, (percent) => setProgress(Math.round(index * 20 + percent / 5)));
    await apiRequest(`/documents/${ticket.document_id}/confirm`, { method: "POST", body: JSON.stringify({}) });
  }

  async function waitForInspection(inspectionId: string) {
    while (true) {
      const inspection = await apiRequest<Inspection>(`/inspections/${inspectionId}`);
      setProgress(Math.max(60, inspection.progress || 60));
      if (inspection.status === "FAILED") throw new Error("Проверка завершилась с ошибкой.");
      if (["COMPLETED", "COMPLETED_WITH_WARNINGS"].includes(inspection.status)) {
        const page = await apiRequest<PageResult<Finding>>(`/inspections/${inspection.id}/findings?page=1&page_size=20`);
        return { inspection, findings: page.items };
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
  }

  async function startCheck() {
    if (!allFilesSelected || checking) return;
    setChecking(true); setProgress(0); setError(null); setResult(null);
    try {
      const project = await apiRequest<Project>("/projects", { method: "POST", body: JSON.stringify({ name: `Проверка ${new Date().toLocaleString("ru-RU")}` }) });
      for (const [index, { id }] of documents.entries()) await upload(project, id, files[id]!, index);
      setProgress(65);
      const started = await apiRequest<InspectionStart>(`/projects/${project.id}/inspections`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({}) });
      const completed = await waitForInspection(started.inspection_id);
      setProgress(100); setResult(completed);
    } catch (cause) { setError(errorMessage(cause)); } finally { setChecking(false); }
  }
  function reset() { setFiles(emptyFiles()); setResult(null); setError(null); setProgress(0); }

  return <div className="app">
    <header className="header"><div className="header-inner"><a href="#top" className="brand"><span>Автоматическая проверка</span><strong>строительной документации</strong></a><button type="button" className={`menu-button ${menuOpen ? "is-open" : ""}`} onClick={() => setMenuOpen((open) => !open)} aria-label="Открыть меню"><span /><span /><span /></button></div></header>
    <main className="main" id="top"><section className="hero"><p className="intro">ИИ сравнивает проектную, рабочую и исполнительную документацию, выявляет несоответствия и показывает, где найдена ошибка.</p><section className="upload-card">{documents.map((slot) => <FileUpload key={slot.id} slot={slot} file={files[slot.id]} onChange={(file) => setFiles((current) => ({ ...current, [slot.id]: file }))} />)}</section>{error && <p className="error" role="alert">{error}</p>}<button type="button" className="start-button" disabled={!allFilesSelected || checking} onClick={() => void startCheck()}>{checking ? `ПРОВЕРКА ${progress}%` : "НАЧАТЬ ПРОВЕРКУ"}</button></section></main>
    <footer className="footer">© 2026 AI Construction Control</footer>
    {menuOpen && <><button className="menu-backdrop" onClick={() => setMenuOpen(false)} aria-label="Закрыть меню" /><aside className="menu-panel"><div className="menu-panel-top"><span>О сервисе</span><button type="button" className="menu-close" onClick={() => setMenuOpen(false)}>×</button></div><p>Загрузите три PDF-документа. Сервис создаст проверку, сравнит данные и покажет найденные несоответствия.</p></aside></>}
    {checking && <div className="modal-layer"><div className="modal-card progress-card"><div className="spinner" /><h2>Проверка документов</h2><p>Файлы загружаются и обрабатываются сервисом.</p><div className="progress-track"><div className="progress-fill" style={{ width: `${progress}%` }} /></div><span className="progress-number">{progress}%</span></div></div>}
    {result && <div className="modal-layer"><div className="modal-card result-card"><div className="result-icon">✓</div><h2>Проверка завершена</h2><p>Статус: {result.inspection.status}. Найдено несоответствий: {result.findings.length}.</p>{result.findings.length > 0 && <ul className="finding-list">{result.findings.map((finding) => <li key={finding.id}><strong>{finding.severity}</strong> {finding.title || finding.description || finding.finding_type}</li>)}</ul>}<div className="result-actions"><button type="button" className="secondary-button" onClick={() => setResult(null)}>Закрыть</button><button type="button" className="primary-small-button" onClick={reset}>Загрузить заново</button></div></div></div>}
  </div>;
}
