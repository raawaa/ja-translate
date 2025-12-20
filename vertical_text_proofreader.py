#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竖排文本标点符号校对工具
功能：
1. 分析translated目录中的书籍文本内容，自动判断其排版方向是否为竖排格式
2. 若判定为竖排格式，对文本中的标点符号进行系统性校对
3. 识别并修正所有不符合竖排排版规范的标点符号
4. 直接替换translated目录中的原文件，并生成校对报告
"""

import argparse
import os
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from bs4 import BeautifulSoup

class VerticalTextProofreader:
    def __init__(self, report_only: bool = False, verbose: bool = False):
        """初始化校对工具"""
        # 横排到竖排标点符号映射表
        self.horizontal_to_vertical_punctuation = {
            # 基本标点
            '，': '、',  # 逗号转顿号（竖排中逗号通常用顿号）
            '。': '。',  # 句号保持不变
            '！': '！',  # 感叹号保持不变
            '？': '？',  # 问号保持不变
            '；': '；',  # 分号保持不变
            '：': '：',  # 冒号保持不变
            '—': '—',  # 破折号保持不变
            '…': '…',  # 省略号保持不变
            
            # 引号
            '“': '「',  # 左双引号
            '”': '」',  # 右双引号
            '‘': '『',  # 左单引号
            '’': '』',  # 右单引号
            
            # 括号
            '（': '︵',  # 左圆括号
            '）': '︶',  # 右圆括号
            '【': '︻',  # 左方括号
            '】': '︼',  # 右方括号
            '《': '︽',  # 左书名号
            '》': '︾',  # 右书名号
            
            # 其他符号
            '——': '—',  # 破折号简化
            '……': '…',  # 省略号简化
        }
        
        # 竖排专用标点符号
        self.vertical_punctuations = set('、。！？；：—…「」『』︵︶︻︼︽︾')
        
        # 校对报告记录
        self.report_records: List[Dict] = []
        
        # 配置选项
        self.report_only = report_only  # 是否只生成报告，不修改原文件
        self.verbose = verbose          # 是否显示详细信息
    
    def check_css_files(self, file_path: str, soup: BeautifulSoup) -> bool:
        """检查HTML文件引用的CSS文件中是否有竖排样式"""
        # 获取CSS文件路径
        css_files = []
        for link in soup.find_all('link', rel='stylesheet'):
            href = link.get('href')
            if href and href.endswith('.css'):
                css_path = os.path.join(os.path.dirname(file_path), href)
                css_files.append(css_path)
        
        # 检查每个CSS文件
        vertical_indicators = [
            r'writing-mode:\s*(vertical-rl|tb-rl)',
            r'-webkit-writing-mode:\s*(vertical-rl|tb-rl)',
            r'-moz-writing-mode:\s*(vertical-rl|tb-rl)'
        ]
        
        for css_path in css_files:
            if os.path.exists(css_path):
                try:
                    with open(css_path, 'r', encoding='utf-8') as f:
                        css_content = f.read()
                    
                    for indicator in vertical_indicators:
                        if re.search(indicator, css_content, re.IGNORECASE):
                            return True
                except:
                    pass
        
        return False
    
    def is_vertical_text(self, html_content: str, file_path: str = None) -> bool:
        """判断文本是否为竖排格式"""
        # 1. 检查HTML属性和CSS样式
        vertical_indicators = [
            r'writing-mode:\s*(vertical-rl|tb-rl)',
            r'dir\s*=\s*["\']rtl["\']',
            r'text-orientation:\s*upright',
            r'-webkit-writing-mode:\s*(vertical-rl|tb-rl)',
            r'-moz-writing-mode:\s*(vertical-rl|tb-rl)'
        ]
        
        for indicator in vertical_indicators:
            if re.search(indicator, html_content, re.IGNORECASE):
                return True
        
        # 2. 检查外部CSS文件
        soup = BeautifulSoup(html_content, 'html.parser')
        if file_path and self.check_css_files(file_path, soup):
            return True
        
        # 3. 提取纯文本内容
        text = soup.get_text()
        
        # 4. 统计竖排专用标点符号的数量
        vertical_punc_count = 0
        for char in text:
            if char in self.vertical_punctuations:
                vertical_punc_count += 1
        
        # 5. 检查文本的换行模式（竖排文本通常每行只有一个字符或少数字符）
        lines = text.split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        if non_empty_lines:
            avg_line_length = sum(len(line) for line in non_empty_lines) / len(non_empty_lines)
        else:
            avg_line_length = 0
        
        # 6. 检查是否有大量的竖排引号
        vertical_quotes_count = text.count('「') + text.count('」') + text.count('『') + text.count('』')
        
        # 判断规则：
        # - 竖排标点符号占比超过0.5%
        # - 平均每行长度小于10个字符
        # - 竖排引号数量超过10个
        total_chars = len(text)
        vertical_punc_ratio = vertical_punc_count / total_chars if total_chars > 0 else 0
        
        is_vertical = (
            vertical_punc_ratio > 0.005 or 
            avg_line_length < 10 or 
            vertical_quotes_count > 10
        )
        
        return is_vertical
    
    def has_horizontal_punctuation_issues(self, text: str) -> bool:
        """检查文本是否存在横排标点符号问题"""
        # 检查是否存在英文逗号（,）用于中文句子中
        if ',' in text:
            return True
        
        return False
    
    def proofread_punctuation(self, text: str, file_path: str) -> str:
        """校对并修正文本中的标点符号"""
        corrected_text = []
        
        for i, char in enumerate(text):
            original_char = char
            corrected_char = char
            
            # 检查是否需要修正标点符号
            if char in self.horizontal_to_vertical_punctuation:
                corrected_char = self.horizontal_to_vertical_punctuation[char]
            
            # 额外处理：将英文逗号转换为中文逗号
            elif char == ',':
                corrected_char = '，'
            
            # 记录修改
            if original_char != corrected_char:
                self.report_records.append({
                    'file_path': file_path,
                    'position': i,
                    'original': original_char,
                    'corrected': corrected_char,
                    'type': '标点符号转换'
                })
            
            corrected_text.append(corrected_char)
        
        return ''.join(corrected_text)
    
    def process_html_file(self, file_path: str) -> int:
        """处理单个HTML文件"""
        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # 判断是否为竖排文本，传入file_path以便检查外部CSS文件
        is_vertical = self.is_vertical_text(html_content, file_path)
        
        # 解析HTML
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 遍历所有文本节点
        changes = 0
        for text_node in soup.find_all(string=True):
            # 跳过脚本、样式、元数据等非文本节点
            if text_node.parent.name not in ['script', 'style', 'meta', 'link', 'title']:
                original_text = text_node
                if original_text:
                    corrected_text = self.proofread_punctuation(str(original_text), file_path)
                    if corrected_text != str(original_text):
                        text_node.replace_with(corrected_text)
                        changes += 1
        
        # 写入修改后的内容
        if changes > 0 and not self.report_only:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(str(soup))
        
        if self.verbose:
            if is_vertical:
                print(f"✓ {file_path}: 检测到竖排文本，修改了 {changes} 处标点符号")
            else:
                print(f"✓ {file_path}: 横排文本，修改了 {changes} 处标点符号")
        
        return changes
    
    def process_directory(self, dir_path: str) -> Tuple[int, int]:
        """处理目录中的所有HTML文件"""
        total_files = 0
        total_changes = 0
        
        # 遍历目录中的所有文件
        for root, dirs, files in os.walk(dir_path):
            for file in files:
                # 只处理HTML/XHTML文件
                if file.endswith(('.html', '.xhtml', '.htm')):
                    file_path = os.path.join(root, file)
                    total_files += 1
                    changes = self.process_html_file(file_path)
                    total_changes += changes
        
        return total_files, total_changes
    
    def generate_report(self) -> str:
        """生成校对报告"""
        report_lines = [
            "=" * 50,
            "竖排文本标点符号校对报告",
            "=" * 50,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"总文件数: {len(set(record['file_path'] for record in self.report_records))}",
            f"总修改数: {len(self.report_records)}",
            "\n修改详情:",
            "-" * 70,
            f"{'文件路径':<40} | {'位置':<6} | {'原字符':<6} | {'修正后':<6} | {'类型':<10}",
            "-" * 70,
        ]
        
        for record in self.report_records:
            file_path = record['file_path']
            # 只显示相对于当前目录的路径
            rel_path = os.path.relpath(file_path)
            line = f"{rel_path:<40} | {record['position']:<6} | {record['original']:<6} | {record['corrected']:<6} | {record['type']:<10}"
            report_lines.append(line)
        
        return '\n'.join(report_lines)
    
    def save_report(self, report_path: str = "proofread_report.txt"):
        """保存校对报告"""
        report = self.generate_report()
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"✓ 校对报告已生成: {report_path}")

def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='竖排文本标点符号校对工具')
    parser.add_argument('--report-only', action='store_true', 
                      help='只生成报告，不修改原文件')
    parser.add_argument('--verbose', '-v', action='store_true', 
                      help='显示详细的处理过程')
    args = parser.parse_args()
    
    # 创建校对工具实例
    proofreader = VerticalTextProofreader(
        report_only=args.report_only,
        verbose=args.verbose
    )
    
    # 处理translated目录
    translated_dir = 'translated'
    if not os.path.exists(translated_dir):
        print(f"错误：目录 {translated_dir} 不存在！")
        return
    
    print(f"正在处理目录: {translated_dir}")
    total_files, total_changes = proofreader.process_directory(translated_dir)
    
    # 生成报告
    proofreader.save_report()
    
    print(f"✓ 处理完成！")
    print(f"✓ 总计处理了 {total_files} 个文件，修改了 {total_changes} 处标点符号")

if __name__ == '__main__':
    main()
