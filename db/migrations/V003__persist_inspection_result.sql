CREATE OR REPLACE FUNCTION persist_inspection_result(p_inspection_id uuid, p_result jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  doc jsonb;
  page jsonb;
  block jsonb;
  entity jsonb;
  comparison jsonb;
  match jsonb;
  finding jsonb;
  evidence jsonb;
  v_finding_id uuid;
  project_uuid uuid;
BEGIN
  SELECT project_id INTO project_uuid FROM inspections WHERE id = p_inspection_id FOR UPDATE;
  IF project_uuid IS NULL THEN
    RAISE EXCEPTION 'inspection not found';
  END IF;

  FOR doc IN SELECT value FROM jsonb_array_elements(p_result->'documents') LOOP
    FOR page IN SELECT value FROM jsonb_array_elements(doc->'pages') LOOP
      INSERT INTO document_pages
        (id, document_id, page_number, width, height, rotation, has_text_layer, text_quality, ocr_used, raw_text)
      VALUES
        ((page->>'page_id')::uuid, (doc->>'document_id')::uuid, (page->>'page_number')::integer,
         (page->>'width')::numeric, (page->>'height')::numeric, (page->>'rotation')::integer,
         NOT (page->>'needs_ocr')::boolean, (page->>'text_quality')::numeric,
         (page->>'ocr_used')::boolean, page->>'raw_text')
      ON CONFLICT (id) DO UPDATE SET
        raw_text=EXCLUDED.raw_text, ocr_used=EXCLUDED.ocr_used, text_quality=EXCLUDED.text_quality;

      FOR block IN SELECT value FROM jsonb_array_elements(page->'blocks') LOOP
        INSERT INTO text_blocks (id, page_id, block_order, text, bbox, source, confidence)
        VALUES ((block->>'id')::uuid, (page->>'page_id')::uuid,
                (block->>'order')::integer, block->>'text', block->'bbox',
                block->>'source', (block->>'confidence')::numeric)
        ON CONFLICT (id) DO UPDATE SET
          text=EXCLUDED.text, bbox=EXCLUDED.bbox, confidence=EXCLUDED.confidence;
      END LOOP;
    END LOOP;

    FOR entity IN SELECT value FROM jsonb_array_elements(doc->'entities') LOOP
      INSERT INTO entities
        (id, project_id, document_id, page_id, entity_type, raw_code, normalized_code,
         canonical_key, attributes, extraction_method, confidence)
      VALUES
        ((entity->>'id')::uuid, project_uuid, (doc->>'document_id')::uuid,
         (entity->'evidence'->>'page_id')::uuid, entity->>'entity_type',
         entity->>'raw_code', entity->>'normalized_code', entity->>'canonical_key',
         entity->'attributes', 'RULE', (entity->>'confidence')::numeric)
      ON CONFLICT (id) DO UPDATE SET
        attributes=EXCLUDED.attributes, confidence=EXCLUDED.confidence;
    END LOOP;

    UPDATE documents SET status='PROCESSED', page_count=(doc->>'page_count')::integer,
      updated_at=now() WHERE id=(doc->>'document_id')::uuid AND project_id=project_uuid;
  END LOOP;

  FOR comparison IN SELECT value FROM jsonb_array_elements(p_result->'comparisons') LOOP
    FOR match IN SELECT value FROM jsonb_array_elements(comparison->'matches') LOOP
      IF match->>'status' = 'MATCHED' THEN
        INSERT INTO entity_matches
          (inspection_id, source_entity_id, target_entity_id, source_stage, target_stage,
           method, confidence, score_breakdown, status)
        VALUES
          (p_inspection_id, (match->>'expected_entity_id')::uuid,
           (match->>'actual_entity_id')::uuid, comparison->>'expected_stage',
           comparison->>'actual_stage', match->>'method',
           (match->>'confidence')::numeric, match->'score_breakdown', match->>'status')
        ON CONFLICT (inspection_id, source_entity_id, target_entity_id) DO UPDATE SET
          confidence=EXCLUDED.confidence, score_breakdown=EXCLUDED.score_breakdown;
      END IF;
    END LOOP;
  END LOOP;

  FOR finding IN SELECT value FROM jsonb_array_elements(p_result->'findings') LOOP
    INSERT INTO findings
      (inspection_id, finding_type, entity_type, canonical_key, field_name, expected_value,
       actual_value, absolute_delta, relative_delta, severity, status, title, description,
       detector_version, dedup_key)
    VALUES
      (p_inspection_id, finding->>'finding_type', finding->>'entity_type',
       finding->>'canonical_key', finding->>'field_name', finding->'expected_value',
       finding->'actual_value', (finding->>'absolute_delta')::numeric,
       (finding->>'relative_delta')::numeric, finding->>'severity', finding->>'status',
       finding->>'title', finding->>'description', finding->>'detector_version',
       finding->>'dedup_key')
    ON CONFLICT (inspection_id, dedup_key) DO UPDATE SET
      expected_value=EXCLUDED.expected_value, actual_value=EXCLUDED.actual_value,
      absolute_delta=EXCLUDED.absolute_delta, relative_delta=EXCLUDED.relative_delta,
      severity=EXCLUDED.severity, title=EXCLUDED.title, description=EXCLUDED.description,
      updated_at=now()
    RETURNING id INTO v_finding_id;

    DELETE FROM finding_evidence WHERE finding_id=v_finding_id;
    FOR evidence IN SELECT value FROM jsonb_array_elements(finding->'evidence') LOOP
      INSERT INTO finding_evidence
        (finding_id, side, document_id, page_id, text_block_id, bbox, quote, entity_id)
      VALUES
        (v_finding_id, evidence->>'side', (evidence->>'document_id')::uuid,
         (evidence->>'page_id')::uuid, (evidence->>'text_block_id')::uuid,
         evidence->'bbox', evidence->>'quote', (evidence->>'entity_id')::uuid);
    END LOOP;
  END LOOP;

  UPDATE inspections SET status='COMPLETED', current_stage='COMPLETE', progress=100,
    stats=p_result->'stats', finished_at=now(), updated_at=now()
  WHERE id=p_inspection_id;

  INSERT INTO audit_events (actor_type, action, resource_type, resource_id, payload)
  VALUES ('SYSTEM', 'INSPECTION_COMPLETED', 'inspection', p_inspection_id::text,
          jsonb_build_object('stats', p_result->'stats'));
  RETURN p_result->'stats';
END;
$$;

GRANT EXECUTE ON FUNCTION persist_inspection_result(uuid, jsonb) TO :"app_user";
