#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR 轨道参数调优基准脚本。

背景：当前 OCR 默认配置（PP-OCRv6_medium / 200 DPI / 4 线程）实测约 13.4s 每页、
      内存峰值约 7.8GB，资源开销偏高。目标是在**识别质量不明显下降**的前提下，
      找到时间/内存更低的组合。

可调杠杆：
1. 模型档位：medium（重、准） vs mobile（轻、快）；
2. 渲染 DPI：决定送入模型的图像像素规模（平方级影响耗时与显/内存）；
3. 线程数：影响并行度与内存占用；
4. text_det_limit_side_len：检测模型输入边长上限，直接约束最大张量规模。

方法：每个配置在**独立子进程**中运行（避免多配置间内存累积干扰读数），
      子进程自行上报峰值 RSS；驱动进程汇总，并与基线做文本相似度比对作为质量参照。

用法：
    python ocr_bench.py                 # 跑全部配置并输出对比表
    python ocr_bench.py --child ...     # 子进程模式（由驱动内部调用）
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PDF_PATH = PROJECT_ROOT / "02_data" / "raw" / "Word的排版艺术_By_侯捷.pdf"
# 取正文页（0-based），避开封面/版权页，保证参与比对的是有实质内容的文本
BENCH_PAGES = [4, 5, 6]
MARKER = "RESULT_JSON:"


def rss_mb() -> float:
    """当前进程常驻内存峰值（MB）。macOS 上 ru_maxrss 单位为字节。"""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def run_child(
    det: str, rec: str, dpi: int, threads: int, det_limit: int | None
) -> None:
    """子进程主体：按给定配置跑 OCR，并把指标以 JSON 打到 stdout。"""
    # OpenMP 线程数必须在 import paddle 之前设定
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    os.environ["OMP_NUM_THREADS"] = str(threads)

    import pymupdf  # noqa: E402
    from paddleocr import PaddleOCR  # noqa: E402

    kwargs: dict[str, object] = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "device": "cpu",
    }
    if det:
        kwargs["text_detection_model_name"] = det
    if rec:
        kwargs["text_recognition_model_name"] = rec
    if det_limit:
        kwargs["text_det_limit_side_len"] = det_limit

    try:
        ocr = PaddleOCR(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - 参数名不兼容时明确失败
        print(MARKER + json.dumps({"error": f"初始化失败: {exc}"}, ensure_ascii=False))
        return

    after_init_rss = rss_mb()
    doc = pymupdf.open(PDF_PATH)

    texts: list[str] = []
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="ocr_bench_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for idx in BENCH_PAGES:
            pix = doc[idx].get_pixmap(dpi=dpi)
            img_path = tmp_path / f"p{idx}.png"
            pix.save(img_path)
            result = ocr.predict(str(img_path))
            for res in result:
                try:
                    texts.extend(res.get("rec_texts", []))
                except Exception:  # noqa: BLE001 - 结构差异不中断基准
                    continue
    elapsed = time.time() - started
    doc.close()

    payload = {
        "det": det,
        "rec": rec,
        "dpi": dpi,
        "threads": threads,
        "det_limit": det_limit,
        "sec_total": round(elapsed, 1),
        "sec_per_page": round(elapsed / len(BENCH_PAGES), 2),
        "peak_rss_mb": round(rss_mb(), 0),
        "after_init_rss_mb": round(after_init_rss, 0),
        "chars": sum(len(t) for t in texts),
        "lines": len(texts),
        "text": "\n".join(texts)[:4000],
    }
    print(MARKER + json.dumps(payload, ensure_ascii=False))


# 基准配置矩阵：(标签, 检测模型, 识别模型, DPI, 线程数, 检测边长上限)
# 命名说明：PP-OCRv6 系列档位为 medium / small / tiny（无 mobile，mobile 属 v4/v5 系列）。
CONFIGS: list[tuple[str, str, str, int, int, int | None]] = [
    ("A 基线 v6_medium/200/4th", "PP-OCRv6_medium_det", "PP-OCRv6_medium_rec", 200, 4, None),
    ("B v6_small/200/4th", "PP-OCRv6_small_det", "PP-OCRv6_small_rec", 200, 4, None),
    ("C v6_small/150/4th", "PP-OCRv6_small_det", "PP-OCRv6_small_rec", 150, 4, None),
    ("D v6_tiny/150/4th", "PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec", 150, 4, None),
    ("E v6_small/150/2th", "PP-OCRv6_small_det", "PP-OCRv6_small_rec", 150, 2, None),
    ("F v6_small/200/4th+det640", "PP-OCRv6_small_det", "PP-OCRv6_small_rec", 200, 4, 640),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 参数调优基准")
    parser.add_argument("--child", action="store_true", help="子进程模式")
    parser.add_argument("--det", default="")
    parser.add_argument("--rec", default="")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--det-limit", type=int, default=0)
    args = parser.parse_args()

    if args.child:
        run_child(args.det, args.rec, args.dpi, args.threads, args.det_limit or None)
        return 0

    if not PDF_PATH.exists():
        print(f"样本不存在: {PDF_PATH}")
        return 1

    print(f"基准样本: {PDF_PATH.name}  页={[p + 1 for p in BENCH_PAGES]}  共 {len(CONFIGS)} 个配置\n")
    results: list[dict[str, object]] = []

    for label, det, rec, dpi, threads, det_limit in CONFIGS:
        print(f"--- 运行 {label} ...", flush=True)
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--det", det,
            "--rec", rec,
            "--dpi", str(dpi),
            "--threads", str(threads),
        ]
        if det_limit:
            cmd += ["--det-limit", str(det_limit)]

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900
        )
        payload: dict[str, object] | None = None
        for line in proc.stdout.splitlines():
            if line.startswith(MARKER):
                payload = json.loads(line[len(MARKER):])
                break
        if payload is None:
            print(f"    [失败] 无结果输出; stderr 末行: {proc.stderr.strip()[-300:]}")
            results.append({"label": label, "error": "no result"})
            continue
        payload["label"] = label
        results.append(payload)
        if "error" in payload:
            print(f"    [失败] {payload['error']}")
        else:
            print(
                f"    {payload['sec_per_page']}s/页, 峰值内存 {payload['peak_rss_mb']:.0f}MB, "
                f"{payload['chars']} 字符 / {payload['lines']} 行"
            )

    # 以基线文本为参照，计算各配置的识别文本相似度（质量代理指标）
    baseline_text = ""
    for r in results:
        if str(r.get("label", "")).startswith("A") and "text" in r:
            baseline_text = str(r["text"])
            break

    print("\n" + "=" * 78)
    print(f"{'配置':<26}{'秒/页':>8}{'峰值内存MB':>12}{'字符数':>9}{'质量相似度':>12}")
    print("=" * 78)
    for r in results:
        if "error" in r:
            print(f"{str(r['label']):<26}{'—':>8}{'—':>12}{'—':>9}{'失败':>12}")
            continue
        sim = "-"
        if baseline_text and "text" in r:
            sim = f"{difflib.SequenceMatcher(None, baseline_text, str(r['text'])).ratio():.3f}"
        print(
            f"{str(r['label']):<26}{float(r['sec_per_page']):>8.2f}"
            f"{float(r['peak_rss_mb']):>12.0f}{int(r['chars']):>9}{sim:>12}"
        )
    print("=" * 78)
    print("说明：质量相似度以配置 A（基线）识别文本为参照，越接近 1.0 表示与基线越一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
