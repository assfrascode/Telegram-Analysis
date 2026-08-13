"""Baseline the SQLAlchemy schema and adopt unversioned MVP databases.

Revision ID: 20260813_0001
Revises:
Create Date: 2026-08-13
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260813_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE_DDL = (
    "CREATE TYPE telegramconnectionstatus AS ENUM ('pending', 'connected', 'invalid', 'disconnected')",
    "CREATE TYPE telegramingestmode AS ENUM ('backend_pull', 'external_push')",
    "CREATE TYPE telegramchatstatus AS ENUM ('active', 'syncing', 'error', 'archived')",
    "CREATE TYPE telegramsyncstatus AS ENUM ('running', 'completed', 'failed')",
    "CREATE TYPE stepstatus AS ENUM ('pending', 'running', 'completed', 'failed_retryable', 'failed_permanent', 'skipped')",
    "CREATE TYPE uploadstatus AS ENUM ('created', 'uploading', 'uploaded', 'rejected')",
    "CREATE TYPE jobsourcetype AS ENUM ('upload', 'telegram_chat')",
    "CREATE TYPE jobstatus AS ENUM ('queued', 'running', 'cancelling', 'cancelled', 'failed', 'completed')",
    'CREATE TABLE users (\n\tid UUID NOT NULL, \n\temail VARCHAR(320) NOT NULL, \n\tpassword_hash VARCHAR(255) NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_users PRIMARY KEY (id)\n)',
    'CREATE UNIQUE INDEX ix_users_email ON users (email)',
    'CREATE TABLE telegram_connections (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tapi_id BIGINT NOT NULL, \n\tapi_hash_encrypted TEXT NOT NULL, \n\tsession_encrypted TEXT NOT NULL, \n\ttelegram_user_id BIGINT NOT NULL, \n\tphone VARCHAR(64), \n\tdisplay_name VARCHAR(512), \n\tstatus telegramconnectionstatus NOT NULL, \n\tlast_error TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlast_verified_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_telegram_connections PRIMARY KEY (id), \n\tCONSTRAINT fk_telegram_connections_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id)\n)',
    'CREATE UNIQUE INDEX ix_telegram_connections_owner_user_id ON telegram_connections (owner_user_id)',
    'CREATE INDEX ix_telegram_connections_telegram_user_id ON telegram_connections (telegram_user_id)',
    'CREATE INDEX ix_telegram_connections_status ON telegram_connections (status)',
    'CREATE TABLE telegram_login_challenges (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tapi_id BIGINT NOT NULL, \n\tapi_hash_encrypted TEXT NOT NULL, \n\tphone VARCHAR(64) NOT NULL, \n\tphone_code_hash_encrypted TEXT NOT NULL, \n\tsession_encrypted TEXT NOT NULL, \n\trequires_password BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_telegram_login_challenges PRIMARY KEY (id), \n\tCONSTRAINT fk_telegram_login_challenges_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id)\n)',
    'CREATE INDEX ix_telegram_login_challenges_expires_at ON telegram_login_challenges (expires_at)',
    'CREATE INDEX ix_telegram_login_challenges_owner_user_id ON telegram_login_challenges (owner_user_id)',
    'CREATE TABLE telegram_ingest_tokens (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tlast_used_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_telegram_ingest_tokens PRIMARY KEY (id), \n\tCONSTRAINT fk_telegram_ingest_tokens_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id)\n)',
    'CREATE INDEX ix_telegram_ingest_tokens_expires_at ON telegram_ingest_tokens (expires_at)',
    'CREATE INDEX ix_telegram_ingest_tokens_revoked_at ON telegram_ingest_tokens (revoked_at)',
    'CREATE INDEX ix_telegram_ingest_tokens_owner_user_id ON telegram_ingest_tokens (owner_user_id)',
    'CREATE UNIQUE INDEX ix_telegram_ingest_tokens_token_hash ON telegram_ingest_tokens (token_hash)',
    'CREATE TABLE question_sets (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tdescription TEXT, \n\tdefault_translate BOOLEAN NOT NULL, \n\tdefault_analyze_media BOOLEAN NOT NULL, \n\tdefault_retrieval_k INTEGER, \n\tdefault_rerank_k INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tarchived_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_question_sets PRIMARY KEY (id), \n\tCONSTRAINT uq_question_sets_owner_user_id UNIQUE (owner_user_id, name), \n\tCONSTRAINT fk_question_sets_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id)\n)',
    'CREATE INDEX ix_question_sets_owner_user_id ON question_sets (owner_user_id)',
    'CREATE TABLE uploads (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tfilename VARCHAR(512) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tobject_key VARCHAR(1024) NOT NULL, \n\tstatus uploadstatus NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_uploads PRIMARY KEY (id), \n\tCONSTRAINT fk_uploads_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT uq_uploads_object_key UNIQUE (object_key)\n)',
    'CREATE INDEX ix_uploads_owner_user_id ON uploads (owner_user_id)',
    'CREATE TABLE telegram_chats (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tconnection_id UUID, \n\tingest_token_id UUID, \n\tingest_mode telegramingestmode NOT NULL, \n\ttelegram_chat_id BIGINT NOT NULL, \n\taccess_hash BIGINT, \n\ttitle VARCHAR(512) NOT NULL, \n\tusername VARCHAR(255), \n\tchat_type VARCHAR(64) NOT NULL, \n\tinitial_sync_from TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsync_interval_minutes INTEGER NOT NULL, \n\tstatus telegramchatstatus NOT NULL, \n\tlast_error TEXT, \n\tlast_sync_at TIMESTAMP WITH TIME ZONE, \n\tlast_collected_message_id BIGINT, \n\tnext_sync_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcoverage_start TIMESTAMP WITH TIME ZONE, \n\tcoverage_end TIMESTAMP WITH TIME ZONE, \n\tlease_owner VARCHAR(255), \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_telegram_chats PRIMARY KEY (id), \n\tCONSTRAINT uq_telegram_chats_owner_user_id UNIQUE (owner_user_id, telegram_chat_id), \n\tCONSTRAINT fk_telegram_chats_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT fk_telegram_chats_connection_id_telegram_connections FOREIGN KEY(connection_id) REFERENCES telegram_connections (id), \n\tCONSTRAINT fk_telegram_chats_ingest_token_id_telegram_ingest_tokens FOREIGN KEY(ingest_token_id) REFERENCES telegram_ingest_tokens (id)\n)',
    'CREATE INDEX ix_telegram_chats_telegram_chat_id ON telegram_chats (telegram_chat_id)',
    'CREATE INDEX ix_telegram_chats_ingest_mode ON telegram_chats (ingest_mode)',
    'CREATE INDEX ix_telegram_chats_next_sync_at ON telegram_chats (next_sync_at)',
    'CREATE INDEX ix_telegram_chats_status ON telegram_chats (status)',
    'CREATE INDEX ix_telegram_chats_connection_id ON telegram_chats (connection_id)',
    'CREATE INDEX ix_telegram_chats_initial_sync_from ON telegram_chats (initial_sync_from)',
    'CREATE INDEX ix_telegram_chats_ingest_token_id ON telegram_chats (ingest_token_id)',
    'CREATE INDEX ix_telegram_chats_lease_expires_at ON telegram_chats (lease_expires_at)',
    'CREATE INDEX ix_telegram_chats_owner_user_id ON telegram_chats (owner_user_id)',
    'CREATE TABLE question_set_items (\n\tid UUID NOT NULL, \n\tquestion_set_id UUID NOT NULL, \n\tquestion_index INTEGER NOT NULL, \n\tclient_question_id VARCHAR(255), \n\ttext TEXT NOT NULL, \n\tCONSTRAINT pk_question_set_items PRIMARY KEY (id), \n\tCONSTRAINT uq_question_set_items_question_set_id UNIQUE (question_set_id, question_index), \n\tCONSTRAINT fk_question_set_items_question_set_id_question_sets FOREIGN KEY(question_set_id) REFERENCES question_sets (id)\n)',
    'CREATE INDEX ix_question_set_items_question_set_id ON question_set_items (question_set_id)',
    'CREATE TABLE collected_telegram_messages (\n\tid UUID NOT NULL, \n\tchat_id UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\ttelegram_message_id BIGINT NOT NULL, \n\ttimestamp TIMESTAMP WITH TIME ZONE NOT NULL, \n\tedited_timestamp TIMESTAMP WITH TIME ZONE, \n\tsender_id VARCHAR(255), \n\tsender_name VARCHAR(512), \n\tmessage_type VARCHAR(128), \n\treply_to_message_id BIGINT, \n\tforwarded_from VARCHAR(512), \n\treactions JSON NOT NULL, \n\ttext TEXT NOT NULL, \n\traw JSON NOT NULL, \n\tcollected_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_collected_telegram_messages PRIMARY KEY (id), \n\tCONSTRAINT uq_collected_telegram_messages_chat_id UNIQUE (chat_id, telegram_message_id), \n\tCONSTRAINT fk_collected_telegram_messages_chat_id_telegram_chats FOREIGN KEY(chat_id) REFERENCES telegram_chats (id), \n\tCONSTRAINT fk_collected_telegram_messages_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id)\n)',
    'CREATE INDEX ix_collected_telegram_messages_sender_id ON collected_telegram_messages (sender_id)',
    'CREATE INDEX ix_collected_telegram_messages_chat_id ON collected_telegram_messages (chat_id)',
    'CREATE INDEX ix_collected_telegram_messages_timestamp ON collected_telegram_messages (timestamp)',
    'CREATE INDEX ix_collected_telegram_messages_reply_to_message_id ON collected_telegram_messages (reply_to_message_id)',
    'CREATE INDEX ix_collected_telegram_messages_owner_user_id ON collected_telegram_messages (owner_user_id)',
    'CREATE INDEX ix_collected_telegram_messages_telegram_message_id ON collected_telegram_messages (telegram_message_id)',
    'CREATE TABLE jobs (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tsource_type jobsourcetype NOT NULL, \n\tupload_id UUID, \n\ttelegram_chat_id UUID, \n\treport_start_at TIMESTAMP WITH TIME ZONE, \n\treport_end_at TIMESTAMP WITH TIME ZONE, \n\tsource_name VARCHAR(512), \n\tstatus jobstatus NOT NULL, \n\toptions JSON NOT NULL, \n\terror_message TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_jobs PRIMARY KEY (id), \n\tCONSTRAINT fk_jobs_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT fk_jobs_upload_id_uploads FOREIGN KEY(upload_id) REFERENCES uploads (id), \n\tCONSTRAINT fk_jobs_telegram_chat_id_telegram_chats FOREIGN KEY(telegram_chat_id) REFERENCES telegram_chats (id)\n)',
    'CREATE INDEX ix_jobs_source_type ON jobs (source_type)',
    'CREATE INDEX ix_jobs_upload_id ON jobs (upload_id)',
    'CREATE INDEX ix_jobs_status ON jobs (status)',
    'CREATE INDEX ix_jobs_owner_user_id ON jobs (owner_user_id)',
    'CREATE INDEX ix_jobs_telegram_chat_id ON jobs (telegram_chat_id)',
    'CREATE TABLE telegram_sync_runs (\n\tid UUID NOT NULL, \n\tchat_id UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tingest_token_id UUID, \n\tjob_id UUID, \n\tstatus telegramsyncstatus NOT NULL, \n\trequested_start TIMESTAMP WITH TIME ZONE NOT NULL, \n\trequested_end TIMESTAMP WITH TIME ZONE NOT NULL, \n\tmessages_seen INTEGER NOT NULL, \n\tattachments_seen INTEGER NOT NULL, \n\tattachments_failed INTEGER NOT NULL, \n\terror_message TEXT, \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_telegram_sync_runs PRIMARY KEY (id), \n\tCONSTRAINT fk_telegram_sync_runs_chat_id_telegram_chats FOREIGN KEY(chat_id) REFERENCES telegram_chats (id), \n\tCONSTRAINT fk_telegram_sync_runs_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT fk_telegram_sync_runs_ingest_token_id_telegram_ingest_tokens FOREIGN KEY(ingest_token_id) REFERENCES telegram_ingest_tokens (id), \n\tCONSTRAINT fk_telegram_sync_runs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_telegram_sync_runs_owner_user_id ON telegram_sync_runs (owner_user_id)',
    'CREATE INDEX ix_telegram_sync_runs_status ON telegram_sync_runs (status)',
    'CREATE INDEX ix_telegram_sync_runs_job_id ON telegram_sync_runs (job_id)',
    'CREATE INDEX ix_telegram_sync_runs_ingest_token_id ON telegram_sync_runs (ingest_token_id)',
    'CREATE INDEX ix_telegram_sync_runs_chat_id ON telegram_sync_runs (chat_id)',
    'CREATE TABLE collected_telegram_media (\n\tid UUID NOT NULL, \n\tchat_id UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tmessage_id UUID NOT NULL, \n\ttelegram_media_key VARCHAR(512) NOT NULL, \n\tmedia_type VARCHAR(64) NOT NULL, \n\tfilename VARCHAR(1024) NOT NULL, \n\tmime_type VARCHAR(255), \n\tminio_object_key VARCHAR(2048), \n\tsize_bytes BIGINT, \n\tsha256 VARCHAR(64), \n\tstatus stepstatus NOT NULL, \n\terror_message TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_collected_telegram_media PRIMARY KEY (id), \n\tCONSTRAINT uq_collected_telegram_media_message_id UNIQUE (message_id, telegram_media_key), \n\tCONSTRAINT fk_collected_telegram_media_chat_id_telegram_chats FOREIGN KEY(chat_id) REFERENCES telegram_chats (id), \n\tCONSTRAINT fk_collected_telegram_media_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT fk_collected_telegram_media_message_id_collected_telegr_5a12 FOREIGN KEY(message_id) REFERENCES collected_telegram_messages (id)\n)',
    'CREATE INDEX ix_collected_telegram_media_owner_user_id ON collected_telegram_media (owner_user_id)',
    'CREATE INDEX ix_collected_telegram_media_media_type ON collected_telegram_media (media_type)',
    'CREATE INDEX ix_collected_telegram_media_message_id ON collected_telegram_media (message_id)',
    'CREATE INDEX ix_collected_telegram_media_sha256 ON collected_telegram_media (sha256)',
    'CREATE INDEX ix_collected_telegram_media_chat_id ON collected_telegram_media (chat_id)',
    'CREATE TABLE telegram_report_schedules (\n\tid UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\ttelegram_chat_id UUID NOT NULL, \n\tquestion_set_id UUID NOT NULL, \n\trun_time_local VARCHAR(5) NOT NULL, \n\ttimezone VARCHAR(128) NOT NULL, \n\trolling_window_days INTEGER NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tallow_partial_telegram_sync BOOLEAN NOT NULL, \n\tnext_run_at TIMESTAMP WITH TIME ZONE, \n\tlast_run_at TIMESTAMP WITH TIME ZONE, \n\tlast_job_id UUID, \n\tlast_error TEXT, \n\tlease_owner VARCHAR(255), \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_telegram_report_schedules PRIMARY KEY (id), \n\tCONSTRAINT fk_telegram_report_schedules_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT fk_telegram_report_schedules_telegram_chat_id_telegram_chats FOREIGN KEY(telegram_chat_id) REFERENCES telegram_chats (id), \n\tCONSTRAINT fk_telegram_report_schedules_question_set_id_question_sets FOREIGN KEY(question_set_id) REFERENCES question_sets (id), \n\tCONSTRAINT fk_telegram_report_schedules_last_job_id_jobs FOREIGN KEY(last_job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_telegram_report_schedules_telegram_chat_id ON telegram_report_schedules (telegram_chat_id)',
    'CREATE INDEX ix_telegram_report_schedules_enabled ON telegram_report_schedules (enabled)',
    'CREATE INDEX ix_telegram_report_schedules_question_set_id ON telegram_report_schedules (question_set_id)',
    'CREATE INDEX ix_telegram_report_schedules_lease_expires_at ON telegram_report_schedules (lease_expires_at)',
    'CREATE INDEX ix_telegram_report_schedules_last_job_id ON telegram_report_schedules (last_job_id)',
    'CREATE INDEX ix_telegram_report_schedules_owner_user_id ON telegram_report_schedules (owner_user_id)',
    'CREATE INDEX ix_telegram_report_schedules_next_run_at ON telegram_report_schedules (next_run_at)',
    'CREATE TABLE job_steps (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tstep_name VARCHAR(128) NOT NULL, \n\tstatus stepstatus NOT NULL, \n\ttotal INTEGER, \n\tdone INTEGER NOT NULL, \n\terror_message TEXT, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_job_steps PRIMARY KEY (id), \n\tCONSTRAINT uq_job_steps_job_id UNIQUE (job_id, step_name), \n\tCONSTRAINT fk_job_steps_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_job_steps_job_id ON job_steps (job_id)',
    'CREATE TABLE job_events (\n\tid BIGSERIAL NOT NULL, \n\tjob_id UUID NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tevent_type VARCHAR(128) NOT NULL, \n\tlevel VARCHAR(32) NOT NULL, \n\tmessage TEXT NOT NULL, \n\tpayload JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_job_events PRIMARY KEY (id), \n\tCONSTRAINT fk_job_events_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), \n\tCONSTRAINT fk_job_events_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id)\n)',
    'CREATE INDEX ix_job_events_job_id ON job_events (job_id)',
    'CREATE INDEX ix_job_events_owner_user_id ON job_events (owner_user_id)',
    'CREATE INDEX ix_job_events_event_type ON job_events (event_type)',
    'CREATE TABLE websocket_tickets (\n\tid UUID NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\towner_user_id UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tused_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_websocket_tickets PRIMARY KEY (id), \n\tCONSTRAINT fk_websocket_tickets_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), \n\tCONSTRAINT fk_websocket_tickets_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_websocket_tickets_used_at ON websocket_tickets (used_at)',
    'CREATE UNIQUE INDEX ix_websocket_tickets_token_hash ON websocket_tickets (token_hash)',
    'CREATE INDEX ix_websocket_tickets_expires_at ON websocket_tickets (expires_at)',
    'CREATE INDEX ix_websocket_tickets_job_id ON websocket_tickets (job_id)',
    'CREATE INDEX ix_websocket_tickets_owner_user_id ON websocket_tickets (owner_user_id)',
    'CREATE TABLE worker_tasks (\n\tid UUID NOT NULL, \n\ttask_key VARCHAR(1024) NOT NULL, \n\tjob_id UUID NOT NULL, \n\tsubject VARCHAR(255) NOT NULL, \n\tstatus stepstatus NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tlast_error TEXT, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_worker_tasks PRIMARY KEY (id), \n\tCONSTRAINT fk_worker_tasks_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_worker_tasks_subject ON worker_tasks (subject)',
    'CREATE INDEX ix_worker_tasks_job_id ON worker_tasks (job_id)',
    'CREATE UNIQUE INDEX ix_worker_tasks_task_key ON worker_tasks (task_key)',
    'CREATE TABLE telegram_messages (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\ttelegram_message_id BIGINT NOT NULL, \n\ttimestamp TIMESTAMP WITH TIME ZONE, \n\tedited_timestamp TIMESTAMP WITH TIME ZONE, \n\tsender_id VARCHAR(255), \n\tsender_name VARCHAR(512), \n\tmessage_type VARCHAR(128), \n\treply_to_message_id BIGINT, \n\tforwarded_from VARCHAR(512), \n\treactions JSON NOT NULL, \n\ttext TEXT NOT NULL, \n\traw JSON NOT NULL, \n\tCONSTRAINT pk_telegram_messages PRIMARY KEY (id), \n\tCONSTRAINT uq_telegram_messages_job_id UNIQUE (job_id, telegram_message_id), \n\tCONSTRAINT fk_telegram_messages_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_telegram_messages_reply_to_message_id ON telegram_messages (reply_to_message_id)',
    'CREATE INDEX ix_telegram_messages_timestamp ON telegram_messages (timestamp)',
    'CREATE INDEX ix_telegram_messages_telegram_message_id ON telegram_messages (telegram_message_id)',
    'CREATE INDEX ix_telegram_messages_message_type ON telegram_messages (message_type)',
    'CREATE INDEX ix_telegram_messages_sender_id ON telegram_messages (sender_id)',
    'CREATE INDEX ix_telegram_messages_job_id ON telegram_messages (job_id)',
    'CREATE TABLE message_chunks (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tchunk_index INTEGER NOT NULL, \n\tchunk_hash VARCHAR(64) NOT NULL, \n\ttext TEXT NOT NULL, \n\tmessage_ids VARCHAR[] NOT NULL, \n\tstart_timestamp TIMESTAMP WITH TIME ZONE, \n\tend_timestamp TIMESTAMP WITH TIME ZONE, \n\thas_media BOOLEAN NOT NULL, \n\tpayload JSON NOT NULL, \n\tembedding_model VARCHAR(512), \n\tembedding_hash VARCHAR(64), \n\tqdrant_point_id VARCHAR(128), \n\tembedded_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_message_chunks PRIMARY KEY (id), \n\tCONSTRAINT uq_message_chunks_job_id UNIQUE (job_id, chunk_index), \n\tCONSTRAINT fk_message_chunks_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_message_chunks_embedding_hash ON message_chunks (embedding_hash)',
    'CREATE INDEX ix_message_chunks_job_id ON message_chunks (job_id)',
    'CREATE INDEX ix_message_chunks_embedding_model ON message_chunks (embedding_model)',
    'CREATE INDEX ix_message_chunks_chunk_hash ON message_chunks (chunk_hash)',
    'CREATE INDEX ix_message_chunks_qdrant_point_id ON message_chunks (qdrant_point_id)',
    'CREATE TABLE questions (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tclient_question_id VARCHAR(255), \n\tquestion_index INTEGER NOT NULL, \n\ttext TEXT NOT NULL, \n\tCONSTRAINT pk_questions PRIMARY KEY (id), \n\tCONSTRAINT fk_questions_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_questions_job_id ON questions (job_id)',
    'CREATE TABLE reports (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tobject_key VARCHAR(2048) NOT NULL, \n\tfilename VARCHAR(512) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_reports PRIMARY KEY (id), \n\tCONSTRAINT fk_reports_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE UNIQUE INDEX ix_reports_job_id ON reports (job_id)',
    'CREATE TABLE collected_media_analysis (\n\tid UUID NOT NULL, \n\tmedia_id UUID NOT NULL, \n\tmodel_name VARCHAR(512) NOT NULL, \n\tprompt_version VARCHAR(128) NOT NULL, \n\tdescription TEXT NOT NULL, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_collected_media_analysis PRIMARY KEY (id), \n\tCONSTRAINT uq_collected_media_analysis_media_id UNIQUE (media_id, model_name, prompt_version), \n\tCONSTRAINT fk_collected_media_analysis_media_id_collected_telegram_media FOREIGN KEY(media_id) REFERENCES collected_telegram_media (id)\n)',
    'CREATE INDEX ix_collected_media_analysis_media_id ON collected_media_analysis (media_id)',
    'CREATE TABLE collected_media_transcripts (\n\tid UUID NOT NULL, \n\tmedia_id UUID NOT NULL, \n\tprovider VARCHAR(64) NOT NULL, \n\tmodel_name VARCHAR(512) NOT NULL, \n\tresponse_format VARCHAR(32) NOT NULL, \n\tstatus stepstatus NOT NULL, \n\tattempts INTEGER NOT NULL, \n\ttranscript_text TEXT NOT NULL, \n\terror_message TEXT, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_collected_media_transcripts PRIMARY KEY (id), \n\tCONSTRAINT uq_collected_media_transcripts_media_id UNIQUE (media_id, provider, model_name, response_format), \n\tCONSTRAINT fk_collected_media_transcripts_media_id_collected_teleg_1498 FOREIGN KEY(media_id) REFERENCES collected_telegram_media (id)\n)',
    'CREATE INDEX ix_collected_media_transcripts_provider ON collected_media_transcripts (provider)',
    'CREATE INDEX ix_collected_media_transcripts_status ON collected_media_transcripts (status)',
    'CREATE INDEX ix_collected_media_transcripts_media_id ON collected_media_transcripts (media_id)',
    'CREATE TABLE worker_dead_letters (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tworker_task_id UUID, \n\ttask_key VARCHAR(1024) NOT NULL, \n\tsubject VARCHAR(255) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\treason VARCHAR(255) NOT NULL, \n\terror_message TEXT NOT NULL, \n\tpayload JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_worker_dead_letters PRIMARY KEY (id), \n\tCONSTRAINT fk_worker_dead_letters_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), \n\tCONSTRAINT fk_worker_dead_letters_worker_task_id_worker_tasks FOREIGN KEY(worker_task_id) REFERENCES worker_tasks (id)\n)',
    'CREATE INDEX ix_worker_dead_letters_worker_task_id ON worker_dead_letters (worker_task_id)',
    'CREATE INDEX ix_worker_dead_letters_task_key ON worker_dead_letters (task_key)',
    'CREATE INDEX ix_worker_dead_letters_reason ON worker_dead_letters (reason)',
    'CREATE INDEX ix_worker_dead_letters_job_id ON worker_dead_letters (job_id)',
    'CREATE INDEX ix_worker_dead_letters_subject ON worker_dead_letters (subject)',
    'CREATE TABLE message_translations (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tmessage_id UUID NOT NULL, \n\tprovider VARCHAR(64) NOT NULL, \n\tsource_text_hash VARCHAR(64) NOT NULL, \n\tdetected_source_language VARCHAR(32), \n\tdetected_source_confidence FLOAT, \n\ttarget_language VARCHAR(16) NOT NULL, \n\ttranslated_text TEXT NOT NULL, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_message_translations PRIMARY KEY (id), \n\tCONSTRAINT uq_message_translations_message_id UNIQUE (message_id, provider, target_language), \n\tCONSTRAINT fk_message_translations_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_message_translations_message_id_telegram_messages FOREIGN KEY(message_id) REFERENCES telegram_messages (id) ON DELETE CASCADE\n)',
    'CREATE INDEX ix_message_translations_message_id ON message_translations (message_id)',
    'CREATE INDEX ix_message_translations_target_language ON message_translations (target_language)',
    'CREATE INDEX ix_message_translations_provider ON message_translations (provider)',
    'CREATE INDEX ix_message_translations_detected_source_language ON message_translations (detected_source_language)',
    'CREATE INDEX ix_message_translations_job_id ON message_translations (job_id)',
    'CREATE INDEX ix_message_translations_source_text_hash ON message_translations (source_text_hash)',
    'CREATE TABLE telegram_media (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tmessage_id UUID, \n\tsource_media_id UUID, \n\tmedia_type VARCHAR(64) NOT NULL, \n\toriginal_path VARCHAR(2048) NOT NULL, \n\tminio_object_key VARCHAR(2048), \n\tsize_bytes BIGINT, \n\tsha256 VARCHAR(64), \n\tstatus stepstatus NOT NULL, \n\tmissing_reason TEXT, \n\tanalysis_attempts INTEGER NOT NULL, \n\tanalyzed_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_telegram_media PRIMARY KEY (id), \n\tCONSTRAINT uq_telegram_media_job_id UNIQUE (job_id, message_id, original_path), \n\tCONSTRAINT fk_telegram_media_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), \n\tCONSTRAINT fk_telegram_media_message_id_telegram_messages FOREIGN KEY(message_id) REFERENCES telegram_messages (id), \n\tCONSTRAINT fk_telegram_media_source_media_id_collected_telegram_media FOREIGN KEY(source_media_id) REFERENCES collected_telegram_media (id)\n)',
    'CREATE INDEX ix_telegram_media_source_media_id ON telegram_media (source_media_id)',
    'CREATE INDEX ix_telegram_media_job_id ON telegram_media (job_id)',
    'CREATE INDEX ix_telegram_media_sha256 ON telegram_media (sha256)',
    'CREATE INDEX ix_telegram_media_message_id ON telegram_media (message_id)',
    'CREATE INDEX ix_telegram_media_media_type ON telegram_media (media_type)',
    'CREATE TABLE question_runs (\n\tid UUID NOT NULL, \n\tquestion_id UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tretrieval_k INTEGER NOT NULL, \n\trerank_k INTEGER NOT NULL, \n\tanswer TEXT, \n\tshort_answer TEXT, \n\tstatus stepstatus NOT NULL, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_question_runs PRIMARY KEY (id), \n\tCONSTRAINT fk_question_runs_question_id_questions FOREIGN KEY(question_id) REFERENCES questions (id), \n\tCONSTRAINT fk_question_runs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id)\n)',
    'CREATE INDEX ix_question_runs_job_id ON question_runs (job_id)',
    'CREATE INDEX ix_question_runs_question_id ON question_runs (question_id)',
    'CREATE TABLE media_analysis (\n\tid UUID NOT NULL, \n\tmedia_id UUID NOT NULL, \n\tmodel_name VARCHAR(512) NOT NULL, \n\tprompt_version VARCHAR(128) NOT NULL, \n\tdescription TEXT NOT NULL, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_media_analysis PRIMARY KEY (id), \n\tCONSTRAINT uq_media_analysis_media_id UNIQUE (media_id, model_name, prompt_version), \n\tCONSTRAINT fk_media_analysis_media_id_telegram_media FOREIGN KEY(media_id) REFERENCES telegram_media (id)\n)',
    'CREATE INDEX ix_media_analysis_media_id ON media_analysis (media_id)',
    'CREATE TABLE media_transcripts (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tmedia_id UUID NOT NULL, \n\tprovider VARCHAR(64) NOT NULL, \n\tmodel_name VARCHAR(512) NOT NULL, \n\tresponse_format VARCHAR(32) NOT NULL, \n\tstatus stepstatus NOT NULL, \n\tattempts INTEGER NOT NULL, \n\ttranscript_text TEXT NOT NULL, \n\terror_message TEXT, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_media_transcripts PRIMARY KEY (id), \n\tCONSTRAINT uq_media_transcripts_media_id UNIQUE (media_id, provider, model_name, response_format), \n\tCONSTRAINT fk_media_transcripts_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id), \n\tCONSTRAINT fk_media_transcripts_media_id_telegram_media FOREIGN KEY(media_id) REFERENCES telegram_media (id)\n)',
    'CREATE INDEX ix_media_transcripts_job_id ON media_transcripts (job_id)',
    'CREATE INDEX ix_media_transcripts_provider ON media_transcripts (provider)',
    'CREATE INDEX ix_media_transcripts_media_id ON media_transcripts (media_id)',
    'CREATE INDEX ix_media_transcripts_status ON media_transcripts (status)',
    'CREATE TABLE retrieval_hits (\n\tid UUID NOT NULL, \n\tquestion_run_id UUID NOT NULL, \n\tchunk_id UUID NOT NULL, \n\tretrieval_rank INTEGER NOT NULL, \n\tretrieval_score FLOAT, \n\trerank_rank INTEGER, \n\trerank_score FLOAT, \n\tused_in_answer BOOLEAN NOT NULL, \n\tCONSTRAINT pk_retrieval_hits PRIMARY KEY (id), \n\tCONSTRAINT fk_retrieval_hits_question_run_id_question_runs FOREIGN KEY(question_run_id) REFERENCES question_runs (id), \n\tCONSTRAINT fk_retrieval_hits_chunk_id_message_chunks FOREIGN KEY(chunk_id) REFERENCES message_chunks (id)\n)',
    'CREATE INDEX ix_retrieval_hits_question_run_id ON retrieval_hits (question_run_id)',
    'CREATE INDEX ix_retrieval_hits_chunk_id ON retrieval_hits (chunk_id)',
    'CREATE TABLE media_transcript_translations (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\ttranscript_id UUID NOT NULL, \n\tprovider VARCHAR(64) NOT NULL, \n\tsource_text_hash VARCHAR(64) NOT NULL, \n\tdetected_source_language VARCHAR(32), \n\tdetected_source_confidence FLOAT, \n\ttarget_language VARCHAR(16) NOT NULL, \n\ttranslated_text TEXT NOT NULL, \n\traw_response JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_media_transcript_translations PRIMARY KEY (id), \n\tCONSTRAINT uq_media_transcript_translations_transcript_id UNIQUE (transcript_id, provider, target_language), \n\tCONSTRAINT fk_media_transcript_translations_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_media_transcript_translations_transcript_id_media_tr_c3f1 FOREIGN KEY(transcript_id) REFERENCES media_transcripts (id) ON DELETE CASCADE\n)',
    'CREATE INDEX ix_media_transcript_translations_detected_source_language ON media_transcript_translations (detected_source_language)',
    'CREATE INDEX ix_media_transcript_translations_job_id ON media_transcript_translations (job_id)',
    'CREATE INDEX ix_media_transcript_translations_source_text_hash ON media_transcript_translations (source_text_hash)',
    'CREATE INDEX ix_media_transcript_translations_transcript_id ON media_transcript_translations (transcript_id)',
    'CREATE INDEX ix_media_transcript_translations_target_language ON media_transcript_translations (target_language)',
    'CREATE INDEX ix_media_transcript_translations_provider ON media_transcript_translations (provider)',
)

EXPECTED_COLUMNS = {
    'users': ('id', 'email', 'password_hash', 'is_active', 'created_at'),
    'question_sets': ('id', 'owner_user_id', 'name', 'description', 'default_translate', 'default_analyze_media', 'default_retrieval_k', 'default_rerank_k', 'created_at', 'updated_at', 'archived_at'),
    'telegram_connections': ('id', 'owner_user_id', 'api_id', 'api_hash_encrypted', 'session_encrypted', 'telegram_user_id', 'phone', 'display_name', 'status', 'last_error', 'created_at', 'updated_at', 'last_verified_at'),
    'telegram_ingest_tokens': ('id', 'owner_user_id', 'name', 'token_hash', 'created_at', 'expires_at', 'revoked_at', 'last_used_at'),
    'telegram_login_challenges': ('id', 'owner_user_id', 'api_id', 'api_hash_encrypted', 'phone', 'phone_code_hash_encrypted', 'session_encrypted', 'requires_password', 'created_at', 'expires_at'),
    'uploads': ('id', 'owner_user_id', 'filename', 'size_bytes', 'object_key', 'status', 'created_at', 'completed_at'),
    'question_set_items': ('id', 'question_set_id', 'question_index', 'client_question_id', 'text'),
    'telegram_chats': ('id', 'owner_user_id', 'connection_id', 'ingest_token_id', 'ingest_mode', 'telegram_chat_id', 'access_hash', 'title', 'username', 'chat_type', 'initial_sync_from', 'sync_interval_minutes', 'status', 'last_error', 'last_sync_at', 'last_collected_message_id', 'next_sync_at', 'coverage_start', 'coverage_end', 'lease_owner', 'lease_expires_at', 'created_at', 'updated_at'),
    'collected_telegram_messages': ('id', 'chat_id', 'owner_user_id', 'telegram_message_id', 'timestamp', 'edited_timestamp', 'sender_id', 'sender_name', 'message_type', 'reply_to_message_id', 'forwarded_from', 'reactions', 'text', 'raw', 'collected_at'),
    'jobs': ('id', 'owner_user_id', 'source_type', 'upload_id', 'telegram_chat_id', 'report_start_at', 'report_end_at', 'source_name', 'status', 'options', 'error_message', 'created_at', 'started_at', 'completed_at'),
    'collected_telegram_media': ('id', 'chat_id', 'owner_user_id', 'message_id', 'telegram_media_key', 'media_type', 'filename', 'mime_type', 'minio_object_key', 'size_bytes', 'sha256', 'status', 'error_message', 'created_at', 'updated_at'),
    'job_events': ('id', 'job_id', 'owner_user_id', 'event_type', 'level', 'message', 'payload', 'created_at'),
    'job_steps': ('id', 'job_id', 'step_name', 'status', 'total', 'done', 'error_message', 'updated_at'),
    'message_chunks': ('id', 'job_id', 'chunk_index', 'chunk_hash', 'text', 'message_ids', 'start_timestamp', 'end_timestamp', 'has_media', 'payload', 'embedding_model', 'embedding_hash', 'qdrant_point_id', 'embedded_at'),
    'questions': ('id', 'job_id', 'client_question_id', 'question_index', 'text'),
    'reports': ('id', 'job_id', 'object_key', 'filename', 'created_at'),
    'telegram_messages': ('id', 'job_id', 'telegram_message_id', 'timestamp', 'edited_timestamp', 'sender_id', 'sender_name', 'message_type', 'reply_to_message_id', 'forwarded_from', 'reactions', 'text', 'raw'),
    'telegram_report_schedules': ('id', 'owner_user_id', 'telegram_chat_id', 'question_set_id', 'run_time_local', 'timezone', 'rolling_window_days', 'enabled', 'allow_partial_telegram_sync', 'next_run_at', 'last_run_at', 'last_job_id', 'last_error', 'lease_owner', 'lease_expires_at', 'created_at', 'updated_at'),
    'telegram_sync_runs': ('id', 'chat_id', 'owner_user_id', 'ingest_token_id', 'job_id', 'status', 'requested_start', 'requested_end', 'messages_seen', 'attachments_seen', 'attachments_failed', 'error_message', 'started_at', 'completed_at'),
    'websocket_tickets': ('id', 'token_hash', 'owner_user_id', 'job_id', 'expires_at', 'used_at', 'created_at'),
    'worker_tasks': ('id', 'task_key', 'job_id', 'subject', 'status', 'attempts', 'last_error', 'updated_at'),
    'collected_media_analysis': ('id', 'media_id', 'model_name', 'prompt_version', 'description', 'raw_response', 'created_at'),
    'collected_media_transcripts': ('id', 'media_id', 'provider', 'model_name', 'response_format', 'status', 'attempts', 'transcript_text', 'error_message', 'raw_response', 'created_at', 'updated_at'),
    'message_translations': ('id', 'job_id', 'message_id', 'provider', 'source_text_hash', 'detected_source_language', 'detected_source_confidence', 'target_language', 'translated_text', 'raw_response', 'created_at', 'updated_at'),
    'question_runs': ('id', 'question_id', 'job_id', 'retrieval_k', 'rerank_k', 'answer', 'short_answer', 'status', 'raw_response', 'created_at'),
    'telegram_media': ('id', 'job_id', 'message_id', 'source_media_id', 'media_type', 'original_path', 'minio_object_key', 'size_bytes', 'sha256', 'status', 'missing_reason', 'analysis_attempts', 'analyzed_at'),
    'worker_dead_letters': ('id', 'job_id', 'worker_task_id', 'task_key', 'subject', 'attempts', 'reason', 'error_message', 'payload', 'created_at'),
    'media_analysis': ('id', 'media_id', 'model_name', 'prompt_version', 'description', 'raw_response', 'created_at'),
    'media_transcripts': ('id', 'job_id', 'media_id', 'provider', 'model_name', 'response_format', 'status', 'attempts', 'transcript_text', 'error_message', 'raw_response', 'created_at', 'updated_at'),
    'retrieval_hits': ('id', 'question_run_id', 'chunk_id', 'retrieval_rank', 'retrieval_score', 'rerank_rank', 'rerank_score', 'used_in_answer'),
    'media_transcript_translations': ('id', 'job_id', 'transcript_id', 'provider', 'source_text_hash', 'detected_source_language', 'detected_source_confidence', 'target_language', 'translated_text', 'raw_response', 'created_at', 'updated_at'),
}

ENUM_TYPES = (
    "jobstatus",
    "jobsourcetype",
    "stepstatus",
    "telegramchatstatus",
    "telegramconnectionstatus",
    "telegramingestmode",
    "telegramsyncstatus",
    "uploadstatus",
)


def _adopt_mvp_schema(bind) -> None:
    # These operations used to run on every process startup in app/db.py.
    # Keeping them idempotent lets this first revision adopt MVP databases
    # that were stopped immediately before or during one of those alterations.
    bind.exec_driver_sql(
        """
        DO $$
        BEGIN
            CREATE TYPE telegramingestmode AS ENUM ('backend_pull', 'external_push');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    bind.exec_driver_sql(
        """
        ALTER TABLE telegram_chats
        ADD COLUMN IF NOT EXISTS ingest_mode telegramingestmode
        DEFAULT 'backend_pull' NOT NULL
        """
    )
    bind.exec_driver_sql(
        "ALTER TABLE telegram_chats ALTER COLUMN connection_id DROP NOT NULL"
    )
    bind.exec_driver_sql(
        """
        ALTER TABLE telegram_chats
        ADD COLUMN IF NOT EXISTS last_collected_message_id bigint
        """
    )
    bind.exec_driver_sql(
        """
        UPDATE telegram_chats AS chat
        SET last_collected_message_id = collected.last_message_id
        FROM (
            SELECT chat_id, MAX(telegram_message_id) AS last_message_id
            FROM collected_telegram_messages
            GROUP BY chat_id
        ) AS collected
        WHERE chat.id = collected.chat_id
          AND chat.last_sync_at IS NOT NULL
          AND chat.last_collected_message_id IS NULL
        """
    )
    bind.exec_driver_sql(
        """
        ALTER TABLE telegram_report_schedules
        ADD COLUMN IF NOT EXISTS allow_partial_telegram_sync boolean
        DEFAULT false NOT NULL
        """
    )
    bind.exec_driver_sql(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_name varchar(512)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_telegram_chats_ingest_mode "
        "ON telegram_chats (ingest_mode)"
    )
    # The ORM supplies these values. The old startup alterations left database
    # defaults behind, so remove them after existing rows have been populated.
    bind.exec_driver_sql(
        "ALTER TABLE telegram_chats ALTER COLUMN ingest_mode DROP DEFAULT"
    )
    bind.exec_driver_sql(
        "ALTER TABLE telegram_report_schedules "
        "ALTER COLUMN allow_partial_telegram_sync DROP DEFAULT"
    )


def _validate_schema(bind) -> None:
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    missing_tables = sorted(set(EXPECTED_COLUMNS) - tables)
    if missing_tables:
        raise RuntimeError(
            "Cannot adopt incomplete MVP schema; missing tables: "
            + ", ".join(missing_tables)
        )
    missing_columns: list[str] = []
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in expected_columns
            if column_name not in actual_columns
        )
    if missing_columns:
        raise RuntimeError(
            "Cannot adopt incomplete MVP schema; missing columns: "
            + ", ".join(sorted(missing_columns))
        )


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names()) - {"alembic_version"}
    if not existing_tables:
        for statement in BASELINE_DDL:
            bind.exec_driver_sql(statement)
    else:
        _adopt_mvp_schema(bind)
    _validate_schema(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(tuple(EXPECTED_COLUMNS)):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
    for type_name in ENUM_TYPES:
        bind.exec_driver_sql(f'DROP TYPE IF EXISTS "{type_name}" CASCADE')

