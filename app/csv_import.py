import csv
import io
from datetime import date, datetime
from typing import Optional


DATE_COLUMN_CANDIDATES = (
    "発生日",
    "日付",
    "対象日",
    "取引日",
    "利用日",
    "決済日",
    "売上日",
    "処理日",
    "date",
    "Date",
)

CARD_TRANSACTION_COLUMN_CANDIDATES = ("取引内容", "処理内容", "明細種別", "transaction")
CANCEL_WORDS = ("取消", "キャンセル", "返品", "返金")


def normalize_sales_date(raw: str) -> Optional[str]:
    v = (raw or "").strip()
    if not v:
        return None
    for sep in ("T", " "):
        if sep in v:
            v = v.split(sep, 1)[0]
    v = (
        v.replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
        .replace(".", "/")
        .replace("-", "/")
    )
    for fmt in ("%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        parts = [p for p in v.split("/") if p]
        if len(parts) == 3:
            y = int(parts[0])
            m = int(parts[1])
            d = int(parts[2])
            return date(y, m, d).isoformat()
    except ValueError:
        return None
    return None


def _decode_csv_text(uploaded_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-8"):
        try:
            return uploaded_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return uploaded_bytes.decode("utf-8", errors="ignore")


def _header_map(fieldnames: list[str]) -> dict[str, str]:
    return {(name or "").strip(): name for name in fieldnames}


def _pick_column(fieldnames: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    headers = _header_map(fieldnames)
    for candidate in candidates:
        if candidate in headers:
            return headers[candidate]
    return fieldnames[0] if fieldnames else None


def _is_effective_sales_row(row: dict[str, str], fieldnames: list[str]) -> bool:
    transaction_col = _pick_column(fieldnames, CARD_TRANSACTION_COLUMN_CANDIDATES)
    if not transaction_col:
        return True
    transaction = (row.get(transaction_col) or "").strip()
    if not transaction:
        return True
    return not any(word in transaction for word in CANCEL_WORDS)


def extract_existing_sales_dates(uploaded_bytes: bytes) -> set[str]:
    if not uploaded_bytes:
        return set()

    text = _decode_csv_text(uploaded_bytes)
    if not text.strip():
        return set()

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return set()

    fieldnames = [name for name in reader.fieldnames if name is not None]
    date_col = _pick_column(fieldnames, DATE_COLUMN_CANDIDATES)
    if not date_col:
        return set()

    existing: set[str] = set()
    for row in reader:
        if not _is_effective_sales_row(row, fieldnames):
            continue
        raw = (row.get(date_col) or "").strip()
        norm = normalize_sales_date(raw)
        if norm:
            existing.add(norm)
    return existing
