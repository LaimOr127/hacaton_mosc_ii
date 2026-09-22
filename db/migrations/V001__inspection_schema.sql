CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, description text,
  status text NOT NULL DEFAULT 'ACTIVE', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id),
  stage text NOT NULL CHECK (stage IN ('PROJECT','WORKING','AS_BUILT')), original_filename text NOT NULL,
  content_type text NOT NULL CHECK (content_type = 'application/pdf'), size_bytes bigint CHECK (size_bytes >= 0), sha256 char(64),
  storage_bucket text NOT NULL, storage_key text NOT NULL UNIQUE,
  status text NOT NULL CHECK (status IN ('UPLOADING','UPLOADED','VALIDATING','READY','PROCESSING','PROCESSED','FAILED')),
  page_count integer CHECK (page_count >= 0), error_code text, error_message text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (project_id, stage, sha256)
);

CREATE TABLE document_pages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number integer NOT NULL CHECK (page_number > 0), width numeric NOT NULL CHECK (width > 0), height numeric NOT NULL CHECK (height > 0),
  rotation integer NOT NULL DEFAULT 0, has_text_layer boolean NOT NULL, text_quality numeric CHECK (text_quality BETWEEN 0 AND 1),
  ocr_used boolean NOT NULL DEFAULT false, raw_text text, processing_ms integer CHECK (processing_ms >= 0), UNIQUE(document_id, page_number)
);

CREATE TABLE text_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), page_id uuid NOT NULL REFERENCES document_pages(id) ON DELETE CASCADE,
  block_order integer NOT NULL CHECK (block_order >= 0), text text NOT NULL, bbox jsonb NOT NULL,
  source text NOT NULL CHECK (source IN ('TEXT_LAYER','OCR')), confidence numeric CHECK (confidence BETWEEN 0 AND 1), created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(page_id, block_order, source)
);

CREATE TABLE inspections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id),
  status text NOT NULL CHECK (status IN ('QUEUED','PROCESSING','COMPLETED','COMPLETED_WITH_WARNINGS','FAILED','CANCELLED')),
  current_stage text, progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100), config jsonb NOT NULL DEFAULT '{}',
  stats jsonb NOT NULL DEFAULT '{}', started_at timestamptz, finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE processing_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), inspection_id uuid NOT NULL REFERENCES inspections(id), document_id uuid REFERENCES documents(id),
  job_type text NOT NULL, status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','RETRY_WAIT','CANCELLED')),
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0), max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
  locked_by text, heartbeat_at timestamptz, started_at timestamptz, finished_at timestamptz, error_code text, error_message text,
  metrics jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id), document_id uuid NOT NULL REFERENCES documents(id),
  page_id uuid NOT NULL REFERENCES document_pages(id), entity_type text NOT NULL CHECK (entity_type IN ('DOOR','WINDOW','WALL','ROOM','EQUIPMENT','GENERIC_REQUIREMENT')),
  raw_code text, normalized_code text, canonical_key text, attributes jsonb NOT NULL DEFAULT '{}', location jsonb NOT NULL DEFAULT '{}',
  extraction_method text NOT NULL CHECK (extraction_method IN ('RULE','LLM','MANUAL')), confidence numeric CHECK (confidence BETWEEN 0 AND 1), created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entity_matches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), inspection_id uuid NOT NULL REFERENCES inspections(id), source_entity_id uuid NOT NULL REFERENCES entities(id), target_entity_id uuid NOT NULL REFERENCES entities(id),
  source_stage text NOT NULL, target_stage text NOT NULL, method text NOT NULL, confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  score_breakdown jsonb NOT NULL, status text NOT NULL CHECK (status IN ('MATCHED','AMBIGUOUS','UNMATCHED','REJECTED')), created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(inspection_id, source_entity_id, target_entity_id)
);

CREATE TABLE findings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), inspection_id uuid NOT NULL REFERENCES inspections(id), finding_type text NOT NULL,
  entity_type text, canonical_key text, field_name text, expected_value jsonb, actual_value jsonb, absolute_delta numeric, relative_delta numeric,
  severity text NOT NULL CHECK (severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
  status text NOT NULL CHECK (status IN ('OPEN','CONFIRMED','REJECTED','NEEDS_REVIEW','RESOLVED')),
  title text NOT NULL, description text NOT NULL, explanation text, detector_version text NOT NULL, dedup_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(inspection_id, dedup_key)
);

CREATE TABLE finding_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), finding_id uuid NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
  side text NOT NULL CHECK (side IN ('EXPECTED','ACTUAL','CONTEXT')), document_id uuid NOT NULL REFERENCES documents(id),
  page_id uuid NOT NULL REFERENCES document_pages(id), text_block_id uuid REFERENCES text_blocks(id), bbox jsonb, quote text, entity_id uuid REFERENCES entities(id), created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), finding_id uuid NOT NULL REFERENCES findings(id), decision text NOT NULL CHECK (decision IN ('CONFIRMED','REJECTED','NEEDS_REVIEW','RESOLVED')),
  comment text, reviewer_id text, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor_type text, actor_id text, action text NOT NULL, resource_type text NOT NULL, resource_id text NOT NULL,
  correlation_id text, payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
  scope text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL, response_code integer, response_body jsonb, expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(scope, idempotency_key)
);

CREATE INDEX documents_project_stage_status_idx ON documents(project_id, stage, status);
CREATE INDEX document_pages_document_id_idx ON document_pages(document_id);
CREATE INDEX text_blocks_page_id_idx ON text_blocks(page_id);
CREATE INDEX inspections_project_id_idx ON inspections(project_id);
CREATE INDEX processing_jobs_inspection_id_idx ON processing_jobs(inspection_id);
CREATE INDEX processing_jobs_status_heartbeat_idx ON processing_jobs(status, heartbeat_at);
CREATE INDEX entities_lookup_idx ON entities(project_id, document_id, entity_type, normalized_code);
CREATE INDEX entity_matches_inspection_status_idx ON entity_matches(inspection_id, status);
CREATE INDEX findings_inspection_severity_status_idx ON findings(inspection_id, severity, status);
CREATE INDEX finding_evidence_finding_id_idx ON finding_evidence(finding_id);
CREATE INDEX reviews_finding_id_idx ON reviews(finding_id);
CREATE INDEX audit_events_resource_idx ON audit_events(resource_type, resource_id, created_at DESC);

GRANT CONNECT ON DATABASE inspection TO inspection_readonly;
GRANT USAGE ON SCHEMA public TO inspection_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO inspection_readonly;
ALTER DEFAULT PRIVILEGES FOR USER inspection_app IN SCHEMA public GRANT SELECT ON TABLES TO inspection_readonly;
