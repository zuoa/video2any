"""Validated public inputs and model output for lecture exercises."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


QuestionType = Literal["single_choice", "fill_blank", "short_answer", "calculation"]
Difficulty = Literal["basic", "practice", "advanced"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Segment(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("结束时间不能早于开始时间")
        return self


class KnowledgePoint(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=8000)
    formulas: str = Field(default="", max_length=8000)
    segment_ids: list[int] = Field(min_length=1)


class Question(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    type: QuestionType
    difficulty: Difficulty
    stem: str = Field(min_length=1, max_length=12000)
    options: list[str] = Field(default_factory=list, max_length=4)
    answer: str = Field(min_length=1, max_length=12000)
    explanation: str = Field(min_length=1, max_length=20000)
    knowledge_point_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def choice_options(self):
        if self.type == "single_choice":
            if len(self.options) != 4 or any(not x.strip() for x in self.options):
                raise ValueError("单选题需要四个非空选项")
            if self.answer not in ("A", "B", "C", "D"):
                raise ValueError("单选题答案必须为 A、B、C 或 D")
        elif self.options:
            raise ValueError("非选择题不应包含选项")
        if self.type == "fill_blank" and not re.search(r"_{3,}|[（(]\s*[）)]|\\(?:underline|boxed)\b", self.stem):
            raise ValueError("填空题必须包含明确的填空位置")
        if len(set(self.knowledge_point_ids)) != len(self.knowledge_point_ids):
            raise ValueError("题目知识点关联重复")
        return self


class PrepareRequest(StrictModel):
    video_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class LessonPatch(StrictModel):
    version: int = Field(ge=1)
    segments: list[Segment] | None = Field(default=None, min_length=1, max_length=50000)
    knowledge_points: list[KnowledgePoint] | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def one_change(self):
        if (self.segments is None) == (self.knowledge_points is None):
            raise ValueError("请仅提交转写文字或知识点中的一种修改")
        return self


class GenerateRequest(StrictModel):
    version: int = Field(ge=1)
    knowledge_point_ids: list[str] = Field(min_length=1, max_length=300)
    types: list[QuestionType] = Field(default=["single_choice", "fill_blank", "short_answer", "calculation"], min_length=1, max_length=4)
    difficulty: Difficulty = "practice"
    count: int = Field(default=10, ge=1, le=30)


class ExportExercisesRequest(StrictModel):
    batch_id: str = Field(min_length=1, max_length=64)
    question_ids: list[str] = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["worksheet", "answers"]
