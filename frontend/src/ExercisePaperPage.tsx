import { Button, TextField } from "@radix-ui/themes";
import { useEffect, useState } from "react";
import { Copy, FileText, Printer } from "lucide-react";
import { apiUrl, SiteFooter, ToolHeader } from "./App";
import type { AppPage } from "./App";
import { difficultyLabels, exercisePageHref, typeLabels } from "./exerciseTypes";
import type { SavedExercisePage } from "./exerciseTypes";
import { MathMarkdown } from "./MathMarkdown";
import "./exercises.css";

export default function ExercisePaperPage({ slug, navigateTo }: { slug: string; navigateTo: (page: AppPage) => void }) {
  const [paper, setPaper] = useState<SavedExercisePage | null>(null);
  const [mode, setMode] = useState<"worksheet" | "answers">("worksheet");
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPaper(null); setError(""); setMode("worksheet"); setCopyStatus("");
    fetch(apiUrl(`/api/exercises/pages/${encodeURIComponent(slug)}`), { signal: controller.signal })
      .then(async response => {
        const value = await response.json();
        if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "试题读取失败");
        if (!controller.signal.aborted) setPaper(value as SavedExercisePage);
      })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [slug, retry]);
  useEffect(() => {
    if (!paper) return;
    const previous = document.title;
    document.title = `${paper.title}${mode === "answers" ? " · 答案解析" : ""}`;
    return () => { document.title = previous; };
  }, [paper, mode]);
  const href = new URL(exercisePageHref(slug), window.location.href).href;
  async function copyLink() {
    try { await navigator.clipboard.writeText(href); setCopyStatus("页面地址已复制"); }
    catch { setCopyStatus("请选中下方页面地址复制"); }
  }
  return <main className="app-shell exercise-shell exercise-paper-shell">
    <div className="exercise-no-print"><ToolHeader currentPage="exercises" title="试题预览与打印" subtitle="保存页面地址，随时回看和打印" icon={<FileText size={24} />} navigateTo={navigateTo} /></div>
    {error ? <div className="exercise-panel exercise-no-print" role="alert">{error}<div className="exercise-row"><Button variant="soft" className="exercise-button" onClick={() => setRetry(value => value + 1)}>重新读取</Button><a href="#/exercises">返回试题记录</a></div></div>
      : !paper ? <div className="exercise-panel exercise-no-print" role="status">正在读取试题…</div> : <>
      <section className="exercise-panel exercise-paper-toolbar exercise-no-print">
        <div className="exercise-row"><a href="#/exercises">← 返回出题与历史记录</a><a href={`#/exercises?lesson=${encodeURIComponent(paper.lesson_id)}`}>继续为这门课程出题</a></div>
        <div className="exercise-row" aria-label="试题显示方式"><Button variant="soft" className="exercise-button" aria-pressed={mode === "worksheet"} onClick={() => setMode("worksheet")}>练习卷</Button><Button variant="soft" className="exercise-button" aria-pressed={mode === "answers"} onClick={() => setMode("answers")}>答案解析</Button><Button variant="soft" className="exercise-button exercise-print-button" onClick={() => window.print()}><Printer size={18} />打印{mode === "worksheet" ? "练习卷" : "答案解析"}</Button><Button variant="soft" className="exercise-button" onClick={() => void copyLink()}><Copy size={16} />复制页面地址</Button></div>
        <label className="exercise-paper-address">页面地址<TextField.Root aria-label="试题页面地址" readOnly value={href} onFocus={event => event.currentTarget.select()} /></label>
        {copyStatus ? <p role="status" className="exercise-note">{copyStatus}</p> : null}
        <p className="exercise-note">A4 排版，练习卷留答题空间。切换到答案解析后可单独打印，也可在打印窗口保存为 PDF。</p>
      </section>
      <article className={`exercise-paper exercise-paper-${mode}`} aria-label={mode === "worksheet" ? "练习卷内容" : "答案解析内容"}>
        <header className="exercise-paper-heading"><h1>{paper.title}</h1>{mode === "answers" ? <h2>参考答案与解析</h2> : <div className="exercise-paper-student">姓名：________________　班级：________________　日期：________________</div>}<p>共 {paper.questions.length} 道题{paper.knowledge_point_titles.length ? ` · ${paper.knowledge_point_titles.join("、")}` : ""}</p></header>
        {paper.questions.map((question, index) => <section className="exercise-paper-question" key={question.id}>
          <h3>第 {index + 1} 题 <span>{typeLabels[question.type]} · {difficultyLabels[question.difficulty]}</span></h3>
          <MathMarkdown text={question.stem} />
          {question.options.map((option, i) => <div className="exercise-option" key={i}><strong>{String.fromCharCode(65 + i)}.</strong><MathMarkdown text={option} /></div>)}
          {mode === "answers" ? <div className="exercise-paper-solution"><h4>答案</h4><MathMarkdown text={question.answer} /><h4>解析</h4><MathMarkdown text={question.explanation} /></div>
            : question.type === "short_answer" || question.type === "calculation" ? <div className={`exercise-answer-space exercise-answer-space-${question.type}`} aria-label="答题空间" /> : null}
        </section>)}
      </article>
    </>}
    <div className="exercise-no-print"><SiteFooter currentPage="exercises" navigateTo={navigateTo} /></div>
  </main>;
}
