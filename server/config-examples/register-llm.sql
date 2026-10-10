-- 在 manager-api 的数据库里注册这个 LLM，并让某个智能体使用它（按需替换 <agent_id>）
SET NAMES utf8mb4;
INSERT INTO ai_model_config (id, model_type, model_code, model_name, is_default, is_enabled, config_json, sort, create_date, update_date)
VALUES ('LLM_LocalPaced', 'LLM', 'LocalPaced', 'Local llama.cpp (GPU turn-taking)', 0, 1,
        '{"type":"llamacpp_paced","base_url":"http://host.docker.internal:18080","voice_health_url":"http://host.docker.internal:18766/health","max_tokens":400,"temperature":0.8,"top_p":0.95,"top_k":64}',
        1, NOW(), NOW())
ON DUPLICATE KEY UPDATE config_json = VALUES(config_json), update_date = NOW();
UPDATE ai_agent SET llm_model_id = 'LLM_LocalPaced' WHERE id = '<agent_id>';
-- 改完清一下 redis 缓存：redis-cli FLUSHDB（或只删对应的 key）
