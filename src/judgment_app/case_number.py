from dataclasses import dataclass, field, replace

import re

COURT_MAP = {
    "": "所有法院",
    "JCC": "憲法法庭",
    "TPC": "司法院刑事補償法庭",
    "TPU": "司法院－訴願決定",
    "TPS": "最高法院",
    "TPA": "最高行政法院(含改制前行政法院)",
    "TPP": "懲戒法院－懲戒法庭",
    "TPJ": "懲戒法院－職務法庭",
    "TPH": "臺灣高等法院",
    "001": "臺灣高等法院－訴願決定",
    "TPB": "臺北高等行政法院 高等庭(含改制前臺北高等行政法院)",
    "TPT": "臺北高等行政法院 地方庭",
    "TCB": "臺中高等行政法院 高等庭(含改制前臺中高等行政法院)",
    "TCT": "臺中高等行政法院 地方庭",
    "KSB": "高雄高等行政法院 高等庭(含改制前高雄高等行政法院)",
    "KST": "高雄高等行政法院 地方庭",
    "IPC": "智慧財產及商業法院",
    "TCH": "臺灣高等法院 臺中分院",
    "TNH": "臺灣高等法院 臺南分院",
    "KSH": "臺灣高等法院 高雄分院",
    "HLH": "臺灣高等法院 花蓮分院",
    "TPD": "臺灣臺北地方法院",
    "SLD": "臺灣士林地方法院",
    "PCD": "臺灣新北地方法院",
    "ILD": "臺灣宜蘭地方法院",
    "KLD": "臺灣基隆地方法院",
    "TYD": "臺灣桃園地方法院",
    "SCD": "臺灣新竹地方法院",
    "MLD": "臺灣苗栗地方法院",
    "TCD": "臺灣臺中地方法院",
    "CHD": "臺灣彰化地方法院",
    "NTD": "臺灣南投地方法院",
    "ULD": "臺灣雲林地方法院",
    "CYD": "臺灣嘉義地方法院",
    "TND": "臺灣臺南地方法院",
    "KSD": "臺灣高雄地方法院",
    "CTD": "臺灣橋頭地方法院",
    "HLD": "臺灣花蓮地方法院",
    "TTD": "臺灣臺東地方法院",
    "PTD": "臺灣屏東地方法院",
    "PHD": "臺灣澎湖地方法院",
    "KMH": "福建高等法院金門分院",
    "KMD": "福建金門地方法院",
    "LCD": "福建連江地方法院",
    "KSY": "臺灣高雄少年及家事法院",
}


@dataclass(frozen=True)
class CaseNumber:
    court: str
    year: int
    case: str
    number: int
    original_case: "CaseNumber | None" = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "court", normalize_court(self.court))
        for label, value in (("年度", self.year), ("號數", self.number)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label}必須是非負整數：{value!r}")
        if not isinstance(self.case, str) or not self.case.strip():
            raise ValueError("字別不可空白")
        object.__setattr__(self, "case", self.case.strip())

    @classmethod
    def from_jid(cls, jid: str, *, original_case: "CaseNumber | None" = None) -> "CaseNumber":
        return replace(JudgmentId.from_string(jid).case_number, original_case=original_case)

    @property
    def court_name(self) -> str:
        return COURT_MAP[self.court]

    @property
    def file_name(self) -> str:
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.to_string(ch=True)).rstrip(". ")

    def to_dict(self) -> dict[str, str | int]:
        return {"court": self.court, "year": self.year, "case": self.case, "number": self.number}

    def to_string(self, ch: bool = True) -> str:
        if ch:
            return f"{self.court_name}{self.year}年度{self.case}字第{self.number}號"
        return f"{self.court},{self.year},{self.case},{self.number}"


@dataclass(frozen=True)
class JudgmentId:
    case_number: CaseNumber
    date: str
    sequence: int
    category: str

    def __post_init__(self) -> None:
        if not isinstance(self.date, str) or not re.fullmatch(r"\d{8}", self.date):
            raise ValueError(f"日期格式不合法：{self.date!r}")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError(f"序號必須是非負整數：{self.sequence!r}")
        if not isinstance(self.category, str) or len(self.category) != 1 or not re.fullmatch(r"[A-Z]+", self.category):
            raise ValueError(f"類別必須是單一字元：{self.category!r}")

    @classmethod
    def from_string(cls, jid: str) -> "JudgmentId":
        if not isinstance(jid, str):
            raise ValueError(f"無法解析 JID：{jid!r}")
        parts = jid.split(",")
        if len(parts) < 6 or not all(parts):
            raise ValueError(f"不完整的 JID：{jid!r}")
        elif len(parts) > 6:
            raise ValueError(f"JID 含有額外欄位：{jid!r}")
        if len(court := parts[0]) != 4 or not re.fullmatch(r"[A-Z]+", court):
            raise ValueError(f"法院代碼不合法：{court!r}")
        court, year, case, number, date, sequence = parts
        category = court[-1]
        try:
            if not all(re.fullmatch(r"[0-9]+", value) for value in (year, number)):
                raise ValueError("年度、號數必須是非負整數")
            case_number = CaseNumber(court, int(year), case, int(number))
            return cls(case_number, date, int(sequence), category)
        except ValueError as exc:
            raise ValueError(f"無法解析 JID：{jid!r}：{exc}") from exc

    @property
    def file_name(self) -> str:
        return self.case_number.file_name

    def to_string(self) -> str:
        parts = [
            self.case_number.court + self.category, 
            str(self.case_number.year), 
            self.case_number.case, 
            str(self.case_number.number),
            self.date,
            str(self.sequence),
        ]
        return ",".join(parts)


def normalize_court(value: str) -> str:
    """法院名稱、簡稱及 JID 法院代碼統一為三碼搜尋代碼。"""
    if not isinstance(value, str):
        raise ValueError(f"法院必須是字串：{value!r}")
    normalized = _normalize_court_name(value)
    if normalized in COURT_MAP:
        return normalized
    if len(normalized) == 4 and normalized[-1] in "VMAPC" and normalized[:3] in COURT_MAP:
        return normalized[:3]
    for code, name in COURT_MAP.items():
        if not code:
            continue
        aliases = {name, name.replace("地方法院", "地院").replace("高等法院", "高院")}
        if name.startswith(("臺灣", "福建")) and name.endswith("地方法院"):
            aliases.update({name[2:], name[2:].replace("地方法院", "地院")})
        if normalized in {_normalize_court_name(alias) for alias in aliases}:
            return code
    raise ValueError(f"法院不在 COURT_MAP 中：{value}")


def _normalize_court_name(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("台", "臺")
