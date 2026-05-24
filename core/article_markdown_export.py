from __future__ import annotations

import html as html_lib
import mimetypes
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.common.file_tools import sanitize_filename
from core.config import cfg
from core.print import print_info, print_warning

_MARKITDOWN_IMPORT_ERROR: str | None = None
try:
    from markitdown import MarkItDown
except Exception as exc:  # pragma: no cover - 运行时环境可能未安装该依赖
    MarkItDown = None
    _MARKITDOWN_IMPORT_ERROR = str(exc)

_MARKITDOWN_INSTANCE = None

_BG_URL_PATTERN = re.compile(r"url\(\s*['\"]?([^'\"\)]+)['\"]?\s*\)", flags=re.IGNORECASE)


def _safe_component(value: Any, fallback: str) -> str:
    text = sanitize_filename(str(value or "").strip())
    return text[:120] if text else fallback


def _format_publish_date(publish_time: Any) -> str:
    if isinstance(publish_time, datetime):
        return publish_time.strftime("%Y-%m-%d")

    if isinstance(publish_time, str):
        raw = publish_time.strip()
        if raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            if raw.isdigit():
                publish_time = int(raw)

    if isinstance(publish_time, (int, float)):
        ts = int(publish_time)
        if ts > 10**12:  # 毫秒时间戳
            ts = ts // 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            pass

    return datetime.now().strftime("%Y-%m-%d")


def _normalize_image_url(raw_url: str, base_url: str = "") -> str:
    if not raw_url:
        return ""

    url = html_lib.unescape(raw_url.strip())
    if not url:
        return ""
    if url.startswith("data:"):
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith(("http://", "https://")):
        return url

    if base_url:
        return urljoin(base_url, url)
    return ""


def _collect_image_urls(content_html: str, article_url: str = "") -> List[str]:
    if not content_html:
        return []

    soup = BeautifulSoup(content_html, "html.parser")
    seen = set()
    ordered_urls: List[str] = []

    def add_url(url: str):
        if not url or url in seen:
            return
        seen.add(url)
        ordered_urls.append(url)

    for img in soup.find_all("img"):
        normalized = _normalize_image_url(img.get("src") or img.get("data-src") or "", article_url)
        add_url(normalized)

    for element in soup.find_all(style=True):
        style = element.get("style", "") or ""
        for match in _BG_URL_PATTERN.findall(style):
            normalized = _normalize_image_url(match, article_url)
            add_url(normalized)

    return ordered_urls


def _guess_image_ext(url: str, content_type: str = "") -> str:
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext and 1 < len(ext) <= 8:
        return ext

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip().lower())
        if guessed:
            return guessed

    return ".jpg"


def _build_http_session(article_url: str = "") -> requests.Session:
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if article_url:
        headers["Referer"] = article_url
    session.headers.update(headers)

    if cfg.get("proxy.enabled", False):
        proxy_url = cfg.get("proxy.http_url", "") or ""
        if proxy_url:
            session.proxies.update({"http": proxy_url, "https": proxy_url})

    return session


def _download_images(content_html: str, images_dir: Path, article_url: str = "") -> Dict[str, str]:
    image_urls = _collect_image_urls(content_html, article_url)
    if not image_urls:
        return {}

    images_dir.mkdir(parents=True, exist_ok=True)
    session = _build_http_session(article_url)
    url_to_local: Dict[str, str] = {}

    for index, image_url in enumerate(image_urls, start=1):
        try:
            response = session.get(image_url, timeout=(10, 30))
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            ext = _guess_image_ext(image_url, content_type)
            file_name = f"img_{index:03d}{ext}"
            image_path = images_dir / file_name
            image_path.write_bytes(response.content)

            rel_path = f"{images_dir.name}/{file_name}"
            url_to_local[image_url] = rel_path
        except Exception as exc:
            print_warning(f"下载图片失败: {image_url} -> {exc}")

    return url_to_local


def _get_markitdown():
    global _MARKITDOWN_INSTANCE
    if _MARKITDOWN_INSTANCE is not None:
        return _MARKITDOWN_INSTANCE

    if MarkItDown is None:
        msg = _MARKITDOWN_IMPORT_ERROR or "markitdown is not installed"
        raise RuntimeError(msg)

    _MARKITDOWN_INSTANCE = MarkItDown()
    return _MARKITDOWN_INSTANCE


def _to_markdown(content_html: str) -> str:
    converter = _get_markitdown()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", encoding="utf-8", delete=False) as tf:
        tf.write(content_html)
        temp_html_path = tf.name

    try:
        result = converter.convert(temp_html_path)
        markdown = getattr(result, "markdown", "") or getattr(result, "text_content", "")
        return markdown or ""
    finally:
        try:
            os.remove(temp_html_path)
        except OSError:
            pass


def _replace_markdown_image_links(markdown: str, url_to_local: Dict[str, str]) -> str:
    if not markdown or not url_to_local:
        return markdown

    output = markdown
    for remote_url, local_path in url_to_local.items():
        variants = {
            remote_url,
            unquote(remote_url),
            remote_url.replace("&", "&amp;"),
            html_lib.escape(remote_url, quote=True),
        }
        for variant in variants:
            if variant:
                output = output.replace(f"({variant})", f"({local_path})")
                output = output.replace(variant, local_path)
    return output


def export_article_markdown(
    *,
    content_html: str,
    mp_name: str,
    title: str,
    publish_time: Any = None,
    article_url: str = "",
) -> str | None:
    """
    将文章 HTML 转为 Markdown 并保存到本地，同时下载文章图片。
    """
    if not content_html or content_html == "DELETED":
        return None

    root_dir = cfg.get("export.markdown.dir", "./data/markdown") or "./data/markdown"
    publish_date = _format_publish_date(publish_time)
    safe_mp_name = _safe_component(mp_name, "未知公众号")
    safe_title = _safe_component(title, "未命名文章")
    file_stem = f"{safe_mp_name}_{publish_date}_{safe_title}"

    target_dir = Path(root_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = target_dir / f"{file_stem}.md"
    images_dir = target_dir / f"{file_stem}_images"

    try:
        markdown = _to_markdown(content_html)
    except Exception as exc:
        print_warning(f"MarkItDown 转换失败: {exc}")
        return None

    image_map = _download_images(content_html, images_dir, article_url)
    markdown = _replace_markdown_image_links(markdown, image_map)
    clean_title = (title or "").strip() or "未命名文章"
    clean_url = (article_url or "").strip()
    if clean_url:
        markdown = f"# {clean_title}\n\n原文链接：{clean_url}\n\n{markdown.strip()}\n"
    else:
        markdown = f"# {clean_title}\n\n{markdown.strip()}\n"
    markdown_path.write_text(markdown, encoding="utf-8")

    if image_map:
        print_info(f"文章Markdown与图片已导出: {markdown_path} (图片{len(image_map)}张)")
    else:
        if images_dir.exists():
            try:
                images_dir.rmdir()
            except OSError:
                pass
        print_info(f"文章Markdown已导出: {markdown_path}")

    return str(markdown_path)
