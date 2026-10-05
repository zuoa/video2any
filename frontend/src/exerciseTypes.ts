export type QType = "single_choice" | "fill_blank" | "short_answer" | "calculation";
export type Difficulty = "basic" | "practice" | "advanced";
export type Point = { id: string; title: string; detail: string; formulas: string; segment_ids: number[] };
export type Question = { id: string; type: QType; difficulty: Difficulty; stem: string; options: string[]; answer: string; explanation: string; knowledge_point_ids: string[] };
export type ExercisePageSummary = { slug: string; title: string; lesson_title: string; lesson_id: string; batch_id: string; version: number; created_at: number; question_count: number; knowledge_point_titles: string[] };
export type SavedExercisePage = ExercisePageSummary & { knowledge_points: Point[]; questions: Question[] };
export const typeLabels: Record<QType, string> = { single_choice: "单选题", fill_blank: "填空题", short_answer: "简答题", calculation: "计算题" };
export const difficultyLabels: Record<Difficulty, string> = { basic: "基础", practice: "巩固", advanced: "提高" };
export function exercisePageHref(slug: string): string { return `#/exercises/papers/${encodeURIComponent(slug)}`; }

