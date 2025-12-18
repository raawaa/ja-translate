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
import traceback
import logging
from pathlib import Path
from typing import List, Dict, Optional
from iflow_sdk import IFlowClient, AssistantMessage, TaskFinishMessage, TimeoutError as SDKTimeoutError, ToolCallMessage, PlanMessage, IFlowOptions, StopReason

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# ======================
# 全局日志配置
# ======================
# 启用详细日志以便调试 - 按照iFlow CLI SDK文档最佳实践
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('debug.log', encoding='utf-8')
    ]
)

# ======================
# 全局配置常量
# ======================
SOURCE_ROOT = Path("source")
TRANSLATED_ROOT = Path("translated")
SOURCE_OEBPS = SOURCE_ROOT / "OEBPS"  # 用于向后兼容
SOURCE_DIR = SOURCE_OEBPS  # 向后兼容：指向 source/OEBPS
TRANSLATED_DIR = TRANSLATED_ROOT  # 向后兼容：指向 translated
TEMP_DIR = Path("temp")  # 过程性文件存放目录
CHECKLIST_FILE = TEMP_DIR / "translate-checklist.md"
GLOSSARY_FILE = "glossary.md"  # 术语表保持在根目录
PROGRESS_FILE = TEMP_DIR / "progress.json"
ERROR_LOG_FILE = TEMP_DIR / "error_log.json"
NEW_TERMS_FILE = TEMP_DIR / "new_terms.json"

MAX_RETRY = 3
TIMEOUT_SEC = 60.0
IFLOW_TIMEOUT = 600.0  # iFlow客户端超时时间（秒）
QUALITY_CHECK_INTERVAL = 5

# 资源监控配置
MEMORY_MONITOR_INTERVAL = 300  # 内存监控间隔（秒）
MEMORY_WARNING_THRESHOLD = 0.8  # 内存使用警告阈值（80%）
MAX_MEMORY_MB = 2048  # 最大允许内存使用量（MB）
CLEANUP_INTERVAL = 600  # 资源清理间隔（秒）

# 日志系统配置
LOG_LEVEL = "INFO"  # 日志级别: DEBUG, INFO, WARNING, ERROR
LOG_FILE = TEMP_DIR / "translation.log"  # 日志文件路径
LOG_MAX_SIZE = 10 * 1024 * 1024  # 日志文件最大大小（10MB）
LOG_BACKUP_COUNT = 5  # 日志文件备份数量
CONNECTION_STATUS_FILE = TEMP_DIR / "connection_status.json"  # 连接状态记录文件

# ======================
# iFlow连接管理器
# ======================

class IFlowConnectionManager:
    """iFlow连接管理器，提供自动重连和状态监控功能"""
    
    def __init__(self, timeout=600.0, max_reconnect_attempts=5, logger=None):
        self.timeout = timeout
        self.max_reconnect_attempts = max_reconnect_attempts
        self.client = None
        self.is_connected = False
        self.connection_stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'reconnections': 0,
            'last_activity': None,
            'connection_start_time': None
        }
        self.logger = logger or self._setup_logger()
    
    def _setup_logger(self):
        """设置连接管理器的日志记录"""
        import logging
        logger = logging.getLogger('IFlowConnectionManager')
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    async def _check_and_restart_iflow_process(self):
        """检查并重启iFlow进程"""
        import subprocess
        import re
        
        try:
            # 检查是否有iFlow进程在运行
            result = subprocess.run(
                ["lsof", "-i", ":8090"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                # 找到进程，尝试杀死
                self.logger.warning("检测到iFlow进程仍在运行，尝试重启...")
                
                # 提取PID并杀死进程
                pid_match = re.search(r'\d+', result.stdout)
                if pid_match:
                    pid = int(pid_match.group())
                    subprocess.run(["kill", "-9", str(pid)], check=True)
                    self.logger.info(f"已杀死iFlow进程 (PID: {pid})")
                    await asyncio.sleep(2)  # 等待进程完全退出
            
            self.logger.info("iFlow进程重启准备完成")
        except Exception as e:
            self.logger.error(f"重启iFlow进程时出错: {e}")
            # 忽略错误，继续尝试连接
            pass
    
    async def connect(self):
        """建立iFlow连接"""
        from iflow_sdk import IFlowOptions
        
        # 从环境变量获取配置
        api_key = os.getenv("IFLOW_API_KEY")
        
        # 检查 API Key 是否配置
        if not api_key or api_key == "your_iflow_api_key_here":
            self.logger.error("iFlow API Key 未配置！")
            self.logger.error("请在 .env 文件中设置 IFLOW_API_KEY 环境变量")
            self.logger.error("示例: IFLOW_API_KEY=your_actual_api_key")
            raise ValueError("iFlow API Key 未配置")
        
        url = os.getenv("IFLOW_WS_URL")
        
        for attempt in range(self.max_reconnect_attempts):
            try:
                self.logger.info(f"尝试建立iFlow连接 (第 {attempt + 1}/{self.max_reconnect_attempts} 次)")
                
                # 在第一次尝试或后续失败时检查并重启iFlow进程
                if attempt > 0:
                    await self._check_and_restart_iflow_process()
                
                # 配置选项，启用详细日志 - 按照iFlow CLI SDK文档
                options = IFlowOptions(
                    timeout=self.timeout,
                    log_level="DEBUG",
                    url=url if url else "ws://localhost:8090/acp",
                    auth_method_id="iflow",
                    auth_method_info={"api_key": api_key},
                    auto_start_process=True  # 启用自动进程管理
                )
                
                # 调试信息：显示IFlow配置（隐藏API Key部分内容）
                api_key_masked = f"{api_key[:5]}***" if api_key else "None"
                self.logger.debug(f"创建IFlow客户端 - 超时: {self.timeout}s, 日志级别: DEBUG, API Key: {api_key_masked}, URL: {options.url}")
                
                # 创建客户端
                self.client = IFlowClient(options)
                await self.client.__aenter__()
                
                self.is_connected = True
                self.connection_stats['connection_start_time'] = time.time()
                self.connection_stats['last_activity'] = time.time()
                
                # 记录连接成功事件
                if hasattr(self.logger, 'log_connection_event'):
                    self.logger.log_connection_event('connection_established', {
                        'attempt': attempt + 1,
                        'timeout': self.timeout
                    })
                
                self.logger.info("iFlow连接建立成功")
                return True
                
            except Exception as e:
                self.logger.error(f"连接失败 (第 {attempt + 1} 次): {type(e).__name__}: {str(e)}")
                
                if attempt < self.max_reconnect_attempts - 1:
                    delay = 5 * (1.5 ** attempt)  # 指数退避
                    self.logger.info(f"等待 {delay:.1f} 秒后重试...")
                    await asyncio.sleep(delay)
                else:
                    self.logger.error("所有连接尝试均已失败")
                    raise e
        
        return False
    
    async def disconnect(self):
        """断开iFlow连接"""
        self.is_connected = False
        
        # 关闭客户端连接
        if self.client:
            try:
                await self.client.__aexit__(None, None, None)
            except Exception as e:
                self.logger.warning(f"关闭连接时出错: {e}")
            finally:
                self.client = None
        
        self.logger.info("iFlow连接已断开")
    
    async def reset_session(self):
        """重置会话，重新创建IFlowClient实例"""
        self.logger.info("重置iFlow会话...")
        
        # 断开当前连接
        await self.disconnect()
        
        # 立即重新建立连接，不等待
        await self.connect()
        self.logger.info("iFlow会话已重置")
    
    async def _reconnect(self):
        """重新连接"""
        if not self.is_connected:
            return
        
        self.logger.warning("检测到连接问题，尝试重新连接...")
        self.connection_stats['reconnections'] += 1
        
        # 先断开当前连接
        await self.disconnect()
        
        # 等待一段时间后重连
        await asyncio.sleep(5)
        
        # 尝试重新连接
        await self.connect()
    
    async def send_message(self, message: str):
        """发送消息，带有连接状态检查"""
        if not self.is_connected or not self.client:
            raise ConnectionError("iFlow连接未建立")
        
        self.connection_stats['total_requests'] += 1
        self.connection_stats['last_activity'] = time.time()
        
        try:
            await self.client.send_message(message)
            self.connection_stats['successful_requests'] += 1
        except Exception as e:
            self.connection_stats['failed_requests'] += 1
            self.logger.error(f"发送消息失败: {e}")
            # 尝试重新连接
            await self._reconnect()
            raise e
    
    def get_message_iterator(self):
        """获取消息迭代器"""
        if not self.is_connected or not self.client:
            raise ConnectionError("iFlow连接未建立")
        return self.client.receive_messages()
    
    def get_connection_stats(self):
        """获取连接统计信息"""
        stats = self.connection_stats.copy()
        if stats['connection_start_time']:
            stats['uptime'] = time.time() - stats['connection_start_time']
        stats['is_connected'] = self.is_connected
        return stats
    
    

# ======================
# 资源监控管理器
# ======================

class ResourceMonitor:
    """资源监控管理器，监控内存使用和系统资源"""
    
    def __init__(self, max_memory_mb=MAX_MEMORY_MB, warning_threshold=MEMORY_WARNING_THRESHOLD):
        self.max_memory_mb = max_memory_mb
        self.warning_threshold = warning_threshold
        self.monitoring = False
        self.monitor_task = None
        self.logger = self._setup_logger()
        self.memory_history = []
        self.cleanup_callbacks = []
        
    def _setup_logger(self):
        """设置资源监控器的日志记录"""
        import logging
        logger = logging.getLogger('ResourceMonitor')
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    def get_memory_usage(self):
        """获取当前内存使用情况"""
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024  # 转换为MB
            memory_percent = memory_mb / self.max_memory_mb
            
            return {
                'memory_mb': memory_mb,
                'memory_percent': memory_percent,
                'max_memory_mb': self.max_memory_mb,
                'timestamp': time.time()
            }
        except ImportError:
            # 如果没有psutil，使用简单的内存监控
            import resource
            memory_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # 在Windows上，ru_maxrss以字节为单位；在Unix上以KB为单位
            if os.name == 'nt':
                memory_mb = memory_kb / 1024 / 1024
            else:
                memory_mb = memory_kb / 1024
            memory_percent = memory_mb / self.max_memory_mb
            
            return {
                'memory_mb': memory_mb,
                'memory_percent': memory_percent,
                'max_memory_mb': self.max_memory_mb,
                'timestamp': time.time(),
                'note': 'Using basic memory monitoring (psutil not available)'
            }
        except Exception as e:
            self.logger.error(f"获取内存使用情况失败: {e}")
            return None
    
    def add_cleanup_callback(self, callback):
        """添加资源清理回调函数"""
        self.cleanup_callbacks.append(callback)
    
    async def cleanup_resources(self):
        """执行资源清理"""
        self.logger.info("开始执行资源清理...")
        
        for callback in self.cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
                self.logger.debug(f"执行清理回调: {callback.__name__}")
            except Exception as e:
                self.logger.error(f"清理回调 {callback.__name__} 执行失败: {e}")
        
        # 强制垃圾回收
        import gc
        collected = gc.collect()
        self.logger.info(f"垃圾回收完成，回收了 {collected} 个对象")
    
    async def _check_iflow_process(self):
        """检查iFlow进程状态"""
        import subprocess
        try:
            # 检查iFlow进程是否在运行
            result = subprocess.run(
                ["lsof", "-i", ":8090"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                # iFlow进程在运行
                pid_match = result.stdout.split()[1] if len(result.stdout.split()) > 1 else "unknown"
                self.logger.debug(f"iFlow进程正在运行 (PID: {pid_match})")
                return True
            else:
                # iFlow进程未在运行
                self.logger.warning("检测到iFlow进程未运行")
                return False
        except Exception as e:
            self.logger.error(f"检查iFlow进程状态时出错: {e}")
            return False
    
    async def _monitor_loop(self):
        """监控循环"""
        while self.monitoring:
            try:
                # 检查iFlow进程状态
                await self._check_iflow_process()
                
                # 检查内存使用情况
                memory_info = self.get_memory_usage()
                if memory_info:
                    self.memory_history.append(memory_info)
                    
                    # 只保留最近100条记录
                    if len(self.memory_history) > 100:
                        self.memory_history = self.memory_history[-100:]
                    
                    # 检查内存使用情况
                    memory_percent = memory_info['memory_percent']
                    memory_mb = memory_info['memory_mb']
                    
                    if memory_percent > self.warning_threshold:
                        self.logger.warning(
                            f"内存使用率过高: {memory_percent:.1%} ({memory_mb:.1f}MB/{self.max_memory_mb}MB)"
                        )
                        
                        # 如果内存使用超过90%，执行清理
                        if memory_percent > 0.9:
                            self.logger.error("内存使用率超过90%，执行紧急清理...")
                            await self.cleanup_resources()
                    
                    # 定期输出内存状态
                    if len(self.memory_history) % 10 == 0:
                        self.logger.info(
                            f"内存状态: {memory_mb:.1f}MB ({memory_percent:.1%}), "
                            f"峰值内存: {max(h['memory_mb'] for h in self.memory_history):.1f}MB"
                        )
                
                await asyncio.sleep(MEMORY_MONITOR_INTERVAL)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"资源监控循环出错: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再继续
    
    async def start_monitoring(self):
        """开始监控"""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info("资源监控已启动")
    
    async def stop_monitoring(self):
        """停止监控"""
        if not self.monitoring:
            return
        
        self.monitoring = False
        
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("资源监控已停止")
    
    def get_memory_stats(self):
        """获取内存统计信息"""
        if not self.memory_history:
            return None
        
        memory_values = [h['memory_mb'] for h in self.memory_history]
        return {
            'current_mb': memory_values[-1],
            'peak_mb': max(memory_values),
            'min_mb': min(memory_values),
            'avg_mb': sum(memory_values) / len(memory_values),
            'samples': len(memory_values),
            'max_memory_mb': self.max_memory_mb
        }

# ======================
# 增强日志系统
# ======================

class EnhancedLogger:
    """增强的日志系统，支持文件输出、日志轮转和连接状态记录"""
    
    def __init__(self, name="EPUBTranslator", log_file=LOG_FILE, log_level=LOG_LEVEL):
        self.name = name
        self.log_file = log_file
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.connection_status_file = CONNECTION_STATUS_FILE
        self.logger = self._setup_logger()
        self.connection_status_history = []
        
    def _setup_logger(self):
        """设置增强的日志记录器"""
        logger = logging.getLogger(self.name)
        logger.setLevel(self.log_level)
        
        # 清除现有处理器
        logger.handlers.clear()
        
        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        
        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 文件处理器（带轮转）
        try:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=LOG_MAX_SIZE,
                backupCount=LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setLevel(self.log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"无法创建文件日志处理器: {e}")
        
        return logger
    
    def log_connection_event(self, event_type, details=None):
        """记录连接事件"""
        timestamp = time.time()
        event = {
            'timestamp': timestamp,
            'datetime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp)),
            'event_type': event_type,
            'details': details or {}
        }
        
        self.connection_status_history.append(event)
        
        # 只保留最近1000条记录
        if len(self.connection_status_history) > 1000:
            self.connection_status_history = self.connection_status_history[-1000:]
        
        # 保存到文件
        self._save_connection_status()
        
        # 记录日志
        message = f"连接事件: {event_type}"
        if details:
            message += f" - {details}"
        
        if event_type in ['connection_lost', 'reconnection_failed']:
            self.logger.error(message)
        elif event_type in ['reconnecting', 'connection_unhealthy']:
            self.logger.warning(message)
        else:
            self.logger.info(message)
    
    def _save_connection_status(self):
        """保存连接状态到文件"""
        try:
            status_data = {
                'last_updated': time.time(),
                'last_updated_datetime': time.strftime('%Y-%m-%d %H:%M:%S'),
                'total_events': len(self.connection_status_history),
                'recent_events': self.connection_status_history[-50:],  # 保存最近50条
                'summary': self._generate_connection_summary()
            }
            
            with open(self.connection_status_file, 'w', encoding='utf-8') as f:
                json.dump(status_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            self.logger.error(f"保存连接状态失败: {e}")
    
    def _generate_connection_summary(self):
        """生成连接状态摘要"""
        if not self.connection_status_history:
            return {}
        
        # 统计各种事件类型
        event_counts = {}
        recent_events = self.connection_status_history[-100:]  # 最近100条
        
        for event in recent_events:
            event_type = event['event_type']
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        # 计算连接稳定性
        total_events = len(recent_events)
        negative_events = sum(
            event_counts.get(t, 0) for t in 
            ['connection_lost', 'reconnection_failed', 'connection_unhealthy']
        )
        
        stability_score = max(0, (total_events - negative_events) / total_events) if total_events > 0 else 1.0
        
        return {
            'event_counts': event_counts,
            'stability_score': stability_score,
            'total_recent_events': total_events,
            'negative_events_ratio': negative_events / total_events if total_events > 0 else 0
        }
    
    def log_translation_progress(self, file_name, block_index, total_blocks, success=True, error_msg=None):
        """记录翻译进度"""
        message = f"翻译进度: {file_name} - 块 {block_index + 1}/{total_blocks}"
        
        if success:
            self.logger.info(message)
        else:
            self.logger.error(f"{message} - 失败: {error_msg}")
    
    def log_resource_usage(self, resource_info):
        """记录资源使用情况"""
        self.logger.info(
            f"资源使用 - 内存: {resource_info.get('memory_mb', 0):.1f}MB "
            f"({resource_info.get('memory_percent', 0):.1%}), "
            f"连接状态: {resource_info.get('connection_status', 'unknown')}"
        )
    
    def log_error_with_context(self, error, context=None):
        """记录带上下文的错误"""
        error_info = {
            'error_type': type(error).__name__,
            'error_message': str(error),
            'context': context,
            'timestamp': time.time(),
            'traceback': traceback.format_exc()
        }
        
        self.logger.error(
            f"错误详情: {error_info['error_type']} - {error_info['error_message']}\n"
            f"上下文: {context}\n"
            f"堆栈跟踪: {error_info['traceback']}"
        )
        
        # 保存错误到错误日志文件
        try:
            error_log = load_json(ERROR_LOG_FILE, {"errors": []})
            error_log["errors"].append(error_info)
            
            # 只保留最近100个错误
            if len(error_log["errors"]) > 100:
                error_log["errors"] = error_log["errors"][-100:]
            
            save_json(error_log, ERROR_LOG_FILE)
        except Exception as e:
            self.logger.error(f"保存错误日志失败: {e}")
    
    def get_connection_report(self):
        """获取连接状态报告"""
        if not self.connection_status_history:
            return "暂无连接状态记录"
        
        summary = self._generate_connection_summary()
        
        report = f"""
连接状态报告
============
总事件数: {summary['total_recent_events']}
稳定性评分: {summary['stability_score']:.2%}
负面事件比例: {summary['negative_events_ratio']:.2%}

事件统计:
"""
        
        for event_type, count in summary['event_counts'].items():
            report += f"  {event_type}: {count} 次\n"
        
        # 最近的事件
        recent_events = self.connection_status_history[-10:]
        if recent_events:
            report += "\n最近事件:\n"
            for event in recent_events:
                report += f"  {event['datetime']} - {event['event_type']}\n"
        
        return report
    
    # 基本日志方法，委托给内部logger
    def info(self, message, *args, **kwargs):
        """记录信息级别日志"""
        self.logger.info(message, *args, **kwargs)
    
    def error(self, message, *args, **kwargs):
        """记录错误级别日志"""
        self.logger.error(message, *args, **kwargs)
    
    def warning(self, message, *args, **kwargs):
        """记录警告级别日志"""
        self.logger.warning(message, *args, **kwargs)
    
    def debug(self, message, *args, **kwargs):
        """记录调试级别日志"""
        self.logger.debug(message, *args, **kwargs)
    
    def critical(self, message, *args, **kwargs):
        """记录严重错误级别日志"""
        self.logger.critical(message, *args, **kwargs)

# ======================
# 目录初始化
# ======================

# 确保输出目录存在
TRANSLATED_ROOT.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)


async def create_connection_manager_with_retry(max_retries=3, delay=5, timeout=600.0, logger=None):
    """创建 IFlowConnectionManager 并使用重试机制处理连接问题"""
    
    for attempt in range(max_retries):
        try:
            print(f"\n{'='*60}")
            print(f"🔗 尝试创建 iFlow 连接管理器 (第 {attempt + 1}/{max_retries} 次尝试)")
            print(f"⏰ 连接时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"⚙️ 超时设置: {timeout}秒")
            
            connect_start = time.time()
            
            # 创建连接管理器
            connection_manager = IFlowConnectionManager(
                timeout=timeout,
                max_reconnect_attempts=max_retries,
                logger=logger
            )
            
            # 建立连接
            success = await connection_manager.connect()
            connect_duration = time.time() - connect_start
            
            if success:
                print(f"✅ 成功创建 iFlow 连接管理器 (耗时 {connect_duration:.2f}秒)")
                
                # 显示连接统计信息
                stats = connection_manager.get_connection_stats()
                print(f"📊 连接统计: {stats}")
                print(f"{'='*60}\n")
                return connection_manager
            else:
                raise ConnectionError("连接管理器连接失败")
                
        except Exception as e:
            connect_duration = time.time() - connect_start if 'connect_start' in locals() else 0
            print(f"⚠️ 连接管理器创建失败 (第 {attempt + 1}/{max_retries} 次尝试)")
            print(f"❌ 错误类型: {type(e).__name__}")
            print(f"❌ 错误详情: {str(e)}")
            print(f"⏱️ 尝试耗时: {connect_duration:.2f}秒")
            
            if attempt < max_retries - 1:
                next_delay = delay * (1.5 ** attempt)
                print(f"⏳ 等待 {next_delay:.1f} 秒后重试...")
                await asyncio.sleep(next_delay)
            else:
                print("❌ 所有连接管理器创建尝试均已失败")
                print(f"{'='*60}\n")
                import traceback
                print("📋 完整错误堆栈:")
                traceback.print_exc()
                raise e

# 保留原函数以向后兼容，但内部使用新的连接管理器
async def create_client_with_retry(max_retries=3, delay=5, timeout=600.0):
    """创建 IFlowClient 并使用重试机制处理连接问题（向后兼容函数）"""
    connection_manager = await create_connection_manager_with_retry(
        max_retries=max_retries,
        delay=delay,
        timeout=timeout
    )
    return connection_manager

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
    # 只检测日语特有的字符：平假名和片假名
    # 不包括汉字，因为中日汉字共用 Unicode 范围，难以准确区分
    return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text))

def check_chinese_punctuation(text: str) -> bool:
    # 检查是否使用中文标点（简单规则）
    jp_punct = '。、・「」『』【】！？'
    for p in jp_punct:
        if p in text:
            return False
    return True

def update_checklist(file_list: List[str], progress_data: dict):
    """更新 translate-checklist.md"""
    content = "# 日文书籍翻译进度追踪\n\n"
    
    # 获取元数据
    meta = progress_data.get("meta", {})
    total_files = meta.get("total_files", len(file_list))
    completed_files_count = meta.get("completed_files", 0)
    total_blocks = meta.get("total_blocks", 0)
    completed_blocks = meta.get("completed_blocks", 0)
    last_updated = meta.get("last_updated", time.strftime("%Y-%m-%d %H:%M:%S"))
    
    # 按类型分组文件
    html_files = [f for f in file_list if f.endswith('.html')]
    ncx_files = [f for f in file_list if f.endswith('.ncx')]
    opf_files = [f for f in file_list if f.endswith('.opf')]
    other_files = [f for f in file_list if f not in html_files + ncx_files + opf_files]

    if html_files:
        content += "## HTML文件\n"
        for f in html_files:
            file_progress = progress_data.get("files", {}).get(f, {})
            is_completed = file_progress.get("is_completed", False)
            mark = "x" if is_completed else " "
            
            # 添加块级进度信息
            if not is_completed:
                completed_blocks_count = file_progress.get("completed_blocks", 0)
                total_blocks_count = file_progress.get("total_blocks", 0)
                if total_blocks_count > 0:
                    block_progress = f" ({completed_blocks_count}/{total_blocks_count} 块)"
                    content += f"- [{mark}] {f}{block_progress}\n"
                else:
                    content += f"- [{mark}] {f}\n"
            else:
                content += f"- [{mark}] {f}\n"
        content += "\n"

    if ncx_files:
        content += "## 目录文件\n"
        for f in ncx_files:
            file_progress = progress_data.get("files", {}).get(f, {})
            is_completed = file_progress.get("is_completed", False)
            mark = "x" if is_completed else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    if opf_files:
        content += "## 元数据文件\n"
        for f in opf_files:
            file_progress = progress_data.get("files", {}).get(f, {})
            is_completed = file_progress.get("is_completed", False)
            mark = "x" if is_completed else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    if other_files:
        content += "## 其他文件\n"
        for f in other_files:
            file_progress = progress_data.get("files", {}).get(f, {})
            is_completed = file_progress.get("is_completed", True)  # 其他文件默认为已完成
            mark = "x" if is_completed else " "
            content += f"- [{mark}] {f}\n"
        content += "\n"

    content += "## 翻译进度统计\n"
    total = len(file_list)
    done = completed_files_count
    percent = done / total * 100 if total > 0 else 0
    content += f"- 总文件数: {total}个文件\n"
    content += f"- 已翻译: {done}个\n"
    content += f"- 待翻译: {total - done}个\n"
    content += f"- 文件完成度: {percent:.1f}%\n"
    
    # 添加块级进度统计
    if total_blocks > 0:
        block_percent = completed_blocks / total_blocks * 100 if total_blocks > 0 else 0
        content += f"- 总文本块: {total_blocks}个\n"
        content += f"- 已翻译块: {completed_blocks}个\n"
        content += f"- 块完成度: {block_percent:.1f}%\n"
    
    content += f"- 最后更新时间: {last_updated}\n"
    
    with open(CHECKLIST_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

# ======================
# 核心翻译函数
# ======================

async def translate_block(
    connection_manager: IFlowConnectionManager,
    current_block: str,
    prev_block: str = "",
    next_block: str = "",
    glossary: Dict[str, str] = None,
    max_retries: int = MAX_RETRY
) -> str:
    """
    翻译单个 HTML 块（使用连接管理器）
    """
    # 关键修改：提取原块的 HTML 标签结构
    original_tag = ""
    tag_name = ""
    content_inside_tag = ""
    leading_spaces = ""  # 初始化开头空格
    trailing_spaces = ""  # 初始化结尾空格
    
    # 使用正则表达式提取标签和内容
    tag_match = re.match(r'<([a-z0-9]+)([^>]*)>(.*)</\1>$', current_block, re.DOTALL | re.IGNORECASE)
    if tag_match:
        tag_name = tag_match.group(1).lower()  # 标签名称
        tag_attributes = tag_match.group(2)  # 标签属性
        original_tag = f"<{tag_name}{tag_attributes}>"  # 完整的开始标签
        closing_tag = f"</{tag_name}>"  # 结束标签
        content_inside_tag = tag_match.group(3)  # 标签内的内容
        
        # 提取并保存原始内容的前后空格
        leading_spaces = re.match(r'^(\s+)', content_inside_tag, re.DOTALL)  # 开头空格
        trailing_spaces = re.search(r'(\s+)$', content_inside_tag, re.DOTALL)  # 结尾空格
        leading_spaces = leading_spaces.group(1) if leading_spaces else ""  # 保存开头空格
        trailing_spaces = trailing_spaces.group(1) if trailing_spaces else ""  # 保存结尾空格
    
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
            # 显示要翻译的内容预览
            preview = re.sub(r'<[^>]+>', '', current_block)[:50]
            print(f"  📋 发送翻译请求 (尝试 {attempt+1}/{max_retries})")
            print(f"  📝 内容预览: {preview}...")
            print(f"  ⏰ 请求发送时间: {time.strftime('%H:%M:%S')}")
            
            # 使用连接管理器发送消息
            await connection_manager.send_message(prompt)
            response = ""
            start_time = time.time()
            message_count = 0
            tool_call_count = 0
            plan_message_count = 0
            current_agent_id = None
            sub_agents = set()

            # 使用连接管理器获取消息迭代器
            last_message_time = start_time
            MESSAGE_TIMEOUT = 30  # 30秒无新消息则超时
            
            async for message in connection_manager.get_message_iterator():
                message_count += 1
                current_time = time.time()
                elapsed = current_time - start_time
                
                # 全局超时检查（每条消息都检查）
                if elapsed > TIMEOUT_SEC:
                    print(f"  ⏱️ 全局超时: 已等待 {elapsed:.1f}秒 > {TIMEOUT_SEC}秒")
                    raise SDKTimeoutError(f"翻译超时 (等待了 {elapsed:.1f}秒)")
                
                # 消息间超时检查
                if current_time - last_message_time > MESSAGE_TIMEOUT:
                    print(f"  ⏱️ 消息超时: {current_time - last_message_time:.1f}秒未收到新消息")
                    raise SDKTimeoutError("消息接收超时")
                
                last_message_time = current_time
                
                # 每10秒输出一次进度信息
                if message_count == 1 or (message_count % 10 == 0):
                    print(f"  📊 进度: 已等待 {elapsed:.1f}秒, 收到 {message_count} 条消息, 响应长度 {len(response)} 字符")
                
                # 检查消息是否包含错误信息
                message_str = str(message)
                if "error" in message_str.lower() or "aborted" in message_str.lower():
                    print(f"  ❌ 检测到错误或中止消息: {message_str[:100]}...")
                    raise ConnectionError(f"iFlow服务端错误: 消息包含错误信息")
                
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
                    print(f"  📝 收到响应片段: {len(message.chunk.text)} 字符 (总计: {len(response)} 字符)")
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
                    
                    # 处理 TaskFinishMessage 中的 stopReason 信息
                    stop_reason = getattr(message, 'stop_reason', None)
                    print(f"  📋 结束原因: {stop_reason}")
                    
                    # 根据结束原因进行不同处理
                    try:
                        session_reset_needed = False
                        if stop_reason == StopReason.MAX_TOKENS:
                            print(f"  ⚠️ 警告: 翻译结果可能被截断，因为达到了最大令牌限制")
                            # 自动重置会话，确保后续翻译有充足的上下文空间
                            print(f"  🔄 自动重置会话，为后续翻译准备充足的上下文空间")
                            await connection_manager.reset_session()
                            session_reset_needed = True
                        elif stop_reason == StopReason.END_TURN:
                            print(f"  📊 翻译正常完成")
                        else:
                            print(f"  ℹ️ 翻译以其他原因结束: {stop_reason}")
                    except (ValueError, TypeError):
                        # 如果 StopReason 不匹配，使用字符串比较作为备选
                        stop_reason_str = str(stop_reason).upper()
                        session_reset_needed = False
                        if 'MAX_TOKENS' in stop_reason_str:
                            print(f"  ⚠️ 警告: 翻译结果可能被截断，因为达到了最大令牌限制")
                            # 自动重置会话，确保后续翻译有充足的上下文空间
                            print(f"  🔄 自动重置会话，为后续翻译准备充足的上下文空间")
                            await connection_manager.reset_session()
                            session_reset_needed = True
                        elif 'END_TURN' in stop_reason_str:
                            print(f"  📊 翻译正常完成")
                        else:
                            print(f"  ℹ️ 翻译以其他原因结束: {stop_reason}")
                    
                    # 任务完成消息，不输出到翻译结果
                    break
                else:
                    # 未知消息类型，记录但不影响流程
                    print(f"  📨 收到未知类型消息: {type(message).__name__}")

            # 处理会话重置需求
            if 'session_reset_needed' in locals() and session_reset_needed:
                # 如果会话被重置，当前翻译可能不完整，需要重新尝试
                print(f"  🔄 将重新翻译当前块以确保完整性")
                if attempt < max_retries - 1:
                    # 继续重试循环，重新翻译当前块
                    await asyncio.sleep(1)  # 短暂等待，确保重置完成
                    continue
                else:
                    print(f"  ⚠️ 警告: 达到最大重试次数，当前块可能翻译不完整")

            # 清理响应：只保留 HTML 块（简单策略）
            response = response.strip()
            if response.startswith("```") and response.endswith("```"):
                response = "\n".join(response.split("\n")[1:-1])

            # 关键修改：处理纯文本翻译结果
            if response and "<" not in response:
                print(f"  ⚠️ 警告: 翻译结果是纯文本，自动包装HTML标签")
                print(f"  📝 原始响应内容: {repr(response[:100])}")
                
                # 如果提取到了原块标签，使用原标签包装
                if original_tag and tag_name:
                    response = f"{original_tag}{leading_spaces}{response}{trailing_spaces}{closing_tag}"
                    print(f"  ✅ 使用原块标签包装: {original_tag.strip()}")
                    print(f"  📝 恢复前后空格: 前 '{repr(leading_spaces)}', 后 '{repr(trailing_spaces)}'")
                else:
                    # 默认使用<p>标签包装
                    response = f"<p>{response}</p>"
                    print(f"  ✅ 使用默认<p>标签包装")
            
            # 优化后的基础验证
            if not response:
                print(f"  ⚠️ 警告: 翻译结果无效 - 长度: {len(response) if response else 0}")
                print(f"  📝 原始响应内容: {repr(response[:100]) if response else 'None'}")
                # 返回原始内容而不是抛出异常，避免程序崩溃
                return f"<!-- 翻译失败: 无效翻译结果 -->"

            print(f"  📊 翻译完成: {len(response)} 字符")
            return response

        except (Exception, asyncio.CancelledError) as e:
            elapsed = time.time() - start_time if 'start_time' in locals() else 0
            msg_count = message_count if 'message_count' in locals() else 0
            print(f"  ⚠️ 翻译失败 (尝试 {attempt+1}/{max_retries})")
            print(f"  ❌ 错误类型: {type(e).__name__}")
            print(f"  ❌ 错误详情: {str(e)}")
            print(f"  ⏱️ 已等待时间: {elapsed:.1f}秒")
            print(f"  📨 已接收消息: {msg_count}条")
            
            # 特殊处理超时错误和连接错误
            if isinstance(e, SDKTimeoutError) or isinstance(e, ConnectionError) or "timeout" in str(e).lower():
                print(f"  🚨 检测到超时或连接错误，尝试重启iFlow进程...")
                try:
                    # 断开当前连接
                    await connection_manager.disconnect()
                    # 重启iFlow进程
                    await connection_manager._check_and_restart_iflow_process()
                    # 重新建立连接
                    await connection_manager.connect()
                    print(f"  ✅ iFlow进程重启成功")
                except Exception as restart_e:
                    print(f"  ⚠️ 重启iFlow进程时出错: {restart_e}")
            
            # 特殊处理iFlow内部错误
            if "operation was aborted" in str(e).lower() or "internal error" in str(e).lower():
                print(f"  🚨 检测到iFlow服务端内部错误，可能需要重启服务或稍后重试")
                if attempt == max_retries - 1:
                    error_info = f"IFLOW_INTERNAL_ERROR: {str(e)}"
                    return f"<!-- {error_info} -->"
                # 对于内部错误，增加等待时间
                wait_time = 5 * (attempt + 1)
                print(f"  ⏳ iFlow内部错误，等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                continue
                
            if attempt == max_retries - 1:
                error_info = f"TRANSLATION_FAILED after {max_retries} attempts: {type(e).__name__} - {str(e)}"
                return f"<!-- {error_info} -->"
            print(f"  🔄 等待 2 秒后重试...")
            await asyncio.sleep(2)
    
    # 确保函数总是返回一个字符串，即使所有重试都失败
    return f"<!-- 翻译失败: 所有重试尝试均失败 -->"

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

def normalize_html_whitespace(html: str) -> str:
    """规范化HTML中的空白字符，用于比较"""
    import re
    # 移除标签之间的多余空白
    html = re.sub(r'>\s+<', '><', html)
    # 规范化标签内的空白
    html = re.sub(r'\s+', ' ', html)
    return html.strip()

def update_file_content_by_type_incremental(
    current_content: str, 
    file_type: str, 
    original_block: str, 
    translated_block: str,
    block_index: int
) -> str:
    """
    增量更新：只更新当前翻译的块，而不是重新构建整个文件
    
    Args:
        current_content: 当前文件内容（可能包含已翻译的部分）
        file_type: 文件类型
        original_block: 原始块内容
        translated_block: 翻译后的块内容
        block_index: 当前块的索引
    
    Returns:
        更新后的文件内容
    """
    import re
    if file_type == 'html':
        # 检查translated_block是否为None
        if translated_block is None:
            print(f"  ⚠️ 警告: translated_block为None，跳过替换")
            return current_content
        
        # 检查原始块是否已经包含bilingual-container，如果包含则跳过处理，避免嵌套
        if 'bilingual-container' in original_block:
            print(f"  ⚠️ 警告: 原始块已包含bilingual-container，跳过处理")
            return current_content
        
        # 检查原始块是否已经被处理过（包含original-text或translated-text类）
        if 'original-text' in original_block or 'translated-text' in original_block:
            print(f"  ⚠️ 警告: 原始块已包含翻译相关类，跳过处理")
            return current_content
            
        # 对于HTML，先尝试直接替换
        if original_block in current_content:
            # 实现双语对照：保留原文，添加译文，并添加样式类区分
            # 为原文添加 original-text 类
            # 修复正则表达式：正确处理已有class属性的情况
            def add_class_to_tag(match):
                tag_name = match.group(1)
                attributes = match.group(2)
                content = match.group(3)
                
                # 检查是否已有class属性
                if 'class=' in attributes:
                    # 提取已有class值
                    class_match = re.search(r'class="([^"]*)"', attributes)
                    if class_match:
                        existing_classes = class_match.group(1)
                        # 如果已有original-text类，则不重复添加
                        if 'original-text' in existing_classes:
                            return match.group(0)
                        # 合并class属性
                        new_classes = f"{existing_classes} original-text"
                        # 修复引号嵌套问题
                        new_class_attr = 'class="' + new_classes + '"'
                        updated_attrs = attributes.replace(class_match.group(0), new_class_attr)
                        return '<' + tag_name + updated_attrs + '>' + content + '</' + tag_name + '>'
                # 没有class属性，直接添加
                return f'<{tag_name}{attributes} class="original-text">{content}</{tag_name}>'
            
            original_with_class = re.sub(r'<([a-z0-9]+)([^>]*)>(.*)</\1>', add_class_to_tag, original_block, flags=re.DOTALL | re.IGNORECASE)
            if original_with_class == original_block:  # 如果没有匹配到标签
                original_with_class = f'<div class="original-text">{original_block}</div>'
            
            # 为译文添加 translated-text 类
            def add_translated_class_to_tag(match):
                tag_name = match.group(1)
                attributes = match.group(2)
                content = match.group(3)
                
                # 检查是否已有class属性
                if 'class=' in attributes:
                    # 提取已有class值
                    class_match = re.search(r'class="([^"]*)"', attributes)
                    if class_match:
                        existing_classes = class_match.group(1)
                        # 如果已有translated-text类，则不重复添加
                        if 'translated-text' in existing_classes:
                            return match.group(0)
                        # 合并class属性
                        new_classes = f"{existing_classes} translated-text"
                        # 修复引号嵌套问题
                        new_class_attr = 'class="' + new_classes + '"'
                        updated_attrs = attributes.replace(class_match.group(0), new_class_attr)
                        return '<' + tag_name + updated_attrs + '>' + content + '</' + tag_name + '>'
                # 没有class属性，直接添加
                return f'<{tag_name}{attributes} class="translated-text">{content}</{tag_name}>'
            
            translated_with_class = re.sub(r'<([a-z0-9]+)([^>]*)>(.*)</\1>', add_translated_class_to_tag, translated_block, flags=re.DOTALL | re.IGNORECASE)
            if translated_with_class == translated_block:  # 如果没有匹配到标签
                translated_with_class = f'<div class="translated-text">{translated_block}</div>'
            
            bilingual_block = f'<div class="bilingual-container">{original_with_class}{translated_with_class}</div>'
            print(f"  🔄 实现HTML双语对照: 替换原始块为双语块，添加样式区分")
            return current_content.replace(original_block, bilingual_block, 1)
        else:
            # 如果直接替换失败，尝试使用文本内容匹配
            from bs4 import BeautifulSoup
            
            try:
                # 解析当前内容
                soup = BeautifulSoup(current_content, 'html.parser')
                orig_soup = BeautifulSoup(original_block, 'html.parser')
                orig_tag = orig_soup.find()
                
                if orig_tag:
                    # 获取原始块的文本内容（用于匹配）
                    orig_text = orig_tag.get_text()
                    
                    # 在文档中查找包含相同文本的标签
                    tags_found = []
                    for tag in soup.find_all(orig_tag.name):
                        if tag.get_text() == orig_text:
                            tags_found.append(tag)
                    
                    # 如果找到多个匹配，使用索引来确定是哪一个
                    if tags_found:
                        target_tag = tags_found[min(block_index, len(tags_found)-1)]
                        
                        # 替换为双语对照结构
                        trans_soup = BeautifulSoup(translated_block, 'html.parser')
                        trans_tag = trans_soup.find()
                        if trans_tag:
                            # 创建双语对照容器
                            from bs4 import Tag
                            bilingual_container = Tag(name='div')
                            bilingual_container['class'] = ['bilingual-container']
                            # 保留原文，添加译文，并添加样式类区分
                            # 为原文添加 original-text 类
                            if 'class' in target_tag.attrs:
                                if 'original-text' not in target_tag.attrs['class']:
                                    target_tag.attrs['class'].append('original-text')
                            else:
                                target_tag.attrs['class'] = ['original-text']
                            
                            # 为译文添加 translated-text 类
                            if 'class' in trans_tag.attrs:
                                if 'translated-text' not in trans_tag.attrs['class']:
                                    trans_tag.attrs['class'].append('translated-text')
                            else:
                                trans_tag.attrs['class'] = ['translated-text']
                            
                            bilingual_container.append(target_tag)
                            bilingual_container.append(trans_tag)
                            # 替换原标签为双语对照容器
                            target_tag.replace_with(bilingual_container)
                            print(f"  🔄 通过BeautifulSoup实现HTML双语对照")
                            return str(soup)
                
                # 如果所有方法都失败，记录警告但不修改内容
                print(f"  ⚠️ 警告：块 {block_index} 替换失败，保持原样")
                return current_content
                
            except Exception as e:
                print(f"  ❌ 块 {block_index} 更新时出错: {str(e)}")
                return current_content
    
    elif file_type == 'ncx':
        # 对于NCX，实现双语对照：保留原文，添加译文
        import re
        # 从翻译后的块中提取文本
        trans_match = re.search(r'<text>(.*?)</text>', translated_block)
        if trans_match:
            trans_text = trans_match.group(1)
            # 从原始块中提取原始文本
            orig_match = re.search(r'<text>(.*?)</text>', original_block)
            if orig_match:
                orig_text = orig_match.group(1)
                # 实现双语对照：保留原文，添加译文
                bilingual_text = f'<text>{orig_text} / {trans_text}</text>'
                # 替换当前内容中的对应部分
                print(f"  🔄 实现NCX双语对照: {orig_text} -> {trans_text}")
                return current_content.replace(
                    f"<text>{orig_text}</text>",
                    bilingual_text,
                    1
                )
        return current_content
    
    elif file_type == 'opf':
        # 对于OPF，实现双语对照：保留原文，添加译文
        import re
        # 识别标签类型
        tag_match = re.search(r'<(\w+)>', original_block)
        if tag_match:
            tag_name = tag_match.group(1)
            # 从翻译后的块中提取文本
            trans_match = re.search(f'<{tag_name}>(.*?)</{tag_name}>', translated_block)
            if trans_match:
                trans_text = trans_match.group(1)
                # 从原始块中提取原始文本
                orig_match = re.search(f'<{tag_name}>(.*?)</{tag_name}>', original_block)
                if orig_match:
                    orig_text = orig_match.group(1)
                    # 实现双语对照：保留原文，添加译文
                    bilingual_text = f'<{tag_name}>{orig_text} / {trans_text}</{tag_name}>'
                    # 替换当前内容中的对应部分
                    print(f"  🔄 实现OPF双语对照: {tag_name}标签 - {orig_text} -> {trans_text}")
                    return current_content.replace(
                        f"<{tag_name}>{orig_text}</{tag_name}>",
                        bilingual_text,
                        1
                    )
        return current_content
    
    return current_content

def update_file_content_by_type(original_content: str, file_type: str, original_blocks: List[str], translated_blocks: List[str]) -> str:
    """
    根据翻译后的块更新原始文件内容（保留此函数用于向后兼容）
    """
    updated_content = original_content
    
    for i, (orig_block, trans_block) in enumerate(zip(original_blocks, translated_blocks)):
        updated_content = update_file_content_by_type_incremental(
            updated_content, file_type, orig_block, trans_block, i
        )
    
    return updated_content

async def main():
    print("🚀 启动 iFlow EPUB 翻译器（完整EPUB结构翻译）")
    print("📋 翻译模式: 上下文感知翻译，保持HTML结构")
    print("🔧 配置: 最大重试次数={}, 超时时间={}秒".format(MAX_RETRY, TIMEOUT_SEC))
    
    # 初始化增强的日志系统
    enhanced_logger = EnhancedLogger("EPUBTranslator", LOG_FILE, LOG_LEVEL)
    enhanced_logger.logger.info("EPUB翻译器启动")
    enhanced_logger.logger.info(f"配置信息 - 超时: {TIMEOUT_SEC}秒, 重试: {MAX_RETRY}次")
    
    # 初始化资源监控器
    resource_monitor = ResourceMonitor(
        max_memory_mb=MAX_MEMORY_MB,
        warning_threshold=MEMORY_WARNING_THRESHOLD
    )
    
    # 添加资源清理回调
    def cleanup_beautifulsoup_cache():
        """清理BeautifulSoup缓存"""
        import bs4
        if hasattr(bs4, '_cached_html5_parser'):
            bs4._cached_html5_parser.clear()
    
    resource_monitor.add_cleanup_callback(cleanup_beautifulsoup_cache)
    
    # 启动资源监控
    await resource_monitor.start_monitoring()
    print("📊 资源监控已启动")

    # 加载状态
    progress_data = load_json(PROGRESS_FILE, {})
    error_log = load_json(ERROR_LOG_FILE, {"errors": []})
    new_terms = load_json(NEW_TERMS_FILE, {"discovered_terms": []})
    glossary = load_glossary()
    
    # 初始化进度数据结构
    if not progress_data or 'meta' not in progress_data:
        progress_data = {
            "meta": {
                "total_files": 0,
                "completed_files": 0,
                "total_blocks": 0,
                "completed_blocks": 0,
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ")
            },
            "files": {}
        }
    
    # 确保所有文件都有正确的字段和统计信息
    completed_files_count = 0
    total_blocks = 0
    completed_blocks = 0
    
    for file_key, file_progress in progress_data["files"].items():
        # 获取当前文件的块数信息
        file_total_blocks = file_progress.get("total_blocks", 0)
        file_completed = file_progress.get("completed", [])
        file_completed_blocks = len(file_completed)
        
        # 更新总块数统计
        total_blocks += file_total_blocks
        completed_blocks += file_completed_blocks
        
        # 更新文件的块数信息
        file_progress["completed_blocks"] = file_completed_blocks
        
        # 确定文件是否已完成
        if file_total_blocks > 0:
            file_progress["is_completed"] = (file_completed_blocks == file_total_blocks)
        else:
            file_progress["is_completed"] = True
        
        # 统计已完成文件
        if file_progress["is_completed"]:
            completed_files_count += 1
    
    # 更新元数据统计
    total_files = len(progress_data["files"])
    progress_data["meta"]["total_files"] = total_files
    progress_data["meta"]["completed_files"] = completed_files_count
    progress_data["meta"]["total_blocks"] = total_blocks
    progress_data["meta"]["completed_blocks"] = completed_blocks
    progress_data["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    
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
    
    # 检查是否需要预生成进度数据（文件存在且有内容时直接使用）
    progress_file_exists = os.path.exists(PROGRESS_FILE) and os.path.getsize(PROGRESS_FILE) > 0
    
    # 如果进度文件不存在或数据为空，预扫描所有文件生成完整进度数据
    if not progress_file_exists or (not progress_data["files"] and all_files):
        print("🔍 预扫描所有文件，生成完整进度数据...")
        
        # 初始化变量
        total_blocks = 0
        completed_files = set()
        
        for filename in all_files:
            file_type = get_file_type(filename)
            source_path = SOURCE_ROOT / filename
            
            # 初始化文件进度
            if filename not in progress_data["files"]:
                progress_data["files"][filename] = {
                    "type": file_type,
                    "total_blocks": 0,
                    "completed_blocks": 0,
                    "completed": [],
                    "failed": [],
                    "current_position": 0,
                    "is_completed": False
                }
                progress_data["meta"]["total_files"] += 1
            
            # 对于可翻译的文件，预提取块数
            if file_type in ['html', 'ncx', 'opf'] and source_path.exists():
                try:
                    original_content = source_path.read_text(encoding='utf-8')
                    blocks = extract_translatable_blocks_by_type(original_content, file_type)
                    
                    # 更新文件总块数
                    file_total_blocks = len(blocks)
                    progress_data["files"][filename]["total_blocks"] = file_total_blocks
                    total_blocks += file_total_blocks
                except Exception as e:
                    print(f"  ⚠️ 预扫描文件 {filename} 时出错: {str(e)}")
                    continue
            else:
                # 非文本文件默认完成
                progress_data["files"][filename]["is_completed"] = True
                completed_files.add(filename)
        
        # 更新总块数
        progress_data["meta"]["total_blocks"] = total_blocks
        progress_data["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # 保存预生成的进度数据
        save_json(progress_data, PROGRESS_FILE)
        print("✅ 预扫描完成，进度数据已保存")
    
    # 从进度数据中构建completed_files集合
    completed_files = set()
    if progress_data and 'files' in progress_data:
        for file_key, file_progress in progress_data["files"].items():
            if file_progress.get("is_completed", False):
                completed_files.add(file_key)
    
    # 初始化 checklist
    update_checklist(all_files, progress_data)
    


    # 创建连接管理器并处理连接问题
    connection_manager = None
    
    # 主循环：处理所有文件，支持连接管理器自动重建
    all_files_completed = False
    while True:
        try:
            # 如果连接管理器不存在或未连接，创建新的连接管理器
            if not connection_manager or not connection_manager.is_connected:
                connection_manager = await create_connection_manager_with_retry(
                    max_retries=5, 
                    delay=3, 
                    timeout=IFLOW_TIMEOUT,
                    logger=enhanced_logger
                )
                print("🔌 已连接到 iFlow 服务")
            
            for file_idx, filename in enumerate(all_files, 1):
                file_type = get_file_type(filename)
                print(f"\n{'='*60}")
                print(f"📄 处理文件 [{file_idx}/{len(all_files)}]: {filename}")
                print(f"📋 文件类型: {file_type}")
                print(f"📊 总体进度: {len(completed_files)}/{len(all_files)} 文件已完成 ({len(completed_files)/len(all_files)*100:.1f}%)")
                print(f"⏰ 当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                file_key = filename

                # 构建源路径和目标路径
                source_path = SOURCE_ROOT / filename
                dest_path = TRANSLATED_ROOT / filename

                # 确保目标目录存在
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                if not source_path.exists():
                    print(f"  ⚠️ 文件不存在，跳过")
                    continue
                
                # 显示文件大小
                file_size = source_path.stat().st_size
                print(f"📦 文件大小: {file_size:,} 字节 ({file_size/1024:.1f} KB)")

                # 确保文件进度数据存在（防止预扫描时遗漏某些文件）
                if file_key not in progress_data["files"]:
                    print(f"  ⚠️ 文件 {file_key} 不在进度数据中，重新初始化")
                    progress_data["files"][file_key] = {
                        "type": file_type,
                        "total_blocks": 0,
                        "completed_blocks": 0,
                        "completed": [],
                        "failed": [],
                        "current_position": 0,
                        "is_completed": False
                    }
                    progress_data["meta"]["total_files"] += 1

                # 根据文件类型决定如何处理
                if file_type in ['html', 'ncx', 'opf']:
                    # 可翻译的文本文件
                    original_content = source_path.read_text(encoding='utf-8')
                    
                    # 根据文件类型提取可翻译块
                    blocks = extract_translatable_blocks_by_type(original_content, file_type)
                    
                    # 更新总块数（如果有变化）
                    current_total_blocks = progress_data["files"][file_key]["total_blocks"]
                    if current_total_blocks == 0 or current_total_blocks != len(blocks):
                        # 计算块数变化
                        old_total = current_total_blocks
                        progress_data["files"][file_key]["total_blocks"] = len(blocks)
                        progress_data["meta"]["total_blocks"] += (len(blocks) - old_total)
                        print(f"  🔄 更新文件块数: {old_total} → {len(blocks)}")

                    # 准备目标内容：如果已有部分翻译，从翻译文件读取；否则从原文开始
                    completed_blocks = len(progress_data["files"][file_key]["completed"])
                    if dest_path.exists() and completed_blocks > 0:
                        print(f"  🔄 检测到部分翻译进度，从已翻译文件恢复")
                        translated_content = dest_path.read_text(encoding='utf-8')
                        
                        # 验证已翻译文件是否真的包含翻译内容
                        sample_jp_check = contains_japanese(translated_content[:500])  # 检查前500字符
                        if sample_jp_check:
                            print(f"  ⚠️ 警告：已翻译文件似乎仍包含大量日文，可能需要重新翻译")
                            # 可以选择从原文重新开始，或继续尝试恢复
                            # 这里选择继续，但会在后续翻译中覆盖日文部分
                        
                        # 初始化translated_blocks数组
                        translated_blocks = [""] * len(blocks)
                        
                        # 对于已完成的块，保持为空字符串（会在增量更新时从文件中读取）
                        # 对于未完成的块，也保持为空字符串
                        print(f"  📋 已完成 {completed_blocks} 个块，将在翻译时逐个更新")
                    else:
                        print(f"  🆕 首次翻译此文件")
                        translated_content = original_content
                        translated_blocks = [""] * len(blocks)

                    # 如果有需要翻译的块，则进行翻译
                    if len(blocks) > 0:
                        # 逐块处理
                        block_start_time = time.time()
                        for i, block in enumerate(blocks):
                            if i in progress_data["files"][file_key]["completed"]:
                                print(f"  ✅ 跳过已翻译块 {i+1}/{len(blocks)}")
                                # 如果块已翻译，从文件中恢复已翻译的块内容
                                translated_blocks[i] = block
                                continue

                            # 计算进度和预计时间
                            completed_count = len(progress_data["files"][file_key]["completed"])
                            remaining = len(blocks) - completed_count
                            if completed_count > 0:
                                elapsed = time.time() - block_start_time
                                avg_time = elapsed / completed_count
                                eta_seconds = avg_time * remaining
                                eta_str = f"{int(eta_seconds//60)}分{int(eta_seconds%60)}秒"
                            else:
                                eta_str = "计算中..."
                            
                            print(f"\n  {'─'*50}")
                            print(f"  🔤 翻译块 [{i+1}/{len(blocks)}] (剩余 {remaining} 块)")
                            print(f"  ⏱️ 预计剩余时间: {eta_str}")
                            
                            # 显示块内容预览
                            block_preview = re.sub(r'<[^>]+>', '', block)[:80]
                            print(f"  📝 内容预览: {block_preview}...")
                            print(f"  📏 块长度: {len(block)} 字符")

                            # 准备上下文
                            prev_blk, curr_blk, next_blk = build_context(blocks, i)

                            # 调用翻译
                            translate_start = time.time()
                            translated_block = await translate_block(
                                connection_manager, curr_blk, prev_blk, next_blk, glossary
                            )
                            translate_duration = time.time() - translate_start
                            print(f"  ⏱️ 翻译耗时: {translate_duration:.1f}秒")

                            # 存储翻译后的块
                            translated_blocks[i] = translated_block

                            # 检查翻译结果是否有效
                            if translated_block is None:
                                print(f"  ⚠️ 警告: 第{i+1}块翻译结果为None")
                                print(f"  🛑 程序将退出，不再继续翻译")
                                raise Exception(f"翻译失败: 第{i+1}块翻译结果为None")
                            
                            # 检查是否是翻译失败的注释
                            if "TRANSLATION_FAILED" in translated_block or "翻译失败" in translated_block:
                                print(f"  ⚠️ 警告: 第{i+1}块翻译失败")
                                print(f"  🛑 程序将退出，不再继续翻译")
                                raise Exception(f"翻译失败: 第{i+1}块翻译失败")

                            # 增量更新：只更新当前翻译的块
                            if dest_path.exists():
                                # 从已翻译的文件中读取当前内容
                                current_content = dest_path.read_text(encoding='utf-8')
                            else:
                                # 如果文件不存在，使用原始内容
                                current_content = original_content
                            
                            # 使用增量更新函数只替换当前块
                            updated_content = update_file_content_by_type_incremental(
                                current_content, file_type, blocks[i], translated_block, i
                            )

                            # 立即写入文件（现在只写入更新后的内容）
                            try:
                                # 创建备份（如果原文件存在）
                                backup_path = dest_path.with_suffix(dest_path.suffix + '.backup')
                                if dest_path.exists():
                                    import shutil
                                    shutil.copy2(dest_path, backup_path)
                                
                                # 写入更新后的内容
                                dest_path.write_text(updated_content, encoding='utf-8')
                                
                                # 验证写入是否成功
                                written_content = dest_path.read_text(encoding='utf-8')
                                if len(written_content) == 0:
                                    raise IOError("写入的文件为空")
                                
                                # 更新内存中的内容，用于后续处理
                                translated_content = updated_content
                                
                                # 删除备份文件（写入成功）
                                if backup_path.exists():
                                    backup_path.unlink()
                                
                            except Exception as write_error:
                                print(f"  ❌ 文件写入失败: {str(write_error)}")
                                print(f"  🔄 尝试恢复...")
                                
                                # 如果有备份，恢复备份
                                if 'backup_path' in locals() and backup_path.exists():
                                    import shutil
                                    shutil.copy2(backup_path, dest_path)
                                    backup_path.unlink()
                                    print(f"  ✅ 已从备份恢复")
                                else:
                                    print(f"  ⚠️ 无法恢复，没有可用的备份")
                                
                                # 记录错误
                                error_log["errors"].append({
                                    "file": filename,
                                    "block": i,
                                    "error": f"文件写入失败: {str(write_error)}",
                                    "content": translated_block
                                })
                                save_json(error_log, ERROR_LOG_FILE)
                                
                                # 跳过当前块的进度更新，但继续翻译下一个块
                                print(f"  ⏱️ 跳过块 {i} 的进度更新，继续下一个块")
                                continue
                            
                            # 质量检查（只有写入成功后才执行）
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

                            # 文件写入成功后，再更新进度（确保进度与文件状态同步）
                            file_progress = progress_data["files"][file_key]
                            
                            # 只在块未标记为完成时添加
                            if i not in file_progress["completed"]:
                                file_progress["completed"].append(i)
                                file_progress["completed_blocks"] += 1
                                progress_data["meta"]["completed_blocks"] += 1
                            
                            file_progress["current_position"] = i
                            
                            # 检查文件是否已完成
                            total_blocks = file_progress["total_blocks"]
                            if total_blocks > 0:
                                file_progress["is_completed"] = (file_progress["completed_blocks"] == total_blocks)
                            else:
                                file_progress["is_completed"] = True
                            
                            # 更新元数据中的完成文件计数
                            if file_progress["is_completed"] and file_key not in completed_files:
                                progress_data["meta"]["completed_files"] += 1
                            
                            # 更新最后修改时间
                            progress_data["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                            
                            # 保存进度数据
                            save_json(progress_data, PROGRESS_FILE)

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
                    
                    # 更新非文本文件的进度状态
                    file_progress = progress_data["files"][file_key]
                    file_progress["is_completed"] = True
                    file_progress["total_blocks"] = 0
                    file_progress["completed_blocks"] = 0
                    file_progress["completed"] = []
                    
                    # 更新元数据
                    if file_key not in completed_files:
                        progress_data["meta"]["completed_files"] += 1
                
                # 更新最后修改时间
                progress_data["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                
                # 保存进度数据
                save_json(progress_data, PROGRESS_FILE)

                # 文件完成
                completed_files.add(filename)
                update_checklist(all_files, progress_data)
                print(f"✅ 完成文件: {filename}")

                # 检查是否所有文件都已完成
                if len(completed_files) == len(all_files):
                    print("\n🎉 所有文件处理完毕！")
                    print(f"输出目录: {TRANSLATED_ROOT.absolute()}")
                    all_files_completed = True
                    break
            
            # 如果所有文件已完成，退出外层循环
            if all_files_completed:
                break
        except (SDKTimeoutError, ConnectionError) as e:
            # 处理连接错误，需要重建连接管理器
            print(f"  🚨 连接失败，尝试重建连接管理器: {e}")
            if connection_manager:
                try:
                    await connection_manager.disconnect()
                except:
                    pass
            connection_manager = None  # 重置连接管理器，触发重新创建
            continue  # 继续循环，创建新的连接管理器
        except Exception as e:
            # 处理其他异常，退出循环
            print(f"  🚨 发生未预期的错误: {e}")
            import traceback
            traceback.print_exc()
            break

    # 确保连接管理器被正确关闭
    try:
        if connection_manager:
            await connection_manager.disconnect()
            print("🔌 连接管理器已断开")
    except Exception as e:
        print(f"⚠️ 断开连接管理器时出错: {e}")
        pass  # 忽略关闭时的错误

    # 停止资源监控并输出统计信息
    try:
        if 'resource_monitor' in locals():
            await resource_monitor.stop_monitoring()
            
            # 输出内存统计信息
            memory_stats = resource_monitor.get_memory_stats()
            if memory_stats:
                print(f"\n📊 资源使用统计:")
                print(f"  当前内存: {memory_stats['current_mb']:.1f}MB")
                print(f"  峰值内存: {memory_stats['peak_mb']:.1f}MB")
                print(f"  平均内存: {memory_stats['avg_mb']:.1f}MB")
                print(f"  最大限制: {memory_stats['max_memory_mb']:.1f}MB")
                print(f"  监控样本: {memory_stats['samples']} 个")
            
            print("📊 资源监控已停止")
    except Exception as e:
        print(f"⚠️ 停止资源监控时出错: {e}")
        pass

    # 输出连接状态报告
    try:
        if 'enhanced_logger' in locals():
            connection_report = enhanced_logger.get_connection_report()
            print(f"\n{connection_report}")
    except Exception as e:
        print(f"⚠️ 生成连接报告时出错: {e}")
        pass

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
