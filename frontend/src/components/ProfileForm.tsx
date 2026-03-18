"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

const AUTO_REPLY_OPTIONS = [
  { key: "expected_salary", label: "薪资" },
  { key: "work_location", label: "工作地点" },
  { key: "start_date", label: "到岗时间" },
  { key: "internship_duration", label: "实习时长" },
  { key: "weekly_availability", label: "每周天数" },
  { key: "education_background", label: "学历背景" },
  { key: "graduation_year", label: "毕业年份" },
  { key: "contact_info", label: "联系方式" },
  { key: "tech_stack", label: "技术栈" },
  { key: "experience", label: "项目经验" },
  { key: "current_status", label: "当前状态" },
  { key: "overtime", label: "加班情况" },
  { key: "english_level", label: "英语水平" },
];

const ESCALATE_OPTIONS = [
  { key: "technical_questions", label: "技术深问" },
  { key: "salary_negotiation", label: "薪资谈判" },
  { key: "project_details", label: "项目细节" },
  { key: "personal_questions", label: "个人隐私" },
  { key: "unknown", label: "无法识别" },
];

const STATUS_OPTIONS = ["在校", "实习中", "在职", "已离职", "待业"];

type ProfileData = {
  personal: {
    name: string;
    education: string;
    major: string;
    graduation_year: number;
    age: number;
    current_status: string;
    phone: string;
    wechat: string;
    email: string;
  };
  skills: {
    tech_stack: string[];
    experience_summary: string;
    english_level: string;
    portfolio_links: string[];
  };
  job_preference: {
    job_type: string;
    target_positions: string[];
    work_cities: string[];
    expected_daily_salary: string;
    internship_duration: string;
    available_days_per_week: number;
    earliest_start_date: string;
    is_remote_ok: boolean;
    overtime_ok: boolean;
    notes: string;
  };
  default_greeting: string;
  reply_policy: {
    auto_reply_topics: string[];
    escalate_topics: string[];
    tone: string;
  };
};

const DEFAULT_PROFILE: ProfileData = {
  personal: { name: "", education: "", major: "", graduation_year: 2027, age: 24, current_status: "在校", phone: "", wechat: "", email: "" },
  skills: { tech_stack: [], experience_summary: "", english_level: "", portfolio_links: [] },
  job_preference: {
    job_type: "intern",
    target_positions: [],
    work_cities: [],
    expected_daily_salary: "",
    internship_duration: "",
    available_days_per_week: 5,
    earliest_start_date: "",
    is_remote_ok: true,
    overtime_ok: true,
    notes: "",
  },
  default_greeting: "",
  reply_policy: {
    auto_reply_topics: ["expected_salary", "work_location", "internship_duration", "education_background", "contact_info", "tech_stack", "experience", "current_status", "overtime", "english_level", "express_interest", "request_resume"],
    escalate_topics: ["technical_questions", "salary_negotiation", "project_details", "unknown"],
    tone: "礼貌、简洁、专业",
  },
};

function safeGet(obj: Record<string, unknown>, key: string): unknown {
  return obj?.[key];
}

function toProfileData(raw: Record<string, unknown>): ProfileData {
  const personal = (safeGet(raw, "personal") ?? {}) as Record<string, unknown>;
  const skills = (safeGet(raw, "skills") ?? {}) as Record<string, unknown>;
  const pref = (safeGet(raw, "job_preference") ?? {}) as Record<string, unknown>;
  const policy = (safeGet(raw, "reply_policy") ?? {}) as Record<string, unknown>;
  const d = DEFAULT_PROFILE;
  return {
    personal: {
      name: String(personal.name ?? d.personal.name),
      education: String(personal.education ?? d.personal.education),
      major: String(personal.major ?? d.personal.major),
      graduation_year: Number(personal.graduation_year ?? d.personal.graduation_year),
      age: Number(personal.age ?? d.personal.age),
      current_status: String(personal.current_status ?? d.personal.current_status),
      phone: String(personal.phone ?? d.personal.phone),
      wechat: String(personal.wechat ?? d.personal.wechat),
      email: String(personal.email ?? d.personal.email),
    },
    skills: {
      tech_stack: Array.isArray(skills.tech_stack) ? skills.tech_stack.map(String) : d.skills.tech_stack,
      experience_summary: String(skills.experience_summary ?? d.skills.experience_summary),
      english_level: String(skills.english_level ?? d.skills.english_level),
      portfolio_links: Array.isArray(skills.portfolio_links) ? skills.portfolio_links.map(String) : d.skills.portfolio_links,
    },
    job_preference: {
      job_type: String(pref.job_type ?? d.job_preference.job_type),
      target_positions: Array.isArray(pref.target_positions) ? pref.target_positions.map(String) : d.job_preference.target_positions,
      work_cities: Array.isArray(pref.work_cities) ? pref.work_cities.map(String) : d.job_preference.work_cities,
      expected_daily_salary: String(pref.expected_daily_salary ?? d.job_preference.expected_daily_salary),
      internship_duration: String(pref.internship_duration ?? d.job_preference.internship_duration),
      available_days_per_week: Number(pref.available_days_per_week ?? d.job_preference.available_days_per_week),
      earliest_start_date: String(pref.earliest_start_date ?? d.job_preference.earliest_start_date),
      is_remote_ok: Boolean(pref.is_remote_ok ?? d.job_preference.is_remote_ok),
      overtime_ok: Boolean(pref.overtime_ok ?? d.job_preference.overtime_ok),
      notes: String(pref.notes ?? d.job_preference.notes),
    },
    default_greeting: String(raw.default_greeting ?? d.default_greeting),
    reply_policy: {
      auto_reply_topics: Array.isArray(policy.auto_reply_topics) ? policy.auto_reply_topics.map(String) : d.reply_policy.auto_reply_topics,
      escalate_topics: Array.isArray(policy.escalate_topics) ? policy.escalate_topics.map(String) : d.reply_policy.escalate_topics,
      tone: String(policy.tone ?? d.reply_policy.tone),
    },
  };
}

export type { ProfileData };

function ImeInput({ value, onValueChange, className, ...rest }: Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange"> & { value: string; onValueChange: (v: string) => void }) {
  const [local, setLocal] = useState(value);
  const composing = useRef(false);
  useEffect(() => { if (!composing.current) setLocal(value); }, [value]);
  return (
    <input
      {...rest}
      className={className}
      value={local}
      onChange={e => { setLocal(e.target.value); if (!composing.current) onValueChange(e.target.value); }}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={e => { composing.current = false; onValueChange((e.target as HTMLInputElement).value); }}
    />
  );
}

function ImeTextarea({ value, onValueChange, className, ...rest }: Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange"> & { value: string; onValueChange: (v: string) => void }) {
  const [local, setLocal] = useState(value);
  const composing = useRef(false);
  useEffect(() => { if (!composing.current) setLocal(value); }, [value]);
  return (
    <textarea
      {...rest}
      className={className}
      value={local}
      onChange={e => { setLocal(e.target.value); if (!composing.current) onValueChange(e.target.value); }}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={e => { composing.current = false; onValueChange((e.target as HTMLTextAreaElement).value); }}
    />
  );
}

export function ProfileForm({ onSaved }: { onSaved?: (p: ProfileData) => void }) {
  const [profile, setProfile] = useState<ProfileData>(DEFAULT_PROFILE);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [tagInputs, setTagInputs] = useState<Record<string, string>>({});

  const fetchProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/profile?profile_id=default`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setProfile(toProfileData(data.profile ?? {}));
      setMessage(data.updated_at ? `已加载（${new Date(data.updated_at).toLocaleString()}）` : "已加载默认配置");
    } catch (e) {
      setError(`加载失败：${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void fetchProfile(); }, [fetchProfile]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: "default", profile }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setProfile(toProfileData(data.profile ?? {}));
      setMessage("保存成功！");
      onSaved?.(profile);
    } catch (e) {
      setError(`保存失败：${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const up = (section: string, field: string, value: unknown) => {
    setProfile(prev => {
      const next = JSON.parse(JSON.stringify(prev)) as Record<string, Record<string, unknown>>;
      next[section][field] = value;
      return next as unknown as ProfileData;
    });
  };

  const toggleArrayItem = (section: string, field: string, item: string) => {
    setProfile(prev => {
      const next = JSON.parse(JSON.stringify(prev)) as Record<string, Record<string, unknown>>;
      const arr = next[section][field] as string[];
      next[section][field] = arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item];
      return next as unknown as ProfileData;
    });
  };

  const addTag = (section: string, field: string, inputKey: string) => {
    const val = (tagInputs[inputKey] ?? "").trim();
    if (!val) return;
    setProfile(prev => {
      const next = JSON.parse(JSON.stringify(prev)) as Record<string, Record<string, unknown>>;
      const arr = next[section][field] as string[];
      if (!arr.includes(val)) arr.push(val);
      return next as unknown as ProfileData;
    });
    setTagInputs(prev => ({ ...prev, [inputKey]: "" }));
  };

  const removeTag = (section: string, field: string, val: string) => {
    setProfile(prev => {
      const next = JSON.parse(JSON.stringify(prev)) as Record<string, Record<string, unknown>>;
      next[section][field] = (next[section][field] as string[]).filter(x => x !== val);
      return next as unknown as ProfileData;
    });
  };

  const inp = "w-full rounded border border-zinc-300 px-3 py-1.5 text-sm outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500";
  const lbl = "block text-sm font-medium text-zinc-700 mb-1";
  const sec = "space-y-3 rounded-lg border border-zinc-200 bg-zinc-50/50 p-4";

  const TagInput = ({ section, field, inputKey, placeholder, color }: { section: string; field: string; inputKey: string; placeholder: string; color: string }) => {
    const arr = ((profile as Record<string, Record<string, unknown>>)[section][field] as string[]) ?? [];
    const bgMap: Record<string, string> = { blue: "bg-blue-100 text-blue-800", green: "bg-green-100 text-green-800", purple: "bg-purple-100 text-purple-800", orange: "bg-orange-100 text-orange-800" };
    const cls = bgMap[color] ?? bgMap.blue;
    return (
      <div>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {arr.map(v => (
            <span key={v} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs ${cls}`}>
              {v}
              <button type="button" onClick={() => removeTag(section, field, v)} className="hover:text-red-600">&times;</button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <ImeInput className={inp} value={tagInputs[inputKey] ?? ""} onValueChange={v => setTagInputs(p => ({ ...p, [inputKey]: v }))}
            placeholder={placeholder}
            onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addTag(section, field, inputKey); } }} />
          <button type="button" onClick={() => addTag(section, field, inputKey)}
            className="shrink-0 rounded border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50">添加</button>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">求职画像配置</h3>
        <div className="flex gap-2">
          <button type="button" onClick={() => void fetchProfile()} disabled={loading}
            className="rounded border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50 disabled:opacity-50">
            {loading ? "加载中..." : "刷新"}
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={saving}
            className="rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
            {saving ? "保存中..." : "保存配置"}
          </button>
        </div>
      </div>

      {error && <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>}
      {message && <p className="rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{message}</p>}

      {/* Personal */}
      <div className={sec}>
        <h4 className="font-medium text-zinc-800">个人信息</h4>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div><label className={lbl}>姓名</label><ImeInput className={inp} value={profile.personal.name} onValueChange={v => up("personal", "name", v)} /></div>
          <div><label className={lbl}>学历</label><ImeInput className={inp} value={profile.personal.education} placeholder="如：211硕士在读（2027届）" onValueChange={v => up("personal", "education", v)} /></div>
          <div><label className={lbl}>专业</label><ImeInput className={inp} value={profile.personal.major} onValueChange={v => up("personal", "major", v)} /></div>
          <div><label className={lbl}>毕业年份</label><input type="number" className={inp} value={profile.personal.graduation_year} onChange={e => up("personal", "graduation_year", Number(e.target.value))} /></div>
          <div><label className={lbl}>年龄</label><input type="number" className={inp} value={profile.personal.age} onChange={e => up("personal", "age", Number(e.target.value))} /></div>
          <div>
            <label className={lbl}>当前状态</label>
            <select className={inp} value={profile.personal.current_status} onChange={e => up("personal", "current_status", e.target.value)}>
              {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div><label className={lbl}>手机号 <span className="text-xs text-zinc-400">(HR 常问)</span></label><ImeInput className={inp} value={profile.personal.phone} placeholder="13800000000" onValueChange={v => up("personal", "phone", v)} /></div>
          <div><label className={lbl}>微信号 <span className="text-xs text-zinc-400">(HR 常问)</span></label><ImeInput className={inp} value={profile.personal.wechat} placeholder="WeChat ID" onValueChange={v => up("personal", "wechat", v)} /></div>
          <div><label className={lbl}>邮箱</label><ImeInput className={inp} type="email" value={profile.personal.email} onValueChange={v => up("personal", "email", v)} /></div>
        </div>
      </div>

      {/* Skills & Experience */}
      <div className={sec}>
        <h4 className="font-medium text-zinc-800">技术能力与经验 <span className="text-xs text-zinc-400">(HR 常问：你会什么技术/有没有项目经验)</span></h4>
        <div>
          <label className={lbl}>核心技术栈</label>
          <TagInput section="skills" field="tech_stack" inputKey="tech" placeholder="如：Python、LangGraph、FastAPI..." color="purple" />
        </div>
        <div>
          <label className={lbl}>项目/实习经验简述 <span className="text-xs text-zinc-400">(2-3 句即可，HR 问经验时自动回复)</span></label>
          <ImeTextarea className={`${inp} h-20`} value={profile.skills.experience_summary}
            placeholder="如：有 AI Agent 全栈项目经验，使用 LangGraph + RAG 构建智能求职助手，含 Playwright 浏览器自动化和飞书通知集成。"
            onValueChange={v => up("skills", "experience_summary", v)} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className={lbl}>英语水平</label><ImeInput className={inp} value={profile.skills.english_level} placeholder="如：CET-6 / 雅思 7.0 / 流利口语" onValueChange={v => up("skills", "english_level", v)} /></div>
        </div>
        <div>
          <label className={lbl}>作品集 / GitHub 链接</label>
          <TagInput section="skills" field="portfolio_links" inputKey="link" placeholder="https://github.com/..." color="orange" />
        </div>
      </div>

      {/* Job Preference */}
      <div className={sec}>
        <h4 className="font-medium text-zinc-800">求职偏好</h4>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className={lbl}>求职类型 <span className="text-xs text-zinc-400">(控制岗位筛选与薪资过滤)</span></label>
            <select className={inp} value={profile.job_preference.job_type} onChange={e => up("job_preference", "job_type", e.target.value)}>
              <option value="intern">实习</option>
              <option value="fulltime">全职/社招</option>
              <option value="all">不限</option>
            </select>
          </div>
        </div>
        <div>
          <label className={lbl}>目标岗位</label>
          <TagInput section="job_preference" field="target_positions" inputKey="pos" placeholder="如：AI Agent 工程师" color="blue" />
        </div>
        <div>
          <label className={lbl}>期望城市</label>
          <TagInput section="job_preference" field="work_cities" inputKey="city" placeholder="如：深圳" color="green" />
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div><label className={lbl}>期望日薪</label><ImeInput className={inp} value={profile.job_preference.expected_daily_salary} placeholder="200-300元/天" onValueChange={v => up("job_preference", "expected_daily_salary", v)} /></div>
          <div><label className={lbl}>实习时长</label><ImeInput className={inp} value={profile.job_preference.internship_duration} placeholder="3-6个月" onValueChange={v => up("job_preference", "internship_duration", v)} /></div>
          <div><label className={lbl}>每周可实习天数</label><input type="number" min={1} max={7} className={inp} value={profile.job_preference.available_days_per_week} onChange={e => up("job_preference", "available_days_per_week", Number(e.target.value))} /></div>
          <div><label className={lbl}>最早到岗时间</label><ImeInput className={inp} value={profile.job_preference.earliest_start_date} placeholder="一周内到岗" onValueChange={v => up("job_preference", "earliest_start_date", v)} /></div>
          <div className="flex items-end gap-4">
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={profile.job_preference.is_remote_ok} onChange={e => up("job_preference", "is_remote_ok", e.target.checked)} className="rounded" /> 接受远程</label>
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={profile.job_preference.overtime_ok} onChange={e => up("job_preference", "overtime_ok", e.target.checked)} className="rounded" /> 可加班</label>
          </div>
        </div>
        <div>
          <label className={lbl}>求职备注 <span className="text-xs text-zinc-400">(补充说明灵活偏好，Agent 匹配和回复时会参考)</span></label>
          <ImeTextarea className={`${inp} h-20`} value={profile.job_preference.notes}
            placeholder="如：城市不限，核心诉求是找到 AI Agent / 大模型应用方向的业务实习，为秋招积累经验。薪资只要能覆盖当地生存成本即可，北京上海租房成本高需要考虑。优先匹配垂直 AI 方向，不考虑传统后端或前端岗位。"
            onValueChange={v => up("job_preference", "notes", v)} />
        </div>
      </div>

      {/* Greeting */}
      <div className={sec}>
        <h4 className="font-medium text-zinc-800">默认打招呼文本</h4>
        <ImeTextarea className={`${inp} h-16`} value={profile.default_greeting}
          placeholder="如：您好，27应届硕士在读，可实习3~6个月，每周可实习5天"
          onValueChange={v => setProfile(p => ({ ...p, default_greeting: v }))} />
      </div>

      {/* Reply Policy */}
      <div className={sec}>
        <h4 className="font-medium text-zinc-800">自动回复策略</h4>
        <div>
          <label className={lbl}>可自动回复的话题 <span className="text-xs text-zinc-400">(勾选的话题 Agent 会根据画像自动回复)</span></label>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {AUTO_REPLY_OPTIONS.map(opt => (
              <label key={opt.key} className="inline-flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={profile.reply_policy.auto_reply_topics.includes(opt.key)}
                  onChange={() => toggleArrayItem("reply_policy", "auto_reply_topics", opt.key)} className="rounded" />
                {opt.label}
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className={lbl}>需通知人工介入的话题 <span className="text-xs text-zinc-400">(勾选的话题 Agent 不回复，飞书通知你)</span></label>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {ESCALATE_OPTIONS.map(opt => (
              <label key={opt.key} className="inline-flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={profile.reply_policy.escalate_topics.includes(opt.key)}
                  onChange={() => toggleArrayItem("reply_policy", "escalate_topics", opt.key)} className="rounded" />
                {opt.label}
              </label>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className={lbl}>回复语气</label><ImeInput className={inp} value={profile.reply_policy.tone} onValueChange={v => up("reply_policy", "tone", v)} /></div>
        </div>
      </div>
    </div>
  );
}
