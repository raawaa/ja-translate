#!/usr/bin/env python3
"""
iFlow CLI 日文 EPUB 翻译器（严格遵循用户 workflow）
作者：Qwen + 用户规范
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional
from iflow_sdk import IFlowClient, AssistantMessage, TaskFinishMessage, TimeoutError as SDKTimeoutError, ToolCallMessage, PlanMessage

# ======================
# 配置区（按需修改）
# ======================
SOURCE_ROOT = Path("source")
TRANSLATED_ROOT = Path("translated")
SOURCE_OEBPS = SOURCE_ROOT / "OEBPS"  # 用于向后兼容
SOURCE_DIR = SOURCE_OEBPS  # 向后兼容：指向 source/OEBPS
TRANSLATED_DIR = TRANSLATED_ROOT  # 向后兼容：指向 translated
TEMP_DIR = Path("temp")  # 过程性文件存放目录
CHECKLIST_FILE = TEMP_DIR / "translate-checklist.md"
GLOSSARY_FILE = "glossary.md"  # 术语表保持在根目录
PROGRESS_FILE = TEMP_DIR / "paragraph_progress.json"
ERROR_LOG_FILE = TEMP_DIR / "error_log.json"
NEW_TERMS_FILE = TEMP_DIR / "new_terms.json"

MAX_RETRY = 3
TIMEOUT_SEC = 60.0
QUALITY_CHECK_INTERVAL = 5

# 确保输出目录存在
TRANSLATED_ROOT.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# ======================
# 辅助函数
# ======================

def load_glossary() -> Dict[str, str]:
    """加载术语表：日文 -> 中文"""
    if not os.path.exists(GLOSSARY_FILE):
        return {}
    glossary = {}
    with open(GLOSSARY_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        for line in lines[2:]:  # 跳过标题行
            if '|' in line:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    ja = parts[1].strip()
                    zh = parts[2].strip()
                    if ja and zh:
                        glossary[ja] = zh
    return glossary

def save_json(data, filepath: str):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_json(filepath: str, default):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def should_translate_file(file_path: Path) -> bool:
    """判断文件是否需要翻译"""
    # 只翻译 OEBPS 目录下的特定文本文件
    if "OEBPS" not in file_path.parts:
        return False
    
    ext = file_path.suffix.lower()
    return ext in ['.html', '.xhtml', '.ncx', '.opf']

def extract_translatable_blocks(html: str) -> List[str]:
    """提取所有可翻译的 HTML 块（保留标签）"""
    # 使用 BeautifulSoup 以更安全地解析 HTML，避免正则表达式处理复杂 HTML 时的问题
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        elements = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div'])
        return [str(elem) for elem in elements]
    except ImportError:
        # 如果没有安装BeautifulSoup，则使用正则表达式作为备选方案
        # 匹配 <p>, <h1>-<h6>, <div>（带 class 的常见正文容器）
        pattern = r'(<(p|h[1-6]|div)(?:\s[^>]*)?>.*?</\2>)'
        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        return [m[0] for m in matches]

def get_file_type(filename: str) -> str:
    """
    根据文件扩展名确定文件类型
    """
    if filename.endswith('.html') or filename.endswith('.xhtml'):
        return 'html'
    elif filename.endswith('.ncx'):
        return 'ncx'
    elif filename.endswith('.opf'):
        return 'opf'
    else:
        return 'other'

def build_context(blocks: List[str], idx: int) -> tuple:
    prev_block = blocks[idx - 1] if idx > 0 else ""
    curr_block = blocks[idx]
    next_block = blocks[idx + 1] if idx < len(blocks) - 1 else ""
    return prev_block, curr_block, next_block

def extract_translatable_blocks_ncx(content: str) -> List[str]:
    """提取NCX文件中的可翻译文本（章节标题等）"""
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(content)
        
        # 定义命名空间
        namespaces = {
            'ncx': 'http://www.daisy.org/z3986/2005/ncx/'
        }
        
        translatable_blocks = []
        
        # 提取所有<navLabel><text>中的内容
        for nav_point in root.findall('.//ncx:navLabel', namespaces):
            text_elem = nav_point.find('ncx:text', namespaces)
            if text_elem is not None and text_elem.text and contains_japanese(text_elem.text or ""):
                # 包含完整的标签结构以便正确替换
                block = f"<text>{text_elem.text}</text>"
                translatable_blocks.append(block)
        
        return translatable_blocks
    except ET.ParseError as e:
        print(f"解析NCX文件时出错: {e}")
        # 如果解析失败，尝试使用正则表达式
        import re
        matches = re.findall(r'<text>([^<]*)</text>', content)
        blocks = []
        for match in matches:
            if contains_japanese(match):
                blocks.append(f"<text>{match}</text>")
        return blocks
    except Exception as e:
        print(f"处理NCX文件时出错: {e}")
        return []

def extract_translatable_blocks_opf(content: str) -> List[str]:
    """提取OPF文件中的可翻译元数据"""
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(content)
        
        # 定义命名空间
        namespaces = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/'
        }
        
        translatable_blocks = []
        
        # 提取所有可能包含日文的元素
        elements_to_check = [
            'dc:title', 'dc:creator', 'dc:subject', 
            'dc:description', 'dc:publisher', 'dc:contributor'
        ]
        
        for elem_name in elements_to_check:
            for elem in root.findall(f'.//{elem_name}', namespaces):
                if elem.text and contains_japanese(elem.text):
                    # 保留标签结构，便于后续替换
                    tag_name = elem_name.split(':')[-1]  # 获取标签名（去掉命名空间前缀）
                    block = f"<{tag_name}>{elem.text}</{tag_name}>"
                    translatable_blocks.append(block)
        
        return translatable_blocks
    except ET.ParseError as e:
        print(f"解析OPF文件时出错: {e}")
        # 备选方案：使用正则表达式
        import re
        matches = re.findall(r'<(?:dc:)?(title|creator|subject|description|publisher|contributor)>([^<]*)</(?:dc:)?\1>', content)
        blocks = []
        for tag, content in matches:
            if contains_japanese(content):
                blocks.append(f"<{tag}>{content}</{tag}>")
        return blocks
    except Exception as e:
        print(f"处理OPF文件时出错: {e}")
        return []

def contains_japanese(text: str) -> bool:
    return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u3400-\u4DBF\u4E00-\u9FFF]', text))

def check_chinese_punctuation(text: str) -> bool:
    # 检查是否使用中文标点（简单规则）
    jp_punct = '。、・「」『』【】！？'
    for p in jp_punct:
        if p in text:
            return False
    return True

def update_checklist(file_list: List[str], completed_files: set):
    """更新 translate-checklist.md"""
    content = "# 日文书籍翻译进度追踪\n\n"
    
    # 按类型分组文件
    html_files = [f for f in file_list if f.endswith('.html')]
    ncx_files = [f for f in file_list if f.endswith('.ncx')]
    opf_files = [f for f in file_list if f.endswith('.opf')]
    other_files = [f for f in file_list if f not in html_files + ncx_files + opf_files]

    if html_files:
        content += "## HTML文件\n"
        for f in html_files:
            mark = "x" if f in completed_files else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    if ncx_files:
        content += "## 目录文件\n"
        for f in ncx_files:
            mark = "x" if f in completed_files else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    if opf_files:
        content += "## 元数据文件\n"
        for f in opf_files:
            mark = "x" if f in completed_files else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    if other_files:
        content += "## 其他文件\n"
        for f in other_files:
            mark = "x" if f in completed_files else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    content += "## 翻译进度统计\n"
    total = len(file_list)
    done = len(completed_files)
    percent = done / total * 100 if total > 0 else 0
    content += f"- 总文件数: {total}个文件\n"
    content += f"- 已翻译: {done}个\n"
    content += f"- 待翻译: {total - done}个\n"
    content += f"- 完成度: {percent:.1f}%\n"
    
    with open(CHECKLIST_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

# ======================
# 核心翻译函数
# ======================

async def translate_block(
    client: IFlowClient,
    current_block: str,
    prev_block: str = "",
    next_block: str = "",
    glossary: Dict[str, str] = None,
    max_retries: int = MAX_RETRY
) -> str:
    """
    翻译单个 HTML 块
    """
    # 构建术语提示
    glossary_text = ""
    if glossary:
        terms = "\n".join([f"{ja} → {zh}" for ja, zh in list(glossary.items())[:10]])  # 限制长度
        glossary_text = f"\n\n请优先使用以下术语翻译：\n{terms}"

    # 构建上下文（截断避免超长）
    context_prompt = ""
    if prev_block or next_block:
        context_parts = []
        if prev_block:
            clean_prev = re.sub(r'<[^>]+>', '', prev_block)[:30]
            context_parts.append(f"前一段：{clean_prev}...")
        if next_block:
            clean_next = re.sub(r'<[^>]+>', '', next_block)[:30]
            context_parts.append(f"后一段：{clean_next}...")
        context_prompt = "上下文：" + "；".join(context_parts)

    prompt = f"""你是一个专业的日中翻译专家，对两种语言的细微差别、文化背景和惯用表达有深入了解。请严格遵守：
- 仅输出翻译后的 HTML 段落，不要任何解释、注释或额外文本
- 保持原始 HTML 标签不变
- 使用中文标点（，。！？）
- 无日文字符残留
- 准确忠实于原文含义，同时确保中文表达自然流畅，符合中文阅读习惯
- 捕捉原文的语气、文化细微差别和隐含意义
- 注意处理敬语和正式程度，在中文中适当调整
- 考虑可能没有直接对等词的文化引用和表达{glossary_text}

{context_prompt}

现在翻译以下段落：
{current_block}
"""

    for attempt in range(max_retries):
        try:
            print(f"  📋 发送翻译请求 (尝试 {attempt+1}/{max_retries})")
            await client.send_message(prompt)
            response = ""
            start_time = time.time()
            message_count = 0
            tool_call_count = 0
            plan_message_count = 0
            current_agent_id = None
            sub_agents = set()

            async for message in client.receive_messages():
                message_count += 1
                
                if isinstance(message, AssistantMessage):
                    # 动态获取 agent_id
                    if not current_agent_id:
                        current_agent_id = message.agent_id or "default"
                        if message.agent_id:
                            agent_name = str(message.agent_id)
                            print(f"  🤖 当前 Agent: {agent_name} (ID: {message.agent_id})")
                        else:
                            print(f"  🤖 当前 Agent: 默认翻译代理")
                    
                    response += message.chunk.text
                    # 防止无限等待
                    if time.time() - start_time > TIMEOUT_SEC:
                        raise SDKTimeoutError("翻译超时")
                elif isinstance(message, ToolCallMessage):
                    tool_call_count += 1
                    # 动态获取工具调用信息
                    tool_name = getattr(message, 'label', 'Unknown')
                    tool_id = getattr(message, 'id', 'Unknown')
                    print(f"  🛠️ 工具调用 #{tool_call_count}: {tool_name}")
                    
                    # 检查是否有 sub agent 信息
                    if hasattr(message, 'agent_id') and message.agent_id:
                        sub_agents.add(message.agent_id)
                    
                    # 静默处理工具调用消息，不输出到翻译结果
                    pass
                elif isinstance(message, PlanMessage):
                    plan_message_count += 1
                    entries_count = len(message.entries) if hasattr(message, 'entries') else 0
                    print(f"  📋 计划消息 #{plan_message_count}: {entries_count} 个计划项")
                    # 静默处理计划消息，不输出到翻译结果
                    pass
                elif isinstance(message, TaskFinishMessage):
                    print(f"  ✅ 任务完成 (共接收 {message_count} 条消息)")
                    if sub_agents:
                        print(f"  🔄 使用的 Sub Agents: {', '.join(sub_agents)}")
                    # 任务完成消息，不输出到翻译结果
                    break

            # 清理响应：只保留 HTML 块（简单策略）
            response = response.strip()
            if response.startswith("```") and response.endswith("```"):
                response = "\n".join(response.split("\n")[1:-1])

            # 基础验证
            if not response or "<" not in response:
                raise ValueError("无效翻译结果")

            print(f"  📊 翻译完成: {len(response)} 字符")
            return response

        except (Exception, asyncio.CancelledError) as e:
            print(f"  ⚠️ 翻译失败 (尝试 {attempt+1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                return f"<!-- TRANSLATION_FAILED: {current_block} -->"
            await asyncio.sleep(2)

# ======================
# 主流程
# ======================

def extract_translatable_blocks_by_type(content: str, file_type: str) -> List[str]:
    """
    根据文件类型提取可翻译的文本块
    """
    if file_type == 'html':
        return extract_translatable_blocks(content)
    elif file_type == 'ncx':
        return extract_translatable_blocks_ncx(content)
    elif file_type == 'opf':
        return extract_translatable_blocks_opf(content)
    else:
        # 对于其他类型的文件，暂时返回空列表
        return []

def update_file_content_by_type(original_content: str, file_type: str, original_blocks: List[str], translated_blocks: List[str]) -> str:
    """
    根据翻译后的块更新原始文件内容
    """
    updated_content = original_content
    
    for i, (orig_block, trans_block) in enumerate(zip(original_blocks, translated_blocks)):
        if file_type == 'html':
            # 对于HTML，直接替换（第一次匹配）
            updated_content = updated_content.replace(orig_block, trans_block, 1)
        elif file_type == 'ncx':
            # 对于NCX，提取翻译后的文本，替换原始的text标签内容
            import re
            # 从翻译后的块中提取文本
            trans_match = re.search(r'<text>(.*?)</text>', trans_block)
            if trans_match:
                trans_text = trans_match.group(1)
                # 从原始块中提取原始文本
                orig_match = re.search(r'<text>(.*?)</text>', orig_block)
                if orig_match:
                    orig_text = orig_match.group(1)
                    # 替换原始内容中的对应部分
                    updated_content = updated_content.replace(
                        f"<text>{orig_text}</text>",
                        f"<text>{trans_text}</text>",
                        1
                    )
        elif file_type == 'opf':
            # 对于OPF，提取翻译后的文本，替换原始的标签内容
            import re
            # 识别标签类型
            tag_match = re.search(r'<(\w+)>', orig_block)
            if tag_match:
                tag_name = tag_match.group(1)
                # 从翻译后的块中提取文本
                trans_match = re.search(f'<{tag_name}>(.*?)</{tag_name}>', trans_block)
                if trans_match:
                    trans_text = trans_match.group(1)
                    # 从原始块中提取原始文本
                    orig_match = re.search(f'<{tag_name}>(.*?)</{tag_name}>', orig_block)
                    if orig_match:
                        orig_text = orig_match.group(1)
                        # 替换原始内容中的对应部分
                        updated_content = updated_content.replace(
                            f"<{tag_name}>{orig_text}</{tag_name}>",
                            f"<{tag_name}>{trans_text}</{tag_name}>",
                            1
                        )
    
    return updated_content

async def main():
    print("🚀 启动 iFlow EPUB 翻译器（完整EPUB结构翻译）")
    print("📋 翻译模式: 上下文感知翻译，保持HTML结构")
    print("🔧 配置: 最大重试次数={}, 超时时间={}秒".format(MAX_RETRY, TIMEOUT_SEC))

    # 加载状态
    progress = load_json(PROGRESS_FILE, {})
    error_log = load_json(ERROR_LOG_FILE, {"errors": []})
    new_terms = load_json(NEW_TERMS_FILE, {"discovered_terms": []})
    glossary = load_glossary()
    
    # 迁移现有进度数据：将简单文件名键转换为相对路径键
    if progress:
        new_progress = {}
        for old_key, value in progress.items():
            # 尝试在SOURCE_ROOT下查找文件
            found = False
            for file_path in SOURCE_ROOT.rglob(old_key):
                if file_path.is_file():
                    rel_path = file_path.relative_to(SOURCE_ROOT)
                    new_progress[str(rel_path)] = value
                    found = True
                    break
            if not found:
                # 如果找不到，可能是文件不存在或路径已变化，丢弃该进度项
                print(f"⚠️  进度数据迁移：未找到文件 '{old_key}'，丢弃其进度")
        progress = new_progress
        save_json(progress, PROGRESS_FILE)  # 立即保存迁移后的数据
    
    # 获取所有待翻译文件（递归遍历整个source目录）
    all_files = []    # 递归遍历整个 source/ 目录树
    for file_path in SOURCE_ROOT.rglob("*"):
        if file_path.is_file():
            # 获取相对于 SOURCE_ROOT 的相对路径
            rel_path = file_path.relative_to(SOURCE_ROOT)
            all_files.append(str(rel_path))
    
    # 按字母顺序排序，保证处理顺序一致
    all_files.sort()
        
    if not all_files:
        print("❌ 未找到 source/ 目录中的文件，请检查路径")
        return
    completed_files = set()

    # 初始化 checklist（扩展后的逻辑）
    update_checklist(all_files, completed_files)

    async with IFlowClient() as client:
        print("🔗 已连接到 iFlow 服务")
        
        # 动态获取客户端配置信息
        try:
            # 获取客户端配置信息
            if hasattr(client, 'options') and client.options:
                options = client.options
                url = getattr(options, 'url', 'Unknown')
                timeout = getattr(options, 'timeout', 'Unknown')
                log_level = getattr(options, 'log_level', 'Unknown')
                print(f"📊 连接配置: URL={url}, 超时={timeout}s, 日志级别={log_level}")
                
                # 检查是否有 MCP 服务器配置
                if hasattr(options, 'mcp_servers') and options.mcp_servers:
                    print(f"🔧 MCP 服务器: {len(options.mcp_servers)} 个已配置")
                    for server in options.mcp_servers:
                        server_name = server.get('name', 'Unknown') if isinstance(server, dict) else str(server)
                        print(f"     - {server_name}")
                else:
                    print("🔧 MCP 服务器: 无额外配置")
            else:
                print("📊 配置信息: 使用默认配置")
        except Exception as e:
            print(f"📊 配置信息: 获取失败 - {str(e)}")
        
        for filename in all_files:
            file_type = get_file_type(filename)
            print(f"\n📄 处理文件: {filename} (类型: {file_type})")
            file_key = filename

            # 构建源路径和目标路径
            source_path = SOURCE_ROOT / filename
            dest_path = TRANSLATED_ROOT / filename

            # 确保目标目录存在
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            if not source_path.exists():
                print(f"  ⚠️ 文件不存在，跳过")
                continue

            # 初始化文件进度
            if file_key not in progress:
                progress[file_key] = {
                    "type": file_type,
                    "total_blocks": 0,
                    "completed": [],
                    "failed": [],
                    "current_position": 0
                }

            # 根据文件类型决定如何处理
            if file_type in ['html', 'ncx', 'opf']:
                # 可翻译的文本文件
                original_content = source_path.read_text(encoding='utf-8')
                
                # 根据文件类型提取可翻译块
                blocks = extract_translatable_blocks_by_type(original_content, file_type)
                progress[file_key]["total_blocks"] = len(blocks)

                # 准备目标内容（初始为原文）
                translated_content = original_content

                # 如果有需要翻译的块，则进行翻译
                if len(blocks) > 0:
                    # 用于存储已翻译的块
                    translated_blocks = [""] * len(blocks)

                    # 逐块处理
                    for i, block in enumerate(blocks):
                        if i in progress[file_key]["completed"]:
                            print(f"  ✅ 跳过已翻译块 {i+1}/{len(blocks)}")
                            # 如果块已翻译，从文件中恢复已翻译的块内容
                            translated_blocks[i] = block
                            continue

                        print(f"  🔤 翻译块 {i+1}/{len(blocks)}")

                        # 准备上下文
                        prev_blk, curr_blk, next_blk = build_context(blocks, i)

                        # 调用翻译
                        translated_block = await translate_block(
                            client, curr_blk, prev_blk, next_blk, glossary
                        )

                        # 存储翻译后的块
                        translated_blocks[i] = translated_block

                        # 更新完整文件内容
                        translated_content = update_file_content_by_type(
                            original_content, file_type, 
                            blocks[:i+1], translated_blocks[:i+1]
                        )

                        # 更新进度
                        progress[file_key]["completed"].append(i)
                        progress[file_key]["current_position"] = i
                        save_json(progress, PROGRESS_FILE)

                        # 每5块保存一次文件 + 质量检查
                        if (i + 1) % QUALITY_CHECK_INTERVAL == 0 or i == len(blocks) - 1:
                            # 保存文件
                            dest_path.write_text(translated_content, encoding='utf-8')

                            # 质量检查
                            if contains_japanese(translated_block):
                                err_msg = f"块 {i} 仍含日文字符"
                                print(f"  ❌ {err_msg}")
                                error_log["errors"].append({
                                    "file": filename,
                                    "block": i,
                                    "error": err_msg,
                                    "content": translated_block
                                })
                                save_json(error_log, ERROR_LOG_FILE)

                            if not check_chinese_punctuation(translated_block):
                                print(f"  ⚠️ 块 {i} 可能使用了日文标点")

                            print(f"  💾 已保存 {filename}（进度 {i+1}/{len(blocks)}）")
                else:
                    print(f"  ℹ️ 文件中没有需要翻译的内容: {filename}")
                    # 仍然保存文件
                    dest_path.write_text(original_content, encoding='utf-8')
            else:
                # 非文本文件（如图片、CSS等），直接复制
                print(f"  📁 复制非文本文件: {filename}")
                import shutil
                shutil.copy2(source_path, dest_path)

            # 文件完成
            completed_files.add(filename)
            update_checklist(all_files, completed_files)
            print(f"✅ 完成文件: {filename}")

    print("\n🎉 所有文件处理完毕！")
    print(f"输出目录: {TRANSLATED_ROOT.absolute()}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 用户中断了翻译过程")
        print("✅ 进度已保存，可以随时恢复")
    except Exception as e:
        print(f"\n❌ 程序异常终止: {str(e)}")
        import traceback
        traceback.print_exc()
