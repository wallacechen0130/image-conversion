"""拖放式檔案轉檔工具（圖形介面）。

用法：雙擊 FileConverter.bat，或執行 `python converter.py`
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

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
BG = "#f2f4f7"
ACCENT = "#2563eb"


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

        root.title(APP_TITLE)
        root.geometry("840x820")
        root.minsize(700, 600)
        root.configure(bg=BG)
        root.option_add("*Font", FONT)

        icon = _resource_path("assets/icon.ico")
        if icon.exists():
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        self._build_ui()
        self._refresh_targets()
        self._set_status("把檔案拖進來，或按「選擇檔案」")
        self.root.after(100, self._poll_events)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG)
        style.configure("TCheckbutton", background=BG)
        style.configure("TRadiobutton", background=BG)
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, font=FONT)
        style.configure("Accent.TButton", font=("Microsoft JhengHei UI", 11, "bold"))

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=3)  # 檔案清單
        outer.rowconfigure(8, weight=2)  # 訊息

        # 標題
        ttk.Label(
            outer, text="檔案轉檔工具", font=("Microsoft JhengHei UI", 16, "bold")
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="支援 PDF、PNG、JPG、WEBP、BMP、TIFF、GIF、ICO",
            font=FONT_SMALL,
            foreground="#5b6472",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        # 拖放區
        self.drop_zone = tk.Label(
            outer,
            text="把檔案拖到這裡\n（也可以點一下選擇檔案）",
            font=("Microsoft JhengHei UI", 12),
            bg="white",
            fg="#5b6472",
            bd=2,
            relief="ridge",
            height=4,
            cursor="hand2",
        )
        self.drop_zone.grid(row=2, column=0, sticky="ew")
        self.drop_zone.bind("<Button-1>", lambda _e: self.add_files())

        hint = (
            "可直接拖入資料夾，會自動加入裡面所有支援的檔案"
            if HAS_DND
            else "未安裝 tkinterdnd2，無法拖放；請使用「選擇檔案」按鈕"
        )
        ttk.Label(outer, text=hint, font=FONT_SMALL, foreground="#7a8494").grid(
            row=3, column=0, sticky="w", pady=(6, 10)
        )

        # 檔案清單
        list_frame = ttk.LabelFrame(outer, text="待轉換檔案", padding=8)
        list_frame.grid(row=4, column=0, sticky="nsew")

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
        options.grid(row=5, column=0, sticky="ew", pady=(12, 0))
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
        out_frame.grid(row=6, column=0, sticky="ew", pady=(12, 0))
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
        action.grid(row=7, column=0, sticky="ew", pady=(14, 0))
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
        log_frame.grid(row=8, column=0, sticky="nsew", pady=(12, 0))
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

    # ------------------------------------------------------------ 檔案清單 --
    def _on_drop(self, event) -> None:
        self.drop_zone.configure(bg="white", fg="#5b6472")
        self._add_paths([Path(p) for p in _parse_dnd_paths(str(event.data))])

    def _on_drag_enter(self, _event) -> None:
        self.drop_zone.configure(bg="#e8f0fe", fg=ACCENT)

    def _on_drag_leave(self, _event) -> None:
        self.drop_zone.configure(bg="white", fg="#5b6472")

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
        self.root.after(100, self._poll_events)

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
