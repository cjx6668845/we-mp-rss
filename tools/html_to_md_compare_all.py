#!/usr/bin/env python3
"""Run 4 HTML->Markdown engines and output a unified comparison report."""

import argparse
import json
import subprocess
from pathlib import Path
from statistics import mean
from typing import Dict, List

DEFAULT_DB_PATH = "/Users/chenjunxi/DEV/docker/we-mp-rss-data/db.db"
DEFAULT_OUT_ROOT = "./data/md_compare"
DEFAULT_CONDA_ENV = "caupd-env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite db path")
    parser.add_argument("--article-id", default="", help="specific article id")
    parser.add_argument("--limit", type=int, default=10, help="latest N articles when article-id is empty")
    parser.add_argument(
        "--prefer-content",
        choices=["html", "raw"],
        default="html",
        help="html=content_html first, raw=content first",
    )
    parser.add_argument("--out-root", default=DEFAULT_OUT_ROOT, help="root output directory")
    parser.add_argument("--conda-env", default=DEFAULT_CONDA_ENV, help="conda env used by python scripts")
    parser.add_argument(
        "--d2m-cmd",
        default="npx -y d2m@1.5.0",
        help="CLI command for dom-to-semantic-markdown script",
    )
    return parser.parse_args()


def run_command(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")


def load_summary(path: Path) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(f"summary not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def avg(data: List[Dict], key: str) -> float:
    if not data:
        return 0.0
    return float(mean(item.get(key, 0) for item in data))


def build_compare_rows(all_summaries: Dict[str, List[Dict]]) -> List[Dict]:
    rows = []
    for engine, data in all_summaries.items():
        rows.append(
            {
                "engine": engine,
                "count": len(data),
                "avg_elapsed_ms": round(avg(data, "elapsed_ms"), 2),
                "avg_output_chars": round(avg(data, "output_chars"), 1),
                "avg_line_count": round(avg(data, "line_count"), 1),
                "avg_heading_count": round(avg(data, "heading_count"), 2),
                "avg_link_count": round(avg(data, "link_count"), 2),
                "avg_image_count": round(avg(data, "image_count"), 2),
            }
        )
    return sorted(rows, key=lambda x: x["engine"])


def to_markdown_table(rows: List[Dict]) -> str:
    header = (
        "| engine | count | avg_elapsed_ms | avg_output_chars | avg_line_count | "
        "avg_heading_count | avg_link_count | avg_image_count |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    )
    body = "\n".join(
        [
            "| {engine} | {count} | {avg_elapsed_ms} | {avg_output_chars} | {avg_line_count} | "
            "{avg_heading_count} | {avg_link_count} | {avg_image_count} |".format(**row)
            for row in rows
        ]
    )
    return f"{header}\n{body}\n"


def main() -> int:
    args = parse_args()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    base_flags = [
        "--db",
        args.db,
        "--prefer-content",
        args.prefer_content,
    ]
    if args.article_id.strip():
        base_flags.extend(["--article-id", args.article_id.strip()])
    else:
        base_flags.extend(["--limit", str(args.limit)])

    scripts = [
        ("markitdown", "tools/html_to_md_markitdown.py", []),
        ("trafilatura", "tools/html_to_md_trafilatura.py", []),
        ("crawl4ai", "tools/html_to_md_crawl4ai.py", []),
        (
            "dom_to_semantic_markdown",
            "tools/html_to_md_dom_semantic_markdown.py",
            ["--d2m-cmd", args.d2m_cmd],
        ),
    ]

    for engine, script_path, extra_flags in scripts:
        out_dir = out_root / engine
        cmd = [
            "conda",
            "run",
            "-n",
            args.conda_env,
            "python",
            script_path,
            *base_flags,
            "--out-dir",
            str(out_dir),
            *extra_flags,
        ]
        print(f"Running [{engine}] ...")
        run_command(cmd)

    all_summaries = {
        engine: load_summary(out_root / engine / "summary.json")
        for engine, _, _ in scripts
    }
    rows = build_compare_rows(all_summaries)

    compare_json = out_root / "compare_summary.json"
    compare_md = out_root / "compare_summary.md"
    compare_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    compare_md.write_text(to_markdown_table(rows), encoding="utf-8")

    print("\nComparison complete.")
    print(to_markdown_table(rows))
    print(f"Saved: {compare_json}")
    print(f"Saved: {compare_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
