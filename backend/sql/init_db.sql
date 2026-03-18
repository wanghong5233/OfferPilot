CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    jd_raw TEXT NOT NULL,
    jd_parsed JSONB,
    match_score REAL,
    gap_analysis TEXT,
    status TEXT DEFAULT 'new',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id),
    resume_version TEXT,
    cover_letter TEXT,
    applied_at TIMESTAMP,
    channel TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id),
    action_type TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    screenshot_path TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material_threads (
    thread_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id),
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    match_score REAL,
    resume_version TEXT NOT NULL,
    status TEXT NOT NULL, -- pending_review/approved/rejected
    draft JSONB,
    last_feedback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resume_sources (
    source_id TEXT PRIMARY KEY,
    resume_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profiles (
    profile_id TEXT PRIMARY KEY,
    profile_json JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS boss_chat_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    hr_name TEXT,
    company TEXT,
    job_title TEXT,
    latest_hr_message TEXT NOT NULL,
    latest_hr_time TEXT,
    message_signature TEXT NOT NULL UNIQUE,
    intent TEXT,
    confidence REAL,
    action TEXT,
    reason TEXT,
    reply_text TEXT,
    needs_send_resume BOOLEAN DEFAULT FALSE,
    needs_user_intervention BOOLEAN DEFAULT FALSE,
    notification_sent BOOLEAN DEFAULT FALSE,
    notification_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS form_fill_threads (
    thread_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    status TEXT NOT NULL, -- pending_review/approved/rejected
    profile JSONB,
    preview JSONB,
    fill_result JSONB,
    last_feedback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_events (
    id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    email_type TEXT NOT NULL,
    company TEXT,
    interview_time TEXT,
    raw_classification JSONB,
    related_job_id TEXT,
    updated_job_status TEXT,
    received_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    source_email_id TEXT,
    company TEXT,
    event_type TEXT NOT NULL,
    start_at TIMESTAMP NOT NULL,
    raw_time_text TEXT,
    mode TEXT,
    location TEXT,
    contact TEXT,
    confidence REAL,
    status TEXT DEFAULT 'scheduled',
    reminder_sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS security_tokens (
    token_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    purpose TEXT,
    status TEXT NOT NULL,
    issued_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    consumed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_budgets (
    session_id TEXT NOT NULL,
    tool_type TEXT NOT NULL,
    used_count INTEGER NOT NULL,
    limit_count INTEGER NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (session_id, tool_type)
);
