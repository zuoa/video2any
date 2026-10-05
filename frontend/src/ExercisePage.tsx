import { useEffect, useRef, useState } from "react";
import { FileText, Loader2, RefreshCw, Upload } from "lucide-react";
import { ToolHeader, SiteFooter, apiUrl, parseBilibiliInput } from "./App";
import type { AppPage } from "./App";
import type { BilibiliPagesResponse, VideoInfo } from "./types";
import { MathMarkdown } from "./MathMarkdown";
import ExerciseHistory from "./ExerciseHistory";
import { difficultyLabels, exercisePageHref, typeLabels } from "./exerciseTypes";
import type { Difficulty, Point, QType, Question, SavedExercisePage } from "./exerciseTypes";
import "./exercises.css";

type ASRBackend = "sensevoice" | "qwen3" | "whisper";
type ASROptions = { default_backend: ASRBackend; backends: { id: ASRBackend; label: string; description: string }[] };
type Segment = { start: number; end: number; text: string };
type Batch = { id: string; version: number; created_at: number; questions: Question[]; knowledge_points?: Point[]; page_slug?: string };
type Lesson = { id: string; video_id: string; title: string; version: number; duration: number; asr_backend?: ASRBackend; segments: Segment[]; knowledge_points: Point[]; batches: Batch[]; latest_job_id: string | null; source: { type: string; bv: string | null; page: number | null } };
type Job = { id: string; lesson_id: string; kind: "prepare" | "generate"; status: "queued" | "running" | "succeeded" | "failed"; stage: string; progress: number; error: string | null; request: Record<string, unknown> | null };
type Task = { lesson_id: string; job_id: string | null };
const stages: Record<string, string> = { queued: "等待处理", waiting_for_transcription: "等待语音识别", loading_model: "加载语音模型（首次使用需要下载）", extracting_audio: "提取视频音轨", detecting_speech: "检测语音片段", transcribing: "识别讲课语音", extracting_knowledge: "提炼知识点", generating_questions: "生成并复核练习题", done: "处理完成", interrupted: "任务已中断" };

async function request<T>(path: string, body?: unknown, method = "POST", signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(path), body === undefined ? { signal } : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "请求失败，请重试");
  return value as T;
}
function lastLesson(): string {
  const query = window.location.hash.split("?")[1] ?? "";
  const linked = new URLSearchParams(query).get("lesson");
  if (linked) return linked;
  try { return localStorage.getItem("v2a-exercises-v1") ?? ""; } catch { return ""; }
}
function time(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}
function toggle(values: string[], id: string): string[] {
  return values.includes(id) ? values.filter(value => value !== id) : [...values, id];
}
function batchTitle(lesson: Lesson, batch?: Batch): string {
  const used = new Set(batch?.questions.flatMap(q => q.knowledge_point_ids) ?? []);
  const topic = (batch?.knowledge_points ?? lesson.knowledge_points).filter(p => used.has(p.id)).map(p => p.title).slice(0, 3).join("、") || lesson.title.replace(/\.[^.]+$/, "");
  return `${topic} 知识练习`.slice(0, 120);
}

export default function ExercisePage({ navigateTo }: { navigateTo: (page: AppPage) => void }) {
  const [lessonId, setLessonId] = useState(lastLesson);
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [points, setPoints] = useState<Point[]>([]);
  const [chosenPoints, setChosenPoints] = useState<string[]>([]);
  const [types, setTypes] = useState<QType[]>(Object.keys(typeLabels) as QType[]);
  const [difficulty, setDifficulty] = useState<Difficulty>("practice");
  const [count, setCount] = useState(10);
  const [batchId, setBatchId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [title, setTitle] = useState("讲课知识练习");
  const [bv, setBv] = useState("");
  const [pages, setPages] = useState<BilibiliPagesResponse | null>(null);
  const [page, setPage] = useState(1);
  const [asrOptions, setAsrOptions] = useState<ASROptions | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const active = job?.status === "queued" || job?.status === "running";
  const busy = pending || active;
  const pointsDirty = lesson ? JSON.stringify(points) !== JSON.stringify(lesson.knowledge_points) : false;
  const segmentsDirty = lesson ? JSON.stringify(segments) !== JSON.stringify(lesson.segments) : false;
  const batch = lesson?.batches.find(value => value.id === batchId);
  const asrBackend = asrOptions?.default_backend;
  const selectedModel = asrOptions?.backends.find(value => value.id === asrBackend);
  const currentModel = asrOptions?.backends.find(value => value.id === (lesson?.asr_backend ?? "whisper"));

  useEffect(() => {
    const controller = new AbortController();
    request<ASROptions>("/api/exercises/asr/options", undefined, "GET", controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        setAsrOptions(value);
      })
      .catch(err => { if (!controller.signal.aborted) setError(`无法读取语音模型配置：${err.message}`); });
    return () => controller.abort();
  }, []);

  function hydrate(value: Lesson) {
    setLesson(value); setSegments(value.segments); setPoints(value.knowledge_points);
    setChosenPoints(value.knowledge_points.map(p => p.id));
    const latest = value.batches[value.batches.length - 1];
    setBatchId(latest?.id ?? ""); setSelected(latest?.questions.map(q => q.id) ?? []);
    setTitle(batchTitle(value, latest));
  }
  useEffect(() => {
    if (!lessonId) return;
    const controller = new AbortController();
    setPending(true); setError("");
    request<Lesson>(`/api/exercises/${lessonId}`, undefined, "GET", controller.signal)
      .then(value => { hydrate(value); setJobId(value.latest_job_id); })
      .catch(err => { if (!controller.signal.aborted) setError(String(err.message)); })
      .finally(() => { if (!controller.signal.aborted) setPending(false); });
    try { localStorage.setItem("v2a-exercises-v1", lessonId); } catch { /* Storage is optional. */ }
    return () => controller.abort();
  }, [lessonId]);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    const poll = async () => {
      try {
        const value = await request<Job>(`/api/exercises/jobs/${jobId}`, undefined, "GET", controller.signal);
        if (controller.signal.aborted) return;
        setJob(value); failures = 0;
        if (value.status === "succeeded" || value.status === "failed") {
          const updated = await request<Lesson>(`/api/exercises/${value.lesson_id}`, undefined, "GET", controller.signal);
          if (!controller.signal.aborted) {
            hydrate(updated);
            if (value.status === "failed") setError(value.error ?? "处理失败，请重试");
          }
          return;
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        failures += 1;
        setError(`状态读取失败，将自动重试：${err instanceof Error ? err.message : "网络异常"}`);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, Math.min(15000, 2000 * (failures + 1)));
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [jobId]);

  async function run(action: () => Promise<void>) {
    setPending(true); setError("");
    try { await action(); } catch (err) { setError(err instanceof Error ? err.message : "操作失败，请重试"); }
    finally { setPending(false); }
  }
  async function acceptTask(task: Task) {
    if (task.lesson_id !== lessonId) {
      setLesson(null); setLessonId(task.lesson_id);
      window.history.replaceState(null, "", `#/exercises?lesson=${task.lesson_id}`);
    }
    if (task.job_id) {
      const current = await request<Job>(`/api/exercises/jobs/${task.job_id}`);
      setJob(current); setJobId(task.job_id);
      if (current.status === "succeeded" || current.status === "failed") {
        hydrate(await request<Lesson>(`/api/exercises/${task.lesson_id}`));
        if (current.status === "failed") setError(current.error ?? "处理失败，请重试");
      }
    } else {
      setJob(null); setJobId(null);
      hydrate(await request<Lesson>(`/api/exercises/${task.lesson_id}`));
    }
  }
  async function prepare(info: VideoInfo) {
    await acceptTask(await request<Task>("/api/exercises/prepare", { video_id: info.id }));
  }
  async function upload(file: File | undefined) {
    if (!file) return;
    await run(async () => {
      const form = new FormData(); form.append("file", file);
      const response = await fetch(apiUrl("/api/videos/upload"), { method: "POST", body: form });
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail ?? "上传失败");
      await prepare(value as VideoInfo);
    });
  }
  async function discoverBili() {
    await run(async () => {
      const parsed = parseBilibiliInput(bv);
      if (!parsed) throw new Error("请输入 BV 号或 Bilibili 视频地址");
      const result = await request<BilibiliPagesResponse>("/api/videos/bilibili/pages", { bv, page: parsed.page });
      setPages(result); setPage(result.selected_page);
      if (result.pages.length === 1) await downloadBili(result.bv, result.selected_page);
    });
  }
  async function downloadBili(id: string, selectedPage: number) {
    await prepare(await request<VideoInfo>("/api/videos/bilibili", { bv: id, page: selectedPage }));
  }
  async function saveTranscript() {
    if (!lesson) return;
    await run(async () => acceptTask(await request<Task>(`/api/exercises/${lesson.id}`, { version: lesson.version, segments }, "PATCH")));
  }
  async function savePoints() {
    if (!lesson) return;
    await run(async () => {
      await request<Task>(`/api/exercises/${lesson.id}`, { version: lesson.version, knowledge_points: points }, "PATCH");
      hydrate(await request<Lesson>(`/api/exercises/${lesson.id}`));
    });
  }
  async function generate() {
    if (!lesson) return;
    await run(async () => acceptTask(await request<Task>(`/api/exercises/${lesson.id}/generate`, { version: lesson.version, knowledge_point_ids: chosenPoints, types, difficulty, count })));
  }
  async function retry() {
    if (!lesson || !job) return;
    await run(async () => acceptTask(job.kind === "generate" && job.request
      ? await request<Task>(`/api/exercises/${lesson.id}/generate`, job.request)
      : await request<Task>("/api/exercises/prepare", { video_id: lesson.video_id })));
  }
  async function switchModel() {
    if (!lesson) return;
    await run(async () => acceptTask(await request<Task>("/api/exercises/prepare", { video_id: lesson.video_id })));
  }
  async function savePage() {
    if (!lesson || !batch) return;
    await run(async () => {
      const saved = await request<SavedExercisePage>(`/api/exercises/${lesson.id}/pages`, { batch_id: batch.id, question_ids: selected, title: title.trim() });
      window.location.hash = exercisePageHref(saved.slug);
    });
  }
  function jump(segmentId: number) {
    const start = segments[segmentId]?.start;
    if (start !== undefined && video.current) { video.current.currentTime = start; void video.current.play().catch(() => {}); }
  }

  return <main className="app-shell exercise-shell">
    <ToolHeader currentPage="exercises" title="讲课视频转练习题" subtitle="提炼知识点，保存试题页面，随时回看与打印" icon={<FileText size={24} />} navigateTo={navigateTo} />
    <ExerciseHistory revision={lesson?.batches.map(b => b.id).join(",") ?? ""} />
    <section className="exercise-panel">
      <h2>1. 导入讲课视频</h2>
      <p className="exercise-note">当前语音识别模型：{selectedModel?.label ?? "读取配置中…"}{selectedModel ? ` · ${selectedModel.description}` : ""}</p>
      <div className="exercise-source">
        <label className="exercise-upload"><Upload size={18} /> 上传视频<input type="file" accept="video/*" disabled={busy || !asrOptions} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ""; }} /></label>
        <div className="exercise-bili"><input aria-label="BV 号或 B 站地址" placeholder="BV 号或 Bilibili 视频地址" value={bv} disabled={busy} onChange={event => { setBv(event.target.value); setPages(null); }} /><button disabled={busy || !asrOptions || !bv.trim()} onClick={() => void discoverBili()}>读取视频</button></div>
      </div>
      {pages && pages.pages.length > 1 ? <div className="exercise-row"><label>分 P <select value={page} disabled={busy} onChange={event => setPage(Number(event.target.value))}>{pages.pages.map(p => <option key={p.page} value={p.page}>P{p.page} · {p.title}</option>)}</select></label><button disabled={busy} onClick={() => void run(() => downloadBili(pages.bv, page))}>下载并识别</button></div> : null}
      <p className="exercise-note">根据讲解语音生成。PPT 或板书中未念出的内容，可在转写文字或知识点中手动补充。</p>
    </section>
    {error ? <div role="alert" className="exercise-error">{error}</div> : null}
    {pending && !active ? <div className="exercise-status" role="status"><Loader2 className="spin" size={18} /> 正在处理请求…</div> : null}
    {job ? <div className="exercise-status" role="status">{active ? <Loader2 className="spin" size={18} /> : null}<span>{stages[job.stage] ?? job.stage} {active ? `${job.progress}%` : ""}</span>{active ? <progress value={job.progress} max={100} /> : null}{job.status === "failed" ? <button disabled={busy} onClick={() => void retry()}><RefreshCw size={16} /> 重试任务</button> : null}</div> : null}
    {lesson ? <>
      <section className="exercise-panel">
        <h2>2. 校对文字，确认知识点</h2>
        <p>{lesson.title} · {time(lesson.duration)} · 内容版本 {lesson.version} · {currentModel?.label ?? lesson.asr_backend ?? "Whisper"}</p>
        {(lesson.asr_backend ?? "whisper") !== asrBackend && asrOptions ? <div className="exercise-model-switch">
          <button disabled={busy || pointsDirty || segmentsDirty} onClick={() => void switchModel()}>使用 {selectedModel?.label} 重新识别</button>
          <p className="exercise-note">重新识别会替换当前转写文字和知识点，旧题目批次保留。需要原视频仍在缓存中。请先保存当前修改。</p>
        </div> : null}
        <details><summary>查看视频与转写文字（{segments.length} 段）</summary>
          <video ref={video} controls preload="metadata" src={apiUrl(`/api/videos/${lesson.video_id}/file`)} className="exercise-video" />
          <p className="exercise-note">原视频缓存过期后仍可使用已保存的文字和题目。时间戳保持原视频位置，补充公式可用 $...$ 或 $$...$$。</p>
          <div className="exercise-transcript">{segments.map((segment, index) => <label key={index}><button type="button" onClick={() => jump(index)}>{time(segment.start)}</button><textarea aria-label={`转写片段 ${index + 1}`} disabled={busy || pointsDirty} value={segment.text} onChange={event => setSegments(values => values.map((value, i) => i === index ? { ...value, text: event.target.value } : value))} /></label>)}</div>
          <button disabled={busy || !segmentsDirty || pointsDirty || segments.some(s => !s.text.trim())} onClick={() => void saveTranscript()}>保存文字并重新提炼知识点</button>
        </details>
        {points.length ? <>
          <div className="exercise-row"><button disabled={busy} onClick={() => setChosenPoints(points.map(p => p.id))}>全选知识点</button><span>已选 {chosenPoints.length} / {points.length}</span>{pointsDirty ? <button disabled={busy || segmentsDirty || points.some(p => !p.title.trim() || !p.detail.trim())} onClick={() => void savePoints()}>保存知识点修改</button> : null}</div>
          <div className="exercise-points">{points.map((point, index) => <article key={point.id} className="exercise-point">
            <label className="exercise-point-heading"><input type="checkbox" checked={chosenPoints.includes(point.id)} disabled={busy} onChange={() => setChosenPoints(values => toggle(values, point.id))} /><strong>{point.title}</strong></label>
            <MathMarkdown text={point.detail} />{point.formulas ? <MathMarkdown text={point.formulas} /> : null}
            <div className="exercise-evidence">{point.segment_ids.slice(0, 8).map(id => <button key={id} onClick={() => jump(id)}>{time(segments[id]?.start ?? 0)}</button>)}{lesson.source.bv ? <a href={`https://www.bilibili.com/video/${lesson.source.bv}?p=${lesson.source.page ?? 1}&t=${Math.floor(segments[point.segment_ids[0]]?.start ?? 0)}`} target="_blank" rel="noreferrer">回看 B 站讲解</a> : null}</div>
            <details><summary>编辑知识点 / 补充公式</summary>{(["title", "detail", "formulas"] as const).map(field => <label key={field}>{({ title: "标题", detail: "讲解", formulas: "公式" })[field]}<textarea disabled={busy || segmentsDirty} value={point[field]} onChange={event => setPoints(values => values.map((value, i) => i === index ? { ...value, [field]: event.target.value } : value))} /></label>)}</details>
          </article>)}</div>
        </> : <p className="exercise-note">{active ? "识别后将在这里展示知识点。" : "尚无知识点；检查任务状态后重试。"}</p>}
      </section>
      <section className="exercise-panel">
        <h2>3. 生成候选题</h2>
        <fieldset disabled={busy}><legend>题型</legend><div className="exercise-row">{(Object.keys(typeLabels) as QType[]).map(type => <label key={type}><input type="checkbox" checked={types.includes(type)} onChange={() => setTypes(values => toggle(values, type) as QType[])} />{typeLabels[type]}</label>)}</div></fieldset>
        <div className="exercise-row"><label>难度<select disabled={busy} value={difficulty} onChange={event => setDifficulty(event.target.value as Difficulty)}>{Object.entries(difficultyLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>生成数量<input type="number" min={1} max={30} disabled={busy} value={count} onChange={event => setCount(Number(event.target.value))} /></label><button disabled={busy || !chosenPoints.length || !types.length || pointsDirty || segmentsDirty || !Number.isInteger(count) || count < 1 || count > 30} onClick={() => void generate()}>{lesson.batches.length ? "再生成一批题目" : "确认知识点并生成题目"}</button></div>
        {pointsDirty || segmentsDirty ? <p className="exercise-note">请先保存修改，再生成题目。</p> : null}
        {chosenPoints.length > count ? <p className="exercise-note">题数少于知识点数，本批会从所选范围均匀取点出题；增加题数可覆盖更多知识点。</p> : null}
      </section>
      {lesson.batches.length ? <section className="exercise-panel">
        <h2>4. 预览选题，打开打印页面</h2>
        <label>题目批次<select disabled={busy} value={batchId} onChange={event => { const value = lesson.batches.find(b => b.id === event.target.value); setBatchId(event.target.value); setSelected(value?.questions.map(q => q.id) ?? []); setTitle(batchTitle(lesson, value)); }}>{lesson.batches.map((value, index) => <option key={value.id} value={value.id}>第 {index + 1} 批 · 内容版本 {value.version} · {value.questions.length} 题</option>)}</select></label>
        {batch && batch.version !== lesson.version ? <p className="exercise-note">此批题目基于内容版本 {batch.version}。当前内容已更新，可重新生成一批题目。</p> : null}
        {batch?.page_slug ? <p className="exercise-saved-link">本批试题已自动保存：<a href={exercisePageHref(batch.page_slug)}>查看完整试题与打印 →</a><span className="exercise-history-slug">{batch.page_slug}</span></p> : null}
        <div className="exercise-row"><button disabled={busy} onClick={() => setSelected(batch?.questions.map(q => q.id) ?? [])}>全选</button><button disabled={busy} onClick={() => setSelected([])}>清空选择</button><strong>已选 {selected.length} 题</strong></div>
        <div className="exercise-questions">{batch?.questions.map((question, index) => <article key={question.id} className="exercise-question"><label className="exercise-point-heading"><input type="checkbox" disabled={busy} checked={selected.includes(question.id)} onChange={() => setSelected(values => toggle(values, question.id))} /><strong>第 {index + 1} 题</strong><span>{typeLabels[question.type]} · {difficultyLabels[question.difficulty]}</span></label><MathMarkdown text={question.stem} />{question.options.map((option, i) => <div className="exercise-option" key={i}><strong>{String.fromCharCode(65 + i)}.</strong><MathMarkdown text={option} /></div>)}<details><summary>查看答案与解析</summary><h4>答案</h4><MathMarkdown text={question.answer} /><h4>解析</h4><MathMarkdown text={question.explanation} /></details></article>)}</div>
        <div className="exercise-export"><label>练习卷标题<input maxLength={120} disabled={busy} value={title} onChange={event => setTitle(event.target.value)} /></label><div className="exercise-row"><button disabled={busy || !selected.length || !title.trim()} onClick={() => void savePage()}><FileText size={17} />保存所选题目并打开页面</button></div><p className="exercise-note">页面地址使用知识点拼音，同名页面按序号区分。保存后可分别查看、打印练习卷和答案解析。</p></div>
      </section> : null}
    </> : null}
    <SiteFooter currentPage="exercises" navigateTo={navigateTo} />
  </main>;
}
