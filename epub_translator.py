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
SOURCE_DIR = Path("source/OEBPS")
TRANSLATED_DIR = Path("translated")
CHECKLIST_FILE = "translate-checklist.md"
GLOSSARY_FILE = "glossary.md"
PROGRESS_FILE = "paragraph_progress.json"
ERROR_LOG_FILE = "error_log.json"
NEW_TERMS_FILE = "new_terms.json"

MAX_RETRY = 3
TIMEOUT_SEC = 60.0
QUALITY_CHECK_INTERVAL = 5

# 确保输出目录存在
TRANSLATED_DIR.mkdir(exist_ok=True)

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

def build_context(blocks: List[str], idx: int) -> tuple:
    prev_block = blocks[idx - 1] if idx > 0 else ""
    curr_block = blocks[idx]
    next_block = blocks[idx + 1] if idx < len(blocks) - 1 else ""
    return prev_block, curr_block, next_block

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
    content = "# 日文书籍翻译进度追踪\n\n## 需要翻译的文件清单\n\n### HTML文件\n"
    for f in file_list:
        mark = "x" if f in completed_files else " "
        content += f"- [{mark}] {f}\n"
    content += "\n## 翻译进度统计\n"
    total = len(file_list)
    done = len(completed_files)
    percent = done / total * 100 if total > 0 else 0
    content += f"- 总文件数: {total}个HTML文件\n"
    content += f"- 已翻译: {done}个\n"
    content += f"- 待翻译: {total - done}个\n"
    content += f"- 完成度: {percent:.1f}%\n"
    with open(CHECKLIST_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

# ======================
# 核心翻译函数
# ======================

async def translate_block_with_agent(
    client: IFlowClient,
    current_block: str,
    prev_block: str = "",
    next_block: str = "",
    glossary: Dict[str, str] = None,
    max_retries: int = MAX_RETRY
) -> str:
    """
    调用 ja-zh-translator 翻译单个 HTML 块
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

    prompt = f"""你是一个专业的日中翻译专家（ja-zh-translator），请严格遵守：
- 仅输出翻译后的 HTML 段落，不要任何解释、注释或额外文本
- 保持原始 HTML 标签不变
- 使用中文标点（，。！？）
- 无日文字符残留
- 语气自然流畅，符合中文阅读习惯{glossary_text}

{context_prompt}

现在翻译以下段落：
{current_block}
"""

    for attempt in range(max_retries):
        try:
            await client.send_message(prompt)
            response = ""
            start_time = time.time()

            async for message in client.receive_messages():
                if isinstance(message, AssistantMessage):
                    response += message.chunk.text
                    # 防止无限等待
                    if time.time() - start_time > TIMEOUT_SEC:
                        raise SDKTimeoutError("翻译超时")
                elif isinstance(message, ToolCallMessage):
                    # 处理工具调用消息（如果有的话）
                    print(f"  🛠️ 工具调用: {message.label} (ID: {message.id})")
                    if message.content:
                        response += f"<!-- 工具调用结果: {message.content} -->"
                elif isinstance(message, PlanMessage):
                    # 处理计划消息（如果有的话）
                    print(f"  📋 计划消息: {len(message.entries)} 个计划项")
                    for entry in message.entries:
                        print(f"     - {entry.content}")
                elif isinstance(message, TaskFinishMessage):
                    break

            # 清理响应：只保留 HTML 块（简单策略）
            response = response.strip()
            if response.startswith("```") and response.endswith("```"):
                response = "\n".join(response.split("\n")[1:-1])

            # 基础验证
            if not response or "<" not in response:
                raise ValueError("无效翻译结果")

            return response

        except (Exception, asyncio.CancelledError) as e:
            print(f"  ⚠️ 翻译失败 (尝试 {attempt+1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                return f"<!-- TRANSLATION_FAILED: {current_block} -->"
            await asyncio.sleep(2)

# ======================
# 主流程
# ======================

async def main():
    print("🚀 启动 iFlow EPUB 翻译器（严格遵循用户 workflow）")

    # 加载状态
    progress = load_json(PROGRESS_FILE, {})
    error_log = load_json(ERROR_LOG_FILE, {"errors": []})
    new_terms = load_json(NEW_TERMS_FILE, {"discovered_terms": []})
    glossary = load_glossary()

    # 获取所有待翻译文件
    html_files = sorted([f.name for f in SOURCE_DIR.glob("text*.html")])
    if not html_files:
        print("❌ 未找到 source/OEBPS/text*.html 文件，请检查路径")
        return

    completed_files = set()

    # 初始化 checklist
    update_checklist(html_files, completed_files)

    async with IFlowClient() as client:
        for html_file in html_files:
            print(f"\n📄 处理文件: {html_file}")
            file_key = html_file

            # 初始化文件进度
            if file_key not in progress:
                progress[file_key] = {
                    "total_paragraphs": 0,
                    "completed": [],
                    "failed": [],
                    "current_position": 0
                }

            # 读取源文件
            source_path = SOURCE_DIR / html_file
            if not source_path.exists():
                print(f"  ⚠️ 文件不存在，跳过")
                continue

            original_content = source_path.read_text(encoding='utf-8')
            blocks = extract_translatable_blocks(original_content)
            progress[file_key]["total_paragraphs"] = len(blocks)

            # 准备目标内容（初始为原文）
            translated_content = original_content

            # 逐段处理
            for i, block in enumerate(blocks):
                if i in progress[file_key]["completed"]:
                    print(f"  ✅ 跳过已翻译段落 {i+1}/{len(blocks)}")
                    continue

                print(f"  🔤 翻译段落 {i+1}/{len(blocks)}")

                # 准备上下文
                prev_blk, curr_blk, next_blk = build_context(blocks, i)

                # 调用翻译
                translated_block = await translate_block_with_agent(
                    client, curr_blk, prev_blk, next_blk, glossary
                )

                # 替换到完整内容（只替换第一次出现）
                translated_content = translated_content.replace(curr_blk, translated_block, 1)

                # 更新进度
                progress[file_key]["completed"].append(i)
                progress[file_key]["current_position"] = i
                save_json(progress, PROGRESS_FILE)

                # 每5段保存一次文件 + 质量检查
                if (i + 1) % QUALITY_CHECK_INTERVAL == 0 or i == len(blocks) - 1:
                    # 保存文件
                    output_path = TRANSLATED_DIR / html_file
                    output_path.write_text(translated_content, encoding='utf-8')

                    # 质量检查
                    if contains_japanese(translated_block):
                        err_msg = f"段落 {i} 仍含日文字符"
                        print(f"  ❌ {err_msg}")
                        error_log["errors"].append({
                            "file": html_file,
                            "paragraph": i,
                            "error": err_msg,
                            "content": translated_block
                        })
                        save_json(error_log, ERROR_LOG_FILE)

                    if not check_chinese_punctuation(translated_block):
                        print(f"  ⚠️ 段落 {i} 可能使用了日文标点")

                    print(f"  💾 已保存 {html_file}（进度 {i+1}/{len(blocks)}）")

            # 文件完成
            completed_files.add(html_file)
            update_checklist(html_files, completed_files)
            print(f"✅ 完成文件: {html_file}")

    print("\n🎉 所有文件处理完毕！")
    print(f"输出目录: {TRANSLATED_DIR.absolute()}")

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
