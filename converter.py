"""拖放式檔案轉檔工具（圖形介面）。

用法：雙擊 FileConverter.bat，或執行 `python converter.py`
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
import tkinter as tk

from PIL import Image, ImageDraw, ImageOps, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except Exception:  # pragma: no cover - 沒安裝時仍可用「選擇檔案」
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False

import convert_core as core

APP_TITLE = "檔案轉檔工具"
FONT = ("Microsoft JhengHei UI", 10)
FONT_SMALL = ("Microsoft JhengHei UI", 9)
BG = "#eef2f7"
CARD_BG = "#f7f9fc"
ACCENT = "#2563eb"
HERO_H = 92
DEFAULT_BG_COLOR = "#dbeafe"

CONFIG_DIR = Path(os.getenv("APPDATA") or Path.home()) / "ImageConverter"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DEFAULT_SETTINGS = {
    "background_mode": "default",   # default | color | image
    "background_color": DEFAULT_BG_COLOR,
    "background_image": "",
    "background_dim": 35,           # 0-70，圖片變暗程度
}


def _is_hex_color(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(c in "0123456789abcdefABCDEF" for c in value[1:])
    )


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            for key in DEFAULT_SETTINGS:
                if key in raw:
                    data[key] = raw[key]
    except (OSError, ValueError):
        pass

    if data["background_mode"] not in ("default", "color", "image"):
        data["background_mode"] = "default"
    if not _is_hex_color(data["background_color"]):
        data["background_color"] = DEFAULT_BG_COLOR
    if not isinstance(data["background_image"], str):
        data["background_image"] = ""
    try:
        data["background_dim"] = max(0, min(70, int(data["background_dim"])))
    except (TypeError, ValueError):
        data["background_dim"] = DEFAULT_SETTINGS["background_dim"]

    if data["background_mode"] == "image" and not Path(data["background_image"]).is_file():
        data["background_mode"] = "default"
    return data


def save_settings(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _resource_path(relative: str) -> Path:
    """原始碼執行與 PyInstaller 打包後都能找到資源檔。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _parse_dnd_paths(data: str) -> list[str]:
    """解析 tkinterdnd2 的檔案清單。

    Windows 路徑含空白時會被大括號包住（例如 ``{C:\\My Files\\a.png}``），
    不能直接用 Tcl 的 splitlist，否則反斜線會被當成跳脫字元吃掉。
    """
    items: list[str] = []
    buf: list[str] = []
    in_brace = False
    for ch in data:
        if ch == "{" and not in_brace and not buf:
            in_brace = True
        elif ch == "}" and in_brace:
            in_brace = False
        elif ch in (" ", "\n", "\t") and not in_brace:
            if buf:
                items.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        items.append("".join(buf))
    return [item for item in items if item.strip()]


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.events: queue.Queue = queue.Queue()
        self.busy = False

        settings = load_settings()
        self.bg_mode: str = settings["background_mode"]
        self.bg_color: str = settings["background_color"]
        self.bg_image: str = settings["background_image"]
        self.bg_dim: int = settings["background_dim"]
        self._bg_source: Image.Image | None = None
        self._bg_source_path: str = ""
        self._bg_photo: ImageTk.PhotoImage | None = None
        self._bg_job: str | None = None
        self._poll_job: str | None = None
        self._bg_dialog: tk.Toplevel | None = None

        root.title(APP_TITLE)
        root.minsize(720, 640)
        root.configure(bg=BG)
        root.option_add("*Font", FONT)
        self._center_window(860, 860)

        icon = _resource_path("assets/icon.ico")
        if icon.exists():
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        self._build_ui()
        self._apply_card_theme(self._card_color())
        self._refresh_targets()
        self._set_status("把檔案拖進來，或按「選擇檔案」")
        self._poll_job = self.root.after(100, self._poll_events)
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._render_background)

    def _center_window(self, width: int, height: int) -> None:
        """把視窗放在 Windows 工作區中央，避免底部被工作列遮住。"""
        left, top = 0, 0
        area_w, area_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            # SPI_GETWORKAREA：不含工作列的可用桌面範圍
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0
            ):
                left, top = rect.left, rect.top
                area_w, area_h = rect.right - rect.left, rect.bottom - rect.top
        except Exception:  # noqa: BLE001 - 非 Windows 或查詢失敗時用螢幕尺寸
            pass

        width = min(width, max(640, area_w - 24))
        height = min(height, max(560, area_h - 24))
        x = left + max(0, (area_w - width) // 2)
        y = top + max(0, (area_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=CARD_BG)
        style.configure("TLabel", background=CARD_BG)
        style.configure("TCheckbutton", background=CARD_BG)
        style.configure("TRadiobutton", background=CARD_BG)
        style.configure("TLabelframe", background=CARD_BG)
        style.configure("TLabelframe.Label", background=CARD_BG, font=FONT)
        style.configure("Accent.TButton", font=("Microsoft JhengHei UI", 11, "bold"))

        # 背景畫布（顏色或圖片）＋ 浮在上面的內容卡片
        self.bg_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=BG)
        self.bg_canvas.pack(fill="both", expand=True)

        self.card = tk.Frame(
            self.root,
            bg=CARD_BG,
            highlightbackground="#d8dfe9",
            highlightthickness=1,
        )
        self.card.place(
            x=24,
            y=HERO_H,
            relwidth=1,
            width=-48,
            relheight=1,
            height=-(HERO_H + 20),
        )

        self.bg_btn = tk.Button(
            self.bg_canvas,
            text="背景設定",
            command=self.open_bg_dialog,
            font=FONT_SMALL,
            bg="white",
            fg="#1f2937",
            activebackground="#eef2f7",
            activeforeground="#1f2937",
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            highlightthickness=0,
        )

        outer = ttk.Frame(self.card, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=3)  # 檔案清單
        outer.rowconfigure(6, weight=2)  # 訊息

        # 拖放區
        self.drop_zone = tk.Label(
            outer,
            text="把檔案拖到這裡\n（也可以點一下選擇檔案）",
            font=("Microsoft JhengHei UI", 12),
            bg="white",
            fg="#5b6472",
            bd=2,
            relief="ridge",
            height=3,
            cursor="hand2",
        )
        self.drop_zone.grid(row=0, column=0, sticky="ew")
        self.drop_zone.bind("<Button-1>", lambda _e: self.add_files())

        hint = (
            "可直接拖入資料夾，會自動加入裡面所有支援的檔案"
            if HAS_DND
            else "未安裝 tkinterdnd2，無法拖放；請使用「選擇檔案」按鈕"
        )
        ttk.Label(outer, text=hint, font=FONT_SMALL, foreground="#7a8494").grid(
            row=1, column=0, sticky="w", pady=(6, 10)
        )

        # 檔案清單
        list_frame = ttk.LabelFrame(outer, text="待轉換檔案", padding=8)
        list_frame.grid(row=2, column=0, sticky="nsew")

        self.listbox = tk.Listbox(
            list_frame,
            selectmode="extended",
            activestyle="none",
            font=FONT,
            bd=1,
            relief="solid",
            highlightthickness=0,
            height=5,
        )
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        side = ttk.Frame(list_frame)
        side.grid(row=0, column=2, sticky="ns", padx=(8, 0))
        ttk.Button(side, text="選擇檔案", command=self.add_files).pack(
            fill="x", pady=(0, 6)
        )
        ttk.Button(side, text="移除選取", command=self.remove_selected).pack(
            fill="x", pady=(0, 6)
        )
        ttk.Button(side, text="清空清單", command=self.clear_files).pack(fill="x")
        self.count_label = ttk.Label(side, text="0 個檔案", font=FONT_SMALL)
        self.count_label.pack(anchor="w", pady=(10, 0))

        self.listbox.bind("<Delete>", lambda _e: self.remove_selected())

        # 轉檔設定
        options = ttk.LabelFrame(outer, text="轉檔設定", padding=10)
        options.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text="輸出格式").grid(row=0, column=0, sticky="w")
        self.format_var = tk.StringVar()
        self.format_box = ttk.Combobox(
            options,
            textvariable=self.format_var,
            state="readonly",
            width=22,
            font=FONT,
        )
        self.format_box.grid(row=0, column=1, sticky="w", padx=(8, 24))
        self.format_box.bind("<<ComboboxSelected>>", self._on_option_change)

        ttk.Label(options, text="品質").grid(row=0, column=2, sticky="w")
        self.quality_var = tk.IntVar(value=90)
        self.quality_scale = ttk.Scale(
            options,
            from_=30,
            to=100,
            variable=self.quality_var,
            command=lambda _v: self._update_quality_label(),
            length=160,
        )
        self.quality_scale.grid(row=0, column=3, sticky="w", padx=(8, 6))
        self.quality_label = ttk.Label(options, text="90", font=FONT_SMALL)
        self.quality_label.grid(row=0, column=4, sticky="w")

        ttk.Label(options, text="PDF 解析度 (DPI)").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.dpi_var = tk.IntVar(value=200)
        self.dpi_spin = ttk.Spinbox(
            options, from_=72, to=600, increment=25, width=8,
            textvariable=self.dpi_var, font=FONT,
        )
        self.dpi_spin.grid(row=1, column=1, sticky="w", padx=(8, 24), pady=(10, 0))

        self.merge_var = tk.BooleanVar(value=True)
        self.merge_check = ttk.Checkbutton(
            options,
            text="多張圖片合併成一個 PDF",
            variable=self.merge_var,
        )
        self.merge_check.grid(row=1, column=2, columnspan=3, sticky="w", pady=(10, 0))

        # 輸出位置
        out_frame = ttk.LabelFrame(outer, text="輸出位置", padding=10)
        out_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        out_frame.columnconfigure(1, weight=1)

        self.out_mode = tk.StringVar(value="source")
        ttk.Radiobutton(
            out_frame,
            text="與來源檔案相同資料夾",
            value="source",
            variable=self.out_mode,
            command=self._on_option_change,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            out_frame,
            text="指定資料夾：",
            value="custom",
            variable=self.out_mode,
            command=self._on_option_change,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.out_entry = ttk.Entry(out_frame, font=FONT)
        self.out_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        self.browse_btn = ttk.Button(
            out_frame, text="瀏覽…", command=self.choose_outdir
        )
        self.browse_btn.grid(row=1, column=2, pady=(8, 0))

        # 開始轉檔
        action = ttk.Frame(outer)
        action.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        action.columnconfigure(1, weight=1)

        self.convert_btn = tk.Button(
            action,
            text="開始轉檔",
            command=self.start_convert,
            font=("Microsoft JhengHei UI", 12, "bold"),
            bg=ACCENT,
            fg="white",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief="flat",
            padx=24,
            pady=8,
            cursor="hand2",
        )
        self.convert_btn.grid(row=0, column=0, rowspan=2, sticky="w")

        self.progress = ttk.Progressbar(action, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=(16, 0))

        self.status_label = ttk.Label(action, text="", font=FONT_SMALL)
        self.status_label.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(4, 0))

        self.open_btn = ttk.Button(
            action, text="開啟輸出資料夾", command=self.open_output, state="disabled"
        )
        self.open_btn.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))

        # 訊息
        log_frame = ttk.LabelFrame(outer, text="訊息", padding=8)
        log_frame.grid(row=6, column=0, sticky="nsew", pady=(12, 0))
        self.log_text = tk.Text(
            log_frame,
            height=5,
            wrap="word",
            font=FONT_SMALL,
            bd=1,
            relief="solid",
            state="disabled",
            highlightthickness=0,
        )
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        if HAS_DND:
            for widget in (self.root, self.drop_zone, self.listbox):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DropEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DropLeave>>", self._on_drag_leave)

    # ------------------------------------------------------------ 背景設定 --
    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        if self._bg_job is not None:
            try:
                self.root.after_cancel(self._bg_job)
            except tk.TclError:
                pass
        self._bg_job = self.root.after(160, self._render_background)

    def _readable_colors(self, color: str) -> tuple[str, str]:
        r, g, b = self.root.winfo_rgb(color)
        r, g, b = r // 257, g // 257, b // 257
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if luminance < 140:
            return "#ffffff", "#dbe4f0"
        return "#111827", "#475569"

    def _mix_color(self, base: str, tint: str, amount: float) -> str:
        """把 tint 混進 base（amount 0–1），用來模擬半透明的玻璃效果。"""
        try:
            br, bg, bb = (c // 257 for c in self.root.winfo_rgb(base))
            tr, tg, tb = (c // 257 for c in self.root.winfo_rgb(tint))
        except tk.TclError:
            return base
        amount = max(0.0, min(1.0, amount))
        r = round(br + (tr - br) * amount)
        g = round(bg + (tg - bg) * amount)
        b = round(bb + (tb - bb) * amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _card_color(self) -> str:
        """讓卡片色調跟背景一致：純色或圖片都用淡色版，帶出一點透明感。"""
        if self.bg_mode == "color":
            return self._mix_color("#ffffff", self.bg_color, 0.26)
        if self.bg_mode == "image":
            source = self._load_bg_source()
            if source is not None:
                r, g, b = source.resize(
                    (1, 1), Image.Resampling.LANCZOS
                ).getpixel((0, 0))
                return self._mix_color("#ffffff", f"#{r:02x}{g:02x}{b:02x}", 0.26)
        return CARD_BG

    def _apply_card_theme(self, color: str) -> None:
        self.card.configure(bg=color)
        # 白色欄位也混一點背景色，避免整片死白
        field = self._mix_color(color, "#ffffff", 0.55)
        self.field_bg = field
        style = ttk.Style(self.root)
        for name in (
            "TFrame",
            "TLabel",
            "TCheckbutton",
            "TRadiobutton",
            "TLabelframe",
            "TLabelframe.Label",
        ):
            style.configure(name, background=color)
        style.configure(
            "TEntry", fieldbackground=field, foreground="#1f2937", insertcolor="#1f2937"
        )
        style.configure(
            "TCombobox",
            fieldbackground=field,
            background=color,
            foreground="#1f2937",
            arrowcolor="#475569",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", field)],
            selectbackground=[("readonly", field)],
            selectforeground=[("readonly", "#1f2937")],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=field,
            background=color,
            foreground="#1f2937",
            arrowcolor="#475569",
        )
        style.configure("TScale", background=color)

        if hasattr(self, "listbox"):
            self.listbox.configure(
                bg=field,
                fg="#1f2937",
                selectbackground=ACCENT,
                selectforeground="white",
            )
        if hasattr(self, "log_text"):
            self.log_text.configure(bg=field, fg="#1f2937")
        if hasattr(self, "drop_zone"):
            self.drop_zone.configure(bg=field)

        # 下拉選單是獨立的 listbox，也要跟著換色
        self.root.option_add("*TCombobox*Listbox.background", field)
        self.root.option_add("*TCombobox*Listbox.foreground", "#1f2937")
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")

    def _load_bg_source(self) -> Image.Image | None:
        if not self.bg_image:
            return None
        if self._bg_source is not None and self._bg_source_path == self.bg_image:
            return self._bg_source
        try:
            with Image.open(self.bg_image) as im:
                source = ImageOps.exif_transpose(im).convert("RGB")
                source.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                self._bg_source = source.copy()
            self._bg_source_path = self.bg_image
            return self._bg_source
        except Exception:  # noqa: BLE001
            self._bg_source = None
            self._bg_source_path = ""
            return None

    def _render_background(self) -> None:
        self._bg_job = None
        canvas = getattr(self, "bg_canvas", None)
        if canvas is None:
            return
        w = max(canvas.winfo_width(), 1)
        h = max(canvas.winfo_height(), 1)
        if w <= 1 or h <= 1:
            return

        canvas.delete("all")
        title_fg, subtitle_fg = "#111827", "#5b6472"
        shadow = False

        if self.bg_mode == "image":
            source = self._load_bg_source()
            if source is None:
                self.bg_mode = "default"
                self._apply_card_theme(self._card_color())
                self._render_background()
                return
            img = ImageOps.fit(
                source, (w, h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.4)
            )
            if self.bg_dim:
                img = Image.blend(
                    img, Image.new("RGB", (w, h), (0, 0, 0)), self.bg_dim / 100
                )
            # 頂部漸層遮罩，讓標題在花俏的圖片上仍然清楚
            mask = Image.new("L", (1, h), 0)
            fade = max(1, min(HERO_H + 60, h))
            for y in range(fade):
                mask.putpixel((0, y), int(150 * (1 - y / fade)))
            mask = mask.resize((w, h))
            img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, mask)
            self._bg_photo = ImageTk.PhotoImage(img)
            canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
            title_fg, subtitle_fg = "#ffffff", "#e2e8f0"
            shadow = True
        elif self.bg_mode == "color":
            canvas.create_rectangle(0, 0, w, h, fill=self.bg_color, outline="")
            title_fg, subtitle_fg = self._readable_colors(self.bg_color)
        else:
            canvas.create_rectangle(0, 0, w, h, fill=BG, outline="")

        if shadow:
            canvas.create_text(
                25, 19, anchor="nw", text=APP_TITLE,
                font=("Microsoft JhengHei UI", 18, "bold"), fill="#0b1220",
            )
        canvas.create_text(
            24, 18, anchor="nw", text=APP_TITLE,
            font=("Microsoft JhengHei UI", 18, "bold"), fill=title_fg,
        )
        canvas.create_text(
            24, 54, anchor="nw",
            text="支援 PDF、PNG、JPG、WEBP、BMP、TIFF、GIF、ICO",
            font=FONT_SMALL, fill=subtitle_fg,
        )
        canvas.create_window(w - 20, 22, anchor="ne", window=self.bg_btn)

    def apply_background(
        self,
        mode: str,
        color: str | None = None,
        image: str | None = None,
        dim: int | None = None,
    ) -> None:
        if mode not in ("default", "color", "image"):
            mode = "default"
        if color and _is_hex_color(color):
            self.bg_color = color
        if image is not None:
            self.bg_image = str(image)
        if dim is not None:
            self.bg_dim = max(0, min(70, int(dim)))
        if mode == "image" and not Path(self.bg_image).is_file():
            mode = "default"

        self.bg_mode = mode
        self._bg_source = None
        self._bg_source_path = ""
        self._save_bg_settings()
        self._apply_card_theme(self._card_color())
        self._render_background()

    def _save_bg_settings(self) -> None:
        save_settings(
            {
                "background_mode": self.bg_mode,
                "background_color": self.bg_color,
                "background_image": self.bg_image,
                "background_dim": self.bg_dim,
            }
        )

    def open_bg_dialog(self) -> None:
        if self._bg_dialog is not None and self._bg_dialog.winfo_exists():
            self._bg_dialog.lift()
            self._bg_dialog.focus_force()
            return

        dlg = tk.Toplevel(self.root)
        self._bg_dialog = dlg
        dlg.title("背景設定")
        dlg.configure(bg=self.card.cget("bg"))
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        icon = _resource_path("assets/icon.ico")
        if icon.exists():
            try:
                dlg.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        body = ttk.Frame(dlg, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        mode_var = tk.StringVar(value=self.bg_mode)
        color_var = tk.StringVar(value=self.bg_color)
        image_var = tk.StringVar(value=self.bg_image)
        dim_var = tk.IntVar(value=self.bg_dim)

        kind = ttk.LabelFrame(body, text="背景類型", padding=8)
        kind.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Radiobutton(
            kind, text="預設（淺色）", value="default", variable=mode_var,
            command=lambda: sync(),
        ).pack(anchor="w")
        ttk.Radiobutton(
            kind, text="純色", value="color", variable=mode_var,
            command=lambda: sync(),
        ).pack(anchor="w")
        ttk.Radiobutton(
            kind, text="圖片", value="image", variable=mode_var,
            command=lambda: sync(),
        ).pack(anchor="w")

        ttk.Label(body, text="顏色").grid(row=1, column=0, sticky="w", pady=(12, 0))
        color_row = ttk.Frame(body)
        color_row.grid(row=1, column=1, sticky="ew", pady=(12, 0))
        swatch = tk.Label(
            color_row, width=4, bg=color_var.get(), relief="solid", bd=1
        )
        swatch.pack(side="left")

        def choose_color() -> None:
            _rgb, hex_color = colorchooser.askcolor(
                color=color_var.get(), parent=dlg, title="選擇背景顏色"
            )
            if hex_color:
                color_var.set(hex_color)
                swatch.configure(bg=hex_color)
                mode_var.set("color")
                sync()

        color_btn = ttk.Button(color_row, text="選擇顏色…", command=choose_color)
        color_btn.pack(side="left", padx=(8, 0))

        ttk.Label(body, text="圖片").grid(row=2, column=0, sticky="w", pady=(8, 0))
        image_row = ttk.Frame(body)
        image_row.grid(row=2, column=1, sticky="ew", pady=(8, 0))
        image_entry = ttk.Entry(
            image_row, textvariable=image_var, width=40, font=FONT_SMALL
        )
        image_entry.pack(side="left", fill="x", expand=True)

        def choose_image() -> None:
            path = filedialog.askopenfilename(
                parent=dlg,
                title="選擇背景圖片",
                filetypes=[
                    ("圖片", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif"),
                    ("所有檔案", "*.*"),
                ],
            )
            if path:
                image_var.set(path)
                mode_var.set("image")
                sync()

        image_btn = ttk.Button(image_row, text="瀏覽…", command=choose_image)
        image_btn.pack(side="left", padx=(8, 0))

        ttk.Label(body, text="變暗").grid(row=3, column=0, sticky="w", pady=(8, 0))
        dim_row = ttk.Frame(body)
        dim_row.grid(row=3, column=1, sticky="ew", pady=(8, 0))
        dim_scale = ttk.Scale(
            dim_row, from_=0, to=70, variable=dim_var, length=210
        )
        dim_scale.pack(side="left")
        dim_label = ttk.Label(dim_row, text=f"{self.bg_dim}%", width=5)
        dim_label.pack(side="left", padx=(8, 0))
        dim_scale.configure(
            command=lambda _v: dim_label.configure(text=f"{int(dim_var.get())}%")
        )

        ttk.Label(
            body,
            text="圖片會自動裁切填滿視窗；調高變暗可讓文字更清楚。",
            font=FONT_SMALL,
            foreground="#7a8494",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def sync() -> None:
            mode = mode_var.get()
            color_btn.state(["!disabled"] if mode == "color" else ["disabled"])
            swatch.configure(
                bg=color_var.get() if mode == "color" else "#e5e7eb"
            )
            for widget in (image_entry, image_btn):
                widget.state(["!disabled"] if mode == "image" else ["disabled"])
            dim_scale.state(["!disabled"] if mode == "image" else ["disabled"])
            dim_label.configure(
                foreground="#111827" if mode == "image" else "#9aa3b2"
            )

        def apply() -> None:
            mode = mode_var.get()
            path = image_var.get().strip()
            if mode == "image":
                if not path or not Path(path).is_file():
                    messagebox.showwarning(
                        "背景設定", "請先選擇一張背景圖片。", parent=dlg
                    )
                    return
                try:
                    with Image.open(path) as im:
                        im.verify()
                except Exception:  # noqa: BLE001
                    messagebox.showwarning(
                        "背景設定", "這個檔案不是可讀取的圖片。", parent=dlg
                    )
                    return
            self.apply_background(mode, color_var.get(), path, int(dim_var.get()))
            dlg.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="取消", command=dlg.destroy).pack(side="right")
        tk.Button(
            buttons,
            text="套用",
            command=apply,
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=ACCENT,
            fg="white",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=4,
            cursor="hand2",
            highlightthickness=0,
        ).pack(side="right", padx=(0, 8))

        sync()
        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + max(20, (self.root.winfo_height() - h) // 3)
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ------------------------------------------------------------ 檔案清單 --
    def _on_drop(self, event) -> None:
        self.drop_zone.configure(bg=self.field_bg, fg="#5b6472")
        self._add_paths([Path(p) for p in _parse_dnd_paths(str(event.data))])

    def _on_drag_enter(self, _event) -> None:
        self.drop_zone.configure(bg="#e8f0fe", fg=ACCENT)

    def _on_drag_leave(self, _event) -> None:
        self.drop_zone.configure(bg=self.field_bg, fg="#5b6472")

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="選擇要轉換的檔案",
            filetypes=[
                ("支援的檔案", "*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.ico"),
                ("PDF", "*.pdf"),
                ("圖片", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.ico"),
                ("所有檔案", "*.*"),
            ],
        )
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        skipped = 0
        known = {f.resolve() for f in self.files}
        for path in paths:
            candidates: list[Path] = []
            if path.is_dir():
                candidates = sorted(
                    p for p in path.rglob("*") if p.is_file() and core.supported(p)
                )
            elif core.supported(path):
                candidates = [path]
            else:
                skipped += 1
                continue

            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved in known:
                    continue
                known.add(resolved)
                self.files.append(candidate)
                added += 1

        if added:
            self._render_file_list()
            self._refresh_targets()
            self._set_status(f"已加入 {added} 個檔案")
        if skipped:
            messagebox.showwarning(
                APP_TITLE, f"有 {skipped} 個檔案不支援，已略過。"
            )
        if added:
            self._log(f"加入 {added} 個檔案")

    def remove_selected(self) -> None:
        selected = sorted(self.listbox.curselection(), reverse=True)
        if not selected:
            return
        for index in selected:
            del self.files[index]
        self._render_file_list()
        self._refresh_targets()

    def clear_files(self) -> None:
        self.files.clear()
        self._render_file_list()
        self._refresh_targets()
        self._set_status("清單已清空")

    def _render_file_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for index, path in enumerate(self.files, start=1):
            try:
                size = path.stat().st_size
                size_text = (
                    f"{size / 1024 / 1024:.1f} MB"
                    if size >= 1024 * 1024
                    else f"{size / 1024:.0f} KB"
                )
            except OSError:
                size_text = "?"
            kind = "PDF" if core.is_pdf(path) else path.suffix.upper().lstrip(".")
            self.listbox.insert(tk.END, f"{index:>2}.  {path.name}   [{kind}]  {size_text}")
        self.count_label.configure(text=f"{len(self.files)} 個檔案")

    # -------------------------------------------------------------- 設定 --
    def _refresh_targets(self) -> None:
        allowed = set(core.TARGETS)
        if any(core.is_pdf(f) for f in self.files):
            allowed &= core.PDF_TARGETS
        if self.files and all(core.is_image(f) for f in self.files):
            allowed &= core.IMAGE_TARGETS

        values = [core.TARGET_LABELS[t] for t in core.TARGETS if t in allowed]
        current = self.current_target()
        self.format_box.configure(values=values)
        if current in allowed:
            self.format_var.set(core.TARGET_LABELS[current])
        elif values:
            preferred = next(
                (t for t in ("png", "jpg") if t in allowed),
                next(t for t in core.TARGETS if t in allowed),
            )
            self.format_var.set(core.TARGET_LABELS[preferred])
        else:
            self.format_var.set("")
        self._on_option_change()

    def current_target(self) -> str | None:
        label = self.format_var.get()
        for key, value in core.TARGET_LABELS.items():
            if value == label:
                return key
        return None

    def _on_option_change(self, _event=None) -> None:
        target = self.current_target()
        quality_state = "normal" if target in ("jpg", "webp") else "disabled"
        self.quality_scale.configure(state=quality_state)

        has_pdf = any(core.is_pdf(f) for f in self.files)
        self.dpi_spin.configure(state="normal" if has_pdf else "disabled")

        merge_state = "normal" if target == "pdf" else "disabled"
        self.merge_check.configure(state=merge_state)

        custom = self.out_mode.get() == "custom"
        self.out_entry.configure(state="normal" if custom else "disabled")
        self.browse_btn.configure(state="normal" if custom else "disabled")

    def _update_quality_label(self) -> None:
        self.quality_label.configure(text=str(int(self.quality_var.get())))

    def choose_outdir(self) -> None:
        folder = filedialog.askdirectory(title="選擇輸出資料夾")
        if folder:
            self.out_mode.set("custom")
            self.out_entry.configure(state="normal")
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, folder)
            self._on_option_change()

    # -------------------------------------------------------------- 轉檔 --
    def start_convert(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "請先加入要轉換的檔案。")
            return

        target = self.current_target()
        if not target:
            messagebox.showwarning(APP_TITLE, "請選擇輸出格式。")
            return

        outdir: Path | None = None
        if self.out_mode.get() == "custom":
            raw = self.out_entry.get().strip()
            if not raw:
                messagebox.showwarning(APP_TITLE, "請選擇輸出資料夾。")
                return
            outdir = Path(raw)

        try:
            quality = int(self.quality_var.get())
            dpi = int(self.dpi_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning(APP_TITLE, "品質或 DPI 數值不正確。")
            return
        dpi = max(72, min(600, dpi))

        self.busy = True
        self.convert_btn.configure(state="disabled", text="轉檔中…")
        self.open_btn.configure(state="disabled")
        self.progress.configure(maximum=len(self.files), value=0)
        self._set_status("轉檔中，請稍候…")
        self._log("── 開始轉檔 ──")

        args = (
            list(self.files), target, outdir, quality, dpi,
            bool(self.merge_var.get()),
        )
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(
        self,
        files: list[Path],
        target: str,
        outdir: Path | None,
        quality: int,
        dpi: int,
        merge_pdf: bool,
    ) -> None:
        try:
            results = core.convert(
                files,
                target,
                outdir=outdir,
                quality=quality,
                dpi=dpi,
                merge_pdf=merge_pdf,
                progress=lambda cur, total, name: self.events.put(
                    ("progress", cur, total, name)
                ),
                log=lambda msg: self.events.put(("log", msg)),
            )
            self.events.put(("done", results, None))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("done", [], str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._log(event[1])
                elif kind == "progress":
                    _, cur, total, name = event
                    self.progress.configure(value=cur)
                    self._set_status(f"({cur}/{total}) {name}")
                elif kind == "done":
                    self._finish(event[1], event[2])
        except queue.Empty:
            pass
        self._poll_job = self.root.after(100, self._poll_events)

    def _on_close(self) -> None:
        for job in (self._poll_job, self._bg_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.root.destroy()

    def _finish(self, results: list[Path], error: str | None) -> None:
        self.busy = False
        self.convert_btn.configure(state="normal", text="開始轉檔")
        if error:
            self._log(f"發生錯誤：{error}")
            self._set_status("轉檔失敗")
            messagebox.showerror(APP_TITLE, f"轉檔失敗：\n{error}")
            return

        if results:
            self._log(f"完成，共產生 {len(results)} 個檔案")
            self._set_status(f"完成！共產生 {len(results)} 個檔案")
            self.open_btn.configure(state="normal")
        else:
            self._log("沒有產生任何檔案")
            self._set_status("沒有產生任何檔案")

    def open_output(self) -> None:
        if not self.files:
            return
        folder = None
        if self.out_mode.get() == "custom":
            folder = self.out_entry.get().strip()
        if not folder:
            folder = str(self.files[0].parent)
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"無法開啟資料夾：\n{exc}")

    # -------------------------------------------------------------- 其他 --
    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self.status_label.configure(text=message)


def main() -> None:
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
