#!/usr/bin/env python3
"""
EPUB 打包脚本 - 将翻译完成的书籍打包成标准 EPUB 格式
作者：Qwen + 用户规范
"""

import argparse
import os
import zipfile
from pathlib import Path
import shutil

# EPUB 目录结构常量
MIMETYPE = "application/epub+zip"
REQUIRED_FILES = [
    "mimetype",
    "META-INF/container.xml",
    "OEBPS/content.opf",
    "OEBPS/toc.ncx"
]


def check_directory_structure(translated_dir: Path) -> bool:
    """
    检查翻译目录是否包含完整的 EPUB 结构
    
    Args:
        translated_dir: 翻译目录路径
    
    Returns:
        bool: 如果目录结构完整返回 True，否则返回 False
    """
    print(f"📁 检查目录结构: {translated_dir}")
    
    # 检查所有必需的文件是否存在
    all_exist = True
    for file_path in REQUIRED_FILES:
        full_path = translated_dir / file_path
        if not full_path.exists():
            print(f"❌ 缺失必需文件: {file_path}")
            all_exist = False
        else:
            print(f"✅ 找到文件: {file_path}")
    
    return all_exist


def create_epub(translated_dir: Path, output_path: Path) -> bool:
    """
    创建标准 EPUB 文件
    
    Args:
        translated_dir: 翻译目录路径
        output_path: 输出 EPUB 文件路径
    
    Returns:
        bool: 如果打包成功返回 True，否则返回 False
    """
    try:
        print(f"📦 开始打包 EPUB 文件...")
        print(f"📁 源目录: {translated_dir}")
        print(f"💾 输出文件: {output_path}")
        
        # 创建 ZIP 文件，使用 DEFLATED 压缩算法
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
            # 1. 首先添加 mimetype 文件，不压缩
            print(f"📄 添加 mimetype 文件 (不压缩)...")
            mimetype_path = translated_dir / "mimetype"
            with open(mimetype_path, 'r', encoding='utf-8') as f:
                mimetype_content = f.read().strip()
            
            # 确保 mimetype 内容正确
            if mimetype_content != MIMETYPE:
                print(f"⚠️ 修正 mimetype 内容为: {MIMETYPE}")
                mimetype_content = MIMETYPE
                
            # 添加 mimetype 文件，设置压缩方法为 STORED (不压缩)
            epub.writestr(zipfile.ZipInfo("mimetype"), mimetype_content, zipfile.ZIP_STORED)
            
            # 2. 添加 META-INF 目录下的所有文件
            print(f"📁 添加 META-INF 目录...")
            meta_inf_dir = translated_dir / "META-INF"
            for root, dirs, files in os.walk(meta_inf_dir):
                for file in files:
                    file_path = Path(root) / file
                    # 计算相对路径，确保以 META-INF/ 开头
                    rel_path = file_path.relative_to(translated_dir)
                    print(f"   ✅ 添加: {rel_path}")
                    epub.write(file_path, rel_path)
            
            # 3. 添加 OEBPS 目录下的所有文件
            print(f"📁 添加 OEBPS 目录...")
            oebps_dir = translated_dir / "OEBPS"
            for root, dirs, files in os.walk(oebps_dir):
                for file in files:
                    file_path = Path(root) / file
                    # 计算相对路径，确保以 OEBPS/ 开头
                    rel_path = file_path.relative_to(translated_dir)
                    print(f"   ✅ 添加: {rel_path}")
                    epub.write(file_path, rel_path)
        
        print(f"🎉 EPUB 文件打包成功: {output_path}")
        print(f"📏 文件大小: {output_path.stat().st_size / 1024:.2f} KB")
        return True
    except Exception as e:
        print(f"❌ 打包 EPUB 文件失败: {e}")
        return False


def copy_source_structure(source_dir: Path, translated_dir: Path) -> bool:
    """
    从源目录复制完整的 EPUB 结构到翻译目录
    仅复制翻译目录中不存在的文件
    
    Args:
        source_dir: 源 EPUB 目录路径
        translated_dir: 翻译目录路径
    
    Returns:
        bool: 如果复制成功返回 True，否则返回 False
    """
    try:
        print(f"📋 从源目录复制 EPUB 结构...")
        print(f"   源: {source_dir}")
        print(f"   目标: {translated_dir}")
        
        # 创建目标目录（如果不存在）
        translated_dir.mkdir(parents=True, exist_ok=True)
        
        # 复制所有文件和目录
        for root, dirs, files in os.walk(source_dir):
            for dir_name in dirs:
                source_dir_path = Path(root) / dir_name
                relative_path = source_dir_path.relative_to(source_dir)
                target_dir_path = translated_dir / relative_path
                target_dir_path.mkdir(parents=True, exist_ok=True)
        
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                source_file_path = Path(root) / file
                relative_path = source_file_path.relative_to(source_dir)
                target_file_path = translated_dir / relative_path
                
                # 只复制目标目录中不存在的文件
                if not target_file_path.exists():
                    shutil.copy2(source_file_path, target_file_path)
                    print(f"   ✅ 复制: {relative_path}")
        
        print(f"✅ 源结构复制完成")
        return True
    except Exception as e:
        print(f"❌ 复制源结构失败: {e}")
        return False


def main():
    """
    主函数 - 解析命令行参数并执行打包操作
    """
    parser = argparse.ArgumentParser(description="将翻译完成的书籍打包成标准 EPUB 格式")
    
    # 输入目录参数
    parser.add_argument(
        "--input", "-i", 
        type=Path, 
        default=Path("translated"),
        help="翻译目录路径 (默认: translated)"
    )
    
    # 输出文件参数
    parser.add_argument(
        "--output", "-o", 
        type=Path, 
        help="输出 EPUB 文件路径 (默认: translated/[书名].epub)"
    )
    
    # 源目录参数（用于复制缺失文件）
    parser.add_argument(
        "--source", 
        type=Path, 
        default=Path("source"),
        help="源 EPUB 目录路径 (默认: source)"
    )
    
    # 强制覆盖参数
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制覆盖输出文件"
    )
    
    args = parser.parse_args()
    
    # 处理输入目录
    translated_dir = args.input.resolve()
    if not translated_dir.exists():
        print(f"❌ 输入目录不存在: {translated_dir}")
        return 1
    
    # 处理源目录
    source_dir = args.source.resolve()
    if not source_dir.exists():
        print(f"❌ 源目录不存在: {source_dir}")
        return 1
    
    # 处理输出文件路径
    if args.output:
        output_path = args.output.resolve()
    else:
        # 默认输出文件名：translated/[书名].epub
        # 从 content.opf 中提取书名（如果可能）
        opf_path = translated_dir / "OEBPS/content.opf"
        book_title = "book"
        if opf_path.exists():
            try:
                with open(opf_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                # 简单提取书名（实际项目中应使用 XML 解析）
                import re
                title_match = re.search(r'<dc:title[^>]*>([^<]+)</dc:title>', content)
                if title_match:
                    book_title = title_match.group(1).strip()
                    # 清理文件名
                    book_title = re.sub(r'[<>:"/\\|?*]', '_', book_title)
            except Exception as e:
                print(f"⚠️ 无法提取书名，使用默认名称: {e}")
        
        # 默认输出到根目录，而不是 translated 目录
        output_path = Path.cwd() / f"{book_title}.epub"
    
    # 检查输出文件是否已存在
    if output_path.exists() and not args.force:
        print(f"❌ 输出文件已存在: {output_path}")
        print(f"   使用 --force 参数强制覆盖")
        return 1
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. 复制源目录结构到翻译目录（只复制缺失文件）
    copy_source_structure(source_dir, translated_dir)
    
    # 2. 检查目录结构
    if not check_directory_structure(translated_dir):
        print("❌ 目录结构不完整，无法打包 EPUB")
        return 1
    
    # 3. 打包 EPUB
    if create_epub(translated_dir, output_path):
        print(f"\n🎉 打包完成！")
        print(f"📦 生成的 EPUB 文件: {output_path}")
        return 0
    else:
        print(f"\n❌ 打包失败")
        return 1


if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
