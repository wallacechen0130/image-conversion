"""檔案轉檔核心邏輯（可獨立於圖形介面使用）。

支援：
  * 圖片互轉：PNG / JPG / WEBP / BMP / TIFF / GIF / ICO
  * PDF -> 圖片（可指定 DPI）
  * 圖片 -> PDF（可合併成一份，或每張各一份）

直接以程式呼叫的範例：
    from convert_core import convert
    convert(["a.png", "b.jpg"], "pdf", outdir="output")
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

try:  # PyMuPDF 新版改用 pymupdf 套件名稱
    import pymupdf as fitz
except ImportError:  # pragma: no cover - 舊版相容
    import fitz

from PIL import Image, ImageOps, ImageSequence

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".webp",
    ".bmp", ".tif", ".tiff", ".gif", ".ico",
}
PDF_EXTS = {".pdf"}

TARGETS = ["png", "jpg", "webp", "bmp", "tiff", "gif", "ico", "pdf"]
TARGET_LABELS = {
    "png": "PNG 圖片 (.png)",
    "jpg": "JPG 圖片 (.jpg)",
    "webp": "WEBP 圖片 (.webp)",
    "bmp": "BMP 圖片 (.bmp)",
    "tiff": "TIFF 圖片 (.tiff)",
    "gif": "GIF 圖片 (.gif)",
    "ico": "ICO 圖示 (.ico)",
    "pdf": "PDF 文件 (.pdf)",
}
PIL_FORMAT = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP",
    "tiff": "TIFF", "gif": "GIF", "ico": "ICO",
}
EXT = {
    "png": ".png", "jpg": ".jpg", "webp": ".webp", "bmp": ".bmp",
    "tiff": ".tiff", "gif": ".gif", "ico": ".ico", "pdf": ".pdf",
}

# 各種來源檔案可以轉成的目標格式
IMAGE_TARGETS = {"png", "jpg", "webp", "bmp", "tiff", "gif", "ico", "pdf"}
PDF_TARGETS = {"png", "jpg", "webp", "bmp", "tiff"}

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def suffix_of(path: Path) -> str:
    return path.suffix.lower()


def is_image(path: Path) -> bool:
    return suffix_of(path) in IMAGE_EXTS


def is_pdf(path: Path) -> bool:
    return suffix_of(path) in PDF_EXTS


def supported(path: Path) -> bool:
    return is_image(path) or is_pdf(path)


def unique_path(path: Path) -> Path:
    """若檔名已存在，自動加上流水號，避免覆蓋既有檔案。"""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _flatten(img: Image.Image, target: str) -> Image.Image:
    """把透明背景壓成白色（JPG / BMP / PDF 不支援透明）。"""
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if target in ("jpg", "bmp", "pdf") and has_alpha:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if img.mode in ("P", "LA", "RGBA"):
        return img.convert("RGBA" if target in ("png", "webp", "tiff", "gif", "ico") else "RGB")
    if img.mode == "CMYK":
        return img.convert("RGB")
    return img


def save_image(img: Image.Image, out: Path, target: str, quality: int = 90) -> None:
    fmt = PIL_FORMAT[target]
    img = _flatten(img, target)
    kwargs: dict = {}

    if target in ("jpg", "webp"):
        kwargs["quality"] = int(quality)
    if target == "jpg":
        kwargs["optimize"] = True
    if target == "tiff":
        kwargs["compression"] = "tiff_lzw"
    if target == "gif":
        if img.mode != "P":
            img = img.convert("RGB").convert(
                "P", palette=Image.Palette.ADAPTIVE, colors=256
            )
    if target == "ico":
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        kwargs["sizes"] = ICO_SIZES

    img.save(out, format=fmt, **kwargs)


def _pdf_page_image(page: "fitz.Page", dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# --------------------------------------------------------------------------- #
# 個別轉換
# --------------------------------------------------------------------------- #
def render_pdf(
    path: Path,
    target: str,
    outdir: Path,
    dpi: int = 200,
    quality: int = 90,
) -> list[Path]:
    """把 PDF 每一頁輸出成圖片。"""
    outdir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    with fitz.open(path) as doc:
        page_count = doc.page_count
        for index, page in enumerate(doc, start=1):
            img = _pdf_page_image(page, dpi)
            suffix = f"_p{index}" if page_count > 1 else ""
            out = unique_path(outdir / f"{path.stem}{suffix}{EXT[target]}")
            save_image(img, out, target, quality)
            outputs.append(out)
    return outputs


def convert_image(
    path: Path,
    target: str,
    outdir: Path,
    quality: int = 90,
) -> list[Path]:
    """轉換單一圖片；多幀圖片（GIF / TIFF）會逐幀輸出。"""
    outdir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    with Image.open(path) as im:
        frames = [ImageOps.exif_transpose(f.copy()) for f in ImageSequence.Iterator(im)]
    for index, frame in enumerate(frames, start=1):
        suffix = f"_{index}" if len(frames) > 1 else ""
        out = unique_path(outdir / f"{path.stem}{suffix}{EXT[target]}")
        save_image(frame, out, target, quality)
        outputs.append(out)
    return outputs


def images_to_pdf(
    paths: Sequence[Path],
    out: Path,
    dpi: int = 96,
) -> Path:
    """把多張圖片合併成一份 PDF（每張一頁）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    pages: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as im:
            frame = ImageOps.exif_transpose(im).convert("RGB")
            pages.append(frame.copy())
    if not pages:
        raise ValueError("沒有可轉換的圖片")
    first, rest = pages[0], pages[1:]
    first.save(out, "PDF", save_all=True, append_images=rest, resolution=dpi)
    return out


# --------------------------------------------------------------------------- #
# 對外主函式
# --------------------------------------------------------------------------- #
def convert(
    paths: Sequence[str | Path],
    target: str,
    outdir: str | Path | None = None,
    quality: int = 90,
    dpi: int = 200,
    merge_pdf: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[Path]:
    """轉換一批檔案。

    Args:
        paths: 來源檔案清單。
        target: 目標格式，需為 TARGETS 之一。
        outdir: 輸出資料夾；None 表示輸出到各來源檔案所在資料夾。
        quality: JPG / WEBP 品質（1-100）。
        dpi: PDF 轉圖片時的解析度。
        merge_pdf: 目標為 pdf 且有多張圖片時，是否合併成一份。
        progress: 進度回呼 (目前第幾個, 總數, 檔名)。
        log: 訊息回呼。

    Returns:
        產生的檔案路徑清單。
    """
    if target not in TARGETS:
        raise ValueError(f"不支援的目標格式：{target}")

    say = log or (lambda _msg: None)
    notify = progress or (lambda _cur, _total, _name: None)

    files = [Path(p) for p in paths]
    files = [p for p in files if p.exists()]
    total = len(files)
    outputs: list[Path] = []

    # ---------- 目標是 PDF：只處理圖片 ----------
    if target == "pdf":
        images = [p for p in files if is_image(p)]
        for p in files:
            if is_pdf(p):
                say(f"略過 {p.name}：它已經是 PDF")

        if not images:
            say("沒有可轉成 PDF 的圖片")
            return []

        base_dir = Path(outdir) if outdir else images[0].parent

        if merge_pdf and len(images) > 1:
            out = unique_path(base_dir / f"合併_{len(images)}張.pdf")
            say(f"合併 {len(images)} 張圖片 → {out.name}")
            images_to_pdf(images, out)
            for i, p in enumerate(images, start=1):
                notify(i, len(images), p.name)
            outputs.append(out)
            return outputs

        for i, p in enumerate(images, start=1):
            notify(i, len(images), p.name)
            out = unique_path((Path(outdir) if outdir else p.parent) / f"{p.stem}.pdf")
            try:
                images_to_pdf([p], out)
                outputs.append(out)
                say(f"{p.name} → {out.name}")
            except Exception as exc:  # noqa: BLE001
                say(f"失敗 {p.name}：{exc}")
        return outputs

    # ---------- 目標是圖片 ----------
    for i, p in enumerate(files, start=1):
        notify(i, total, p.name)
        folder = Path(outdir) if outdir else p.parent
        try:
            if is_pdf(p):
                if target not in PDF_TARGETS:
                    say(f"略過 {p.name}：PDF 無法轉成 {target.upper()}")
                    continue
                made = render_pdf(p, target, folder, dpi=dpi, quality=quality)
            elif is_image(p):
                made = convert_image(p, target, folder, quality=quality)
            else:
                say(f"略過 {p.name}：不支援的檔案類型")
                continue
            outputs.extend(made)
            say(f"{p.name} → " + "、".join(m.name for m in made))
        except Exception as exc:  # noqa: BLE001
            say(f"失敗 {p.name}：{exc}")

    return outputs
