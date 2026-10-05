import { useEffect, useState } from "react";
import { apiUrl } from "./App";
import { exercisePageHref } from "./exerciseTypes";
import type { ExercisePageSummary } from "./exerciseTypes";
import "./exercises.css";

const pageSize = 8;
export default function ExerciseHistory({ revision = "", compact = false }: { revision?: string; compact?: boolean }) {
  const [items, setItems] = useState<ExercisePageSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => { setOffset(0); }, [revision]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    fetch(apiUrl(`/api/exercises/pages?limit=${compact ? 5 : pageSize}&offset=${offset}`), { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("暂时无法读取试题记录");
        const value = await response.json() as { items: ExercisePageSummary[]; total: number };
        if (!controller.signal.aborted) { setItems(value.items); setTotal(value.total); }
      })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [compact, offset, revision, retry]);

  return <section className="exercise-panel exercise-history" aria-label="最近生成的试题">
    <h2>最近生成的试题</h2>
    <p className="exercise-note">已生成的题目会自动保存。点击标题即可回看和打印。</p>
    {error ? <p role="alert">{error} <button onClick={() => setRetry(value => value + 1)}>重新读取</button></p>
      : loading ? <p role="status">正在读取试题记录…</p>
      : items.length ? <ul className="exercise-history-list">{items.map(item => <li key={item.slug}>
        <a href={exercisePageHref(item.slug)}><strong>{item.title}</strong><span className="exercise-history-slug">{item.slug}</span></a>
        <p>{item.question_count} 道题 · {item.created_at ? new Date(item.created_at * 1000).toLocaleString("zh-CN") : "历史批次"}</p>
        <p className="exercise-note">{item.knowledge_point_titles.join("、") || item.lesson_title}</p>
      </li>)}</ul> : <p className="exercise-note">还没有生成过试题，完成出题后会显示在这里。</p>}
    {compact ? <a className="exercise-history-more" href="#/exercises">进入出题工作台，查看全部记录 →</a>
      : total > pageSize ? <div className="exercise-row"><button disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>上一页</button><span>第 {Math.floor(offset / pageSize) + 1} 页 · 共 {total} 份试题</span><button disabled={loading || offset + pageSize >= total} onClick={() => setOffset(value => value + pageSize)}>下一页</button></div> : null}
  </section>;
}
