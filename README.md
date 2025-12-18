# 日文EPUB书籍翻译项目

## 项目概述

日文EPUB书籍翻译项目是一个使用iFlow SDK进行日文电子书翻译的工具集，能够将日文EPUB格式的书籍自动翻译为中文，并保持原有的EPUB结构和样式。

## 功能特点

- 📚 **支持完整EPUB结构**：保留原始EPUB文件的所有结构和样式
- 🔤 **智能文本提取**：自动识别并提取可翻译的文本内容
- 🌐 **专业翻译质量**：基于iFlow SDK的专业日中翻译能力
- 📝 **术语表支持**：可使用自定义术语表确保翻译一致性
- 📊 **进度跟踪**：实时显示翻译进度和状态
- 📦 **一键打包**：翻译完成后可直接打包为标准EPUB格式
- 🔄 **自动重试机制**：网络问题时自动重试翻译请求
- 📈 **资源监控**：实时监控内存使用情况，优化性能
- 📋 **详细日志**：完整的翻译日志，便于故障排除

## 技术栈

- **Python**：3.8+
- **iFlow CLI SDK**：>=0.1.14
- **Beautiful Soup 4**：>=4.12.0
- **psutil**：>=5.8.0
- **python-dotenv**：>=1.0.0

## 环境配置指南

### 操作系统兼容性

- ✅ Windows 10/11
- ✅ macOS 10.15+
- ✅ Linux (Ubuntu 20.04+, CentOS 7+)

### 必要的依赖软件

1. **Python 3.8+**：确保已安装Python 3.8或更高版本
2. **uv**：用于管理Python依赖和项目环境
3. **iFlow CLI**：用于连接iFlow翻译服务

### 配置文件说明

项目使用`.env`文件进行配置，主要配置项包括：

```bash
# iFlow API 配置
# 请在此处设置您的 iFlow API Key
IFLOW_API_KEY=your_iflow_api_key_here

# 可选：iFlow 服务地址（默认为 ws://localhost:8090/acp）
# IFLOW_WS_URL=ws://localhost:8090/acp

# 可选：连接超时时间（默认600秒）
# IFLOW_TIMEOUT=600
```

## 安装步骤

### 1. 克隆项目

```bash
git clone <repository-url>
cd ja-translate
```

### 2. 安装依赖

使用uv安装项目依赖：

```bash
uv sync
```

### 3. 配置iFlow API Key

创建`.env`文件并填写您的iFlow API Key，配置文件样板如下：

#### 1. 首先创建.env文件

```bash
# Windows示例：
notepad .env
# Linux/macOS示例：
touch .env && vi .env
```

#### 2. 填写配置内容

在`.env`文件中添加以下内容，并替换为您的实际iFlow API Key：

```bash
# iFlow API 配置
# 请在此处设置您的 iFlow API Key
IFLOW_API_KEY=your_iflow_api_key_here

# 可选：iFlow 服务地址（默认为 ws://localhost:8090/acp）
# IFLOW_WS_URL=ws://localhost:8090/acp

# 可选：连接超时时间（默认600秒）
# IFLOW_TIMEOUT=600
```

#### 3. 保存并关闭文件

保存配置后，即可开始使用翻译功能。

## 启动与运行方法

### 翻译命令

直接运行Python脚本进行翻译：

```bash
# 翻译脚本
uv run python epub_translator.py
```

### 打包命令

翻译完成后，直接运行Python脚本打包为标准EPUB格式：

```bash
# 打包脚本
uv run python epub_packer.py
```

## 基本使用示例

### 标准工作流程

1. **准备EPUB文件**：将需要翻译的日文EPUB文件解压到`source`目录
2. **配置API Key**：在`.env`文件中设置您的iFlow API Key
3. **启动翻译**：运行`uv run python epub_translator.py`命令开始翻译
4. **监控进度**：查看翻译进度和日志输出
5. **打包结果**：翻译完成后，运行`uv run python epub_packer.py`命令打包为EPUB文件
6. **获取结果**：在项目根目录获取生成的中文EPUB文件


### 术语表使用

在项目根目录创建`glossary.md`文件，格式如下：

```markdown
# 术语表

| 日文术语 | 中文翻译 |
|---------|---------|
| 例えば   | 例如     |
| テスト   | 测试     |
```

翻译时会自动使用此术语表确保翻译一致性。

## 目录结构

```
ja-translate/
├── .env                 # 环境配置文件
├── .gitignore          # Git忽略文件
├── epub_packer.py      # EPUB打包脚本
├── epub_translator.py  # 核心翻译脚本
├── pyproject.toml      # 项目配置文件
├── README.md           # 项目说明文档
├── source/             # 源EPUB文件目录（需手动创建）
├── translated/         # 翻译结果目录（自动生成）
└── temp/               # 临时文件目录（自动生成）
```

## 故障排除指南


### 日志文件说明

项目生成的日志文件位于`temp`目录：

- `debug.log`：详细的调试日志
- `translation.log`：翻译过程日志
- `progress.json`：翻译进度数据
- `error_log.json`：错误日志记录
- `translate-checklist.md`：翻译进度清单


## 许可证

MIT License
