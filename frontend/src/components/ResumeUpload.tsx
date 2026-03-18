"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

export function ResumeUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceId] = useState("resume_v1");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedText, setSavedText] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [loadingExisting, setLoadingExisting] = useState(false);
  const [uploadSuccess, setUploadSuccess] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchExisting = useCallback(async () => {
    setLoadingExisting(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/resume/source/${encodeURIComponent(sourceId)}`);
      if (resp.ok) {
        const data = await resp.json();
        setSavedText(data.resume_text ?? null);
        setSavedAt(data.updated_at ? new Date(data.updated_at).toLocaleString() : null);
      } else {
        setSavedText(null);
        setSavedAt(null);
      }
    } catch {
      setSavedText(null);
    } finally {
      setLoadingExisting(false);
    }
  }, [sourceId]);

  useEffect(() => { void fetchExisting(); }, [fetchExisting]);

  const handleUploadFile = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) { setError("请选择文件"); return; }
    setUploading(true);
    setError(null);
    setUploadSuccess(false);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("source_id", sourceId);
      const resp = await fetch(`${API_BASE_URL}/api/resume/upload`, { method: "POST", body: form });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setSavedText(data.text_preview ?? null);
      setSavedAt(new Date().toLocaleString());
      setUploadSuccess(true);
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      void fetchExisting();
    } catch (err) {
      setError(`上传失败：${String(err)}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">简历管理</h3>

      {loadingExisting ? (
        <p className="text-sm text-zinc-500">正在加载已有简历...</p>
      ) : savedText ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-2 text-sm font-medium text-emerald-800">
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              已有简历（{savedText.length} 字）
            </span>
            {savedAt && <span className="text-xs text-emerald-600">更新于 {savedAt}</span>}
          </div>
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-emerald-700 hover:underline">查看简历内容</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-white p-3 text-xs leading-relaxed text-zinc-700">
              {savedText}
            </pre>
          </details>
        </div>
      ) : (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          尚未上传简历，请上传 PDF/Word 文件
        </p>
      )}

      {uploadSuccess && (
        <p className="rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          简历上传成功！文本已提取并保存。
        </p>
      )}

      <form onSubmit={handleUploadFile} className="space-y-3">
        <div
          className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-zinc-300 bg-zinc-50 px-6 py-6 transition hover:border-blue-400 hover:bg-blue-50/30"
          onClick={() => fileRef.current?.click()}
        >
          <svg className="mb-2 h-7 w-7 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 16V4m0 0l-4 4m4-4l4 4M4 14v4a2 2 0 002 2h12a2 2 0 002-2v-4" />
          </svg>
          {file ? (
            <p className="text-sm font-medium text-blue-700">{file.name} ({(file.size / 1024).toFixed(1)} KB)</p>
          ) : (
            <>
              <p className="text-sm font-medium text-zinc-600">{savedText ? "重新上传简历" : "点击选择文件"}</p>
              <p className="mt-1 text-xs text-zinc-400">支持 .pdf / .docx / .txt / .md</p>
            </>
          )}
          <input ref={fileRef} type="file" className="hidden" accept=".pdf,.docx,.doc,.txt,.md"
            onChange={e => { setFile(e.target.files?.[0] ?? null); setUploadSuccess(false); setError(null); }} />
        </div>
        <button type="submit" disabled={uploading || !file}
          className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
          {uploading ? "上传处理中..." : savedText ? "重新上传" : "上传简历"}
        </button>
      </form>

      {error && <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>}
    </div>
  );
}
