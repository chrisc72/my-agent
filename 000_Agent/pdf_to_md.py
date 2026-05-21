"""
PDF 轉 Markdown 腳本
用法：
  python pdf_to_md.py 單一檔案.pdf
  python pdf_to_md.py 單一檔案.pdf -o 輸出資料夾
  python pdf_to_md.py PDF資料夾/  -o 輸出資料夾
"""

import sys
import pathlib
import argparse
import pymupdf4llm


def convert(pdf_path: pathlib.Path, output_dir: pathlib.Path) -> None:
    images_dir = output_dir / f"{pdf_path.stem}_images"
    images_dir.mkdir(parents=True, exist_ok=True)

    md_text = pymupdf4llm.to_markdown(
        str(pdf_path),
        write_images=True,
        image_path=str(images_dir),
    )

    out_file = output_dir / f"{pdf_path.stem}.md"
    out_file.write_text(md_text, encoding="utf-8")
    print(f"✅ {pdf_path.name}  →  {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF 轉 Markdown")
    parser.add_argument("input", help="PDF 檔案路徑，或包含多個 PDF 的資料夾")
    parser.add_argument("-o", "--output", default="output", help="輸出資料夾（預設：output）")
    args = parser.parse_args()

    input_path = pathlib.Path(args.input)
    output_dir = pathlib.Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        convert(input_path, output_dir)

    elif input_path.is_dir():
        pdfs = sorted(input_path.glob("*.pdf"))
        if not pdfs:
            print(f"❌ 在 {input_path} 裡找不到 PDF 檔案")
            sys.exit(1)
        print(f"找到 {len(pdfs)} 個 PDF，開始轉換…")
        for pdf in pdfs:
            convert(pdf, output_dir)
        print(f"\n完成！輸出在 {output_dir.resolve()}")

    else:
        print(f"❌ 找不到檔案：{input_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
