CREATE OR REPLACE FUNCTION fn_audit_log()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
  v_user_id UUID;
  v_ip      TEXT;
  v_row     JSONB;
BEGIN
  v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
  v_ip      := NULLIF(current_setting('app.client_ip', TRUE), '');
  v_row := CASE TG_OP WHEN 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  INSERT INTO audit_log (id, user_id, action, module, details_json, ip_address, created_at)
  VALUES (
    gen_random_uuid(),
    v_user_id,
    TG_OP,
    TG_TABLE_NAME,
    v_row::TEXT,
    v_ip::BYTEA,
    NOW()
  );
  RETURN COALESCE(NEW, OLD);
END;
$$;
