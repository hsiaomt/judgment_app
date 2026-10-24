"""比對報告格式；與裁判分析模型分開，避免影響分析用 JSON schema。"""

from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator


NonBlankText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
REPORT_CONFIG = ConfigDict(strict=True, extra="allow")


class MatchedCandidate(BaseModel):
    model_config = REPORT_CONFIG
    json_path: str = Field(alias="json")
    crime_id: PositiveInt


class ComparisonResult(BaseModel):
    """單列或單份判決的比對結果。"""
    model_config = ConfigDict(strict=True, extra="forbid")

    status: Literal["equal", "unmatched", "judgment_not_found"]
    reason: NonBlankText | None = None
    excel: str | None = None
    sheet: str | None = None
    row: PositiveInt | None = None
    name: str | None = None
    case_number: str | None = None
    json_path: str | None = Field(default=None, alias="json")
    candidate_crime_ids: list[PositiveInt] = Field(default_factory=list)
    matched_candidates: list[MatchedCandidate] = Field(default_factory=list)
    document_results: list[Self] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_reason(self) -> Self:
        if self.status != "equal" and self.reason is None:
            raise ValueError(f"{self.status} 必須提供 reason")
        if self.status == "equal" and self.reason is not None:
            raise ValueError("equal 不可提供 reason")
        return self


class ReportError(BaseModel):
    model_config = REPORT_CONFIG
    error: NonBlankText
    file: str | None = None
    sheet: str | None = None
    row: PositiveInt | None = None
    stage: str | None = None


class SheetReport(BaseModel):
    model_config = REPORT_CONFIG
    file: str
    sheet: str
    uncompared_columns: list[str] = Field(default_factory=list)


class ExcelOutput(BaseModel):
    model_config = REPORT_CONFIG
    source: NonBlankText
    output: NonBlankText


class ComparisonReport(BaseModel):
    model_config = REPORT_CONFIG
    rows: list[ComparisonResult]
    errors: list[ReportError]
    sheets: list[SheetReport] = Field(default_factory=list)
    excel_outputs: list[ExcelOutput] = Field(default_factory=list)

    def to_dict(self) -> dict:
        """維持既有字典介面；不補出未提供的欄位，摘要一律依結果重新計算。"""
        data = self.model_dump(mode="json", by_alias=True, exclude_unset=True)
        data["summary"] = dict(Counter(row.status for row in self.rows))
        data["summary"].update(total=len(self.rows), errors=len(self.errors))
        return data
