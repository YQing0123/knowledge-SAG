"""Resolve local Markdown images and enrich their source document with MinerU text."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sag_api.core.config import Settings
from sag_api.core.errors import ConfigurationError
from sag_api.db.models.folder_sync import FolderSyncConfig
from sag_api.parsing.mineru_official import OfficialMinerUClient

_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))", re.IGNORECASE)
_HTML_IMAGE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1", re.IGNORECASE)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp"}
_ENRICHMENT_SIGNATURE_VERSION = "3"


@dataclass(frozen=True)
class ImageReference:
    source: str
    line: int
    path: Path | None
    relative_path: str
    content_hash: str
    error: str | None = None


@dataclass(frozen=True)
class MarkdownEnrichment:
    content: bytes
    fingerprint: str
    status: str
    image_count: int
    enriched_count: int
    errors: list[str]


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _extract_source(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith("#") or value.startswith("data:"):
        return ""
    return unquote(parsed.path)


def collect_image_references(text: str, markdown_path: Path, root: Path) -> list[ImageReference]:
    """Collect local raster references while ignoring fenced code blocks and unsafe paths."""
    references: list[ImageReference] = []
    seen: set[str] = set()
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        values = [match.group(1) or match.group(2) for match in _MARKDOWN_IMAGE.finditer(line)]
        values.extend(match.group(2) for match in _HTML_IMAGE.finditer(line))
        for value in values:
            source = value.strip()
            relative = _extract_source(source).replace("\\", "/")
            if not relative or relative in seen:
                continue
            seen.add(relative)
            # Markdown commonly uses paths relative to the document, but some
            # exporters retain paths rooted at the configured sync mount. Try
            # the document form first, then the controlled sync root.
            candidates = (markdown_path.parent / Path(relative), root / Path(relative))
            unresolved = next((item for item in candidates if item.exists()), candidates[0])
            candidate = unresolved.resolve()
            display_path = Path(relative).as_posix()
            if not _inside(candidate, root):
                references.append(ImageReference(source, line_number, None, display_path, "", "图片路径超出同步目录"))
                continue
            if candidate.suffix.lower() not in _IMAGE_SUFFIXES:
                references.append(ImageReference(source, line_number, None, display_path, "", "不支持的图片格式"))
                continue
            try:
                if unresolved.is_symlink() or not candidate.is_file():
                    raise OSError("图片文件不存在或是符号链接")
                digest = _hash_file(candidate)
                references.append(ImageReference(source, line_number, candidate, display_path, digest))
            except OSError as exc:
                references.append(ImageReference(source, line_number, None, display_path, "", str(exc)))
    return references


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint_for(
    text: str, markdown_path: Path, root: Path, raw_hash: str, config: FolderSyncConfig, app_settings: Settings
) -> tuple[str, list[ImageReference]]:
    refs = collect_image_references(text, markdown_path, root)
    signature = [
        _ENRICHMENT_SIGNATURE_VERSION,
        raw_hash,
        str(config.image_enrichment_enabled),
        config.image_enrichment_mode,
        str(config.image_max_count),
        str(config.image_max_mb),
        app_settings.mineru_official_model,
    ]
    signature.extend(
        f"{ref.relative_path}:{ref.content_hash}:{ref.error or ''}" for ref in refs[: config.image_max_count]
    )
    return hashlib.sha256("\n".join(signature).encode()).hexdigest(), refs


def _fenced_text(value: str) -> str:
    longest = max((len(item) for item in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{value.rstrip()}\n{fence}"


def _has_textual_content(value: str) -> bool:
    """Reject MinerU results that only point at an extracted image asset."""
    without_images = _MARKDOWN_IMAGE.sub("", value)
    without_images = _HTML_IMAGE.sub("", without_images)
    return bool(re.search(r"[\w\u4e00-\u9fff]", without_images))


def _supplement(ref: ImageReference, text: str, source_path: str, mode: str, index: int) -> str:
    label = "视觉解析" if mode == "vlm" else "OCR"
    return "\n".join(
        [
            f"### 图片 {index}：`{ref.relative_path}`",
            "",
            "> [!image]- 图片解析补充（MinerU）",
            ">",
            f"> - 解析方式：{label}",
            f"> - 来源文件：`{source_path}`",
            f"> - Markdown 行号：{ref.line}",
            f"> - 图片路径：`{ref.relative_path}`",
            f"> - 图片 SHA-256：`{ref.content_hash}`",
            ">",
            "> 识别文本：",
            ">",
            _fenced_text(text),
            "",
        ]
    )


async def enrich_markdown(
    raw: bytes,
    markdown_path: Path,
    root: Path,
    source_path: str,
    source_id: str,
    config: FolderSyncConfig,
    app_settings: Settings,
) -> MarkdownEnrichment:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return MarkdownEnrichment(raw, hashlib.sha256(raw).hexdigest(), "not_requested", 0, 0, [])
    fingerprint, refs = fingerprint_for(
        text, markdown_path, root, hashlib.sha256(raw).hexdigest(), config, app_settings
    )
    if not config.image_enrichment_enabled or not refs:
        return MarkdownEnrichment(raw, fingerprint, "not_requested", len(refs), 0, [])
    refs = refs[: config.image_max_count]
    cache_root = Path(app_settings.upload_dir) / "folder-sync-images" / source_id
    cache_root.mkdir(parents=True, exist_ok=True)
    client: OfficialMinerUClient | None = None
    supplements: list[str] = []
    errors: list[str] = []
    for index, ref in enumerate(refs, 1):
        if ref.error or ref.path is None:
            errors.append(f"{ref.relative_path}：{ref.error or '无法读取图片'}")
            continue
        if ref.path.stat().st_size > config.image_max_mb * 1024 * 1024:
            errors.append(f"{ref.relative_path}：超过 {config.image_max_mb} MB 限制")
            continue
        cache_key = hashlib.sha256(
            f"{ref.content_hash}:{config.image_enrichment_mode}:{app_settings.mineru_official_model}".encode()
        ).hexdigest()
        cache_text = cache_root / f"{cache_key}.md"
        cache_image = cache_root / f"{cache_key}{ref.path.suffix.lower()}"
        try:
            if not cache_image.exists():
                shutil.copy2(ref.path, cache_image)
            if cache_text.exists():
                parsed = cache_text.read_text(encoding="utf-8")
            else:
                if client is None:
                    if app_settings.mineru_provider != "official" or not app_settings.mineru_configured:
                        raise ConfigurationError("图片解析需要配置 MinerU 官方服务")
                    client_settings = app_settings.model_copy(
                        update={"mineru_parse_method": "ocr" if config.image_enrichment_mode == "ocr" else "auto"}
                    )
                    client = OfficialMinerUClient(client_settings)
                parsed = await client.parse(str(cache_image))
                if not _has_textual_content(parsed):
                    raise ValueError("文档解析结果未包含可合并的文本")
                cache_text.write_text(parsed, encoding="utf-8")
            if not _has_textual_content(parsed):
                raise ValueError("文档解析结果未包含可合并的文本")
            supplements.append(_supplement(ref, parsed, source_path, config.image_enrichment_mode, index))
        except Exception as exc:  # noqa: BLE001 - one bad image must not discard the source document
            errors.append(f"{ref.relative_path}：{str(exc)[:300]}")
    if not supplements and errors:
        return MarkdownEnrichment(raw, fingerprint, "failed", len(refs), 0, errors)
    extra = "\n\n## 图片解析补充（MinerU）\n\n" + "\n".join(supplements)
    if errors:
        extra += "\n> [!warning] 部分图片未完成解析\n>\n" + "\n".join(f"> - {error}" for error in errors) + "\n"
    return MarkdownEnrichment(
        (text.rstrip() + extra + "\n").encode("utf-8"),
        fingerprint,
        "failed" if errors else "ready",
        len(refs),
        len(supplements),
        errors,
    )
