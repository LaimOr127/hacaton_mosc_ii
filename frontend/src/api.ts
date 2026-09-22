export type ApiEnvelope<T> = {
  data: T | null;
  error: ApiError | null;
  meta?: { correlation_id?: string; timestamp?: string };
};

export type ApiError = {
  code?: string;
  message?: string;
  details?: unknown;
};

export type Project = {
  id: string;
  name: string;
  description?: string | null;
  created_at?: string;
};

export type UploadStage = "PROJECT" | "WORKING" | "AS_BUILT";

export type UploadTicket = {
  document_id: string;
  upload_url: string;
  method?: "PUT";
  required_headers?: Record<string, string>;
  expires_at?: string;
};

export type InspectionStart = {
  inspection_id: string;
  status: string;
  status_url?: string;
};

export type Inspection = {
  id: string;
  project_id: string;
  status: string;
  current_stage?: string;
  progress?: number;
  stats?: Record<string, number>;
  warnings?: string[];
};

export type Finding = {
  id: string;
  severity: string;
  status: string;
  type: string;
  title?: string;
  message?: string;
  source_text?: string;
  target_text?: string;
};

declare global {
  interface Window {
    __APP_CONFIG__?: { apiBaseUrl?: string };
  }
}

export class PublicApiError extends Error {
  constructor(
    message: string,
    public code?: string,
    public correlationId?: string,
    public status?: number,
  ) {
    super(message);
  }
}

export function apiBaseUrl() {
  return (window.__APP_CONFIG__?.apiBaseUrl || "/api/v1").replace(/\/$/, "");
}

async function parseEnvelope<T>(response: Response): Promise<ApiEnvelope<T>> {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new PublicApiError("Public API is unavailable or returned a non-JSON response.", "NON_JSON_RESPONSE", undefined, response.status);
  }

  return (await response.json()) as ApiEnvelope<T>;
}

export async function apiRequest<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  const envelope = await parseEnvelope<T>(response);
  if (!response.ok || envelope.error || envelope.data === null) {
    throw new PublicApiError(
      envelope.error?.message || `Request failed with HTTP ${response.status}`,
      envelope.error?.code,
      envelope.meta?.correlation_id,
      response.status,
    );
  }
  return envelope.data;
}

export function uploadToPresignedUrl(ticket: UploadTicket, file: File, onProgress: (percent: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(ticket.method || "PUT", ticket.upload_url);
    for (const [key, value] of Object.entries(ticket.required_headers || {})) {
      request.setRequestHeader(key, value);
    }
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve();
      else reject(new PublicApiError("Upload failed before confirmation.", "UPLOAD_FAILED", undefined, request.status));
    };
    request.onerror = () => reject(new PublicApiError("Upload endpoint is unavailable.", "UPLOAD_UNAVAILABLE"));
    request.send(file);
  });
}
