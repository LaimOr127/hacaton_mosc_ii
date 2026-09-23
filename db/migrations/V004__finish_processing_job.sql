CREATE FUNCTION finish_processing_job()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED')
     AND NEW.status IS DISTINCT FROM OLD.status THEN
    UPDATE processing_jobs
    SET status = CASE WHEN NEW.status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS') THEN 'SUCCEEDED' ELSE NEW.status END,
        finished_at = now(), updated_at = now()
    WHERE inspection_id = NEW.id AND status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT');
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER inspections_finish_processing_job
AFTER UPDATE OF status ON inspections
FOR EACH ROW EXECUTE FUNCTION finish_processing_job();
