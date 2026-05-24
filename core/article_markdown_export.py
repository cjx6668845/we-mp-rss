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
from bs4.element import Comment, Tag

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
_MAIN_CONTENT_SELECTORS = (
    "#js_content",
    "#js_article #js_content",
    "div.rich_media_content",
    "article",
    "main",
    "body",
)
_DROP_TAGS = {
    "script",
    "style",
    "noscript",
    "iframe",
    "form",
    "button",
    "input",
    "select",
    "option",
    "canvas",
    "svg",
    "path",
    "video",
    "audio",
    "link",
    "meta",
}
_NOISE_ID_CLASS_KEYWORDS = (
    "js_pc_qr_code",
    "qr_code",
    "js_profile_card",
    "profile_card",
    "js_novel_card",
    "js_related_articles",
    "js_minipro_dialog",
    "js_toobar",
    "js_read_area",
    "reward",
    "recommend",
    "js_share",
    "share_media",
    "copyright",
    "js_preview_reward",
    "discuss",
    "comment",
)
_NOISE_TEXT_PATTERNS = (
    re.compile(r"^点击关注\s*>?$"),
    re.compile(r"^去阅读$"),
    re.compile(r"^原创$"),
    re.compile(r"^在小说阅读器.*"),
    re.compile(r"^微信扫一扫.*"),
    re.compile(r"^预览时标签不可点.*"),
    re.compile(r"^知道了$"),
    re.compile(r"^取消$"),
    re.compile(r"^允许$"),
    re.compile(r"^继续滑动看下一个$"),
    re.compile(r"^轻触阅读原文$"),
    re.compile(r"^向上滑动看下一个$"),
    re.compile(r"^阅读原文$"),
    re.compile(r"^分析$"),
    re.compile(r"^×$"),
    re.compile(r"^使用完整服务$"),
)
_TRAILING_CUTOFF_PATTERNS = (
    re.compile(r"^欢迎在朋友圈转发.*"),
    re.compile(r"^微信改版后.*"),
    re.compile(r"^官方投稿网址[:：].*"),
)
_CJK_CHAR_PATTERN = r"\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"


def _is_noise_text(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return True
    for pattern in _NOISE_TEXT_PATTERNS:
        if pattern.match(value):
            return True
    return False


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    value = html_lib.unescape(text).replace("\u00a0", " ")
    value = re.sub(r"[\u200b\u200c\u200d\u2060]", "", value)
    value = re.sub(r"[\s\u3000]+", " ", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(fr"([{_CJK_CHAR_PATTERN}])\s+([{_CJK_CHAR_PATTERN}])", r"\1\2", value)
    value = re.sub(fr"([{_CJK_CHAR_PATTERN}])\s+([，。！？；：、）】》”’])", r"\1\2", value)
    value = re.sub(fr"([，。！？；：、])\s+([{_CJK_CHAR_PATTERN}])", r"\1\2", value)
    value = re.sub(fr"([（【《“‘])\s+([{_CJK_CHAR_PATTERN}])", r"\1\2", value)
    value = re.sub(r"([“‘])\s+", r"\1", value)
    value = re.sub(r"\s+([”’])", r"\1", value)
    value = re.sub(fr"([”’])\s+([{_CJK_CHAR_PATTERN}])", r"\1\2", value)
    value = re.sub(r"\s+([,.;:!?%])", r"\1", value)
    value = re.sub(r"\(\s+", "(", value)
    value = re.sub(r"\s+\)", ")", value)
    return value.strip()


def _has_hidden_style(style_text: str) -> bool:
    style = (style_text or "").replace(" ", "").lower()
    return ("display:none" in style) or ("visibility:hidden" in style)


def _is_noise_node(tag: Tag) -> bool:
    attrs = " ".join(
        [
            tag.get("id", "") or "",
            " ".join(tag.get("class", []) or []),
            tag.get("role", "") or "",
            tag.get("data-role", "") or "",
        ]
    ).lower()
    return any(keyword in attrs for keyword in _NOISE_ID_CLASS_KEYWORDS)


def _pick_main_content_node(soup: BeautifulSoup) -> Tag | None:
    best_node = None
    best_score = 0
    for selector in _MAIN_CONTENT_SELECTORS:
        for node in soup.select(selector):
            if not isinstance(node, Tag):
                continue
            text_len = len((node.get_text(" ", strip=True) or "").strip())
            if text_len > best_score:
                best_score = text_len
                best_node = node
    return best_node


def _cleanup_content_node(content_node: Tag) -> None:
    for comment in content_node.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    for tag_name in _DROP_TAGS:
        for node in content_node.find_all(tag_name):
            node.decompose()

    for node in content_node.find_all(True):
        if not isinstance(node, Tag):
            continue
        if _is_noise_node(node):
            node.decompose()
            continue
        if node.get("aria-hidden") == "true" or _has_hidden_style(node.get("style", "")):
            node.decompose()


def _build_semantic_html(content_node: Tag, article_url: str = "") -> str:
    semantic = BeautifulSoup("<div id='js_content'></div>", "html.parser")
    root = semantic.div

    block_tags = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "pre", "figcaption", "img")
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}

    for element in content_node.find_all(block_tags):
        if not isinstance(element, Tag):
            continue

        if element.name == "img":
            source = element.get("src") or element.get("data-src") or ""
            source = _normalize_image_url(source, article_url)
            if not source:
                continue
            wrapper = semantic.new_tag("p")
            image = semantic.new_tag("img", src=source)
            wrapper.append(image)
            root.append(wrapper)
            continue

        text_value = _normalize_text(element.get_text(" ", strip=True))
        if not text_value or _is_noise_text(text_value):
            continue

        tag_name = element.name if element.name in heading_tags else "p"
        tag = semantic.new_tag(tag_name)
        tag.string = text_value
        root.append(tag)

    if root and root.find():
        return str(root)

    fallback_text = _normalize_text(content_node.get_text("\n", strip=True))
    if fallback_text:
        fallback = BeautifulSoup("<div id='js_content'></div>", "html.parser")
        paragraph = fallback.new_tag("p")
        paragraph.string = fallback_text
        fallback.div.append(paragraph)
        return str(fallback.div)

    return ""


def _extract_article_html(content_html: str, article_url: str = "") -> str:
    if not content_html:
        return ""

    try:
        soup = BeautifulSoup(content_html, "html.parser")
    except Exception:
        return content_html

    content_node = _pick_main_content_node(soup)
    if not content_node:
        return content_html

    cloned = BeautifulSoup(str(content_node), "html.parser")
    wrapper = cloned.find(True)
    if not isinstance(wrapper, Tag):
        return content_html

    _cleanup_content_node(wrapper)
    semantic_html = _build_semantic_html(wrapper, article_url)
    return semantic_html or str(wrapper)


def _is_markdown_control_line(line: str) -> bool:
    value = (line or "").strip()
    if not value:
        return False
    if value in {"---", "***", "___"}:
        return True
    if value.startswith(("```", "#", "![", "> ", "| ")):
        return True
    if re.match(r"^[-*+]\s+", value):
        return True
    if re.match(r"^\d+\.\s+", value):
        return True
    return False


def _postprocess_markdown(markdown: str, title: str) -> str:
    if not markdown:
        return ""

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned: List[str] = []
    dropped_title = False
    clean_title = _normalize_text(title)

    for raw in lines:
        line = _normalize_text(raw)
        if not line:
            if not cleaned or cleaned[-1] == "":
                continue
            cleaned.append("")
            continue
        if any(pattern.match(line) for pattern in _TRAILING_CUTOFF_PATTERNS):
            break
        if _is_noise_text(line):
            continue
        if clean_title and not dropped_title and line.lstrip("# ").strip() == clean_title:
            dropped_title = True
            continue
        cleaned.append(line)

    merged: List[str] = []
    buffer = ""

    for line in cleaned:
        if not line:
            if buffer:
                merged.append(_normalize_text(buffer))
                buffer = ""
            if not merged or merged[-1] == "":
                continue
            merged.append("")
            continue

        if _is_markdown_control_line(line):
            if buffer:
                merged.append(_normalize_text(buffer))
                buffer = ""
            merged.append(line)
            continue

        buffer = _normalize_text(f"{buffer} {line}") if buffer else line

    if buffer:
        merged.append(_normalize_text(buffer))

    while merged and merged[-1] == "":
        merged.pop()

    return "\n".join(merged).strip()


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
    normalized_html = _extract_article_html(content_html, article_url) or content_html
    clean_title = (title or "").strip() or "未命名文章"

    try:
        markdown = _to_markdown(normalized_html)
    except Exception as exc:
        print_warning(f"MarkItDown 转换失败: {exc}")
        return None

    image_map = _download_images(normalized_html, images_dir, article_url)
    markdown = _postprocess_markdown(markdown, clean_title)
    markdown = _replace_markdown_image_links(markdown, image_map)
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
