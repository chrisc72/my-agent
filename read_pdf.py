import fitz
import sys
import io
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DEFAULT_OUTPUT_DIR = Path(r"D:\OneDrive\00 Claude Code\相關資訊_非系統存取用\PDF轉成MD")


def read_pdf(pdf_path: str, output_path: str | None = None) -> None:
    path = Path(pdf_path)
    if not path.exists():
        print(f"找不到檔案：{pdf_path}")
        sys.exit(1)

    doc = fitz.open(pdf_path)
    lines: list[str] = []

    lines.append(f"檔案：{path.name}｜共 {doc.page_count} 頁\n")
    lines.append("=" * 60)

    for page in doc:
        lines.append(f"\n【第 {page.number + 1} 頁】")
        lines.append("-" * 40)
        text = page.get_text().strip()
        lines.append(text if text else "（本頁無文字內容）")

    doc.close()

    content = "\n".join(lines)

    if output_path is None:
        output_path = str(DEFAULT_OUTPUT_DIR / (path.stem + ".txt"))

    Path(output_path).write_text(content, encoding="utf-8")
    print(f"已儲存至：{output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="讀取 PDF 並輸出文字")
    parser.add_argument("pdf", help="PDF 檔案路徑")
    parser.add_argument("-o", "--output", help="輸出檔案路徑（預設存至 PDF轉成MD 資料夾）")
    args = parser.parse_args()

    read_pdf(args.pdf, args.output)
