import re
import build_dataset

MAIN_PATTERN = re.compile(r"主\s*文")
FACT_PATTERN = re.compile(r"犯罪事實及理由|犯罪事實|事實及理由|理由|事\s*實")
ATTACHMENT_PATTERN = re.compile(r"附件：")

def split_text(text: str, start_pattern: re.Pattern[str], end_pattern: re.Pattern[str] = None) -> str:
    """從裁判書全文中擷取內容。"""

    start_match = start_pattern.search(text)
    if not start_match:
        return "", ""

    context = text[start_match.end():]
    if end_pattern == None:
        left = context
        right = None
    else:
        end_match = end_pattern.search(context)
        if end_match:
            left = context[:end_match.start()]
            right = context[end_match.end():]

    # 整理前後空白
    return left.strip(), right.strip()

if __name__ == "__main__":
    text = build_dataset.extract_judgment(build_dataset.RAW_DIR / "TPDM,115,交簡,760,20260827,1.json")["text"]
    print(split_text(text, MAIN_PATTERN, FACT_PATTERN))