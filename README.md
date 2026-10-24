# 檔案轉檔工具

拖放式轉檔程式：把檔案丟進視窗，選好格式，按「開始轉檔」就好。

支援 PDF、PNG、JPG、WEBP、BMP、TIFF、GIF、ICO。

> Repository: <https://github.com/wallacechen0130/image-conversion>

## 直接使用（免安裝 Python）

執行 **`dist\ImageConverter.exe`**（Windows 64 位元）。

單檔 exe 第一次啟動需要解壓縮，約需數秒；之後即可直接把檔案拖進視窗使用。

## 從原始碼執行

```powershell
cd image-conversion
pip install -r requirements.txt
python converter.py
```

或雙擊 `FileConverter.bat`（用 pythonw 開啟，不跳黑色視窗）。

## 建置 exe

雙擊 `build_exe.bat`，或：

```powershell
build_exe.bat
```

腳本會自動建立 `.venv-build` 虛擬環境、安裝相依套件，並用 PyInstaller 產生
`dist\ImageConverter.exe`（單檔、無主控台視窗、含圖示）。

> `requirements.txt` 將 PyMuPDF 限制在 `<2.0`。PyMuPDF 2.0 會在 import 時載入
> 約 50MB 的 ONNX 版面分析模型，會讓 exe 暴增且需要額外資料檔；1.28.x 對本工具已足夠。

## 功能

| 來源 | 可以轉成 |
| --- | --- |
| 圖片（PNG / JPG / WEBP / BMP / TIFF / GIF / ICO） | PNG、JPG、WEBP、BMP、TIFF、GIF、ICO、PDF |
| PDF | PNG、JPG、WEBP、BMP、TIFF（每一頁一張圖） |

- 拖放檔案或整個資料夾（資料夾會自動遞迴加入支援的檔案）。
- 也可以按「選擇檔案」挑選。
- GIF / 多頁 TIFF 會逐幀輸出（`檔名_1.png`、`檔名_2.png`…）。
- 多頁 PDF 會逐頁輸出（`檔名_p1.png`、`檔名_p2.png`…），DPI 可調整 72–600。
- 多張圖片可合併成一份 PDF，或每張各自一份。
- 透明背景轉 JPG / BMP / PDF 時會自動補白底；照片依 EXIF 自動轉正。
- 輸出不覆蓋舊檔：同名時自動加上 `_2`、`_3`。
- 可選「與來源相同資料夾」或指定輸出資料夾。
- 轉檔在背景執行緒進行，大量檔案時介面不會卡住，並顯示進度與訊息。

## 介面選項

- **輸出格式**：依清單中的檔案類型自動過濾可用格式（例如清單裡有 PDF 時，不會出現 ICO / GIF / PDF 選項）。
- **品質**：只有 JPG / WEBP 會用到（30–100）。
- **PDF 解析度 (DPI)**：只有清單裡有 PDF 時才能調整，預設 200。
- **合併成一個 PDF**：目標格式為 PDF 且有多張圖片時有效。

## 檔案說明

| 檔案 | 用途 |
| --- | --- |
| `converter.py` | 圖形介面（tkinter + tkinterdnd2） |
| `convert_core.py` | 轉檔核心，也可用程式呼叫 |
| `dist/ImageConverter.exe` | 打包好的 Windows 執行檔 |
| `FileConverter.bat` | 從原始碼啟動 |
| `build_exe.bat` | 重新建置 exe |
| `assets/icon.ico` | 程式圖示 |
| `requirements.txt` | 相依套件 |

## 用程式呼叫核心

```python
from convert_core import convert

# 圖片轉 JPG，輸出到 output 資料夾
convert(["照片.png"], "jpg", outdir="output", quality=85)

# PDF 每頁轉 PNG（300 DPI）
convert(["報告.pdf"], "png", outdir="output", dpi=300)

# 多張圖片合併成一份 PDF
convert(["a.png", "b.jpg"], "pdf", outdir="output", merge_pdf=True)
```

## 備註

- 支援的輸入格式由 Pillow 與 PyMuPDF 決定；HEIC、AVIF 等需要額外套件，目前未列入。
- 有密碼保護的 PDF 無法轉換，會在訊息區顯示錯誤。
- exe 未進行程式碼簽章，Windows SmartScreen 可能顯示「未知的發行者」，選擇「仍要執行」即可。
