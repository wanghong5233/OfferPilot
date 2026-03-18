---
name: boss-chat-copilot
description: Pull BOSS chat messages and generate safe reply decisions.
---

# Boss Chat Copilot Skill

## Trigger

Activate when user asks:

- "拉取 BOSS 未读消息并给我回复建议"
- "批量处理 BOSS 对话，哪些需要我介入？"
- "看下 HR 最新消息并生成可回复内容"
- "执行一次 BOSS 消息巡检"

## Workflow

1. If user asks to inspect chat list only:
   - Call `POST http://127.0.0.1:8010/api/boss/chat/pull`
   - Body:
     - `{"max_conversations":30,"unread_only":true,"fetch_latest_hr":true}`
2. If user asks for batch decisions:
   - Call `POST http://127.0.0.1:8010/api/boss/chat/process`
   - Body:
     - `{"max_conversations":30,"unread_only":true,"profile_id":"default","notify_on_escalate":true,"fetch_latest_hr":true}`
3. If this is scheduled heartbeat巡检 (cron/heartbeat):
   - Prefer `POST http://127.0.0.1:8010/api/boss/chat/heartbeat/trigger`
   - Body:
     - `{"max_conversations":30,"unread_only":true,"profile_id":"default","notify_on_escalate":true,"fetch_latest_hr":true,"notify_channel_on_hits":true}`
   - Return the backend `summary` directly first, then list top manual items.
4. Parse response and summarize by priority:
   - `needs_user_intervention=true` first
   - then `action=send_resume`
   - then `action=reply_from_profile`
5. If user gives a single HR message and asks for manual preview:
   - Call `POST http://127.0.0.1:8010/api/boss/chat/reply-preview`
6. Always report:
   - `processed_count/new_count/duplicated_count`
   - who needs manual intervention
   - suggested reply text if available
7. If API fails or returns `ok=false`, report `summary`/`error` and ask whether to retry. Heartbeat trigger always returns 200; check `ok` field for success.

## Command templates (exec tool + curl)

- Pull list:
  - `curl -sS -X POST "http://127.0.0.1:8010/api/boss/chat/pull" -H "Content-Type: application/json" -d '{"max_conversations":30,"unread_only":true,"fetch_latest_hr":true}'`
- Batch process:
  - `curl -sS -X POST "http://127.0.0.1:8010/api/boss/chat/process" -H "Content-Type: application/json" -d '{"max_conversations":30,"unread_only":true,"profile_id":"default","notify_on_escalate":true,"fetch_latest_hr":true}'`
- Heartbeat trigger:
  - `curl -sS -X POST "http://127.0.0.1:8010/api/boss/chat/heartbeat/trigger" -H "Content-Type: application/json" -d '{"max_conversations":30,"unread_only":true,"profile_id":"default","notify_on_escalate":true,"fetch_latest_hr":true,"notify_channel_on_hits":true}'`
- Single preview:
  - `curl -sS -X POST "http://127.0.0.1:8010/api/boss/chat/reply-preview" -H "Content-Type: application/json" -d '{"hr_message":"你好，请问你的期望日薪和到岗时间？","profile_id":"default","notify_on_escalate":true}'`

## Constraints

- Never auto-send messages unless user explicitly requests execution mode (request body `auto_execute: true` and backend `BOSS_CHAT_AUTO_EXECUTE_ENABLED=true`).
- If confidence is low or topic is out-of-profile, force manual intervention.
- Never expose or fabricate personal profile fields not present in `profile`.
