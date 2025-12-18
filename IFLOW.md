# 日文书籍翻译项目

## 项目概述

这是一个基于iFlow CLI智能代理系统的日文EPUB书籍翻译项目，专门用于将日文EPUB格式的书籍内容翻译成中文。

项目采用先进的Agent架构，通过专业的日中翻译Agent实现高质量的文学翻译，确保翻译结果既准确又符合中文阅读习惯。

**iFlow CLI SDK 文档**: https://platform.iflow.cn/cli/sdk/sdk-python
项目使用uv来管理python虚拟环境、依赖、运行。

## 项目结构

```
ja-translate/
├── IFLOW.md                 # 项目文档（本文件）
├── epub_translator.py       # EPUB翻译器主程序
├── pyproject.toml           # 项目配置文件
├── .gitignore               # Git忽略文件配置
├── .iflow/                  # iFlow CLI配置目录
│   └── agents/
│       └── ja-zh-translator.md  # 日中翻译Agent配置
├── .trae/                   # 项目规则目录
│   └── rules/
│       └── project_rules.md # 项目规则定义
├── .venv/                   # Python虚拟环境
├── source/                  # 源文件目录（EPUB解压后的内容）
│   ├── META-INF/            # EPUB元数据目录
│   └── OEBPS/               # EPUB内容目录
├── translated/              # 翻译输出目录
│   ├── META-INF/            # 翻译后的元数据
│   └── OEBPS/               # 翻译后的内容
├── temp/                    # 临时文件目录（进度、日志等）
│   ├── translate-checklist.md      # 翻译进度清单
│   ├── paragraph_progress.json     # 段落进度记录
│   ├── error_log.json              # 错误日志
│   └── new_terms.json              # 新发现的术语
├── glossary.md              # 术语表文件（可选）
└── __pycache__/             # Python缓存目录
```

## 核心功能

### EPUB翻译器 (epub_translator.py)

#### 主要特性
- **完整EPUB结构翻译**: 递归遍历整个source目录，处理所有文件类型
- **多格式支持**: 
  - HTML/XHTML文件：提取段落、标题等文本块
  - NCX文件：翻译目录章节标题
  - OPF文件：翻译元数据（书名、作者、描述等）
  - 其他文件：直接复制（CSS、图片等）
- **智能翻译引擎**:
  - 调用iFlow的ja-zh-translator智能代理
  - 保持原始HTML/XML标签结构
  - 上下文感知翻译（前一段、当前段、后一段）
  - 术语表支持（优先使用预定义术语）
- **可靠性保证**:
  - 连接重试机制（最多5次重试，递增延迟）
  - 断点续传功能（记录翻译进度）
  - 翻译失败自动重试（最多3次）
  - 超时保护（默认60秒）
- **质量控制**:
  - 检测日文残留字符
  - 验证中文标点符号使用
  - 术语一致性检查
  - 每5段自动保存和质量检查
- **进度管理**:
  - 实时更新翻译进度清单
  - 记录每个文件和段落的完成状态
  - 错误日志记录和分析
  - 新术语发现和记录

#### 技术实现
- 使用BeautifulSoup4解析HTML结构
- 使用xml.etree.ElementTree处理NCX和OPF文件
- 异步IO处理（asyncio）提高效率
- 智能块提取和上下文构建
- 动态Agent信息获取和日志记录

### 翻译质量保证
- 使用中文标点符号（，。！？等）
- 保持原文语气自然流畅
- 术语一致性检查
- 翻译结果验证
- 文化细微差别处理
- 敬语和正式程度适当调整

## 使用方法

### 1. 环境准备

#### 安装依赖
```bash
# 使用uv安装依赖（推荐）
uv sync

# 或者使用pip
pip install -e .
```

#### 配置iFlow CLI
确保已安装并配置iFlow CLI，参考：https://platform.iflow.cn/cli/sdk/sdk-python

### 2. 准备EPUB文件
1. 将待翻译的EPUB文件解压到`source/`目录
2. 确保EPUB内容位于`source/OEBPS/`目录下
3. 元数据文件应位于`source/META-INF/`目录

### 3. 配置术语表（可选）

创建`glossary.md`文件，格式如下：

```markdown
# 术语表
| 日文 | 中文 |
|------|------|
| 魔法 | 魔法 |
| 勇者 | 勇者 |
| 冒険 | 冒险 |
```

### 4. 运行翻译

```bash
# 方法1：使用uv运行（推荐）
uv run python epub_translator.py

# 方法2：直接运行
python epub_translator.py

# 方法3：使用项目脚本
uv run translate
```

### 5. 监控进度

翻译过程中可以查看以下文件了解进度：

- `temp/translate-checklist.md` - 翻译进度清单（实时更新）
- `temp/paragraph_progress.json` - 段落级进度记录
- `temp/error_log.json` - 错误日志
- `temp/new_terms.json` - 新发现的术语

### 6. 中断和恢复

- 使用 `Ctrl+C` 可以安全中断翻译
- 进度会自动保存
- 重新运行程序会从上次中断处继续

## 配置参数

可在`epub_translator.py`顶部修改以下配置：

### 目录配置
- `SOURCE_ROOT`: 源文件根目录（默认：`source/`）
- `TRANSLATED_ROOT`: 翻译输出根目录（默认：`translated/`）
- `SOURCE_OEBPS`: 源文件OEBPS目录（默认：`source/OEBPS/`）
- `TEMP_DIR`: 临时文件目录（默认：`temp/`）

### 文件路径配置
- `CHECKLIST_FILE`: 进度清单文件（默认：`temp/translate-checklist.md`）
- `GLOSSARY_FILE`: 术语表文件（默认：`glossary.md`）
- `PROGRESS_FILE`: 进度文件（默认：`temp/paragraph_progress.json`）
- `ERROR_LOG_FILE`: 错误日志文件（默认：`temp/error_log.json`）
- `NEW_TERMS_FILE`: 新术语文件（默认：`temp/new_terms.json`）

### 性能配置
- `MAX_RETRY`: 翻译失败最大重试次数（默认：`3`）
- `TIMEOUT_SEC`: 单次翻译超时时间（默认：`60.0`秒）
- `QUALITY_CHECK_INTERVAL`: 质量检查和保存间隔（默认：每`5`段）

## 依赖项

根据 `pyproject.toml` 文件：

- **Python** >= 3.8
- **iflow-cli-sdk** >= 0.1.14
- **beautifulsoup4** >= 4.12.0

## 工作流程

### 完整翻译流程

1. **初始化阶段**
   - 加载术语表（glossary.md）
   - 加载翻译进度（paragraph_progress.json）
   - 加载错误日志（error_log.json）
   - 创建必要的输出目录

2. **连接iFlow服务**
   - 使用重试机制连接（最多5次）
   - 递增延迟策略（3秒起始，每次×1.5）
   - 获取并显示连接配置信息

3. **文件扫描**
   - 递归遍历`source/`目录
   - 识别所有文件类型
   - 按字母顺序排序处理

4. **文件处理**
   - **HTML/XHTML文件**:
     - 使用BeautifulSoup提取文本块（p, h1-h6, div）
     - 保持HTML标签结构
     - 逐块翻译并替换
   - **NCX文件**:
     - 解析XML结构
     - 提取章节标题（`<text>`标签）
     - 翻译并更新
   - **OPF文件**:
     - 解析元数据
     - 翻译书名、作者、描述等
     - 更新元数据
   - **其他文件**:
     - 直接复制到输出目录

5. **翻译处理**
   - 构建上下文（前一段、当前段、后一段）
   - 应用术语表
   - 调用iFlow翻译代理
   - 验证翻译质量
   - 记录翻译进度

6. **质量检查**
   - 检测日文字符残留
   - 验证中文标点使用
   - 每5段执行一次检查
   - 记录问题到错误日志

7. **进度保存**
   - 每5段保存一次文件
   - 实时更新进度JSON
   - 更新翻译清单
   - 记录错误信息

8. **完成处理**
   - 生成完整的翻译清单
   - 统计翻译结果
   - 显示输出目录位置

## 输出文件说明

### 翻译结果
- `translated/` - 翻译后的完整EPUB结构
  - 保持与源文件相同的目录结构
  - 所有文本文件已翻译
  - 非文本文件原样复制

### 进度和日志
- `temp/translate-checklist.md` - 翻译进度清单
  - 按文件类型分组
  - 显示完成状态
  - 统计完成百分比
  
- `temp/paragraph_progress.json` - 段落级进度
  ```json
  {
    "相对路径/文件名.html": {
      "type": "html",
      "total_blocks": 100,
      "completed": [0, 1, 2, ...],
      "failed": [],
      "current_position": 50
    }
  }
  ```

- `temp/error_log.json` - 错误日志
  ```json
  {
    "errors": [
      {
        "file": "文件名",
        "block": 块索引,
        "error": "错误描述",
        "content": "问题内容"
      }
    ]
  }
  ```

- `temp/new_terms.json` - 新发现的术语
  ```json
  {
    "discovered_terms": [
      {"japanese": "日文", "context": "上下文"}
    ]
  }
  ```

## 故障排除

### 常见问题

1. **连接失败**
   - 检查网络连接
   - 确认iFlow CLI配置正确
   - 查看重试日志，等待自动重试

2. **翻译中断**
   - 程序会自动保存进度
   - 重新运行即可从断点继续
   - 检查`temp/paragraph_progress.json`确认进度

3. **翻译质量问题**
   - 检查`temp/error_log.json`查看具体问题
   - 调整术语表改善术语翻译
   - 修改`QUALITY_CHECK_INTERVAL`增加检查频率

4. **文件未找到**
   - 确保EPUB已正确解压到`source/`目录
   - 检查目录结构是否包含`OEBPS/`
   - 查看控制台输出的文件列表

5. **超时错误**
   - 增大`TIMEOUT_SEC`参数
   - 检查网络稳定性
   - 查看iFlow服务状态

### 调试技巧

- 查看控制台输出了解当前处理进度
- 检查`temp/`目录下的日志文件
- 使用`temp/translate-checklist.md`快速了解整体进度
- 对比`source/`和`translated/`目录验证翻译结果

## 高级功能

### 自定义翻译提示词

在`translate_block()`函数中可以修改翻译提示词，调整翻译风格：

```python
prompt = f"""你是一个专业的日中翻译专家...
[自定义提示词]
"""
```

### 扩展文件类型支持

在`get_file_type()`和相关提取函数中添加新的文件类型支持：

```python
def get_file_type(filename: str) -> str:
    if filename.endswith('.新格式'):
        return '新类型'
    # ...
```

### 调整质量检查规则

修改`check_chinese_punctuation()`和相关验证函数：

```python
def check_chinese_punctuation(text: str) -> bool:
    # 自定义检查逻辑
    pass
```

## 项目管理

### 依赖管理

该项目使用 `uv` 进行依赖管理：

```bash
# 安装依赖
uv sync

# 添加新依赖
uv add package-name

# 更新依赖
uv lock --upgrade

# 运行脚本
uv run translate
```

### 版本控制

- 使用Git进行版本控制
- `.gitignore`已配置忽略临时文件和输出目录
- 建议定期提交进度

## 技术架构

### 核心组件

1. **IFlowClient** - iFlow SDK客户端
   - 异步通信
   - 自动重连
   - 消息流处理

2. **文件处理器**
   - HTML解析器（BeautifulSoup）
   - XML解析器（ElementTree）
   - 文件类型识别

3. **翻译引擎**
   - 上下文构建
   - 术语表应用
   - 质量验证

4. **进度管理**
   - JSON持久化
   - 断点续传
   - 状态追踪

### 设计模式

- **异步编程**: 使用asyncio提高IO效率
- **重试机制**: 指数退避策略处理临时故障
- **状态持久化**: JSON文件保存进度和日志
- **模块化设计**: 功能分离，易于扩展

## 参考资源

- **iFlow CLI SDK文档**: https://platform.iflow.cn/cli/sdk/sdk-python
- **BeautifulSoup文档**: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
- **EPUB规范**: http://idpf.org/epub

## 许可证

请根据项目实际情况添加许可证信息。

## 贡献指南

欢迎提交Issue和Pull Request改进项目！
