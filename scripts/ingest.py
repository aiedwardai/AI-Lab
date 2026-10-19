#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 双轨摄取入口（第二大脑 · 文档 ingestion 管线）。

设计依据：`PDF摄取方案与安全审计.md`。
职责边界：只负责「把 PDF 变成规范 Markdown」，不做任何检索/嵌入/问答逻辑，
         以便与下游「知识归属」（Smart Connections 或其他 RAG）彻底解耦。

双轨策略：
- 文本轨道：PDF 自带可用文本层 -> PyMuPDF 直接抽取（快、高保真）。
- OCR 轨道：扫描件/无文本层    -> 本机原生 PaddleOCR（arm64，进程内调用，
                                  三个预处理模型全部关闭以规避 40GB+ 内存坑）。

用法示例：
    # 处理单个文件（自动判定轨道）
    python ingest.py --file 02_data/raw/xxx.pdf

    # 处理整个目录
    python ingest.py --dir 02_data/raw

    # 扫描件测试：只跑前 10 页，并额外产出可搜索 PDF
    python ingest.py --file 02_data/raw/xxx.pdf --max-pages 10 --searchable

    # 选择 OCR 质量档位（默认 balanced；清晰印刷体可用 fast 再省 2.7 倍时间）
    python ingest.py --file 02_data/raw/xxx.pdf --ocr-quality fast

注意：本脚本不修改任何原始 PDF；可搜索 PDF 以「另存为新文件」方式产出。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# OCR 相关环境变量必须在 import paddle/paddleocr 之前设置：
# KMP_DUPLICATE_LIB_OK 用于规避 macOS 上 OpenMP 运行时库重复加载导致的崩溃。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import pymupdf  # PyMuPDF 1.28+ 推荐 import 名（fitz 已弃用）  # noqa: E402

# ---- 全局常量（集中配置，便于审计与调参） ------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_RAW_DIR = PROJECT_ROOT / "02_data" / "raw"
DEFAULT_OUT_DIR = PROJECT_ROOT / "02_data" / "processed"
DEFAULT_SEARCHABLE_DIR = DEFAULT_OUT_DIR / "searchable"

# 文本层探测阈值：采样页中「平均字符数」与「有文字页占比」同时达标才算文本型。
DETECT_MIN_AVG_CHARS = 50.0
DETECT_MIN_TEXT_PAGE_RATIO = 0.5
DETECT_SAMPLE_PAGES = 30  # 最多采样页数（均匀分布），兼顾速度与判定准确性
DETECT_PAGE_MIN_CHARS = 30  # 单页被计为「有文字」的字符下限

# OCR 轨道默认（可由 CLI 覆盖）
# 质量档位预设 = 模型档位 + 渲染 DPI（实测于 M5 Max，详见设计文档 4.4 节）。
# 命名注意：PP-OCRv6 系列档位为 medium / small / tiny，**无 mobile**（mobile 属 v4/v5 系列）。
# 实测（侯捷扫描书）：fast≈1.35s/页·1.8GB；balanced≈3.6s/页·2.1GB；quality≈14s/页·8.9GB。
OCR_PRESETS: dict[str, tuple[str, str, int]] = {
    "fast": ("PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec", 150),
    "balanced": ("PP-OCRv6_small_det", "PP-OCRv6_small_rec", 150),
    "quality": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec", 200),
}
DEFAULT_OCR_QUALITY = "balanced"
CJK_FONT_NAME = "china-s"  # PyMuPDF 内置简体中文字体，用于可搜索 PDF 文字层


@dataclass
class IngestResult:
    """单个 PDF 的摄取结果摘要，便于调用方（launchd / Hermes）消费。"""

    source_path: Path
    track: str  # "text" | "ocr"
    pages: int
    processed_pages: int
    chars: int
    output_path: Path | None
    searchable_path: Path | None = None
    elapsed_sec: float = 0.0
    errors: list[str] = field(default_factory=list)


def log(msg: str) -> None:
    """统一日志输出（便于 launchd 的 StandardOutPath 接管）。"""
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """流式计算文件 SHA-256，用于 frontmatter 溯源与去重。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- 轨道判定 ---------------------------------------------------------------

def detect_has_text_layer(doc: pymupdf.Document) -> tuple[bool, dict[str, Any]]:
    """判定 PDF 是否存在可用文本层。

    采用「均匀采样」而非全量扫描，避免 400+ 页大书上判定阶段就变慢；
    采样量足够代表整本（扫描件几乎每页都无文字，文本型几乎每页都有）。

    Returns:
        (是否为文本型, 判定指标明细)
    """
    total = doc.page_count
    if total == 0:
        return False, {"pages": 0, "avg_chars": 0.0, "text_ratio": 0.0}

    step = max(1, total // DETECT_SAMPLE_PAGES)
    sample_indices = range(0, total, step)

    chars_total = 0
    pages_with_text = 0
    sampled = 0
    for i in sample_indices:
        text = (doc[i].get_text("text") or "").strip()
        chars_total += len(text)
        if len(text) >= DETECT_PAGE_MIN_CHARS:
            pages_with_text += 1
        sampled += 1

    avg_chars = chars_total / sampled if sampled else 0.0
    text_ratio = pages_with_text / sampled if sampled else 0.0
    is_text = avg_chars >= DETECT_MIN_AVG_CHARS and text_ratio >= DETECT_MIN_TEXT_PAGE_RATIO
    metrics = {
        "pages": total,
        "sampled": sampled,
        "avg_chars": round(avg_chars, 1),
        "text_ratio": round(text_ratio, 2),
    }
    return is_text, metrics


# ---- 轨道一：文本层直抽 -----------------------------------------------------

def extract_text_track(doc: pymupdf.Document, max_pages: int | None) -> str:
    """从自带文本层的 PDF 直接抽取文本。

    每页以 `## Page N` 作为分节标记：既保留页码溯源，又让下游分块时能按页切分。
    """
    limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
    parts: list[str] = []
    for i in range(limit):
        text = (doc[i].get_text("text") or "").strip()
        parts.append(f"## Page {i + 1}\n\n{text}\n")
    return "\n".join(parts)


# ---- 轨道二：原生 PaddleOCR（arm64） ----------------------------------------

def _build_ocr_engine(quality: str) -> tuple[Any, int]:
    """按质量档位构造 PaddleOCR 引擎。

    惰性 import 的目的：纯文本场景下，未安装 PaddleOCR 的机器也能跑本脚本。

    三个预处理模型（文档方向 / 弯曲矫正 / 文本行方向）在 macOS ARM 上默认开启会
    占用 40GB+ 内存并卡死系统，这里全部显式关闭 —— 这是 arm64 的必修坑。

    Returns:
        (引擎实例, 该档位对应的默认渲染 DPI)
    """
    if quality not in OCR_PRESETS:
        raise ValueError(f"未知质量档位 {quality}，可选: {sorted(OCR_PRESETS)}")
    det_model, rec_model, dpi = OCR_PRESETS[quality]

    from paddleocr import PaddleOCR  # 惰性导入，见函数文档说明

    engine = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
        text_detection_model_name=det_model,
        text_recognition_model_name=rec_model,
    )
    return engine, dpi


def _render_page_to_image(
    doc: pymupdf.Document, page_index: int, dpi: int, out_dir: Path
) -> tuple[Path, float]:
    """把指定页渲染为 PNG，返回 (图片路径, 像素->PDF点 的缩放系数)。"""
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi)
    img_path = out_dir / f"page_{page_index + 1:05d}.png"
    pix.save(img_path)
    # 渲染缩放系数：PDF 点 = 像素 / (dpi / 72)
    return img_path, dpi / 72.0


def _parse_ocr_result(result: Any) -> list[tuple[str, list[list[float]]]]:
    """解析 PaddleOCR 3.x 结果，返回 [(文本, 四点多边形), ...]。

    结果结构兼容两种访问方式（dict-like 的 Result 与 dict），解析失败时降级为
    仅取文本，保证 OCR 主流程不因结构差异中断。
    """
    items: list[tuple[str, list[list[float]]]] = []
    for res in result:
        try:
            texts = res.get("rec_texts", []) if hasattr(res, "get") else []
            polys = res.get("dt_polys", []) if hasattr(res, "get") else []
            for idx, text in enumerate(texts):
                poly: list[list[float]] = []
                if idx < len(polys):
                    poly = [[float(p[0]), float(p[1])] for p in polys[idx]]
                items.append((str(text), poly))
        except Exception:  # noqa: BLE001 - 结构差异不应中断主流程
            continue
    return items


def extract_ocr_track(
    doc: pymupdf.Document,
    max_pages: int | None,
    quality: str,
    dpi_override: int | None,
    want_searchable: bool,
    searchable_path: Path | None,
) -> tuple[str, int, int]:
    """OCR 轨道：逐页渲染 -> PaddleOCR 识别 -> 拼装文本（可选产出可搜索 PDF）。

    Returns:
        (拼装后的 Markdown 文本, 已处理页数, 实际使用的渲染 DPI)
    """
    engine, preset_dpi = _build_ocr_engine(quality)
    dpi = dpi_override or preset_dpi
    limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)

    parts: list[str] = []
    # 可搜索 PDF：在原始文档上叠加「不可见但可选中」的文字层，另存为新文件
    target_doc = doc if want_searchable else None
    started = time.time()

    with tempfile.TemporaryDirectory(prefix="pdf_ingest_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for i in range(limit):
            img_path, scale = _render_page_to_image(doc, i, dpi, tmp_path)
            result = engine.predict(str(img_path))
            items = _parse_ocr_result(result)

            page_text = "\n".join(text for text, _ in items).strip()
            parts.append(f"## Page {i + 1}\n\n{page_text}\n")

            if target_doc is not None:
                _insert_searchable_text(target_doc[i], items, scale)

            # 临时图片用完即删，避免 400+ 页时磁盘堆积
            img_path.unlink(missing_ok=True)

            if (i + 1) % 10 == 0 or i + 1 == limit:
                elapsed = time.time() - started
                rate = (i + 1) / elapsed if elapsed else 0
                log(
                    f"  OCR 进度 {i + 1}/{limit} 页, 已用 {elapsed:.0f}s, "
                    f"{rate:.2f} 页/s, 预计剩余 {max(0, (limit - i - 1)) / rate if rate else 0:.0f}s"
                )

    if target_doc is not None and searchable_path is not None:
        searchable_path.parent.mkdir(parents=True, exist_ok=True)
        target_doc.save(searchable_path, garbage=3, deflate=True)
        log(f"  可搜索 PDF 已写出: {searchable_path}")

    return "\n".join(parts), limit, dpi


def _insert_searchable_text(
    page: pymupdf.Page,
    items: list[tuple[str, list[list[float]]]],
    scale: float,
) -> None:
    """在页面上叠加不可见文字层（render_mode=3），产出可搜索 PDF。

    坐标换算：OCR 多边形基于渲染图像（像素），需除以缩放系数回到 PDF 点坐标。
    字体用内置简体中文字体 `china-s`，避免依赖外部字体文件。
    本函数失败不影响文本产物，因此吞掉异常并记录。
    """
    for text, poly in items:
        if not text or len(poly) < 4:
            continue
        try:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            x0, y0 = min(xs) / scale, min(ys) / scale
            height_pt = (max(ys) - min(ys)) / scale
            # 字号按检测框高度估算；0.8 系数用于补偿字体的上下留白
            fontsize = max(1.0, height_pt * 0.8)
            # insert_text 以「基线」定位，基线位于检测框底部略上方
            baseline_y = y0 + height_pt * 0.85
            page.insert_text(
                (x0, baseline_y),
                text,
                fontsize=fontsize,
                fontname=CJK_FONT_NAME,
                render_mode=3,  # 不可见但可搜索/可复制
            )
        except Exception:  # noqa: BLE001 - 文字层为副产物，失败不影响主文本
            continue


# ---- 产物写出 ---------------------------------------------------------------

def build_markdown(
    source_path: Path,
    track: str,
    pages: int,
    processed_pages: int,
    body: str,
    dpi: int | None,
) -> str:
    """拼装带 YAML frontmatter 的规范 Markdown。

    frontmatter 是摄取与检索之间的契约：承载来源、页数、哈希、摄取时间与所走轨道，
    便于下游溯源、去重与全量重建。
    """
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    sha = sha256_of(source_path)
    dpi_line = f"ocr_dpi: {dpi}\n" if dpi else ""
    frontmatter = (
        "---\n"
        f"source_file: {source_path.name}\n"
        f"source_path: {source_path}\n"
        f"sha256: {sha}\n"
        f"pages: {pages}\n"
        f"processed_pages: {processed_pages}\n"
        f"track: {track}\n"
        f"{dpi_line}"
        f"ingested_at: {now}\n"
        "---\n\n"
    )
    title = source_path.stem
    return f"{frontmatter}# {title}\n\n{body}"


def ingest_one(
    pdf_path: Path,
    out_dir: Path,
    max_pages: int | None = None,
    quality: str = DEFAULT_OCR_QUALITY,
    dpi_override: int | None = None,
    want_searchable: bool = False,
    force: bool = False,
) -> IngestResult:
    """摄取单个 PDF：探测 -> 分流到对应轨道 -> 写出规范 Markdown。"""
    started = time.time()
    errors: list[str] = []

    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 - 损坏/加密 PDF 需记录并跳过
        return IngestResult(pdf_path, "error", 0, 0, 0, None, errors=[f"打开失败: {exc}"])

    try:
        is_text, metrics = detect_has_text_layer(doc)
        track = "text" if is_text else "ocr"
        log(
            f"{pdf_path.name}: {doc.page_count} 页, 采样{metrics.get('sampled', 0)}页 "
            f"平均{metrics.get('avg_chars', 0)}字符, 有文字页占比{metrics.get('text_ratio', 0)} "
            f"-> 轨道: {track}"
        )

        body = ""
        processed = 0
        used_dpi: int | None = None
        searchable_path: Path | None = None

        if is_text:
            body = extract_text_track(doc, max_pages)
            processed = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        else:
            if want_searchable:
                searchable_path = DEFAULT_SEARCHABLE_DIR / f"{pdf_path.stem}.searchable.pdf"
            body, processed, used_dpi = extract_ocr_track(
                doc, max_pages, quality, dpi_override, want_searchable, searchable_path
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{pdf_path.stem}.md"
        if out_path.exists() and not force:
            log(f"  已存在 {out_path.name}，跳过（如需覆盖加 --force）")
        else:
            md = build_markdown(
                pdf_path, track, doc.page_count, processed, body, used_dpi
            )
            out_path.write_text(md, encoding="utf-8")
            log(f"  产出 {out_path.name} ({len(body)} 字符)")

        return IngestResult(
            source_path=pdf_path,
            track=track,
            pages=doc.page_count,
            processed_pages=processed,
            chars=len(body),
            output_path=out_path,
            searchable_path=searchable_path,
            elapsed_sec=round(time.time() - started, 1),
            errors=errors,
        )
    finally:
        doc.close()


def main() -> int:
    """命令行入口：支持单文件 / 整目录，三种触发器（手动/launchd/Hermes）共用。"""
    parser = argparse.ArgumentParser(description="PDF 双轨摄取入口")
    parser.add_argument("--file", type=Path, help="单个 PDF 文件路径")
    parser.add_argument("--dir", type=Path, default=None, help="批量目录（默认 02_data/raw）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Markdown 输出目录")
    parser.add_argument("--max-pages", type=int, default=None, help="只处理前 N 页（测试用）")
    parser.add_argument(
        "--ocr-quality",
        choices=sorted(OCR_PRESETS),
        default=DEFAULT_OCR_QUALITY,
        help="OCR 质量档位：fast(最快) / balanced(默认) / quality(最准)，见 OCR_PRESETS",
    )
    parser.add_argument("--ocr-dpi", type=int, default=None, help="覆盖档位默认的渲染 DPI")
    parser.add_argument("--searchable", action="store_true", help="OCR 轨道额外产出可搜索 PDF")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 Markdown")
    args = parser.parse_args()

    if args.file:
        targets = [args.file]
    else:
        src_dir = args.dir or DEFAULT_RAW_DIR
        targets = sorted(p for p in src_dir.glob("*.pdf"))
        if not targets:
            log(f"{src_dir} 下无 PDF。")
            return 1

    log(f"待处理 {len(targets)} 个 PDF")
    results: list[IngestResult] = []
    for idx, target in enumerate(targets, 1):
        log(f"({idx}/{len(targets)}) 处理 {target.name}")
        results.append(
            ingest_one(
                target,
                args.out,
                max_pages=args.max_pages,
                quality=args.ocr_quality,
                dpi_override=args.ocr_dpi,
                want_searchable=args.searchable,
                force=args.force,
            )
        )

    log("=" * 56)
    for r in results:
        status = "OK" if not r.errors else "ERR"
        log(
            f"{status} {r.source_path.name}: 轨道={r.track}, "
            f"{r.processed_pages}/{r.pages} 页, {r.chars} 字符, {r.elapsed_sec}s"
        )
        for err in r.errors:
            log(f"    ! {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
