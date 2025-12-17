# 日文书籍翻译项目

## 项目概述

这是一个基于iFlow CLI智能代理系统的日文EPUB书籍翻译项目，专门用于将日文EPUB格式的书籍内容翻译成中文。

项目采用先进的Agent架构，通过专业的日中翻译Agent实现高质量的文学翻译，确保翻译结果既准确又符合中文阅读习惯。

## 项目结构

```
ja-translate/
├── IFLOW.md                 # 项目文档（本文件）
├── epub_translator.py       # EPUB翻译器主程序
├── pyproject.toml           # 项目配置文件
├── uv.lock                  # 依赖锁定文件
├── .iflow/                  # iFlow CLI配置目录
│   └── agents/
│       └── ja-zh-translator.md  # 日中翻译Agent配置
├── source/                  # 源文件目录（EPUB格式，需包含OEBPS/text*.html文件）
├── translated/              # 翻译输出目录
├── translate-checklist.md   # 翻译进度追踪清单
├── paragraph_progress.json  # 段落翻译进度记录
├── error_log.json           # 翻译错误日志
└── __pycache__/             # Python缓存目录
```

## 核心功能

### EPUB翻译器 (epub_translator.py)
- 自动提取EPUB中的HTML文本块（段落、标题等）
- 调用iFlow的ja-zh-translator智能代理进行翻译
- 保持原始HTML标签结构
- 支持上下文感知翻译（前一段、当前段、后一段）
- 术语表支持（优先使用预定义术语）
- 断点续传功能（记录翻译进度）
- 质量检查（检测日文残留、标点符号使用）
- 错误日志记录

### 翻译质量保证
- 使用中文标点符号（，。！？等）
- 保持原文语气自然流畅
- 术语一致性检查
- 翻译结果验证

## 使用方法

### 1. 准备工作
1. 将待翻译的EPUB文件解压到`source/`目录
2. 确保EPUB内容位于`source/OEBPS/`目录下
3. 可选：准备`glossary.md`术语表文件

### 2. 术语表格式 (glossary.md)
```
# 术语表
| 日文 | 中文 |
|------|------|
| 用語 | 术语 |
| ...  | ...  |
```

### 3. 运行翻译
```bash
# 使用uv运行（推荐）
uv run python epub_translator.py

# 或者直接运行
python epub_translator.py

# 或使用项目脚本
uv run translate
```

### 4. 进度追踪
- 实时更新`translate-checklist.md`文件
- 记录翻译进度到`paragraph_progress.json`
- 记录错误到`error_log.json`

## 配置参数

可在`epub_translator.py`中调整以下参数：

- `SOURCE_DIR`: 源文件目录（默认：source/OEBPS）
- `TRANSLATED_DIR`: 翻译输出目录（默认：translated）
- `MAX_RETRY`: 最大重试次数（默认：3）
- `TIMEOUT_SEC`: 翻译超时时间（默认：60.0秒）
- `QUALITY_CHECK_INTERVAL`: 质量检查间隔（默认：5段）
- `CHECKLIST_FILE`: 进度清单文件（默认：translate-checklist.md）
- `GLOSSARY_FILE`: 术语表文件（默认：glossary.md）
- `PROGRESS_FILE`: 进度文件（默认：paragraph_progress.json）
- `ERROR_LOG_FILE`: 错误日志文件（默认：error_log.json）
- `NEW_TERMS_FILE`: 新术语文件（默认：new_terms.json）

## 依赖项

根据 `pyproject.toml` 文件：

- Python >= 3.8
- iflow-sdk >= 0.1.0
- beautifulsoup4 >= 4.12.0

### 安装依赖
```bash
# 使用uv安装依赖（推荐）
uv sync

# 或者使用pip
pip install -r requirements.txt
```

## 工作流程

1. 扫描`source/OEBPS/`目录下的所有`text*.html`文件
2. 提取可翻译的HTML块（段落、标题等）
3. 使用上下文信息调用iFlow翻译代理
4. 保持原始HTML标签结构
5. 实时保存翻译进度
6. 生成翻译进度报告和错误日志

## 输出文件

- 翻译后的HTML文件保存在`translated/`目录
- `translate-checklist.md`显示翻译进度
- `paragraph_progress.json`记录段落完成状态
- `error_log.json`记录翻译错误
- `new_terms.json`记录新发现的术语

## 故障排除

- 如果翻译过程中断，程序会从上次进度继续
- 检查`error_log.json`了解翻译失败原因
- 确保`source/OEBPS/`目录包含有效的HTML文件
- 确保网络连接正常以便调用iFlow翻译服务

## 依赖管理

该项目使用 `uv` 进行依赖管理：

- `pyproject.toml`: 项目元数据和依赖声明
- `uv.lock`: 锁定依赖版本
- 使用 `uv sync` 安装依赖
- 使用 `uv run` 执行项目命令

## iFlow CLI SDK

iFlow CLI SDK 文档：https://platform.iflow.cn/cli/sdk/sdk-python