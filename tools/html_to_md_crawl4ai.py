#!/usr/bin/env python3
"""Convert article HTML from SQLite to Markdown via crawl4ai."""

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List

from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

DEFAULT_DB_PATH = "/Users/chenjunxi/DEV/docker/we-mp-rss/data/db.db"
DEFAULT_OUT_DIR = "./data/md_compare/crawl4ai"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite db path")
    parser.add_argument("--article-id", default="", help="specific article id")
    parser.add_argument("--limit", type=int, default=3, help="latest N articles when article-id is empty")
    parser.add_argument(
        "--prefer-content",
        choices=["html", "raw"],
        default="html",
        help="html=content_html first, raw=content first",
    )
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="output directory")
    parser.add_argument("--no-citations", action="store_true", help="disable citations in markdown generation")
    return parser.parse_args()


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("_") or "untitled"


def fetch_articles(conn: sqlite3.Connection, article_id: str, limit: int, prefer_content: str) -> List[Dict]:
    content_expr = "coalesce(content_html, content, '')" if prefer_content == "html" else "coalesce(content, content_html, '')"
    if article_id:
        rows = conn.execute(
            f"""
            select id, title, mp_id, url, publish_time, {content_expr} as html
            from articles
            where id = ? and {content_expr} != ''
            """,
            (article_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            select id, title, mp_id, url, publish_time, {content_expr} as html
            from articles
            where {content_expr} != ''
            order by publish_time desc
            limit ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "id": row[0],
            "title": row[1] or "",
            "mp_id": row[2] or "",
            "url": row[3] or "",
            "publish_time": int(row[4] or 0),
            "html": row[5] or "",
        }
        for row in rows
    ]


def convert_html_to_markdown(generator: DefaultMarkdownGenerator, html: str, use_citations: bool) -> str:
    result = generator.generate_markdown(
        input_html=html,
        citations=use_citations,
        options={"ignore_links": False, "ignore_images": False},
    )
    return (result.raw_markdown or "").strip()


def build_metrics(markdown: str, html: str, elapsed_ms: float) -> Dict:
    lines = markdown.splitlines()
    return {
        "elapsed_ms": round(elapsed_ms, 2),
        "input_chars": len(html),
        "output_chars": len(markdown),
        "line_count": len(lines),
        "heading_count": sum(1 for line in lines if line.lstrip().startswith("#")),
        "image_count": markdown.count("!["),
        "link_count": markdown.count("]("),
    }


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        articles = fetch_articles(conn, args.article_id.strip(), args.limit, args.prefer_content)
    finally:
        conn.close()

    if not articles:
        print("No article with HTML content found.")
        return 1

    generator = DefaultMarkdownGenerator()
    use_citations = not args.no_citations
    summary = []

    for article in articles:
        start = time.perf_counter()
        markdown = convert_html_to_markdown(generator, article["html"], use_citations)
        elapsed_ms = (time.perf_counter() - start) * 1000
        metrics = build_metrics(markdown, article["html"], elapsed_ms)

        base_name = safe_name(f"{article['id']}_{article['title'][:40]}")
        md_file = out_dir / f"{base_name}.md"
        md_file.write_text(markdown, encoding="utf-8")

        item = {
            "engine": "crawl4ai",
            "article_id": article["id"],
            "title": article["title"],
            "mp_id": article["mp_id"],
            "url": article["url"],
            "publish_time": article["publish_time"],
            "markdown_file": str(md_file),
            "citations": use_citations,
            **metrics,
        }
        summary.append(item)
        print(json.dumps(item, ensure_ascii=False))

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
