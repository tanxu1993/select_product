# Windows EXE 打包说明

这个文档对应新的 GUI 启动脚本：

- `scripts/search_1688_by_csv_main_images_gui.py`

这个 GUI 不替换原命令行脚本：

- `scripts/search_1688_by_csv_main_images.py`

## 功能

- 通过窗口选择 `CSV / XLS / XLSX` 文件
- 通过输入框填写多个 BitBrowser 浏览器 ID
- 通过输入框填写 `max products` 和 `max results`
- `workers` 自动等于输入的浏览器 ID 数量
- 保留下载目录、后台运行、忽略断点续跑选项
- GUI 内直接显示运行日志

## 运行前准备

在 Windows 机器上准备这些内容：

1. 安装 Python 3.11 或兼容版本
2. 安装项目依赖
3. 安装 PyInstaller
4. 准备 `.env`
5. 确保 BitBrowser 已安装并且对应窗口已经登录好不同的 1688 账号

推荐命令：

```bash
pip install -r requirements.txt
pip install pyinstaller
playwright install chromium
```

`.env` 里至少要保证这些配置可用：

```env
ALIBABA1688_BITBROWSER_API_URL=http://127.0.0.1:54345
OPENAI_API_KEY=...
OPENAI_PRODUCT_PARSE_MODEL=...
DEFAULT_EXCHANGE_RATE_CNY_TO_RUB=...
```

## 本地直接运行 GUI

先验证 GUI 脚本本身：

```bash
python scripts/search_1688_by_csv_main_images_gui.py
```

## 打包 EXE

仓库已经提供了 `PyInstaller` 规格文件：

- `search_1688_by_csv_main_images_gui.spec`

在 Windows 上执行：

```bash
pyinstaller --clean search_1688_by_csv_main_images_gui.spec
```

打包完成后，输出目录通常是：

```text
dist/search_1688_by_csv_main_images_gui/
```

可执行文件通常是：

```text
dist/search_1688_by_csv_main_images_gui/search_1688_by_csv_main_images_gui.exe
```

## 发布时建议一起带上的文件

建议把这些内容和 exe 放在同一份发布目录里：

- `.env`
- `dist/search_1688_by_csv_main_images_gui/` 整个目录

运行时输出数据会按项目配置写到 exe 所在目录下，例如：

- `data/exports/`
- `data/raw/`
- `data/processed/`

## GUI 字段说明

- `表格文件`
  - 选择 CSV 或 Excel 文件
- `Excel Sheet`
  - 仅 Excel 时可选
- `BitBrowser IDs`
  - 支持逗号、空格、换行分隔
- `Max Products`
  - 处理前 N 个有效商品
- `Max Results`
  - 每个商品最多抓取前 N 个 1688 结果
- `Workers`
  - 自动计算，只读
- `下载目录`
  - 原图下载目录
- `后台运行浏览器`
  - 对应原脚本 `--background`
- `忽略断点续跑`
  - 对应原脚本 `--no-resume`

## 注意事项

- 这个 exe 是给 Windows 打包的，应该在 Windows 机器上执行 PyInstaller
- 当前仓库环境如果不是 Windows，不应该直接产出可用的 Windows exe
- 多 worker 仍然依赖 `multiprocessing`，所以必须保留 GUI 脚本里的 `freeze_support`
- 如果输入了 3 个浏览器 ID，那么 GUI 会自动按 `workers=3` 执行
