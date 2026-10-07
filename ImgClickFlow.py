# -*- coding: UTF-8 -*-
"""
 Copyright (c) 2026 yushihong. 
 # This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of the MPL was not distributed with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
 作者知乎主页：https://www.zhihu.com/people/mhaksy
 作者：yushihong 
 地址：福建.福州
【10秒快速上手】
  1. 把这个文件放到你的项目里任意位置即可（支持中文路径）。
  2. 在你的脚本里写一行代码：auto.click("你的按钮名称")  然后运行。
  3. 首次运行会在【你的脚本所在目录】自动创建 templates/、logs/、
     screenshots_author/、error_snapshots/ 等文件夹并打印提示。
  4. 把按钮截图（PNG格式）丢进 templates/ 就能用了。

【ImgClickFlow v2.0 核心特性】
  ★ 工作目录自动识别：被其他脚本 import 时，所有文件夹都在【调用方脚本同级目录】
    创建与管理，而不是本模块所在目录；也可用 auto.set_workspace_dir() 指定。
  ★ 多模板文件夹来源：auto.set_template_dir()/add_template_dir() 指定模板目录；
    支持"第一个模板在 temp1、第二个模板在 temp2"，可逐次调用指定来源：
      auto.click("保存", template_dir="temp1")
      auto.click("temp2/确定")            # 名字里带路径即可
      auto.click_seq(["temp1/登录", "temp2/确定"])
  ★ DPI自适应：启动即声明 DPI 感知，图像匹配与点击在统一物理坐标系下完成，
    并对声明失败做了回退，适配 100%/125%/150%/200% 缩放
  ★ 精准选点：按匹配得分取最高分位置（minMaxLoc），不会因为屏幕上有相似图案
    就点到扫描顺序更靠前的那个
  ★ 智能去重：局部极大值 + 邻近合并，接近 O(n)，低阈值下也不会卡死
  ★ 零负担等待：内置重试与超时，超时时间即真实等待时间
  ★ 批量识别：find_all() 返回所有坐标（按匹配分数从高到低排序）
  ★ 链式流程：auto.do() 流式编排，条件分支与循环已修复并支持嵌套
  ★ 区域搜索：click/dclick/rclick/find 支持 rect 参数限定查找区域
  ★ 彩色优先：默认彩色模板匹配，更准确（可区分链接状态），可启用极速模式（灰度）
"""

import sys
import os
import time
import datetime
import subprocess
import logging
import re
import json
import hashlib
import sched
import threading
from ctypes import Structure, c_ulong, windll, byref
from typing import List, Tuple, Union, Optional, Callable, Dict
import ctypes


# ---------- 依赖自动安装设置 ----------
# 默认使用官方 PyPI。如需国内加速，可自行改为：
#   PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
# 注意：改用第三方镜像意味着把依赖来源交给该镜像站，请自行评估。
PIP_INDEX_URL = None
AUTO_INSTALL_DEPS = True          # 设为 False 则只打印提示、不自动安装

_required_packages = {
    'cv2': 'opencv-python',
    'numpy': 'numpy',
    'PIL': 'Pillow',
    'win32gui': 'pywin32',
    'win32api': 'pywin32',
    'win32con': 'pywin32',
    'win32clipboard': 'pywin32',
    'win32print': 'pywin32',
}


def _query_dpi_awareness():
    """查询当前进程 DPI 感知状态：0=不感知 1=System 2=PerMonitor，None=查询失败"""
    try:
        _val = ctypes.c_int()
        if ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(_val)) == 0:
            return _val.value
    except Exception:
        pass
    try:
        _ctx = ctypes.windll.user32.GetThreadDpiAwarenessContext()
        return ctypes.windll.user32.GetAwarenessFromDpiAwarenessContext(_ctx)
    except Exception:
        pass
    return None


def _setup_dpi_awareness():
    """声明进程 DPI 感知，让整个库工作在物理像素坐标系。

    返回 (是否处于DPI感知状态, 模式说明)。
    这里不假设"设置调用成功 == 已感知"：宿主程序（IDE、Qt 程序、带 manifest 的
    程序）可能早已设置过，此时设置调用会失败，但进程其实是感知的。所以设置完
    统一用查询接口确认真实状态，避免据此做出错误的坐标换算。
    """
    try:
        _user32 = ctypes.windll.user32
    except Exception:
        return False, "非Windows"
    try:                                    # Windows 10 1703+：Per-Monitor V2
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True, "Per-Monitor V2"
    except Exception:
        pass
    try:                                    # Windows 8.1+
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:   # 2 = Per-Monitor
            return True, "Per-Monitor"
    except Exception:
        pass
    try:                                    # Vista+
        if _user32.SetProcessDPIAware():
            return True, "System"
    except Exception:
        pass
    _aware = _query_dpi_awareness()          # 设置失败不代表不感知，查询真身
    if _aware in (1, 2, 3):
        return True, "已由宿主程序设置"
    return False, "未设置"


# 进程是否处于 DPI 感知状态（决定坐标换算方式：感知=物理坐标系）
_DPI_AWARE, _DPI_MODE = _setup_dpi_awareness()

_missing = []
for _mod, _pkg in _required_packages.items():
    try:
        __import__(_mod)
    except ImportError:
        _missing.append(_pkg)

if _missing:
    _pkgs = sorted(set(_missing))
    print("=" * 60)
    print("ImgClickFlow 检测到缺少依赖包：" + "、".join(_pkgs))
    if not AUTO_INSTALL_DEPS:
        print("自动安装已关闭（AUTO_INSTALL_DEPS = False），请手动执行：")
        print("  " + sys.executable + " -m pip install " + " ".join(_pkgs))
        print("=" * 60)
        raise SystemExit(1)
    print("正在自动安装；如不希望自动安装，请把 AUTO_INSTALL_DEPS 改为 False")
    print("=" * 60)
    for _pkg in _pkgs:
        _cmd = [sys.executable, "-m", "pip", "install", _pkg]
        if PIP_INDEX_URL:
            _cmd += ["-i", PIP_INDEX_URL]
        try:
            subprocess.check_call(_cmd)
            print(f"  ✓ {_pkg} 安装成功")
        except Exception as _e:
            print(f"  ✗ {_pkg} 安装失败：{_e}")
            print(f"    请手动运行: {sys.executable} -m pip install {_pkg}")
    print("=" * 60)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageGrab
import win32gui
import win32api
import win32con
import win32print
from win32.win32api import GetSystemMetrics
from win32clipboard import (
    GetClipboardData,
    OpenClipboard,
    CloseClipboard,
    EmptyClipboard,
    SetClipboardData,
)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    配置类 - 只需修改这里                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class Config:
    """所有用户可配置的参数。"""

    # ===== 路径设置（相对"工作目录"，也可写绝对路径）=====
    template_dir = "templates"              # 主模板目录
    template_dirs = []                      # 追加的模板搜索目录（按顺序查找）
    log_dir = "logs"
    screenshot_dir = "screenshots_author"   # 用户主动截图存放目录
    error_dir = "error_snapshots"

    # ===== 图像识别参数 =====
    default_similarity = 0.9
    dedup_radius = 5
    forced_image_format = ".png"
    default_fast_mode = False   # 默认关闭极速模式（使用彩色匹配）
    max_match_candidates = 1000     # 单次匹配保留的候选点上限（防止病态输入卡死）
    degenerate_template_std = 1.0   # 模板标准差低于此值视为"纯色模板"，改用逐像素差异匹配

    # ===== 超时与重试 =====
    default_timeout = 10
    retry_interval = 0.2
    post_click_delay = 0.3

    # ===== 调试设置 =====
    verbose_log = True
    auto_screenshot_on_error = True
    uto_screenshot_on_error = True          # 兼容旧拼写，等价于上一行
    trace_enabled = False                   # 是否把每步操作落成 JSONL（默认关）

    # ===== 流程引擎设置 =====
    flow_default_retries = 0
    flow_default_retry_wait = 1


    # ===== 调试录像设置 =====
    debug_max_screenshots = 20   # 最多保留截图张数
    debug_max_age_days = 7       # 自动清理超过7天的任务文件夹（预留参数）
    debug_watermark_bottom_offset = 80 # 水印距底边像素（向上偏移，避开任务栏）
    debug_watermark_right_margin = 10  # 水印距右边像素
    debug_max_failed_tasks = 3         # 最多保留几个失败任务文件夹（预留）


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              工作目录解析（被其他脚本 import 时用调用方脚本目录）              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE = {"dir": None}


def _detect_caller_dir():
    """推断"调用方脚本"所在目录。

    典型场景：C:\\Users\\123\\Desktop\\forpython\\amazon\\测试.py 里 import 本模块，
    此时 templates/、logs/ 等应当建立在 测试.py 同级目录，而不是本模块所在目录。
    优先级：__main__.__file__ → sys.argv[0] → 本模块目录。
    """
    _main = sys.modules.get('__main__')
    _f = getattr(_main, '__file__', None)
    if not _f:
        _a0 = sys.argv[0] if sys.argv else ''
        if _a0 and not _a0.startswith('-') and _a0 not in ('', 'ipython', 'ipykernel_launcher.py'):
            _f = _a0
    if _f:
        try:
            _d = os.path.dirname(os.path.abspath(_f))
            if os.path.isdir(_d):
                return _d
        except Exception:
            pass
    return _MODULE_DIR


def get_workspace_dir():
    """当前工作目录（模板/日志/截图的根目录）"""
    if _WORKSPACE["dir"] is None:
        _WORKSPACE["dir"] = _detect_caller_dir()
    return _WORKSPACE["dir"]


def set_workspace_dir(path, create=True, verbose=True):
    """指定工作目录（模板/日志/截图都相对于它）。

    path 可以是绝对路径，也可以是相对当前工作目录的路径。
    """
    _p = os.path.abspath(os.path.expanduser(str(path)))
    _WORKSPACE["dir"] = _p
    if create:
        ensure_workspace(verbose=verbose)
    _init_logging()
    return _p


def workspace_path(name):
    """把 Config 里的目录名（相对或绝对）解析成绝对路径（相对=相对工作目录）"""
    if not name:
        return get_workspace_dir()
    name = os.path.expanduser(str(name))
    return os.path.normpath(name if os.path.isabs(name) else os.path.join(get_workspace_dir(), name))


def resolve_template_dir(name):
    """解析「追加/显式指定」的模板目录。

    绝对路径直接使用；相对路径优先相对工作目录（即脚本同级），
    如果那里不存在，再尝试相对「主模板目录」，两种书写直觉都能命中。
    """
    if not name:
        return get_workspace_dir()
    name = os.path.expanduser(str(name))
    if os.path.isabs(name):
        return os.path.normpath(name)
    _primary = os.path.normpath(os.path.join(get_workspace_dir(), name))
    if os.path.isdir(_primary):
        return _primary
    _alt = os.path.normpath(os.path.join(workspace_path(Config.template_dir), name))
    if os.path.isdir(_alt):
        return _alt
    return _primary          # 都不存在时按"工作目录同级"处理（可由调用方创建）


def get_template_dirs():
    """返回当前生效的模板搜索目录列表（绝对路径，按查找顺序，已去重）"""
    _dirs = [workspace_path(Config.template_dir)]
    for _d in (Config.template_dirs or []):
        _dirs.append(resolve_template_dir(_d))
    _seen, _out = set(), []
    for _d in _dirs:
        _k = os.path.normcase(_d)
        if _k not in _seen:
            _seen.add(_k)
            _out.append(_d)
    return _out


def set_template_dir(path, create=True, verbose=False):
    """设置主模板目录（覆盖默认的 templates/）。支持绝对路径。"""
    Config.template_dir = str(path)
    if create:
        _mk = ensure_dir(workspace_path(path))
        if _mk and verbose:
            print(f"[ImgClickFlow] 已创建模板目录：{workspace_path(path)}")
    return workspace_path(path)


def add_template_dir(path, create=True, verbose=False):
    """追加一个模板搜索目录（放在查找顺序末尾）。"""
    if Config.template_dirs is None:
        Config.template_dirs = []
    if str(path) not in [str(x) for x in Config.template_dirs]:
        Config.template_dirs.append(str(path))
    if create:
        _mk = ensure_dir(workspace_path(path))
        if _mk and verbose:
            print(f"[ImgClickFlow] 已创建模板目录：{workspace_path(path)}")
    return get_template_dirs()


def set_template_dirs(paths, create=True, verbose=False):
    """一次性设置模板搜索顺序：第一个是主目录，其余为追加目录。"""
    _paths = list(paths or [])
    if not _paths:
        return get_template_dirs()
    set_template_dir(_paths[0], create=create, verbose=verbose)
    Config.template_dirs = []
    for _p in _paths[1:]:
        add_template_dir(_p, create=create, verbose=verbose)
    return get_template_dirs()


def ensure_dir(path):
    """确保目录存在，返回 True 表示本次新建。创建失败只告警不中断（如只读目录）。"""
    try:
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            return True
    except Exception as _e:
        print(f"[ImgClickFlow] 警告：无法创建目录 {path}（{_e}）")
    return False


def ensure_workspace(verbose=True):
    """确保工作目录及各子目录存在；只对本次新建的目录打印提示。"""
    _created = []
    for _attr in ("template_dir", "log_dir", "screenshot_dir", "error_dir"):
        _p = workspace_path(getattr(Config, _attr))
        if ensure_dir(_p):
            _created.append((_attr, _p))
    for _extra in (Config.template_dirs or []):
        _p = workspace_path(_extra)
        if ensure_dir(_p):
            _created.append(("template_dir", _p))
    if _created and verbose:
        print(f"[ImgClickFlow] 工作目录：{get_workspace_dir()}")
        _hint = {
            "template_dir": "把按钮截图（PNG）放进来",
            "log_dir": "运行日志",
            "screenshot_dir": "auto.shot()/auto.snap() 的截图",
            "error_dir": "找图失败时的现场截图",
        }
        for _attr, _p in _created:
            print(f"  已创建 {os.path.relpath(_p, get_workspace_dir())}/  —— {_hint.get(_attr, '')}")
        print("  提示：可用 auto.set_template_dir() / auto.add_template_dir() 指定其它模板目录")
    return [p for _, p in _created]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                     自动初始化                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 兼容旧代码：原来 _base_dir 指向本模块目录；现在指向工作目录（调用方脚本目录）
_base_dir = get_workspace_dir()

_log_level = logging.DEBUG if Config.verbose_log else logging.INFO
_log_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

logger = logging.getLogger('ImgClickFlow')
logger.setLevel(_log_level)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_log_fmt)


def _init_logging():
    """（重新）把文件日志指向当前工作目录的 logs/"""
    global _file_handler
    for _h in list(logger.handlers):
        if isinstance(_h, logging.FileHandler):
            try:
                _h.close()
            except Exception:
                pass
            logger.removeHandler(_h)
    if not any(isinstance(_h, logging.StreamHandler) and not isinstance(_h, logging.FileHandler)
               for _h in logger.handlers):
        logger.addHandler(_console_handler)
    try:
        _log_path = os.path.join(workspace_path(Config.log_dir), f"log_{time.strftime('%Y%m%d')}.log")
        ensure_dir(os.path.dirname(_log_path))
        _file_handler = logging.FileHandler(_log_path, encoding='utf-8')
        _file_handler.setLevel(_log_level)
        _file_handler.setFormatter(_log_fmt)
        logger.addHandler(_file_handler)
    except Exception as _e:
        print(f"[ImgClickFlow] 警告：无法写入日志文件（{_e}）")


ensure_workspace(verbose=True)
_init_logging()
logger.info("=" * 40)
logger.info("ImgClickFlow 办公助手 启动")
logger.info(f"工作目录={get_workspace_dir()}  (DPI感知={_DPI_AWARE}, 模式={_DPI_MODE})")
logger.info(f"模板搜索目录={get_template_dirs()}")
logger.info(f"相似度={Config.default_similarity}, 超时={Config.default_timeout}s")
logger.info(f"默认匹配模式: {'极速(灰度)' if Config.default_fast_mode else '彩色(精准)'}")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    屏幕工具（DPI双重坐标体系）                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def get_real_resolution():
    """获取真实的物理分辨率（DPI缩放前）"""
    hDC = win32gui.GetDC(0)
    try:
        w = win32print.GetDeviceCaps(hDC, win32con.DESKTOPHORZRES)
        h = win32print.GetDeviceCaps(hDC, win32con.DESKTOPVERTRES)
    finally:
        win32gui.ReleaseDC(0, hDC)
    return w, h


def get_screen_size():
    """获取当前进程坐标系下的屏幕分辨率"""
    return GetSystemMetrics(0), GetSystemMetrics(1)


def get_dpi_info():
    """返回 (缩放系数, 是否DPI感知, 感知模式, 物理分辨率, 坐标系分辨率)，供诊断用"""
    return (get_scale_factor(), _DPI_AWARE, _DPI_MODE,
            get_real_resolution(), get_screen_size())


def get_scale_factor():
    """DPI 缩放系数。

    进程已声明 DPI 感知时，Windows 不再对坐标做虚拟化，进程坐标系就是物理像素
    坐标系，系数恒为 1.0 —— 此时千万不能再乘/除一次，否则等于缩放两次。
    进程未声明感知时（回退情况：宿主已锁死或系统过旧），GetSystemMetrics 返回
    虚拟化后的逻辑分辨率，而 DESKTOPHORZRES 仍是物理分辨率，两者之比才是真实
    缩放系数，需要在输出坐标时换回去。
    """
    if _DPI_AWARE:
        return 1.0
    w_real, _ = get_real_resolution()
    w_logic, _ = get_screen_size()
    return w_real / w_logic if w_logic > 0 else 1.0


def logical_to_real(x, y, scale=None):
    """将逻辑坐标转换为真实（物理）坐标"""
    if scale is None:
        scale = get_scale_factor()
    if _DPI_AWARE or scale == 1.0:
        return int(x), int(y)
    return int(x * scale), int(y * scale)


def real_to_logical(x, y, scale=None):
    """将真实（物理）坐标转换为逻辑坐标"""
    if scale is None:
        scale = get_scale_factor()
    if _DPI_AWARE or scale == 1.0:
        return int(x), int(y)
    return int(x / scale), int(y / scale)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    鼠标操作                                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class POINT(Structure):
    _fields_ = [("x", c_ulong), ("y", c_ulong)]


def get_mouse_point():
    """获取鼠标当前逻辑坐标"""
    time.sleep(0.05)
    po = POINT()
    windll.user32.GetCursorPos(byref(po))
    return int(po.x), int(po.y)


def mouse_moveto(x, y):
    """移动鼠标到逻辑坐标"""
    windll.user32.SetCursorPos(int(x), int(y))


def mouse_left_down():
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def mouse_left_up():
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def mouse_right_down():
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)


def mouse_right_up():
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def mouse_left_click(x=None, y=None, k=1):
    """在逻辑坐标处左键单击（支持多次连续点击）"""
    if x is not None and y is not None:
        mouse_moveto(int(x), int(y))
        time.sleep(0.05)
    for _ in range(int(k)):
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def mouse_right_click(x=None, y=None):
    """在逻辑坐标处右键单击"""
    if x is not None and y is not None:
        mouse_moveto(int(x), int(y))
        time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def drag_mouse(x1, y1, x2, y2, duration=0.5):
    """平滑拖拽（逻辑坐标）"""
    mouse_moveto(int(x1), int(y1))
    time.sleep(0.1)
    mouse_left_down()
    time.sleep(0.1)
    for i in range(1, 11):
        cx = int(x1 + (x2 - x1) * i / 10)
        cy = int(y1 + (y2 - y1) * i / 10)
        mouse_moveto(cx, cy)
        time.sleep(duration / 10)
    mouse_left_up()


def scroll_mouse(clicks=3, direction='down', x=None, y=None):
    """
    鼠标滚轮滚动
    clicks: 滚动刻度数
    direction: 'up' 或 'down'
    x, y: 指定鼠标位置（逻辑坐标），不传则使用当前位置
    """
    if x is not None and y is not None:
        mouse_moveto(int(x), int(y))
        time.sleep(0.02)
    delta = 120 if direction == 'up' else -120
    for _ in range(clicks):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(0.01)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    键盘操作                                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

VK_CODE = {
    'backspace': 0x08, 'tab': 0x09, 'clear': 0x0C, 'enter': 0x0D,
    'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12, 'pause': 0x13,
    'caps_lock': 0x14, 'esc': 0x1B, 'spacebar': 0x20,
    'page_up': 0x21, 'page_down': 0x22, 'end': 0x23, 'home': 0x24,
    'left_arrow': 0x25, 'up_arrow': 0x26, 'right_arrow': 0x27, 'down_arrow': 0x28,
    'select': 0x29, 'print': 0x2A, 'execute': 0x2B, 'print_screen': 0x2C,
    'ins': 0x2D, 'del': 0x2E, 'help': 0x2F,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45,
    'f': 0x46, 'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A,
    'k': 0x4B, 'l': 0x4C, 'm': 0x4D, 'n': 0x4E, 'o': 0x4F,
    'p': 0x50, 'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54,
    'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58, 'y': 0x59, 'z': 0x5A,
    'numpad_0': 0x60, 'numpad_1': 0x61, 'numpad_2': 0x62, 'numpad_3': 0x63,
    'numpad_4': 0x64, 'numpad_5': 0x65, 'numpad_6': 0x66, 'numpad_7': 0x67,
    'numpad_8': 0x68, 'numpad_9': 0x69,
    'multiply_key': 0x6A, 'add_key': 0x6B, 'separator_key': 0x6C,
    'subtract_key': 0x6D, 'decimal_key': 0x6E, 'divide_key': 0x6F,
    'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73, 'F5': 0x74,
    'F6': 0x75, 'F7': 0x76, 'F8': 0x77, 'F9': 0x78, 'F10': 0x79,
    'F11': 0x7A, 'F12': 0x7B,
    'num_lock': 0x90, 'scroll_lock': 0x91,
    'left_shift': 0xA0, 'right_shift': 0xA1,
    'left_control': 0xA2, 'right_control': 0xA3,
    'left_win': 0x5B, 'right_win': 0x5C,
    '+': 0xBB, ',': 0xBC, '-': 0xBD, '.': 0xBE, '/': 0xBF,
    '`': 0xC0, ';': 0xBA, '[': 0xDB, '\\': 0xDC, ']': 0xDD, "'": 0xDE,
}


def key_down(keys_str=''):
    """按下按键（不释放）"""
    for c in keys_str:
        vk = VK_CODE.get(c.lower())
        if vk is not None:
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)


def key_up(keys_str=''):
    """释放按键"""
    for c in keys_str:
        vk = VK_CODE.get(c.lower())
        if vk is not None:
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.01)


def key_press(key_str='', k=1):
    """按下并释放指定键k次"""
    vk = VK_CODE.get(key_str.lower())
    if vk is None:
        return
    for _ in range(k):
        win32api.keybd_event(vk, 0, 0, 0)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)


def key_press_plus(keys_list=[]):
    """按下组合键（按顺序全部按下，再反向全部释放）"""
    for item in keys_list:
        vk = VK_CODE.get(item.lower())
        if vk is not None:
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.05)
    for item in reversed(keys_list):
        vk = VK_CODE.get(item.lower())
        if vk is not None:
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    剪贴板操作（升级：上下文管理器）                           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _Clipboard:
    """剪贴板上下文管理器"""

    def __enter__(self):
        OpenClipboard()
        return self

    def __exit__(self, *args):
        CloseClipboard()

    def get_text(self, encoding='gbk'):
        try:
            return GetClipboardData(win32con.CF_TEXT).decode(encoding, errors='ignore')
        except:
            return ""

    def set_text(self, text):
        EmptyClipboard()
        SetClipboardData(win32con.CF_UNICODETEXT, str(text))


def _get_clipboard_text():
    with _Clipboard() as cb:
        return cb.get_text()


def _set_clipboard_text(text):
    with _Clipboard() as cb:
        cb.set_text(text)


def saystring(string, k=1):
    """通过剪贴板粘贴文本（支持中文）"""
    time.sleep(0.1)
    _set_clipboard_text(str(string))
    time.sleep(0.1)
    for _ in range(k):
        key_press_plus(['ctrl', 'v'])
        time.sleep(0.05)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║               图像识别引擎（升级：DPI双重坐标体系，彩色优先）                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _ImageEngine:
    """图像识别引擎

    - 模板可来自多个目录：Config.template_dir（主）+ Config.template_dirs（追加），
      也支持模板名自带路径、或每次调用显式传 template_dir
    - 匹配在物理像素坐标系中进行，返回物理坐标，点击时按需换算
    - 取"匹配得分最高"的位置，而不是扫描顺序第一个过阈值的点
    - 去重：局部极大值（膨胀法，近 O(n)） + 邻近合并
    - 模板图片按「路径 + 修改时间」缓存，重复查找不再反复解码 PNG
    """

    def __init__(self):
        self.imgstyle = Config.forced_image_format
        self.threshold = Config.default_similarity
        self.dedup_radius = Config.dedup_radius
        self.refresh_screen_info()
        self.tempsize = (0, 0)
        self.zuobiao = []
        self._tpl_cache = {}
        # 截图缓存
        self._cached_screenshot = None
        self._cached_screenshot_time = 0
        self._cache_duration = 0.1

    # ---------- 屏幕信息 ----------
    def refresh_screen_info(self):
        """重新读取屏幕分辨率/缩放（切换显示器后可调用）"""
        self.real_w, self.real_h = get_real_resolution()
        self.logic_w, self.logic_h = get_screen_size()
        self.scale = get_scale_factor()
        self.search_area_real = (0, 0, self.real_w, self.real_h)

    @property
    def search_dirs(self):
        """当前模板搜索目录（绝对路径，按顺序）"""
        return get_template_dirs()

    @property
    def path(self):
        """主模板目录（兼容旧属性名）"""
        return self.search_dirs[0]

    # ---------- 模板解析 ----------
    def template_path(self, tempname, template_dir=None):
        """把模板名解析成实际文件路径，找不到返回 None。

        解析顺序：
          1. 绝对路径
          2. 模板名自带相对路径（如 "temp2/确定"）
          3. 本次调用显式指定的 template_dir（可为列表）
          4. Config.template_dir + Config.template_dirs 依次查找（首个命中即用）
        """
        name = str(tempname).strip()
        _exts = ['', Config.forced_image_format, '.png', '.PNG', '.jpg', '.jpeg', '.bmp']
        if os.path.splitext(name)[1].lower() in ('.png', '.jpg', '.jpeg', '.bmp'):
            _exts = ['']            # 名字已带扩展名，不再补

        def _variants(p):
            return [p + e if e else p for e in _exts]

        cands = []
        if os.path.isabs(name):
            cands += _variants(name)

        has_sep = ('/' in name) or ('\\' in name) or (os.sep in name)
        if has_sep and not os.path.isabs(name):
            cands += _variants(workspace_path(name))
            cands += _variants(os.path.abspath(name))

        dirs = []
        if template_dir:
            if isinstance(template_dir, (list, tuple)):
                dirs += [resolve_template_dir(d) for d in template_dir]
            else:
                dirs.append(resolve_template_dir(template_dir))
        dirs += [d for d in self.search_dirs if os.path.normcase(d) not in
                 {os.path.normcase(x) for x in dirs}]

        for d in dirs:
            cands += _variants(os.path.join(d, name))

        for c in cands:
            try:
                if os.path.isfile(c):
                    return os.path.normpath(c)
            except OSError:
                continue
        return None

    def searched_dirs(self, template_dir=None):
        """列出某次查找会用到的目录，用于报错提示"""
        dirs = []
        if template_dir:
            if isinstance(template_dir, (list, tuple)):
                dirs += [resolve_template_dir(d) for d in template_dir]
            else:
                dirs.append(resolve_template_dir(template_dir))
        dirs += [d for d in self.search_dirs if os.path.normcase(d) not in
                 {os.path.normcase(x) for x in dirs}]
        return dirs

    def _load_template(self, tempname, template_dir=None):
        """加载模板图片（支持中文路径 + 多目录查找 + 按修改时间缓存）"""
        template_file = self.template_path(tempname, template_dir)
        if template_file is None:
            _dirs = "\n  ".join(self.searched_dirs(template_dir))
            raise FileNotFoundError(
                f"找不到模板图片 '{tempname}'\n"
                f"已搜索以下目录：\n  {_dirs}\n"
                f"提示：模板名可以自带路径（如 temp2/确定），"
                f"也可以用 auto.set_template_dir()/add_template_dir() 指定模板目录。"
            )
        try:
            _mtime = os.path.getmtime(template_file)
        except OSError:
            _mtime = 0
        _hit = self._tpl_cache.get(template_file)
        if _hit and _hit[0] == _mtime:
            return _hit[1]

        img_array = np.fromfile(template_file, dtype=np.uint8)   # fromfile 支持中文路径
        template = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError(f"模板图片读取失败: {template_file}")
        self._tpl_cache[template_file] = (_mtime, template)
        return template

    # ---------- 截屏 ----------
    def _bbox(self, area_real=None):
        """把 (left, top, w, h) 物理区域转成 ImageGrab 需要的 (l, t, r, b)"""
        if area_real is None:
            area_real = (0, 0, self.real_w, self.real_h)
        left, top, width, height = area_real
        return (int(left), int(top), int(left + width), int(top + height))

    def _grab_screen(self, area_real=None):
        """截取屏幕指定区域，返回 RGB ndarray（物理像素坐标系）"""
        cacheable = (area_real is None)
        if cacheable and self._cached_screenshot is not None:
            if time.time() - self._cached_screenshot_time < self._cache_duration:
                return self._cached_screenshot

        # 关键修复：匹配与截图都在物理像素坐标系下工作，直接把物理框交给
        # ImageGrab。若在此处再除以缩放系数，只会截到屏幕左上角一小块，
        # 右侧/下方的按钮永远找不到，坐标也会整体算错。
        img_pil = ImageGrab.grab(bbox=self._bbox(area_real))
        img_np = np.asarray(img_pil)

        if cacheable:
            self._cached_screenshot = img_np
            self._cached_screenshot_time = time.time()
        return img_np

    def logical_rect_to_real(self, rect):
        """(逻辑 left, top, w, h) → (物理 left, top, w, h)"""
        left, top, width, height = rect
        left_real, top_real = logical_to_real(left, top, self.scale)
        return (left_real, top_real, int(width * self.scale), int(height * self.scale))

    # ---------- 去重 ----------
    def _non_max_suppression(self, points):
        """邻近点合并。points 需已按分数从高到低排序（先到先留=保留最高分）。"""
        if not points:
            return []
        merged = []
        _r = self.dedup_radius
        for pt in points:
            conflict = False
            for m in merged:
                if abs(pt[0] - m[0]) <= _r and abs(pt[1] - m[1]) <= _r:
                    conflict = True
                    break
            if not conflict:
                merged.append(pt)
        return merged

    def _peaks(self, result, threshold):
        """局部极大值筛选：用膨胀替代两两比较，避免低阈值下的 O(n^2) 卡死。

        返回 (xs, ys, scores)，已按分数从高到低排序，并受
        Config.max_match_candidates 上限保护（防止病态输入把进程拖死）。
        """
        k = max(3, int(self.dedup_radius) * 2 + 1)
        kernel = np.ones((k, k), np.uint8)
        dilated = cv2.dilate(result, kernel)
        mask = (result >= threshold) & (result >= dilated - 1e-6)
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return np.array([], dtype=int), np.array([], dtype=int), np.array([])
        scores = result[ys, xs]
        order = np.argsort(-scores)          # 分数降序
        cap = int(getattr(Config, 'max_match_candidates', 1000))
        if cap > 0 and len(order) > cap:
            logger.warning(f"候选点过多（{len(order)}），已截断到前 {cap} 个"
                           f"（可调 Config.max_match_candidates）")
            order = order[:cap]
        return xs[order], ys[order], scores[order]

    def _score_map(self, screen_cmp, temp_cmp):
        """生成"越大越像"的得分图，并自动选择匹配算法。

        关键：模板几乎没有纹理时（例如纯色按钮、纯色区域截图），
        TM_CCOEFF_NORMED 会退化成"整幅图都是 1.0"——实测 800x600 的屏幕会产出
        38 万个候选点，旧版把这些点丢进 O(n^2) 去重会直接把进程卡死（数十亿次比较）。
        这种情况改用 TM_SQDIFF_NORMED（逐像素差异），它对纯色模板是良定义的。
        """
        _std = float(temp_cmp.std())
        if _std < float(getattr(Config, 'degenerate_template_std', 1.0)):
            _sq = cv2.matchTemplate(screen_cmp, temp_cmp, cv2.TM_SQDIFF_NORMED)
            logger.debug(f"模板近乎纯色（标准差 {_std:.3f}），改用 TM_SQDIFF_NORMED 匹配")
            return 1.0 - _sq
        return cv2.matchTemplate(screen_cmp, temp_cmp, cv2.TM_CCOEFF_NORMED)


    def _match_template(self, tempname, area_real, threshold, color_mode='colorful',
                        template_dir=None):
        """模板匹配，返回**按匹配得分从高到低排序**的物理坐标列表。

        与原实现的关键差别：不再按 np.where 的扫描顺序返回，而是先取分数、
        再降序排序，因此 find_best 拿到的 pts[0] 是"最像"的那个，而不是
        "屏幕上最靠左上角"的那个。
        """
        if area_real is None:
            area_real = self.search_area_real   # 使用全屏区域作为默认值
        screen = self._grab_screen(area_real)
        temp_bgr = self._load_template(tempname, template_dir)
        self.tempsize = (temp_bgr.shape[1], temp_bgr.shape[0])

        # 统一成 3 通道
        if screen.ndim == 2:
            screen = cv2.cvtColor(screen, cv2.COLOR_GRAY2RGB)
        elif screen.shape[2] == 4:
            screen = cv2.cvtColor(screen, cv2.COLOR_RGBA2RGB)

        if color_mode == 'colorful':
            screen_cmp = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR)
            temp_cmp = temp_bgr
        else:  # 'gray'
            screen_cmp = cv2.cvtColor(screen, cv2.COLOR_RGB2GRAY)
            temp_cmp = cv2.cvtColor(temp_bgr, cv2.COLOR_BGR2GRAY)

        # 模板比搜索区域还大时 matchTemplate 会报错，提前挡掉并给出可读提示
        sh, sw = screen_cmp.shape[:2]
        th, tw = temp_cmp.shape[:2]
        if th > sh or tw > sw:
            logger.warning(f"模板 '{tempname}'({tw}x{th}) 比搜索区域({sw}x{sh})还大，跳过匹配")
            return []

        result = self._score_map(screen_cmp, temp_cmp)
        if result.size == 0:
            return []

        xs, ys, _scores = self._peaks(result, threshold)
        points = [(int(x + area_real[0]), int(y + area_real[1])) for x, y in zip(xs, ys)]
        return self._non_max_suppression(points)

    def best_match_score(self, tempname, area_real=None, threshold=None, fast_mode=None,
                         template_dir=None):
        """返回最佳匹配的 (物理坐标, 分数)；找不到返回 ((-1,-1), 分数)。"""
        if area_real is None:
            area_real = self.search_area_real
        if threshold is None:
            threshold = self.threshold
        if fast_mode is None:
            fast_mode = Config.default_fast_mode
        mode = 'gray' if fast_mode else 'colorful'
        screen = self._grab_screen(area_real)
        temp_bgr = self._load_template(tempname, template_dir)
        self.tempsize = (temp_bgr.shape[1], temp_bgr.shape[0])
        if screen.ndim == 2:
            screen = cv2.cvtColor(screen, cv2.COLOR_GRAY2RGB)
        elif screen.shape[2] == 4:
            screen = cv2.cvtColor(screen, cv2.COLOR_RGBA2RGB)
        if mode == 'colorful':
            screen_cmp = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR)
            temp_cmp = temp_bgr
        else:
            screen_cmp = cv2.cvtColor(screen, cv2.COLOR_RGB2GRAY)
            temp_cmp = cv2.cvtColor(temp_bgr, cv2.COLOR_BGR2GRAY)
        sh, sw = screen_cmp.shape[:2]
        th, tw = temp_cmp.shape[:2]
        if th > sh or tw > sw:
            return (-1, -1), -1.0
        result = self._score_map(screen_cmp, temp_cmp)
        _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(result)
        x = int(max_l[0] + area_real[0])
        y = int(max_l[1] + area_real[1])
        return (x, y), float(max_v)







    def find_img(self, tempname, area_real=None, threshold=None, template_dir=None):
        """灰度匹配，返回全部匹配点（物理坐标，按匹配分数降序）"""
        if area_real is None:
            area_real = self.search_area_real
        if threshold is None:
            threshold = self.threshold
        self.zuobiao = self._match_template(tempname, area_real, threshold, 'gray', template_dir)
        return self.zuobiao

    def find_img_colorful(self, tempname, area_real=None, threshold=None, template_dir=None):
        """彩色匹配，返回全部匹配点（物理坐标，按匹配分数降序）"""
        if area_real is None:
            area_real = self.search_area_real
        if threshold is None:
            threshold = self.threshold
        self.zuobiao = self._match_template(tempname, area_real, threshold, 'colorful', template_dir)
        return self.zuobiao

    def find_best(self, tempname, timeout=None, area_real=None, threshold=None, fast_mode=None,
                  template_dir=None):
        """
        查找最佳匹配点（匹配得分最高者），返回物理坐标；找不到返回 (-1, -1)。
        fast_mode: True=灰度模式, False=彩色模式(默认), None=使用Config.default_fast_mode
        支持 tempname 为 str 或 list（多个备选模板，按顺序尝试）
        template_dir: 本次查找指定的模板目录（None=使用全局模板搜索目录）
        timeout 即实际最长等待时间（旧版会被放大 5 倍以上）
        """
        timeout = Config.default_timeout if timeout is None else float(timeout)
        threshold = self.threshold if threshold is None else threshold
        if fast_mode is None:
            fast_mode = Config.default_fast_mode
        candidates = [tempname] if isinstance(tempname, str) else list(tempname)
        mode = 'gray' if fast_mode else 'colorful'

        deadline = time.time() + max(timeout, 0.0)
        while True:
            for name in candidates:
                pts = self._match_template(name, area_real, threshold, mode, template_dir)
                if pts:
                    return pts[0]          # pts 已按分数降序 → 这是最像的那个
            _left = deadline - time.time()
            if _left <= 0:
                break
            time.sleep(min(Config.retry_interval, _left))

        logger.warning(f"匹配失败: {tempname}，阈值={threshold}，超时={timeout}s，"
                       f"模式={'灰度' if fast_mode else '彩色'}")
        return -1, -1


    def find_all(self, tempname, timeout=None, area_real=None, threshold=None, fast_mode=None,
                 template_dir=None):
        """查找所有匹配目标，返回物理坐标列表（按匹配分数降序）"""
        timeout = Config.default_timeout if timeout is None else float(timeout)
        threshold = self.threshold if threshold is None else threshold
        if fast_mode is None:
            fast_mode = Config.default_fast_mode
        mode = 'gray' if fast_mode else 'colorful'

        deadline = time.time() + max(timeout, 0.0)
        while True:
            pts = self._match_template(tempname, area_real, threshold, mode, template_dir)
            if pts:
                return pts
            _left = deadline - time.time()
            if _left <= 0:
                break
            time.sleep(min(Config.retry_interval, _left))

        return []

    def click_by_img(self, tempname, timeout=None, fast_mode=None, template_dir=None, rect=None):
        """找图并点击中心（自动转换物理→逻辑坐标）。timeout 即真实最长等待时间。"""
        timeout = Config.default_timeout if timeout is None else float(timeout)
        area_real = self.logical_rect_to_real(rect) if rect is not None else None
        result = self.find_best(tempname, timeout=timeout, area_real=area_real,
                                fast_mode=fast_mode, template_dir=template_dir)
        if result != (-1, -1):
            real_x, real_y = result
            center_real_x = real_x + self.tempsize[0] // 2
            center_real_y = real_y + self.tempsize[1] // 2
            logic_x, logic_y = real_to_logical(center_real_x, center_real_y, self.scale)
            mouse_left_click(logic_x, logic_y)
            return True

        logger.warning(f"点击失败: {tempname}，超时={timeout}s")
        return False

    def find_one_logical(self, tempname, timeout=None, fast_mode=None, template_dir=None,
                         threshold=None):
        """查找并返回逻辑坐标（供 auto.find 使用）"""
        result = self.find_best(tempname, timeout, None, threshold, fast_mode, template_dir)
        if result != (-1, -1):
            return real_to_logical(result[0], result[1], self.scale)
        return -1, -1
    
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    定位融合层（支持 fast_mode 参数）                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _LocatorFusion:
    """定位融合层：对外统一使用逻辑坐标，支持指定模板来源目录"""

    def __init__(self):
        self.image_engine = _ImageEngine()

    def _area_real(self, area):
        """把逻辑区域的 (left, top, w, h) 转成物理区域"""
        if area is None:
            return None
        return self.image_engine.logical_rect_to_real(area)

    def find(self, tempname, timeout=None, area=None, threshold=None, fast_mode=None,
             template_dir=None):
        """查找目标，返回逻辑坐标 (x, y) 或 (-1, -1)"""
        result = self.image_engine.find_best(tempname, timeout, self._area_real(area),
                                             threshold, fast_mode, template_dir)
        if result != (-1, -1):
            return real_to_logical(result[0], result[1], self.image_engine.scale)
        return -1, -1

    def find_with_score(self, tempname, timeout=None, area=None, threshold=None, fast_mode=None,
                        template_dir=None):
        """查找目标，返回 (逻辑坐标, 匹配分数)"""
        (rx, ry), score = self.image_engine.best_match_score(
            tempname, self._area_real(area), threshold, fast_mode, template_dir)
        if (rx, ry) != (-1, -1):
            return real_to_logical(rx, ry, self.image_engine.scale), score
        return (-1, -1), score

    def find_all(self, tempname, timeout=None, area=None, threshold=None, fast_mode=None,
                 template_dir=None):
        """查找所有目标，返回逻辑坐标列表（按匹配分数降序）"""
        real_coords = self.image_engine.find_all(tempname, timeout, self._area_real(area),
                                                 threshold, fast_mode, template_dir)
        return [real_to_logical(x, y, self.image_engine.scale) for x, y in real_coords]

    def click(self, tempname, timeout=None, fast_mode=None, template_dir=None, rect=None):
        return self.image_engine.click_by_img(tempname, timeout, fast_mode, template_dir, rect)

    def get_template_size(self):
        return self.image_engine.tempsize

    def get_scale_factor(self):
        return self.image_engine.scale

    def find_template_file(self, tempname, template_dir=None):
        """返回模板实际命中的文件路径（找不到返回 None）"""
        return self.image_engine.template_path(tempname, template_dir)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    操作追踪器                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _StepRecorder:
    """操作追踪器 + 可选的结构化落盘（JSONL）

    - enabled=True       ：在内存里累积步骤，可生成 HTML 报告
    - trace_enabled=True ：每步追加写一行 JSON 到 logs/trace_<run_id>.jsonl，
      即使调试截图被清理（默认只留 20 张），也能保留完整可检索的操作历史
    """

    def __init__(self):
        self.enabled = False
        self.trace_enabled = bool(getattr(Config, 'trace_enabled', False))
        self.steps = []
        self.flow_name = "未命名流程"
        self.start_time = None
        self._trace_path = None
        self._trace_lock = threading.Lock()

    def on(self):
        self.enabled = True
        self.steps = []
        self.start_time = time.time()
        logger.info("操作追踪已开启")

    def off(self):
        self.enabled = False
        logger.info("操作追踪已关闭")

    def trace(self, on=True):
        """打开/关闭结构化落盘（JSONL）"""
        self.trace_enabled = bool(on)
        logger.info(f"操作追踪落盘: {'开启' if self.trace_enabled else '关闭'}")
        print(f"[trace] 结构化操作记录: {'开启' if self.trace_enabled else '关闭'}"
              + (f" → {workspace_path(Config.log_dir)}" if self.trace_enabled else ""))
        return self.trace_enabled

    def trace_path(self, run_id=None):
        """指定 run_id 的 trace 文件路径；不传则返回当前正在写的那个"""
        if run_id:
            return os.path.join(workspace_path(Config.log_dir), f"trace_{run_id}.jsonl")
        return self._trace_path

    def _ensure_trace_file(self, run_id):
        if (not self._trace_path) or (run_id and run_id not in self._trace_path):
            _dir = workspace_path(Config.log_dir)
            ensure_dir(_dir)
            self._trace_path = os.path.join(_dir, f"trace_{run_id or 'run'}.jsonl")
        return self._trace_path

    def record(self, action, target, result, duration, extra=None, flow=None, run_id=None):
        if not (self.enabled or self.trace_enabled):
            return
        _row = {
            'step': len(self.steps) + 1,
            'action': action,
            'target': str(target),
            'result': 'success' if result else 'failed',
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'duration': round(float(duration), 3),
        }
        if extra:
            _row.update(extra)
        self.steps.append(_row)

        if not self.trace_enabled:
            return
        try:
            _path = self._ensure_trace_file(run_id)
            _rec = dict(_row)
            _rec.update({
                'run_id': run_id or 'run',
                'flow': flow or self.flow_name,
                'pid': os.getpid(),
                'script': os.path.basename(sys.argv[0]) if sys.argv else '',
                'screen': list(get_screen_size()),
                'dpi_scale': get_scale_factor(),
                'dpi_aware': bool(_DPI_AWARE),
            })
            with self._trace_lock, open(_path, 'a', encoding='utf-8') as _f:
                _f.write(json.dumps(_rec, ensure_ascii=False) + "\n")
        except Exception as _e:
            logger.debug(f"trace 写入失败: {_e}")

    # ---------- 读取与查询（直接读 JSONL，不依赖数据库） ----------
    @staticmethod
    def read_trace(path=None):
        """读取 trace JSONL 为 list[dict]；不传 path 则读 logs/ 下全部 trace"""
        if path:
            _paths = [path]
        else:
            _dir = workspace_path(Config.log_dir)
            _paths = []
            if os.path.isdir(_dir):
                _paths = [os.path.join(_dir, _f) for _f in sorted(os.listdir(_dir))
                          if _f.startswith('trace_') and _f.endswith('.jsonl')]
        _rows = []
        for _p in _paths:
            try:
                with open(_p, encoding='utf-8') as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _rows.append(json.loads(_line))
                        except Exception:
                            pass
            except Exception as _e:
                logger.debug(f"读取 trace 失败 {_p}: {_e}")
        return _rows

    @classmethod
    def slowest(cls, n=10):
        """最慢的 N 个步骤"""
        _rows = [r for r in cls.read_trace() if isinstance(r.get('duration'), (int, float))]
        _rows.sort(key=lambda r: -r['duration'])
        return _rows[:int(n)]

    @classmethod
    def weak_templates(cls, threshold=0.95):
        """匹配分数长期偏低的模板 —— 说明该截图快不靠谱了，建议重新截图"""
        _agg = {}
        for _r in cls.read_trace():
            _s, _p = _r.get('score'), _r.get('template_path')
            if _s is None or not _p:
                continue
            _agg.setdefault(_p, []).append(float(_s))
        _out = []
        for _p, _ss in _agg.items():
            _avg = sum(_ss) / len(_ss)
            if _avg < float(threshold):
                _out.append({'template': _p, 'count': len(_ss),
                             'avg_score': round(_avg, 4), 'min_score': round(min(_ss), 4)})
        _out.sort(key=lambda d: d['avg_score'])
        return _out

    @classmethod
    def failures(cls):
        """历史失败点汇总"""
        _agg = {}
        for _r in cls.read_trace():
            if _r.get('result') != 'failed':
                continue
            _k = f"{_r.get('action')}({_r.get('target')})"
            _agg[_k] = _agg.get(_k, 0) + 1
        return sorted(({'step': _k, 'count': _v} for _k, _v in _agg.items()),
                      key=lambda d: -d['count'])

    def generate_report(self):
        if not self.steps:
            print("没有可报告的执行记录。")
            return None

        total = len(self.steps)
        success = sum(1 for s in self.steps if s['result'] == 'success')
        failed = sum(1 for s in self.steps if s['result'] == 'failed')
        total_duration = sum(s['duration'] for s in self.steps)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>执行报告 - {self.flow_name}</title>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
        .header {{ background: #fff; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .summary {{ display: flex; gap: 20px; }}
        .summary-card {{ flex: 1; background: #fff; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .summary-card .number {{ font-size: 36px; font-weight: bold; }}
        .success {{ color: #4CAF50; }}
        .failed {{ color: #f44336; }}
        .steps {{ background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .step {{ border-left: 3px solid #ddd; padding: 10px 15px; margin: 10px 0; }}
        .step.success {{ border-left-color: #4CAF50; }}
        .step.failed {{ border-left-color: #f44336; background: #fff5f5; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>执行报告</h1>
        <p>流程名称: {self.flow_name}</p>
        <p>总耗时: {total_duration:.2f}秒</p>
    </div>
    <div class="summary">
        <div class="summary-card"><div>总步骤</div><div class="number">{total}</div></div>
        <div class="summary-card"><div class="success">成功</div><div class="number success">{success}</div></div>
        <div class="summary-card"><div class="failed">失败</div><div class="number failed">{failed}</div></div>
    </div>
    <div class="steps"><h2>步骤详情</h2>"""
        for step in self.steps:
            sc = 'success' if step['result'] == 'success' else 'failed'
            icon = '✓' if step['result'] == 'success' else '✗'
            html += f"""<div class="step {sc}"><span>#{step['step']} {step['action']}({step['target']}) {icon} {step['duration']}s</span></div>"""
        html += "</div></body></html>"

        _log_dir = workspace_path(Config.log_dir)
        ensure_dir(_log_dir)
        report_path = os.path.join(_log_dir, f"report_{time.strftime('%Y%m%d_%H%M%S')}.html")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html)
        logger.info(f"执行报告已保存: {report_path}")
        print(f"执行报告已保存: {report_path}")
        print(f"总步骤: {total} | 成功: {success} | 失败: {failed} | 总耗时: {total_duration:.2f}s")
        return report_path


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    错误处理器                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _ErrorHandler:
    """错误处理"""

    @staticmethod
    def _safe_name(template_name):
        """把模板名（可能带路径或非法字符）压成安全的文件名片段"""
        _s = re.sub(r'[\\/:*?"<>|\s]+', '_', str(template_name))
        _s = _s.strip('._') or 'template'
        return _s[:60]

    @staticmethod
    def save_error_screenshot(template_name):
        if not getattr(Config, 'auto_screenshot_on_error', True):
            return None
        try:
            _dir = workspace_path(Config.error_dir)
            ensure_dir(_dir)
            path = os.path.join(
                _dir,
                f"not_found_{_ErrorHandler._safe_name(template_name)}_"
                f"{time.strftime('%H%M%S')}.png"
            )
            ImageGrab.grab().save(path)
            logger.info(f"错误截图已保存: {path}")
            return path
        except Exception as _e:
            logger.debug(f"错误截图保存失败: {_e}")
            return None

    @staticmethod
    def format_error_message(template_name, similarity, timeout):
        return (
            f"✗ 查找{template_name}图片失败，流程终止\n"
            f"  相似度阈值: {similarity}\n"
            f"  超时时间: {timeout}秒\n"
            f"  可能原因:\n"
            f"   1. 按钮尚未出现\n"
            f"   2. 按钮外观已变化（需重新截图）\n"
            f"   3. 模板截图是JPG格式（必须用PNG）"
        )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    环境检测器                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _EnvChecker:
    """环境诊断"""

    @staticmethod
    def check():
        print("\n" + "=" * 60)
        print("环境诊断")
        print("=" * 60)

        print(f"\n工作目录: {get_workspace_dir()}")
        print(f"模块目录: {_MODULE_DIR}")
        print(f"Python版本: {sys.version}")

        real_w, real_h = get_real_resolution()
        logic_w, logic_h = get_screen_size()
        scale = get_scale_factor()
        print(f"\n屏幕与DPI:")
        print(f"  物理分辨率: {real_w}x{real_h}")
        print(f"  进程坐标系: {logic_w}x{logic_h}")
        print(f"  DPI感知: {'是' if _DPI_AWARE else '否'}（{_DPI_MODE}）")
        print(f"  缩放系数: {scale:.2f}")
        if _DPI_AWARE:
            print(f"  进程工作在物理像素坐标系，匹配与点击无需额外缩放")
        else:
            print(f"  ⚠ 未能声明DPI感知，将按 {scale:.2f} 倍做坐标回退换算")

        print(f"\n模板搜索目录（按查找顺序）:")
        _total = 0
        for _idx, _d in enumerate(get_template_dirs(), 1):
            if os.path.isdir(_d):
                _png = [f for f in os.listdir(_d) if f.lower().endswith('.png')]
                _non = [f for f in os.listdir(_d)
                        if f.lower().endswith(('.jpg', '.jpeg', '.bmp'))]
                _total += len(_png) + len(_non)
                print(f"  {_idx}. {_d}")
                print(f"     模板 {len(_png) + len(_non)} 个（PNG {len(_png)}，非PNG {len(_non)}）")
                if _non:
                    print(f"     ⚠ 非PNG格式: {_non}（建议统一用PNG）")
            else:
                print(f"  {_idx}. {_d}   （目录不存在）")
        print(f"  合计: {_total} 个模板")

        print(f"\n核心依赖:")
        for mod_name in ['cv2', 'numpy', 'PIL', 'win32gui', 'win32clipboard']:
            try:
                __import__(mod_name)
                print(f"  {mod_name}: OK")
            except ImportError:
                print(f"  {mod_name}: 缺失")

        print("\n" + "=" * 60)

    @staticmethod
    def check_templates():
        engine = _ImageEngine()
        _dirs = get_template_dirs()
        _all = []
        for _d in _dirs:
            if os.path.isdir(_d):
                _all += [(os.path.basename(f)[:-4], os.path.join(_d, f))
                         for f in os.listdir(_d) if f.lower().endswith('.png')]
        if not _all:
            print("没有找到任何模板目录或PNG模板")
            print("模板搜索目录: " + " ; ".join(_dirs))
            return

        print(f"\n模板健康检查 - 共 {len(_all)} 个模板")
        print("-" * 50)
        ok_count = 0
        for name, _path in _all:
            result = engine.find_best(name, timeout=1)
            if result != (-1, -1):
                print(f"  OK  {name}   ({_path})")
                ok_count += 1
            else:
                print(f"  未找到  {name}   ({_path})")

        print("-" * 50)
        print(f"当前可见: {ok_count}/{len(_all)}")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              内部辅助函数（消除重复，行为与原来完全等价）                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _logic_size_from_phys(tw, th, scale):
    """根据物理模板大小和缩放系数返回逻辑宽高"""
    return int(tw / scale), int(th / scale)

def _center_from_rect(lx, ly, logic_w, logic_h):
    """根据左上角逻辑坐标和逻辑宽高返回中心坐标"""
    return int(lx + logic_w / 2), int(ly + logic_h / 2)

def _build_rect_focus_shape(lx, ly, logic_w, logic_h):
    """构造矩形标记字典，用于录像截图"""
    return {'type': 'rectangle', 'left': lx, 'top': ly, 'width': logic_w, 'height': logic_h}

def _build_circle_focus_shape(x, y):
    """构造圆形标记字典"""
    return {'type': 'circle', 'center': (x, y)}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    流程引擎（支持 rect 和 fast_mode）                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _FlowAbort(Exception):
    """内部异常：中止整个流程。

    循环体里某一步失败时，用它穿过任意层循环直接把失败抛到 run()，
    避免旧版"循环体失败却 return 成功"的静默丢数据问题。
    """


class _BreakLoop(Exception):
    """内部异常：跳出当前循环（break_if 使用）"""


class _ContinueLoop(Exception):
    """内部异常：跳过本次循环剩余步骤（continue_if 使用）"""


# ── 控制块定义表：起始指令 → (结束指令, 分支分隔符, 用于报错的中文名) ──
_BLOCK_DEFS = {
    'if_start':     ('endif',     ('else', 'else_if'), 'if_see()'),
    'if_not_start': ('endif',     ('else', 'else_if'), 'if_not_see()'),
    'if_count':     ('endif',     ('else', 'else_if'), 'if_count()'),
    'if_color':     ('endif',     ('else', 'else_if'), 'if_color()'),
    'if_window':    ('endif',     ('else', 'else_if'), 'if_window()'),
    'if_python':    ('endif',     ('else', 'else_if'), 'if_python()'),
    'for_start':    ('for_end',   (),                  'for_data()'),
    'while_start':  ('while_end', (),                  'while_see()'),
    'until_start':  ('until_end', (),                  'until()'),
    'try_start':    ('try_end',   ('onfail',),         'try_do()'),
}
_BLOCK_STARTS = set(_BLOCK_DEFS)
_BLOCK_ENDS = {_v[0] for _v in _BLOCK_DEFS.values()}

# 只读（不改动鼠标键盘/输入）的动作，dry_run 下照常执行
_DRY_RUN_ALLOWED = {
    'moveto', 'pause', 'snap', 'wait', 'wait_not', 'wait_idle', 'wait_count', 'wait_any',
    'expect', 'expect_not', 'assert_count', 'mark', 'highlight', 'log', 'read_clipboard',
    'call_python', 'activate_window', 'assert_foreground', 'find_all',
}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║        窗口焦点工具（防止把输入打进错误的窗口）                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def get_foreground_window_title():
    """当前前台窗口标题（拿不到返回空串）"""
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def title_matches(title, pattern):
    """标题匹配：pattern 用 | 分隔多个关键字，任一命中即算匹配（不区分大小写）"""
    if not pattern:
        return False
    _t = str(title or "").lower()
    for _kw in str(pattern).split('|'):
        _kw = _kw.strip().lower()
        if _kw and _kw in _t:
            return True
    return False


def _force_foreground(hwnd):
    """把窗口切到前台（绕过 Windows 前台锁定）。

    直接调 SetForegroundWindow 在"调用进程不是前台进程"时会被拒绝（error 5），
    这里用 AttachThreadInput 临时把本线程输入挂到目标/前台线程上再切。
    """
    try:
        _user32 = ctypes.windll.user32
        _kernel32 = ctypes.windll.kernel32
        _fg = _user32.GetForegroundWindow()
        if _fg == hwnd:
            return True
        _cur = _kernel32.GetCurrentThreadId()
        _tgt = _user32.GetWindowThreadProcessId(hwnd, None)
        _fgt = _user32.GetWindowThreadProcessId(_fg, None) if _fg else 0
        _attached = []
        for _th in (_tgt, _fgt):
            if _th and _th != _cur:
                if _user32.AttachThreadInput(_cur, _th, True):
                    _attached.append(_th)
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetActiveWindow(hwnd)
        finally:
            for _th in _attached:
                _user32.AttachThreadInput(_cur, _th, False)
        time.sleep(0.2)
        return _user32.GetForegroundWindow() == hwnd
    except Exception as _e:
        logger.debug(f"切换前台失败: {_e}")
        return False


def activate_window(pattern, timeout=5.0):
    """把标题匹配的可见窗口切到前台；返回是否成功。

    pattern 支持 '记事本|Notepad' 这种多关键字写法。
    """
    _deadline = time.time() + max(float(timeout), 0)
    while True:
        _found = []

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            _t = win32gui.GetWindowText(hwnd)
            if _t and title_matches(_t, pattern):
                _found.append((hwnd, _t))

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            pass

        for _hwnd, _t in _found:
            try:
                if win32gui.IsIconic(_hwnd):
                    win32gui.ShowWindow(_hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
            if _force_foreground(_hwnd) and title_matches(get_foreground_window_title(), pattern):
                logger.info(f"已激活窗口: {_t}")
                return True
        if time.time() >= _deadline:
            logger.warning(f"未找到/无法激活窗口: {pattern}")
            return False
        time.sleep(0.3)


def find_windows(pattern):
    """返回标题匹配的可见窗口列表 [(hwnd, title), ...]"""
    _found = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _t = win32gui.GetWindowText(hwnd)
        if _t and title_matches(_t, pattern):
            _found.append((hwnd, _t))

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return _found


def highlight_on_screen(x, y, w, h, seconds=2.0, color='red', label=None):
    """在屏幕上把一块区域框出来闪一下（用 tkinter 透明置顶窗，标准库实现）。

    用于"让用户肉眼确认引擎认的是不是这个按钮"。
    """
    try:
        import tkinter as tk
    except Exception:
        logger.warning("highlight 需要 tkinter，当前环境不可用，已跳过")
        return False
    try:
        _root = tk.Tk()
        _root.overrideredirect(True)
        _root.attributes('-topmost', True)
        try:
            _root.attributes('-alpha', 0.4)
        except Exception:
            pass
        _w, _h = max(int(w), 12), max(int(h), 12)
        _root.geometry(f"{_w}x{_h}+{int(x)}+{int(y)}")
        _cv = tk.Canvas(_root, width=_w, height=_h, highlightthickness=0, bg='black')
        _cv.pack()
        _cv.create_rectangle(2, 2, _w - 2, _h - 2, outline=color, width=4)
        if label:
            _cv.create_text(6, 6, anchor='nw', text=label, fill=color)
        _root.update()
        _root.after(int(max(float(seconds), 0.1) * 1000), _root.destroy)
        _root.mainloop()
        return True
    except Exception as _e:
        logger.debug(f"highlight 失败: {_e}")
        return False


def frame_diff_ratio(a, b):
    """两帧图像的差异像素占比（0~1），用于判断画面是否还在变化"""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    try:
        _d = cv2.absdiff(a, b)
        _changed = np.count_nonzero(_d.max(axis=2) > 12)
        return float(_changed) / float(a.shape[0] * a.shape[1])
    except Exception:
        return 1.0


def _parse_color(color):
    """'#RRGGBB' / 'RRGGBB' / (r,g,b) → (r,g,b)；解析失败返回 None"""
    try:
        if isinstance(color, (tuple, list)) and len(color) >= 3:
            return tuple(int(v) for v in color[:3])
        _s = str(color).strip().lstrip('#')
        if len(_s) == 6:
            return tuple(int(_s[_i:_i + 2], 16) for _i in (0, 2, 4))
    except Exception:
        pass
    return None


def _compare_count(value, op, n):
    """按 op 比较 value 与 n（op: >= <= == != > <）"""
    try:
        _n = int(n)
    except Exception:
        return False
    return {
        '>=': value >= _n, '<=': value <= _n, '==': value == _n,
        '!=': value != _n, '>': value > _n, '<': value < _n,
    }.get(str(op), False)


_TEMPLATE_SHA_CACHE = {}


def _file_sha1_short(path):
    """文件 sha1 前 16 位（按 路径+修改时间 缓存），用于识别模板有没有被换过"""
    if not path:
        return None
    try:
        _mt = os.path.getmtime(path)
    except OSError:
        return None
    _key = (path, _mt)
    if _key in _TEMPLATE_SHA_CACHE:
        return _TEMPLATE_SHA_CACHE[_key]
    try:
        with open(path, 'rb') as _f:
            _h = hashlib.sha1(_f.read()).hexdigest()[:16]
    except Exception:
        _h = None
    _TEMPLATE_SHA_CACHE[_key] = _h
    return _h


def type_char(ch):
    """输入单个字符：ASCII 用 keybd_event，其它字符回退到剪贴板粘贴"""
    try:
        _u32 = ctypes.windll.user32
        if ord(ch) < 128:
            _vk = _u32.VkKeyScanW(ch)
            if _vk != -1:
                _v = _vk & 0xFF
                _shift = (_vk >> 8) & 0x01
                _up = 0x0002
                if _shift:
                    _u32.keybd_event(0x10, 0, 0, 0)
                _u32.keybd_event(_v, 0, 0, 0)
                _u32.keybd_event(_v, 0, _up, 0)
                if _shift:
                    _u32.keybd_event(0x10, 0, _up, 0)
                return True
    except Exception:
        pass
    saystring(ch)
    return True


class Flow:
    """流程引擎"""

    def __init__(self, locator, recorder, error_handler, debug_recorder=None, name=None):
        self.locator = locator
        self.recorder = recorder
        self.error_handler = error_handler
        self._debug_recorder = debug_recorder
        self.name = name or "未命名流程"
        self.steps = []

        # ---- 重试设置 ----
        self._retry_count = 0
        self._retry_wait = 0
        self._retry_mode = "all"          # all=整体重放(兼容旧行为) step=只重跑失败步骤

        # ---- 调试/节奏 ----
        self._step_mode = False
        self._step_pace = 0
        self._dry_run = False             # dry_run：只报告"本来会点哪里"，不出手

        # ---- 执行状态 ----
        self._loop_stack = []             # 循环上下文栈（支持嵌套循环的 {item}/{index}）
        self._step_ok = set()             # step 模式下已成功的步骤键
        self._failed_step = None          # (action, target, 原因)
        self._cancelled = False           # cancel() 置位，解释器每轮检查
        self._has_run = False
        self._deadline = None             # 流程整体死线 time.time()
        self._timeout = None              # 流程整体超时（秒），timeout() 设置
        self._vars = {}                   # 运行时命名变量 {name: value}
        self._run_id = None
        self._match_seq = 0
        self.last_result = None           # run() 结束后写入结构化结果
        self.last_matched = None          # wait_any 命中的目标名
        self._saved_pos = None            # remember_pos 记住的鼠标位置
        self._dry_run_actions = []        # dry_run 期间"本来会做"的动作列表
        self._last_match = None           # 最近一次匹配详情（写进 trace）

    # ---------- 静态校验 ----------
    def validate(self):
        """静态校验控制块是否配对，返回错误信息列表（空列表=通过）。

        旧版漏写 endif/end_for 只会静默跳过剩余流程并返回成功，这里改成显式报错。
        本版覆盖全部块类型：if_* / for / while / until / try。
        """
        errors = []
        stack = []          # [[起始action, 下标, 已用过的分支分隔符集合], ...]
        for idx, (action, _target, kwargs) in enumerate(self.steps):
            if action in _BLOCK_STARTS:
                if action in ('while_start', 'until_start'):
                    _kw = kwargs or {}
                    if _kw.get('max') is None and _kw.get('timeout') is None:
                        errors.append(
                            f"第{idx + 1}条指令 {_BLOCK_DEFS[action][2]} 必须指定 "
                            f"max= 或 timeout=（防止写出无上限死循环）")
                stack.append([action, idx, set()])
            elif action in _BLOCK_ENDS:
                if not stack:
                    errors.append(f"第{idx + 1}条指令 {action}() 没有匹配的起始指令")
                elif _BLOCK_DEFS[stack[-1][0]][0] != action:
                    errors.append(
                        f"第{idx + 1}条指令 {action}() 与第{stack[-1][1] + 1}条指令 "
                        f"{_BLOCK_DEFS[stack[-1][0]][2]} 不匹配")
                    stack.pop()
                else:
                    stack.pop()
            elif action in ('else', 'else_if', 'onfail'):
                _seps = _BLOCK_DEFS[stack[-1][0]][1] if stack else ()
                if not stack or action not in _seps:
                    errors.append(f"第{idx + 1}条指令 {action}() 不在任何条件块内")
                elif action in stack[-1][2]:
                    errors.append(f"第{idx + 1}条指令 {action}() 同一个块内重复出现")
                else:
                    stack[-1][2].add(action)
        for _act, _idx, _ in stack:
            errors.append(
                f"第{_idx + 1}条指令 {_BLOCK_DEFS[_act][2]} 缺少 {_BLOCK_DEFS[_act][0]}()")
        return errors

    def _check_pairing(self):
        errors = self.validate()
        if errors:
            raise ValueError("流程控制块配对错误：\n  - " + "\n  - ".join(errors))

    # ---------- 参数打包（三个点击方法共用） ----------
    @staticmethod
    def _pack_args(args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """把 (位置参数, 关键字参数) 打包成 (target, extra)"""
        target = None
        extra = {}
        if len(args) == 1:
            target = args[0]
        elif len(args) >= 2:
            target = args[0]
            extra['y'] = args[1]
        if rect is not None:
            extra['rect'] = rect
        if fast_mode is not None:
            extra['fast_mode'] = fast_mode
        if template_dir is not None:
            extra['template_dir'] = template_dir
        if similarity is not None:
            extra['similarity'] = similarity
        return target, extra

    # ---------- 单次点击 (支持 rect / fast_mode / template_dir) ----------
    def click(self, *args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """
        单次左键点击
        用法：
            .click()                                    # 点击当前位置
            .click("按钮")                               # 找图单击（全屏，默认模式）
            .click("按钮", rect=(100,200,300,400))       # 限定区域
            .click("按钮", fast_mode=True)               # 极速模式（灰度优先）
            .click("按钮", template_dir="temp2")         # 指定模板来源目录
            .click(100, 200)                           # 坐标单击
        """
        target, extra = self._pack_args(args, rect, fast_mode, template_dir, similarity)
        self.steps.append(('click', target, extra))
        return self

    def dclick(self, *args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """
        双击
        用法：
            .dclick()                                   # 当前位置双击
            .dclick("按钮")                              # 找图双击（全屏）
            .dclick("按钮", rect=(100,200,300,400))      # 限定区域双击
            .dclick("按钮", fast_mode=True)              # 极速模式
            .dclick("按钮", template_dir="temp2")        # 指定模板来源目录
            .dclick(100, 200)                          # 坐标双击
        """
        target, extra = self._pack_args(args, rect, fast_mode, template_dir, similarity)
        self.steps.append(('dclick', target, extra))
        return self

    def rclick(self, *args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """
        右键单击
        用法：
            .rclick()                                   # 当前位置右键
            .rclick("按钮")                              # 找图右键（全屏）
            .rclick("按钮", rect=(100,200,300,400))      # 限定区域右键
            .rclick("按钮", fast_mode=True)              # 极速模式
            .rclick("按钮", template_dir="temp2")        # 指定模板来源目录
            .rclick(100, 200)                          # 坐标右键
        """
        target, extra = self._pack_args(args, rect, fast_mode, template_dir, similarity)
        self.steps.append(('rclick', target, extra))
        return self

    # ---------- 鼠标移动 ----------
    def moveto(self, x, y):
        """移动鼠标到指定逻辑坐标"""
        self.steps.append(('moveto', (x, y), {}))
        return self

    # ---------- 拖拽 ----------
    def drag(self, x1, y1, x2, y2, duration=0.5):
        """鼠标拖拽操作"""
        self.steps.append(('drag', (x1, y1, x2, y2), {'duration': duration}))
        return self

    # ---------- 滚轮 ----------
    def scroll(self, direction, clicks=3, x=None, y=None):
        """鼠标滚轮滚动"""
        self.steps.append(('scroll', None, {'direction': direction, 'clicks': clicks, 'x': x, 'y': y}))
        return self

    # ---------- 截图 ----------
    def snap(self, note=""):
        """将当前屏幕截图保存到 screenshots_author 目录"""
        self.steps.append(('snap', note, {}))
        return self

    # ---------- 多次点击 (支持 rect / fast_mode / template_dir) ----------
    def click_multi(self, *args, k=None, wait=None, times=None, interval=None,
                    rect=None, fast_mode=None, template_dir=None, similarity=None):
        """
        多次左键点击（图片仅定位一次）
        用法：
            .click_multi("按钮", k=5, wait=0.2)
            .click_multi("按钮", times=5, interval=0.2)                  # 兼容旧文档写法
            .click_multi("按钮", k=5, wait=0.2, rect=(100,200,300,400))   # 限定区域
            .click_multi("按钮", k=5, wait=0.2, template_dir="temp2")     # 指定模板目录
            .click_multi(100, 200, k=10, wait=0.5)                       # 坐标点击
        """
        k = k if k is not None else (times if times is not None else 1)
        wait = wait if wait is not None else (interval if interval is not None else 0.0)
        target, extra = self._pack_args(args, rect, fast_mode, template_dir, similarity)
        extra['k'] = k
        extra['wait'] = wait
        self.steps.append(('click_multi', target, extra))
        return self

    # ---------- 连续点击序列 ----------
    def click_seq(self, targets, wait=0.2, template_dir=None):
        """依次点击多个目标（支持图片名与坐标混合，list/tuple 均可）"""
        self.steps.append(('click_seq', targets,
                           {'wait': wait, 'template_dir': template_dir}))
        return self

    # ---------- 候补点击 ----------
    def click_any(self, targets, timeout=None, wait=0.1, fast_mode=None, template_dir=None):
        """按顺序尝试点击列表中第一个出现的模板或坐标"""
        self.steps.append(('click_any', targets,
                           {'timeout': timeout, 'wait': wait,
                            'fast_mode': fast_mode, 'template_dir': template_dir}))
        return self

    # ---------- 鲁棒点击（对应旧文档里的 click_robust） ----------
    def click_robust(self, tempname, retries=3, interval=0.5, timeout=None,
                     rect=None, fast_mode=None, template_dir=None, similarity=None):
        """鲁棒点击：找不到就等 interval 秒再试，最多 retries 次。

        与 click() 的区别：click() 内部已带超时轮询，click_robust 额外提供
        "多次独立尝试"的语义，适合界面加载很慢的场景。
        """
        self.steps.append(('click_robust', tempname,
                           {'retries': retries, 'interval': interval, 'timeout': timeout,
                            'rect': rect, 'fast_mode': fast_mode,
                            'template_dir': template_dir, 'similarity': similarity}))
        return self

    # ---------- 其他步骤 ----------
    def write(self, text):
        self.steps.append(('write', text, {}))
        return self

    def press(self, key, times=1):
        self.steps.append(('press', key, {'times': times}))
        return self

    def hotkey(self, *keys):
        self.steps.append(('hotkey', keys, {}))
        return self

    def wait(self, target, timeout=None, fast_mode=None, optional=False, template_dir=None):
        """等待图片出现；optional=True 时超时不算失败（流程继续）"""
        self.steps.append(('wait', target, {'timeout': timeout, 'fast_mode': fast_mode,
                                            'optional': optional,
                                            'template_dir': template_dir}))
        return self

    def wait_not(self, target, timeout=None, fast_mode=None, optional=False, template_dir=None):
        """等待图片消失；optional=True 时超时不算失败（流程继续）"""
        self.steps.append(('wait_not', target, {'timeout': timeout, 'fast_mode': fast_mode,
                                                'optional': optional,
                                                'template_dir': template_dir}))
        return self

    def pause(self, seconds):
        self.steps.append(('pause', seconds, {}))
        return self

    def delay(self, seconds):
        """pause 的别名，方便链式书写：.delay(0.5)"""
        return self.pause(seconds)

    # ==================== 条件分支 ====================
    def if_see(self, target, **kwargs):
        """条件：能看到 target 就执行块内步骤（需 endif 收尾）

        kwargs: rect / similarity / fast_mode / template_dir
        """
        self.steps.append(('if_start', target, kwargs))
        return self

    def if_not_see(self, target, **kwargs):
        """条件：看不到 target 就执行块内步骤"""
        self.steps.append(('if_not_start', target, kwargs))
        return self

    def if_window(self, pattern):
        """条件：当前前台窗口标题匹配 pattern（'记事本|Notepad' 多关键字任一命中）"""
        self.steps.append(('if_window', pattern, {}))
        return self

    def if_count(self, target, op=">=", n=1, **kwargs):
        """条件：匹配个数满足 op n（op: >= <= == != > <）"""
        _kw = dict(kwargs)
        _kw.update({'op': op, 'n': n})
        self.steps.append(('if_count', target, _kw))
        return self

    def if_color(self, x, y, color, tol=12):
        """条件：坐标 (x,y) 的像素接近 color（'#RRGGBB' 或 (r,g,b)），tol 为容差"""
        self.steps.append(('if_color', None, {'x': x, 'y': y, 'color': color, 'tol': tol}))
        return self

    def if_python(self, func):
        """条件：执行任意 Python 表达式，返回真值则进入块内"""
        self.steps.append(('if_python', None, {'func': func}))
        return self

    def else_if(self, target, **kwargs):
        """多分支：前面的条件都不成立时，再判断这一个"""
        self.steps.append(('else_if', target, kwargs))
        return self

    def else_do(self):
        """否则分支"""
        self.steps.append(('else', None, {}))
        return self

    def endif(self):
        """条件块结束"""
        self.steps.append(('endif', None, {}))
        return self

    # ==================== 条件循环 ====================
    def while_see(self, target, max=None, timeout=None, interval=None, **kwargs):
        """重复执行块内步骤，直到【看不到】target（需 end_while 收尾）

        max 与 timeout 至少要给一个，否则 .run() 前会直接报错（防止死循环）。
        """
        _kw = dict(kwargs)
        _kw.update({'max': max, 'timeout': timeout, 'interval': interval})
        self.steps.append(('while_start', target, _kw))
        return self

    def end_while(self):
        self.steps.append(('while_end', None, {}))
        return self

    def until(self, target, max=None, timeout=None, interval=None, **kwargs):
        """重复执行块内步骤，直到【能看到】target（需 end_until 收尾）"""
        _kw = dict(kwargs)
        _kw.update({'max': max, 'timeout': timeout, 'interval': interval})
        self.steps.append(('until_start', target, _kw))
        return self

    def end_until(self):
        self.steps.append(('until_end', None, {}))
        return self

    def loop_n(self, times):
        """单纯重复 times 次（等价 for_data(range(times))，用 end_loop 收尾）"""
        self.steps.append(('for_start', list(range(int(times))), {}))
        return self

    def break_if(self, target=None, **kwargs):
        """条件成立则跳出当前循环"""
        self.steps.append(('break_if', target, kwargs))
        return self

    def continue_if(self, target=None, **kwargs):
        """条件成立则跳过本次循环剩余步骤"""
        self.steps.append(('continue_if', target, kwargs))
        return self

    # ==================== 失败处理 ====================
    def try_do(self):
        """开始一个"允许失败"的块：块内任一步失败则跳到 on_fail 分支"""
        self.steps.append(('try_start', None, {}))
        return self

    def on_fail(self):
        """try_do 块的失败分支"""
        self.steps.append(('onfail', None, {}))
        return self

    def end_try(self):
        """try 块结束"""
        self.steps.append(('try_end', None, {}))
        return self

    # ==================== 断言 ====================
    def expect(self, target, timeout=None, similarity=None, rect=None, fast_mode=None,
               template_dir=None):
        """断言 target 可见；看不到则流程失败并留证"""
        self.steps.append(('expect', target, {
            'timeout': timeout, 'similarity': similarity, 'rect': rect,
            'fast_mode': fast_mode, 'template_dir': template_dir, 'negate': False}))
        return self

    def expect_not(self, target, timeout=None, similarity=None, rect=None, fast_mode=None,
                   template_dir=None):
        """断言 target 不可见"""
        self.steps.append(('expect_not', target, {
            'timeout': timeout, 'similarity': similarity, 'rect': rect,
            'fast_mode': fast_mode, 'template_dir': template_dir, 'negate': True}))
        return self

    def assert_count(self, target, n=1, op="==", similarity=None, rect=None, fast_mode=None,
                     template_dir=None):
        """断言匹配个数满足 op n"""
        self.steps.append(('assert_count', target, {
            'n': n, 'op': op, 'similarity': similarity, 'rect': rect,
            'fast_mode': fast_mode, 'template_dir': template_dir}))
        return self

    # ==================== 输入输出 ====================
    def clear_and_write(self, text, optional=False):
        """先 Ctrl+A 清空输入框，再粘贴文本（避免与旧内容拼接）"""
        self.steps.append(('clear_and_write', text, {'optional': optional}))
        return self

    def type_slowly(self, text, interval=0.05):
        """逐字键入（有些程序拒绝剪贴板粘贴时用）"""
        self.steps.append(('type_slowly', text, {'interval': interval}))
        return self

    def read_clipboard(self, var=None):
        """读取剪贴板文本；给了 var 就存成 {var} 供后续步骤引用"""
        self.steps.append(('read_clipboard', None, {'var': var}))
        return self

    def set_var(self, name, value):
        """设置运行时变量，后续步骤可用 {name} 引用"""
        self.steps.append(('set_var', None, {'name': name, 'value': value}))
        return self

    def find_all(self, target, var=None, timeout=None, rect=None, fast_mode=None,
                 similarity=None, template_dir=None):
        """把所有匹配位置存进变量（配合 for_data(var) 逐个处理）"""
        self.steps.append(('find_all', target, {
            'var': var, 'timeout': timeout, 'rect': rect, 'fast_mode': fast_mode,
            'similarity': similarity, 'template_dir': template_dir}))
        return self

    def notify(self, message="流程执行完毕"):
        """弹出系统提示（长流程跑完通知用户）"""
        self.steps.append(('notify', message, {}))
        return self

    def remember_pos(self):
        """记住当前鼠标位置"""
        self.steps.append(('remember_pos', None, {}))
        return self

    def restore_pos(self):
        """把鼠标放回 remember_pos 记住的位置"""
        self.steps.append(('restore_pos', None, {}))
        return self

    # ==================== 窗口焦点（防止输入打错窗口） ====================
    def activate_window(self, pattern, timeout=5.0):
        """把标题匹配的窗口切到前台"""
        self.steps.append(('activate_window', pattern, {'timeout': timeout}))
        return self

    def assert_foreground(self, pattern):
        """断言当前前台窗口匹配 pattern，否则失败（拒绝在错误窗口输入）"""
        self.steps.append(('assert_foreground', pattern, {}))
        return self

    # ==================== 等待界面稳定 ====================
    def wait_idle(self, rect=None, stable=1.0, timeout=30, diff=0.002):
        """等指定区域连续 stable 秒不再变化（rect=None 为全屏）—— 取代 pause(n)"""
        self.steps.append(('wait_idle', None, {
            'rect': rect, 'stable': stable, 'timeout': timeout, 'diff': diff}))
        return self

    def wait_count(self, target, n=1, op=">=", timeout=None, **kwargs):
        """等匹配个数满足 op n（例如等列表加载到 5 行）"""
        _kw = dict(kwargs)
        _kw.update({'n': n, 'op': op, 'timeout': timeout})
        self.steps.append(('wait_count', target, _kw))
        return self

    def wait_any(self, targets, timeout=None, interval=0.3, **kwargs):
        """等候选里任意一个出现；命中的名字写入 last_result['matched']"""
        _kw = dict(kwargs)
        _kw.update({'timeout': timeout, 'interval': interval})
        self.steps.append(('wait_any', targets, _kw))
        return self

    # ==================== 批量 / 相对定位 ====================
    def click_all(self, target, wait=0.1, **kwargs):
        """点击所有匹配到的 target（批量勾选、批量关闭）"""
        _kw = dict(kwargs)
        _kw['wait'] = wait
        self.steps.append(('click_all', target, _kw))
        return self

    def click_nth(self, target, index=1, **kwargs):
        """点击第 index 个匹配（从 1 开始）"""
        _kw = dict(kwargs)
        _kw['index'] = index
        self.steps.append(('click_nth', target, _kw))
        return self

    def click_offset(self, target, dx=0, dy=0, **kwargs):
        """点击 target 命中位置偏移 (dx, dy) 处（点标签右边的输入框等）"""
        _kw = dict(kwargs)
        _kw.update({'dx': dx, 'dy': dy})
        self.steps.append(('click_offset', target, _kw))
        return self

    def hover(self, target, wait=0.5, **kwargs):
        """把鼠标移到 target 上停留一会儿（展开悬停菜单）"""
        _kw = dict(kwargs)
        _kw['wait'] = wait
        self.steps.append(('hover', target, _kw))
        return self

    def drag_to(self, source, target, duration=0.6, **kwargs):
        """从 source 拖到 target（两者都用模板定位）"""
        _kw = dict(kwargs)
        _kw.update({'to': target, 'duration': duration})
        self.steps.append(('drag_to', source, _kw))
        return self

    # ==================== 逃生舱 ====================
    def call_python(self, func, *fargs, **fkwargs):
        """在流程中间执行任意 Python 函数（DSL 覆盖不到的场景用）"""
        self.steps.append(('call_python', None,
                           {'func': func, 'fargs': fargs, 'fkwargs': fkwargs}))
        return self

    # ==================== 调试与创作 ====================
    def mark(self, note=""):
        """在操作追踪 / 截图水印 / trace 里插入一个标记，方便定位"""
        self.steps.append(('mark', note, {}))
        return self

    def log(self, message):
        """打印一条日志（同时写入 trace）"""
        self.steps.append(('log', message, {}))
        return self

    def highlight(self, target, seconds=2.0, color='red', **kwargs):
        """在屏幕上把 target 框出来闪一下，让用户确认引擎认的是不是它"""
        _kw = dict(kwargs)
        _kw.update({'seconds': seconds, 'color': color})
        self.steps.append(('highlight', target, _kw))
        return self

    def dump_state(self, note="state"):
        """存一张全屏图，并把流程里用到的模板匹配位置全部标出来"""
        self.steps.append(('dump_state', note, {}))
        return self

    def dry_run(self, on=True):
        """打开后只报告"本来会点哪里"，不真的操作鼠标键盘（安全试跑）"""
        self._dry_run = bool(on)
        return self

    def timeout(self, seconds):
        """整个流程的最长执行时间（秒），超时中止"""
        self._timeout = float(seconds) if seconds else None
        return self

    def opts(self, retry=None, interval=0.5, optional=None):
        """给【上一条】指令附加执行选项（对所有动作统一生效）

        用法：
            .click("保存").opts(retry=3, interval=1)     # 这一步最多再试 3 次
            .click("广告弹窗").opts(optional=True)        # 点不到也不算失败
        """
        if not self.steps:
            raise RuntimeError("opts() 之前必须至少有一条指令")
        _action, _t, _kw = self.steps[-1]
        _kw = dict(_kw or {})
        if retry is not None:
            _kw['_retry'] = int(retry)
        if optional is not None:
            _kw['optional'] = bool(optional)
        if interval is not None:
            _kw['_retry_interval'] = float(interval)
        self.steps[-1] = (_action, _t, _kw)
        return self

    def explain(self, show_paths=True):
        """打印整个流程的可读步骤清单（不执行），运行前核对用"""
        print(f"\n流程「{self.name}」共 {len(self.steps)} 条指令")
        print("-" * 66)
        _depth = 0
        _img_actions = ('click', 'dclick', 'rclick', 'click_robust', 'click_multi',
                        'click_nth', 'click_offset', 'click_all', 'hover', 'wait',
                        'wait_not', 'expect', 'expect_not', 'highlight')
        for _i, (_a, _t, _kw) in enumerate(self.steps, 1):
            if _a in _BLOCK_ENDS or _a in ('else', 'else_if', 'onfail'):
                _depth = max(0, _depth - 1)
            _kw = _kw or {}
            _desc = self._make_action_str(_a, _t, _kw)
            _extra = ""
            if show_paths and _a in _img_actions and isinstance(_t, str):
                _p = self.locator.find_template_file(_t, _kw.get('template_dir'))
                _extra = f"   → {_p}" if _p else "   → ⚠ 模板未找到"
            print(f"  {'  ' * _depth}{_i:>3}. {_desc}{_extra}")
            if _a in _BLOCK_STARTS or _a in ('else', 'else_if', 'onfail'):
                _depth += 1
        print("-" * 66)
        return self

    # ==================== 重试与循环数据 ====================
    def retry(self, count, wait=1, mode="all"):
        """整体重试次数。

        mode="all"  ：每次重试都从第一步重放（旧行为，兼容现有脚本；
                      注意已成功的点击会被重复执行）
        mode="step" ：只重新执行上次失败的步骤及其之后的步骤，不会重复点击
                      已经成功的按钮，推荐用于有副作用的操作
        """
        self._retry_count = count
        self._retry_wait = wait
        if mode not in ("all", "step"):
            raise ValueError("retry(mode=) 只能是 'all' 或 'step'")
        self._retry_mode = mode
        return self

    def for_data(self, data_list):
        """循环数据。可以是列表，也可以是一个函数（运行时求值）、或已设置的变量名。

        数据会被快照进指令本身，因此同一个流程里可以有多个循环、也支持嵌套
        （旧实现只有一个共享槽位，多个循环会互相串数据）。
        """
        if callable(data_list) or isinstance(data_list, str):
            _data = data_list            # 运行时再解析
        else:
            _data = list(data_list) if data_list is not None else []
        self.steps.append(('for_start', _data, {}))
        return self

    def end_for(self):
        self.steps.append(('for_end', None, {}))
        return self

    def end_loop(self):
        """loop_n 的收尾（与 end_for 等价）"""
        return self.end_for()

    def run(self, dry_run=None):
        """执行流程，返回 True/False；结构化信息见 self.last_result。

        与旧版的差别：
        1. 执行前做控制块配对校验（漏写 endif/end_for 会明确报错）
        2. 任何步骤失败都会返回 False 并留下失败证据
        3. cancel() 可中断；timeout(n) 可限制整体时长
        4. dry_run=True 时只报告"本来会做什么"，不真的操作鼠标键盘
        """
        if dry_run is not None:
            self._dry_run = bool(dry_run)
        self._dry_run_actions = []

        # ---- 控制块配对校验 ----
        _errors = self.validate()
        if _errors:
            _msg = "流程控制块配对错误：\n  - " + "\n  - ".join(_errors)
            logger.error(_msg)
            print(f"✗ {_msg}")
            if self._debug_recorder:
                self._debug_recorder.failure(_errors[0])
            self.last_result = self._build_result(False, _msg, 0, 0.0)
            return False

        if self._has_run:
            print("⚠ 该流程已执行过，再次 run() 会重放全部步骤（副作用会重复）")
            logger.warning(f"流程「{self.name}」被重复执行")
        self._has_run = True

        self._run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}"
        self._cancelled = False
        self._deadline = time.time() + self._timeout if self._timeout else None
        self._step_ok = set()

        if self._debug_recorder:
            try:
                self._debug_recorder.begin_run(self.name)
            except Exception as _e:
                logger.debug(f"begin_run 失败: {_e}")

        _t_start = time.time()
        if self._dry_run:
            print(f"\n[dry-run] 流程「{self.name}」开始试跑（不会真的操作鼠标键盘）")

        max_attempts = self._retry_count + 1
        last_error = None

        for attempt in range(max_attempts):
            self._failed_step = None
            if attempt > 0:
                if self._retry_mode == "step":
                    print(f"重试第 {attempt}/{self._retry_count} 次"
                          f"（只重跑失败步骤及其之后）...")
                else:
                    print(f"重试第 {attempt}/{self._retry_count} 次"
                          f"（整体重放：已成功的点击会再次执行）...")
                logger.info(f"重试第 {attempt}/{self._retry_count} 次, mode={self._retry_mode}")
                time.sleep(self._retry_wait)

            try:
                if self._execute_steps(self.steps):
                    if self._debug_recorder:
                        self._debug_recorder.success()
                    if self._dry_run:
                        print(f"\n[dry-run] 共 {len(self._dry_run_actions)} 个操作，清单：")
                        for _i, (_a, _t) in enumerate(self._dry_run_actions, 1):
                            print(f"  {_i:>3}. {_a}({_t})")
                    self.last_result = self._build_result(
                        True, None, attempt + 1, time.time() - _t_start)
                    return True
            except _FlowAbort as e:
                last_error = str(e)
                logger.error(f"流程中止: {e}")
                if "取消" in str(e):
                    self._cancelled = True
                    break
            except Exception as e:
                last_error = e
                logger.error(f"流程异常: {e}")

        if self._failed_step:
            _act, _tgt = self._failed_step[0], self._failed_step[1]
            last_error = last_error or f"{_act}({_tgt}) 执行失败"
        _final = str(last_error or "流程失败")
        if self._debug_recorder:
            self._debug_recorder.failure(_final)
        logger.error(f"最终错误: {_final}")
        print(f"✗ 流程失败：{_final}")
        self.last_result = self._build_result(False, _final, max_attempts,
                                              time.time() - _t_start)
        return False

    def _build_result(self, success, error, attempts, duration):
        """组装结构化结果，写进 self.last_result"""
        _fs = self._failed_step or (None, None, None, None)
        return {
            'success': bool(success),
            'attempts': int(attempts),
            'failed_step': (_fs[0], _fs[1]) if _fs[0] else None,
            'index': _fs[3],
            'error': error,
            'cancelled': bool(self._cancelled),
            'duration': round(float(duration), 3),
            'dry_run': bool(self._dry_run),
            'run_id': self._run_id,
            'flow': self.name,
            'matched': self.last_matched,
            'steps': len(self.steps),
        }

    def _make_action_str(self, action, target, kwargs):
        """为调试录像生成操作描述字符串"""
        if action in ('click', 'dclick', 'rclick', 'click_multi'):
            if isinstance(target, (int, float)):
                return f"auto.do().{action}({target}, {kwargs.get('y', '')})"
            elif target is None:
                return f"auto.do().{action}()"
            else:
                return f"auto.do().{action}({target!r})"
        elif action == 'moveto':
            return f"auto.do().moveto({target[0]}, {target[1]})"
        elif action == 'drag':
            x1, y1, x2, y2 = target
            return f"auto.do().drag({x1}, {y1}, {x2}, {y2})"
        elif action == 'scroll':
            d = kwargs.get('direction', 'down')
            c = kwargs.get('clicks', 3)
            return f"auto.do().scroll('{d}', clicks={c})"
        elif action == 'snap':
            note = target if target else ""
            return f"auto.do().snap('{note}')"
        elif action == 'click_seq':
            return f"auto.do().click_seq({target!r}, wait={kwargs.get('wait', 0.2)})"
        elif action == 'click_any':
            return f"auto.do().click_any({target!r})"
        elif action == 'write':
            return f'auto.do().write("{target}")'
        elif action == 'press':
            times = kwargs.get('times', 1)
            return f'auto.do().press("{target}", times={times})'
        elif action == 'hotkey':
            keys = "+".join(target)
            return f'auto.do().hotkey({keys})'
        elif action == 'wait':
            return f'auto.do().wait("{target}")'
        elif action == 'wait_not':
            return f'auto.do().wait_not("{target}")'
        elif action == 'pause':
            return f'auto.do().pause({target})'
        elif action == 'if_start':
            return f'auto.do().if_see("{target}")'
        elif action == 'if_not_start':
            return f'auto.do().if_not_see("{target}")'
        elif action == 'else':
            return 'auto.do().else_do()'
        elif action == 'endif':
            return 'auto.do().endif()'
        elif action == 'for_start':
            return 'auto.do().for_data(...)'
        elif action == 'for_end':
            return 'auto.do().end_for()'
        else:
            return f'auto.do().{action}({target})'

    def _debug_capture(self, action, target, kwargs, coords=None, size=None, search_rect=None):
        """录像截图，根据坐标和尺寸自动绘制焦点标记以及搜索区域"""
        if not self._debug_recorder or not self._debug_recorder.enabled:
            return
        code = self._make_action_str(action, target, kwargs)
        shape = None
        if coords is not None:
            if size is not None:
                shape = _build_rect_focus_shape(coords[0], coords[1], size[0], size[1])
            else:
                shape = _build_circle_focus_shape(coords[0], coords[1])
        self._debug_recorder.capture(code, focus_shape=shape, search_rect=search_rect)

    def _execute_steps(self, steps, lo=0, hi=None):
        """单指针解释器。lo/hi 限定执行区间，各控制块复用同一份逻辑（因此支持嵌套）。

        ★ 控制伪指令分支**无论是否跳转都必须推进指针** —— 旧版在 skip_to is None 时
        既不跳转也不自增，会在 if_start/else/endif 上原地死循环
        （实测 3 秒空转 434 万次 find 调用）。

        步骤失败时抛 _FlowAbort，以便穿过任意层循环直接中止整个流程。
        """
        if hi is None:
            hi = len(steps)
        i = lo
        while i < hi:
            self._check_stop()                      # 取消 / 整体超时

            action, target, kwargs = steps[i]

            if self._step_mode:
                input(f"[单步] 即将执行: {action}({target})，按回车继续...")

            # ---- 条件块：if_* / else_if / else / endif ----
            if (action in _BLOCK_STARTS and _BLOCK_DEFS[action][0] == 'endif') \
                    or action in ('else', 'else_if', 'endif'):
                skip_to = self._handle_conditional(steps, i, hi)
                i = skip_to if skip_to is not None else i + 1
                continue

            # ---- 其它控制块 ----
            if action == 'for_start':
                i = self._execute_loop(steps, i, hi)
                continue
            if action == 'while_start':
                i = self._execute_while(steps, i, hi, until=False)
                continue
            if action == 'until_start':
                i = self._execute_while(steps, i, hi, until=True)
                continue
            if action == 'try_start':
                i = self._execute_try(steps, i, hi)
                continue

            # ---- 块结束标记：直接前进 ----
            if action in ('for_end', 'while_end', 'until_end', 'try_end', 'onfail'):
                i += 1
                continue

            # ---- 循环内提前跳出 / 跳过 ----
            if action in ('break_if', 'continue_if'):
                _hit = True if target is None else \
                    self._check_condition('if_start', target, kwargs or {})
                if _hit:
                    raise _BreakLoop() if action == 'break_if' else _ContinueLoop()
                i += 1
                continue

            # ---- step 重试模式：跳过上一轮已成功的步骤 ----
            if self._retry_mode == "step" and self._step_key(i) in self._step_ok:
                logger.debug(f"[重试] 跳过已成功步骤 #{i + 1}: {action}({target})")
                i += 1
                continue

            # ---- 普通步骤（含步骤级重试 / optional / dry_run）----
            _target, _kwargs = self._apply_placeholders(target, kwargs)
            if not self._run_step_with_retry(action, _target, _kwargs, raw_target=target):
                self._failed_step = (action, target, "步骤执行失败", i)
                raise _FlowAbort(f"{action}({target}) 执行失败")
            self._step_ok.add(self._step_key(i))

            i += 1
            if self._step_pace > 0:
                time.sleep(self._step_pace)
        return True

    # ---------- 停止条件 ----------
    def _check_stop(self):
        """取消 / 整体超时检查，命中则抛 _FlowAbort"""
        if self._cancelled:
            raise _FlowAbort("流程已被 cancel() 取消")
        if self._deadline is not None and time.time() > self._deadline:
            raise _FlowAbort(f"流程整体超时（{self._timeout}s）")

    def cancel(self):
        """请求中断流程（可在其它线程/回调里调用）。

        解释器在每个步骤前检查该标志；命中后 run() 返回 False，
        且 last_result['cancelled'] 为 True。
        """
        self._cancelled = True
        logger.info(f"流程「{self.name}」收到取消请求")
        return self

    # ---------- 步骤级重试 / optional / dry_run ----------
    def _run_step_with_retry(self, action, target, kwargs, raw_target=None):
        """执行一个步骤，处理步骤级 retry、optional 与 dry_run"""
        kwargs = kwargs or {}
        _retry = int(kwargs.get('_retry', 0) or 0)
        _interval = float(kwargs.get('_retry_interval', 0.5) or 0)
        _optional = bool(kwargs.get('optional', False))
        _display = raw_target if raw_target is not None else target
        _attempt = 0

        while True:
            self._check_stop()

            if self._dry_run and action not in _DRY_RUN_ALLOWED:
                print(f"[dry-run] 本来会执行: {action}({_display})")
                logger.info(f"[dry-run] 跳过实际操作: {action}({_display})")
                self._dry_run_actions.append((action, _display))
                return True

            _attempt += 1
            # 只有最后一次尝试才输出失败提示，避免重试时刷"流程终止"
            _last = (_retry <= 0) and not _optional
            if self._execute_single(action, target, kwargs, report=_last):
                if _attempt > 1:
                    logger.info(f"{action}({_display}) 第 {_attempt} 次尝试成功")
                return True

            if _optional:
                logger.info(f"{action}({_display}) 失败，按 optional 继续")
                print(f"⚠ {action}({_display}) 失败，按 optional=True 继续")
                return True
            if _retry > 0:
                _retry -= 1
                logger.info(f"{action}({_display}) 失败，{_interval}s 后重试")
                time.sleep(_interval)
                continue
            return False

    # ---------- 循环上下文与占位符 ----------
    def _step_key(self, idx):
        """步骤唯一键：同一指令在不同循环轮次里算不同步骤"""
        return (idx, tuple(_c['index'] for _c in self._loop_stack))

    def _current_item(self):
        if self._loop_stack:
            _c = self._loop_stack[-1]
            return _c['item'], _c['index']
        return None, None

    _PLACEHOLDER_RE = re.compile(
        r'\{\{\s*([A-Za-z_]\w*)\s*\}\}|\{\s*([A-Za-z_]\w*)\s*\}')

    def _apply_placeholders(self, target, kwargs):
        """单遍替换 {item} / {index} / {变量名}，并把替换扩展到 kwargs。

        - 单遍扫描：数据里含 {index} 字面量不会被二次替换（旧版会）
        - {{name}} 转义为字面量 {name}
        - 未知占位符原样保留（不会报错）
        """
        item, idx = self._current_item()
        if item is None and idx is None and not self._vars:
            return target, kwargs

        def _sub(text):
            def _repl(m):
                if m.group(0).startswith('{{'):
                    return '{' + (m.group(1) or m.group(2)) + '}'
                key = m.group(1) or m.group(2)
                if key == 'item' and item is not None:
                    return str(item)
                if key == 'index' and idx is not None:
                    return str(idx)
                if key in self._vars:
                    return str(self._vars[key])
                return m.group(0)
            return self._PLACEHOLDER_RE.sub(_repl, text)

        new_target = _sub(target) if isinstance(target, str) else target
        new_kwargs = kwargs
        if kwargs:
            new_kwargs = {}
            for _k, _v in kwargs.items():
                if _k in ('func', 'fargs', 'fkwargs'):
                    new_kwargs[_k] = _v         # 不要替换函数对象
                elif isinstance(_v, str):
                    new_kwargs[_k] = _sub(_v)
                elif isinstance(_v, (list, tuple)):
                    new_kwargs[_k] = type(_v)(
                        _sub(_x) if isinstance(_x, str) else _x for _x in _v)
                else:
                    new_kwargs[_k] = _v
        return new_target, new_kwargs

    def _handle_conditional(self, steps, start_idx, hi=None):
        """返回下一条要执行的指令下标；返回 None 表示"接着执行下一条"。"""
        if hi is None:
            hi = len(steps)
        action, target, kwargs = steps[start_idx]
        if action in _BLOCK_STARTS and _BLOCK_DEFS[action][0] == 'endif':
            if self._check_condition(action, target, kwargs):
                return None                     # 条件成立 → 进入块内
            return self._skip_to_next_branch(steps, start_idx, hi)
        if action == 'else_if':
            if self._check_condition('if_start', target, kwargs):
                return None
            return self._skip_to_next_branch(steps, start_idx, hi)
        if action == 'else':
            # 能走到 else，说明上面的分支已执行完 → 跳过 else 块
            return self._skip_to_next_branch(steps, start_idx, hi)
        return None                             # endif：继续

    def _skip_to_next_branch(self, steps, start_idx, hi=None):
        """找下一个同层分支（else_if / else）或块结束，返回其后一个下标。

        同时不越过 onfail（try 块边界）与嵌套块。
        """
        if hi is None:
            hi = len(steps)
        depth = 0
        for i in range(start_idx + 1, hi):
            a = steps[i][0]
            if a in _BLOCK_STARTS:
                depth += 1
            elif a in _BLOCK_ENDS:
                if depth == 0:
                    return i + 1
                depth -= 1
            elif depth == 0:
                if a == 'else_if':
                    # 关键：不能直接跳进 else_if 的分支体，
                    # 必须把指针停到 else_if 本身，让解释器判定它自己的条件
                    return i
                if a == 'else':
                    return i + 1
                if a == 'onfail':
                    return i                    # 不要越过 try 的分支边界
        return hi

    def _check_condition(self, action, target, kwargs):
        """统一的块条件判定（if_see / if_not_see / if_count / if_color /
        if_window / if_python 共用）"""
        kwargs = kwargs or {}

        if action == 'if_window':
            _fg = get_foreground_window_title()
            _hit = title_matches(_fg, target)
            logger.debug(f"if_window({target}) 前台='{_fg}' → {_hit}")
            return _hit

        if action == 'if_python':
            _fn = kwargs.get('func')
            try:
                return bool(_fn()) if callable(_fn) else False
            except Exception as e:
                logger.error(f"if_python 执行出错: {e}")
                return False

        if action == 'if_color':
            try:
                _x, _y = int(kwargs.get('x', 0)), int(kwargs.get('y', 0))
                _img = ImageGrab.grab(bbox=(_x, _y, _x + 1, _y + 1))
                _px = np.asarray(_img)[0, 0][:3]
            except Exception as e:
                logger.error(f"if_color 取色失败: {e}")
                return False
            _want = _parse_color(kwargs.get('color'))
            if _want is None:
                logger.error(f"if_color 颜色格式无法解析: {kwargs.get('color')}")
                return False
            _tol = int(kwargs.get('tol', 12))
            _diff = max(abs(int(_px[i]) - int(_want[i])) for i in range(3))
            _hit = _diff <= _tol
            logger.debug(f"if_color({_x},{_y}) 实测={tuple(int(v) for v in _px)} "
                         f"期望={_want} 最大差={_diff} → {_hit}")
            return _hit

        if target is None:
            return False

        _rect = kwargs.get('rect')
        _fast = kwargs.get('fast_mode')
        _dir = kwargs.get('template_dir')
        _sim = kwargs.get('similarity')

        if action == 'if_count':
            _pts = self.locator.find_all(target, timeout=1, area=_rect, fast_mode=_fast,
                                         template_dir=_dir)
            return _compare_count(len(_pts), kwargs.get('op', '>='), kwargs.get('n', 1))

        _res = self.locator.find(target, timeout=1, area=_rect, fast_mode=_fast,
                                 template_dir=_dir, threshold=_sim)
        _visible = _res != (-1, -1)
        return (not _visible) if action == 'if_not_start' else _visible

    # ---------- 各控制块的执行 ----------
    def _execute_loop(self, steps, start_idx, hi=None):
        """执行 for_data 块。

        - 数据可以是列表 / 函数（运行时求值）/ 变量名
        - 循环体复用 _execute_steps，因此支持 if、while、嵌套 for
        - 循环体失败会抛 _FlowAbort，不再"静默跳过剩余数据并返回成功"
        """
        if hi is None:
            hi = len(steps)
        loop_end = self._find_block_end(steps, start_idx, hi)
        if loop_end is None:
            raise ValueError(f"for_data 缺少配对的 end_for()（第 {start_idx + 1} 条指令）")

        data = self._resolve_loop_data(steps[start_idx][1])
        if not data:
            logger.warning("循环数据为空，跳过循环体")
            return loop_end + 1

        for idx, item in enumerate(data):
            self._loop_stack.append({'item': item, 'index': idx})
            self._vars['item'] = item
            self._vars['index'] = idx
            logger.info(f"循环 {idx + 1}/{len(data)}: {item}")
            try:
                self._execute_steps(steps, start_idx + 1, loop_end)
            except _BreakLoop:
                logger.info("break_if 触发，跳出循环")
                break
            except _ContinueLoop:
                logger.info("continue_if 触发，跳过本次剩余步骤")
                continue
            finally:
                self._loop_stack.pop()
        return loop_end + 1

    def _resolve_loop_data(self, data):
        """循环数据：列表 / 函数 / 变量名"""
        if callable(data):
            try:
                data = data()
            except Exception as e:
                logger.error(f"for_data 运行时求值失败: {e}")
                return []
        elif isinstance(data, str):
            data = self._vars.get(data, [])
        if not data:
            return []
        return list(data)

    def _execute_while(self, steps, start_idx, hi=None, until=False):
        """执行 while_see / until 条件循环"""
        if hi is None:
            hi = len(steps)
        end_idx = self._find_block_end(steps, start_idx, hi)
        if end_idx is None:
            raise ValueError(f"{'until' if until else 'while_see'} 缺少配对的结束指令"
                             f"（第 {start_idx + 1} 条指令）")

        _action, target, kwargs = steps[start_idx]
        kwargs = kwargs or {}
        _max = kwargs.get('max')
        _timeout = kwargs.get('timeout')
        _interval = kwargs.get('interval')
        _deadline = time.time() + float(_timeout) if _timeout else None
        _rounds = 0
        _label = 'until' if until else 'while_see'

        while True:
            self._check_stop()
            if _max is not None and _rounds >= int(_max):
                logger.warning(f"{_label}({target}) 达到 max={_max} 上限，退出循环")
                break
            if _deadline is not None and time.time() >= _deadline:
                logger.warning(f"{_label}({target}) 达到 timeout={_timeout}s，退出循环")
                break

            _visible = self._check_condition('if_start', target, kwargs)
            if until and _visible:
                break
            if (not until) and (not _visible):
                break

            _rounds += 1
            logger.info(f"{_label}({target}) 第 {_rounds} 轮")
            try:
                self._execute_steps(steps, start_idx + 1, end_idx)
            except _BreakLoop:
                break
            except _ContinueLoop:
                pass
            if _interval:
                time.sleep(float(_interval))
        return end_idx + 1

    def _execute_try(self, steps, start_idx, hi=None):
        """执行 try_do / on_fail / end_try 块"""
        if hi is None:
            hi = len(steps)
        end_idx = None
        onfail_idx = None
        depth = 0
        for i in range(start_idx + 1, hi):
            a = steps[i][0]
            if a in _BLOCK_STARTS:
                depth += 1
            elif a in _BLOCK_ENDS:
                if depth == 0:
                    if a == 'try_end':
                        end_idx = i
                        break
                else:
                    depth -= 1
            elif a == 'onfail' and depth == 0:
                onfail_idx = i
        if end_idx is None:
            raise ValueError(f"try_do 缺少配对的 end_try()（第 {start_idx + 1} 条指令）")

        _body_hi = onfail_idx if onfail_idx is not None else end_idx
        try:
            self._execute_steps(steps, start_idx + 1, _body_hi)
        except _FlowAbort as e:
            if onfail_idx is None:
                raise
            logger.info(f"try 块失败（{e}），进入 on_fail 分支")
            print(f"↪ try 块失败，执行 on_fail 分支")
            self._failed_step = None
            self._execute_steps(steps, onfail_idx + 1, end_idx)
        return end_idx + 1

    def _find_block_end(self, steps, start_idx, hi=None):
        """找与 steps[start_idx] 配对的块结束指令下标（返回结束指令自身下标）"""
        if hi is None:
            hi = len(steps)
        _want = _BLOCK_DEFS.get(steps[start_idx][0], (None,))[0]
        if _want is None:
            return None
        depth = 0
        for i in range(start_idx + 1, hi):
            a = steps[i][0]
            if a in _BLOCK_STARTS:
                depth += 1
            elif a in _BLOCK_ENDS:
                if depth == 0:
                    return i if a == _want else None
                depth -= 1
        return None

    def _find_loop_end(self, steps, start_idx, hi=None):
        """兼容旧名：找 for 块的 for_end"""
        return self._find_block_end(steps, start_idx, hi)

    # -------------------------------------------------------------------------
    # 执行单个操作（支持 rect / fast_mode / template_dir / dry_run）
    # -------------------------------------------------------------------------
    def _execute_single(self, action, target, kwargs, report=True):
        """执行单个动作。report=False 时不打印失败提示（用于重试的中间尝试）。"""
        start_time = time.time()
        result = True
        self._last_match = None

        try:
            if action == 'moveto':
                result = self._exec_moveto(target)
            elif action == 'click':
                result = self._exec_click(target, kwargs)
            elif action == 'dclick':
                result = self._exec_dclick(target, kwargs)
            elif action == 'rclick':
                result = self._exec_rclick(target, kwargs)
            elif action == 'click_robust':
                result = self._exec_click_robust(target, kwargs)
            elif action == 'click_all':
                result = self._exec_click_all(target, kwargs)
            elif action == 'click_nth':
                result = self._exec_click_nth(target, kwargs)
            elif action == 'click_offset':
                result = self._exec_click_offset(target, kwargs)
            elif action == 'hover':
                result = self._exec_hover(target, kwargs)
            elif action == 'drag':
                result = self._exec_drag(target, kwargs)
            elif action == 'drag_to':
                result = self._exec_drag_to(target, kwargs)
            elif action == 'scroll':
                result = self._exec_scroll(kwargs)
            elif action == 'snap':
                result = self._exec_snap(target)
            elif action == 'click_multi':
                result = self._exec_click_multi(target, kwargs)
            elif action == 'click_seq':
                result = self._exec_click_seq(target, kwargs)
            elif action == 'click_any':
                result = self._exec_click_any(target, kwargs)
            elif action == 'write':
                result = self._exec_write(target)
            elif action == 'clear_and_write':
                result = self._exec_clear_and_write(target, kwargs)
            elif action == 'type_slowly':
                result = self._exec_type_slowly(target, kwargs)
            elif action == 'read_clipboard':
                result = self._exec_read_clipboard(kwargs)
            elif action == 'set_var':
                result = self._exec_set_var(kwargs)
            elif action == 'press':
                result = self._exec_press(target, kwargs)
            elif action == 'hotkey':
                result = self._exec_hotkey(target)
            elif action == 'wait':
                result = self._exec_wait(target, kwargs)
            elif action == 'wait_not':
                result = self._exec_wait_not(target, kwargs)
            elif action == 'wait_idle':
                result = self._exec_wait_idle(kwargs)
            elif action == 'wait_count':
                result = self._exec_wait_count(target, kwargs)
            elif action == 'wait_any':
                result = self._exec_wait_any(target, kwargs)
            elif action == 'expect':
                result = self._exec_expect(target, kwargs, negate=False)
            elif action == 'expect_not':
                result = self._exec_expect(target, kwargs, negate=True)
            elif action == 'assert_count':
                result = self._exec_assert_count(target, kwargs)
            elif action == 'find_all':
                result = self._exec_find_all(target, kwargs)
            elif action == 'activate_window':
                result = self._exec_activate_window(target, kwargs)
            elif action == 'assert_foreground':
                result = self._exec_assert_foreground(target)
            elif action == 'notify':
                result = self._exec_notify(target)
            elif action == 'remember_pos':
                self._saved_pos = get_mouse_point()
                logger.info(f"已记住鼠标位置 {self._saved_pos}")
                result = True
            elif action == 'restore_pos':
                result = self._exec_restore_pos()
            elif action == 'call_python':
                result = self._exec_call_python(kwargs)
            elif action == 'mark':
                logger.info(f"===== 标记: {target} =====")
                print(f"— 标记: {target}")
                result = True
            elif action == 'log':
                logger.info(f"{target}")
                print(f"[log] {target}")
                result = True
            elif action == 'highlight':
                result = self._exec_highlight(target, kwargs)
            elif action == 'dump_state':
                result = self._exec_dump_state(target)
            elif action == 'pause':
                result = self._exec_pause(target)
            else:
                raise ValueError(f"未知操作: {action}（拼写错误，或该动作尚未实现）")
        except FileNotFoundError:
            # 模板缺失属于"配置错误"，直接上抛：run() 会立刻中止且不做无意义的重试
            logger.error(f"模板缺失: {action}({target})")
            raise
        except (_FlowAbort, _BreakLoop, _ContinueLoop, ValueError):
            raise                     # 控制块错误 / 未知动作：不吞，交给 run() 明确报错
        except Exception as e:
            logger.error(f"操作异常: {action}({target}) - {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            result = False

        duration = time.time() - start_time
        self._record_step(action, target, kwargs, result, duration)

        if result:
            return True
        if not report:
            return False

        # ---------- 失败提示（旧版把 wait 失败静默丢弃，这里全部显式化）----------
        if action in ('wait', 'wait_not', 'wait_idle', 'wait_count'):
            if kwargs.get('optional'):
                logger.info(f"{action}({target}) 未满足条件，按 optional=True 继续")
                print(f"⚠ {action}({target}) 超时，按 optional=True 继续执行")
                return True
            print(f"✗ {action}({target}) 超时，流程终止（可用 .opts(optional=True) 忽略）")
            return False
        if action == 'pause':
            return False

        _names = None
        if isinstance(target, (list, tuple)):
            _names = [str(_t) for _t in target if isinstance(_t, str)]
        if action in ('click', 'dclick', 'rclick', 'click_multi', 'click_robust',
                      'click_nth', 'click_offset', 'hover', 'click_all',
                      'expect', 'expect_not') and isinstance(target, str):
            self.error_handler.save_error_screenshot(target)
            print(self.error_handler.format_error_message(
                target,
                kwargs.get('similarity') or Config.default_similarity,
                kwargs.get('timeout') or Config.default_timeout))
        elif _names:
            print(f"✗ 查找 {_names} 图片失败，流程终止")
        else:
            print(f"✗ 操作 {action}({target}) 失败，流程终止")

        return result

    def _record_step(self, action, target, kwargs, result, duration):
        """记录一步：写内存追踪（_StepRecorder）并落 JSONL trace"""
        _extra = {}
        _m = getattr(self, '_last_match', None)
        if _m:
            _extra = {
                'template_path': _m.get('path'),
                'template_sha1': _m.get('sha1'),
                'score': _m.get('score'),
                'threshold': _m.get('threshold'),
                'coords': list(_m.get('center') or []),
                'search_rect': list(kwargs.get('rect')) if (kwargs or {}).get('rect') else None,
                'fast_mode': bool(_m.get('fast_mode')),
            }
        try:
            self.recorder.record(action, target, result, duration,
                                 extra=_extra, flow=self.name, run_id=self._run_id)
        except Exception as _e:
            logger.debug(f"trace 记录失败: {_e}")

    # --- 具体执行方法（修正坐标转换）---
    def _exec_moveto(self, target):
        x, y = target
        mouse_moveto(int(x), int(y))
        time.sleep(0.02)
        return True

    def _exec_click(self, target, kwargs):
        """单击（支持 rect 和 fast_mode）"""
        if isinstance(target, str):
            rect = kwargs.get('rect')
            fast_mode = kwargs.get('fast_mode')
            found = self._find_image_coords(target, area=rect, fast_mode=fast_mode,
                                            template_dir=kwargs.get('template_dir'),
                                            threshold=kwargs.get('similarity'))
            if found is not None:
                lx, ly, logic_w, logic_h = found
                self._debug_capture('click', target, kwargs,
                                    coords=(lx, ly), size=(logic_w, logic_h), search_rect=rect)
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                mouse_left_click(cx, cy)
                return True
            else:
                self._debug_capture('click', target, kwargs, search_rect=rect)
                return False
        elif isinstance(target, (int, float)):
            x, y = int(target), int(kwargs.get('y', 0))
            self._debug_capture('click', target, kwargs, coords=(x, y))
            mouse_left_click(x, y)
            return True
        else:
            x, y = get_mouse_point()
            self._debug_capture('click', target, kwargs, coords=(x, y))
            mouse_left_click(x, y)
            return True

    def _exec_dclick(self, target, kwargs):
        """双击"""
        if isinstance(target, str):
            rect = kwargs.get('rect')
            fast_mode = kwargs.get('fast_mode')
            found = self._find_image_coords(target, area=rect, fast_mode=fast_mode,
                                            template_dir=kwargs.get('template_dir'),
                                            threshold=kwargs.get('similarity'))
            if found is not None:
                lx, ly, logic_w, logic_h = found
                self._debug_capture('dclick', target, kwargs,
                                    coords=(lx, ly), size=(logic_w, logic_h), search_rect=rect)
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                mouse_left_click(cx, cy, k=2)
                return True
            else:
                self._debug_capture('dclick', target, kwargs, search_rect=rect)
                return False
        elif isinstance(target, (int, float)):
            x, y = int(target), int(kwargs.get('y', 0))
            self._debug_capture('dclick', target, kwargs, coords=(x, y))
            mouse_left_click(x, y, k=2)
            return True
        else:
            x, y = get_mouse_point()
            self._debug_capture('dclick', target, kwargs, coords=(x, y))
            mouse_left_click(x, y, k=2)
            return True

    def _exec_rclick(self, target, kwargs):
        """右键"""
        if isinstance(target, str):
            rect = kwargs.get('rect')
            fast_mode = kwargs.get('fast_mode')
            found = self._find_image_coords(target, area=rect, fast_mode=fast_mode,
                                            template_dir=kwargs.get('template_dir'),
                                            threshold=kwargs.get('similarity'))
            if found is not None:
                lx, ly, logic_w, logic_h = found
                self._debug_capture('rclick', target, kwargs,
                                    coords=(lx, ly), size=(logic_w, logic_h), search_rect=rect)
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                mouse_right_click(cx, cy)
                return True
            else:
                self._debug_capture('rclick', target, kwargs, search_rect=rect)
                return False
        elif isinstance(target, (int, float)):
            x, y = int(target), int(kwargs.get('y', 0))
            self._debug_capture('rclick', target, kwargs, coords=(x, y))
            mouse_right_click(x, y)
            return True
        else:
            x, y = get_mouse_point()
            self._debug_capture('rclick', target, kwargs, coords=(x, y))
            mouse_right_click(x, y)
            return True

    def _exec_drag(self, target, kwargs):
        x1, y1, x2, y2 = target
        drag_mouse(x1, y1, x2, y2, duration=kwargs.get('duration', 0.5))
        return True

    def _exec_scroll(self, kwargs):
        direction = kwargs.get('direction', 'down')
        clicks = int(kwargs.get('clicks', 3))
        x = kwargs.get('x', None)
        y = kwargs.get('y', None)
        scroll_mouse(clicks, direction, x, y)
        return True

    def _exec_snap(self, target):
        note = str(target) if target else ""
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f"snap_{timestamp}_{note}.png" if note else f"snap_{timestamp}.png"
        _dir = workspace_path(Config.screenshot_dir)
        ensure_dir(_dir)
        path = os.path.join(_dir, filename)
        ImageGrab.grab().save(path)
        logger.info(f"流程截图已保存: {path}")
        print(f"流程截图已保存: {path}")
        return True

    def _exec_click_multi(self, target, kwargs):
        k = int(kwargs.get('k', 1))
        wait_time = float(kwargs.get('wait', 0.0))
        if isinstance(target, str):
            rect = kwargs.get('rect')
            fast_mode = kwargs.get('fast_mode')
            found = self._find_image_coords(target, area=rect, fast_mode=fast_mode,
                                            template_dir=kwargs.get('template_dir'),
                                            threshold=kwargs.get('similarity'))
            if found is not None:
                lx, ly, logic_w, logic_h = found
                self._debug_capture('click_multi', target, kwargs,
                                    coords=(lx, ly), size=(logic_w, logic_h), search_rect=rect)
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                for i in range(k):
                    mouse_left_click(cx, cy)
                    if i < k - 1 and wait_time > 0:
                        time.sleep(wait_time)
                return True
            else:
                self._debug_capture('click_multi', target, kwargs, search_rect=rect)
                return False
        elif isinstance(target, (int, float)):
            x = int(target)
            y = int(kwargs.get('y', 0))
            self._debug_capture('click_multi', target, kwargs, coords=(x, y))
            for i in range(k):
                mouse_left_click(x, y)
                if i < k - 1 and wait_time > 0:
                    time.sleep(wait_time)
            return True
        else:
            x, y = get_mouse_point()
            self._debug_capture('click_multi', target, kwargs, coords=(x, y))
            for i in range(k):
                mouse_left_click(x, y)
                if i < k - 1 and wait_time > 0:
                    time.sleep(wait_time)
            return True

    def _exec_click_seq(self, target, kwargs):
        """依次点击。list / tuple / 单个字符串都接受。

        旧版只认 list，传 tuple 会得到空列表 → 一次都不点却 return True
        （"假装成功"），现在改为兼容并显式处理空列表。
        """
        wait_time = float(kwargs.get('wait', 0.2) or 0)
        default_dir = kwargs.get('template_dir')
        if isinstance(target, (list, tuple)):
            targets_list = list(target)
        elif target:
            targets_list = [target]
        else:
            targets_list = []
        if not targets_list:
            logger.error("click_seq 目标列表为空")
            return False

        for idx, t in enumerate(targets_list):
            if isinstance(t, str):
                found = self._find_image_coords(t, template_dir=default_dir)
                if found is not None:
                    lx, ly, logic_w, logic_h = found
                    self._debug_capture('click', t, {'template_dir': default_dir},
                                        coords=(lx, ly), size=(logic_w, logic_h))
                    cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                    mouse_left_click(cx, cy)
                else:
                    self._debug_capture('click', t, {'template_dir': default_dir})
                    return False
            elif isinstance(t, (tuple, list)) and len(t) >= 2:
                x, y = int(t[0]), int(t[1])
                self._debug_capture('click', t, {}, coords=(x, y))
                mouse_left_click(x, y)
            else:
                logger.warning(f"click_seq 目标格式不支持: {t}")
                return False
            if idx < len(targets_list) - 1:
                time.sleep(wait_time)
        return True

    def _exec_click_any(self, target, kwargs):
        """候补点击：按顺序尝试，命中任意一个即成功（容器同样接受 tuple）"""
        timeout_val = kwargs.get('timeout') or Config.default_timeout
        wait_val = float(kwargs.get('wait', 0.1) or 0)
        default_dir = kwargs.get('template_dir')
        fast_mode = kwargs.get('fast_mode')
        if isinstance(target, (list, tuple)):
            targets_list = list(target)
        elif target:
            targets_list = [target]
        else:
            targets_list = []
        if not targets_list:
            logger.error("click_any 目标列表为空")
            return False

        start = time.time()
        while True:
            for t in targets_list:
                if isinstance(t, str):
                    found_coords = self._find_image_coords(t, timeout=0.01,
                                                           template_dir=default_dir,
                                                           fast_mode=fast_mode)
                    if found_coords is not None:
                        lx, ly, logic_w, logic_h = found_coords
                        self._debug_capture('click', t, {'template_dir': default_dir},
                                            coords=(lx, ly), size=(logic_w, logic_h))
                        cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                        mouse_left_click(cx, cy)
                        return True
                elif isinstance(t, (tuple, list)) and len(t) >= 2:
                    x, y = int(t[0]), int(t[1])
                    self._debug_capture('click', t, {}, coords=(x, y))
                    mouse_left_click(x, y)
                    return True
            if time.time() - start >= timeout_val:
                break
            time.sleep(wait_val)
        self._debug_capture('click_any', target, kwargs)
        return False

    def _exec_click_robust(self, target, kwargs):
        """鲁棒点击：找不到就等 interval 秒再试，最多 retries 次

        （旧文档把 auto.click_robust 称为"招牌功能"，但代码里根本没有这个 API）
        """
        retries = int(kwargs.get('retries', 3) or 0)
        interval = float(kwargs.get('interval', 0.5) or 0)
        for attempt in range(retries + 1):
            found = self._find_image_coords(target, timeout=kwargs.get('timeout'),
                                            area=kwargs.get('rect'),
                                            fast_mode=kwargs.get('fast_mode'),
                                            template_dir=kwargs.get('template_dir'),
                                            threshold=kwargs.get('similarity'))
            if found is not None:
                lx, ly, logic_w, logic_h = found
                self._debug_capture('click', target, kwargs,
                                    coords=(lx, ly), size=(logic_w, logic_h),
                                    search_rect=kwargs.get('rect'))
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                mouse_left_click(cx, cy)
                return True
            if attempt < retries:
                logger.info(f"click_robust 第 {attempt + 1}/{retries} 次未找到 {target}，"
                            f"{interval}s 后重试")
                time.sleep(interval)
        self._debug_capture('click', target, kwargs, search_rect=kwargs.get('rect'))
        return False

    def _exec_write(self, target):
        saystring(str(target))
        return True

    def _exec_press(self, target, kwargs):
        times = kwargs.get('times', 1)
        key_press(str(target), k=times)
        return True

    def _exec_hotkey(self, target):
        key_press_plus(list(target))
        return True

    def _exec_wait(self, target, kwargs):
        timeout = kwargs.get('timeout') or Config.default_timeout
        return self.locator.find(target, timeout=timeout,
                                 fast_mode=kwargs.get('fast_mode'),
                                 template_dir=kwargs.get('template_dir')) != (-1, -1)

    def _exec_wait_not(self, target, kwargs):
        timeout = kwargs.get('timeout') or Config.default_timeout
        fast_mode = kwargs.get('fast_mode')
        template_dir = kwargs.get('template_dir')
        start = time.time()
        while time.time() - start < timeout:
            if self.locator.find(target, timeout=1, fast_mode=fast_mode,
                                 template_dir=template_dir) == (-1, -1):
                return True
            time.sleep(0.5)
        return False

    def _exec_pause(self, target):
        time.sleep(float(target))
        return True

    # ==================== v2.1 新增执行器 ====================

    # ---------- 查找辅助：带完整匹配信息（用于 trace 与调试） ----------
    def _find_image_match(self, tempname, timeout=None, area=None, fast_mode=None,
                          template_dir=None, threshold=None):
        """找图并返回完整匹配信息 dict；找不到返回 None。

        与 find() 一样带超时轮询（timeout 即真实等待时间），额外返回
        score / 命中的模板文件 / sha1，供 trace 与调试使用。
        """
        _eng = self.locator.image_engine
        _area_real = self.locator._area_real(area)
        _timeout = Config.default_timeout if timeout is None else float(timeout)
        _deadline = time.time() + max(_timeout, 0.0)
        while True:
            _sw = time.time()
            (rx, ry), score = _eng.best_match_score(tempname, _area_real, threshold,
                                                    fast_mode, template_dir)
            if (rx, ry) != (-1, -1):
                tw, th = _eng.tempsize
                lx, ly = real_to_logical(rx, ry, _eng.scale)
                lw, lh = _logic_size_from_phys(tw, th, _eng.scale)
                _path = (_eng.template_path(tempname, template_dir)
                         if isinstance(tempname, str) else None)
                return {
                    'coords': (lx, ly),
                    'size': (lw, lh),
                    'center': (int(lx + lw / 2), int(ly + lh / 2)),
                    'score': round(float(score), 4),
                    'threshold': threshold if threshold is not None else _eng.threshold,
                    'path': _path,
                    'sha1': _file_sha1_short(_path),
                    'fast_mode': bool(
                        Config.default_fast_mode if fast_mode is None else fast_mode),
                    'elapsed': round(time.time() - _sw, 3),
                }
            _left = _deadline - time.time()
            if _left <= 0:
                return None
            self._check_stop()
            time.sleep(min(Config.retry_interval, _left))

    def _all_matches(self, target, kwargs):
        return self.locator.find_all(target, timeout=kwargs.get('timeout'),
                                     area=kwargs.get('rect'),
                                     fast_mode=kwargs.get('fast_mode'),
                                     template_dir=kwargs.get('template_dir'))

    def _match_size(self):
        tw, th = self.locator.get_template_size()
        return _logic_size_from_phys(tw, th, self.locator.get_scale_factor())

    # ---------- 批量 / 相对定位 ----------
    def _exec_click_all(self, target, kwargs):
        """点击所有匹配到的目标（批量勾选、批量关闭）"""
        pts = self._all_matches(target, kwargs)
        if not pts:
            return False
        _wait = float(kwargs.get('wait', 0.1) or 0)
        lw, lh = self._match_size()
        for (lx, ly) in pts:
            self._debug_capture('click', target, kwargs, coords=(lx, ly), size=(lw, lh),
                                search_rect=kwargs.get('rect'))
            cx, cy = _center_from_rect(lx, ly, lw, lh)
            mouse_left_click(cx, cy)
            if _wait:
                time.sleep(_wait)
        logger.info(f"click_all({target}) 共点击 {len(pts)} 个")
        print(f"✓ click_all「{target}」点击了 {len(pts)} 个")
        return True

    def _exec_click_nth(self, target, kwargs):
        """点击第 index 个匹配（从 1 开始）"""
        pts = self._all_matches(target, kwargs)
        _idx = int(kwargs.get('index', 1) or 1)
        if _idx < 1 or len(pts) < _idx:
            logger.warning(f"click_nth({target}) 想点第 {_idx} 个，实际只有 {len(pts)} 个")
            return False
        lx, ly = pts[_idx - 1]
        lw, lh = self._match_size()
        self._debug_capture('click', target, kwargs, coords=(lx, ly), size=(lw, lh),
                            search_rect=kwargs.get('rect'))
        cx, cy = _center_from_rect(lx, ly, lw, lh)
        mouse_left_click(cx, cy)
        return True

    def _exec_click_offset(self, target, kwargs):
        """点击命中位置偏移 (dx, dy)"""
        m = self._find_image_match(target, timeout=kwargs.get('timeout'),
                                   area=kwargs.get('rect'),
                                   fast_mode=kwargs.get('fast_mode'),
                                   template_dir=kwargs.get('template_dir'),
                                   threshold=kwargs.get('similarity'))
        if not m:
            self._debug_capture('click', target, kwargs, search_rect=kwargs.get('rect'))
            return False
        self._last_match = m
        _cx = int(m['center'][0] + int(kwargs.get('dx', 0) or 0))
        _cy = int(m['center'][1] + int(kwargs.get('dy', 0) or 0))
        self._debug_capture('click_offset', target, kwargs, coords=m['coords'],
                            size=m['size'], search_rect=kwargs.get('rect'))
        mouse_left_click(_cx, _cy)
        return True

    def _exec_hover(self, target, kwargs):
        """把鼠标移到目标上停留（展开悬停菜单）"""
        m = self._find_image_match(target, timeout=kwargs.get('timeout'),
                                   area=kwargs.get('rect'),
                                   fast_mode=kwargs.get('fast_mode'),
                                   template_dir=kwargs.get('template_dir'))
        if not m:
            return False
        self._last_match = m
        self._debug_capture('hover', target, kwargs, coords=m['coords'], size=m['size'],
                            search_rect=kwargs.get('rect'))
        mouse_moveto(m['center'][0], m['center'][1])
        time.sleep(float(kwargs.get('wait', 0.5) or 0))
        return True

    def _exec_drag_to(self, source, kwargs):
        """从 source 拖到 kwargs['to']（两者都用模板定位）"""
        m1 = self._find_image_match(source, timeout=kwargs.get('timeout'),
                                    fast_mode=kwargs.get('fast_mode'),
                                    template_dir=kwargs.get('template_dir'))
        if not m1:
            return False
        m2 = self._find_image_match(kwargs.get('to'), timeout=kwargs.get('timeout'),
                                    fast_mode=kwargs.get('fast_mode'),
                                    template_dir=kwargs.get('template_dir'))
        if not m2:
            return False
        self._last_match = m1
        self._debug_capture('drag_to', source, kwargs, coords=m1['coords'], size=m1['size'])
        drag_mouse(m1['center'][0], m1['center'][1], m2['center'][0], m2['center'][1],
                   duration=kwargs.get('duration', 0.6))
        return True

    # ---------- 输入输出 ----------
    def _exec_clear_and_write(self, target, kwargs):
        """Ctrl+A → Delete → 粘贴，避免与输入框已有内容拼接"""
        key_press_plus(['ctrl', 'a'])
        time.sleep(0.08)
        key_press('del')
        time.sleep(0.05)
        saystring(str(target))
        return True

    def _exec_type_slowly(self, target, kwargs):
        """逐字键入（不依赖剪贴板，适合老 ERP / 远程桌面）"""
        _iv = float(kwargs.get('interval', 0.05) or 0)
        for _ch in str(target):
            type_char(_ch)
            if _iv:
                time.sleep(_iv)
        return True

    def _exec_read_clipboard(self, kwargs):
        """读取剪贴板文本（可存进变量供后续 {var} 引用）"""
        text = _get_clipboard_text()
        _var = kwargs.get('var')
        if _var:
            self._vars[str(_var)] = text
            logger.info(f"read_clipboard → {{{_var}}} = {str(text)[:40]!r}")
        print(f"剪贴板内容: {str(text)[:80]!r}")
        return True

    def _exec_set_var(self, kwargs):
        _name = str(kwargs.get('name'))
        self._vars[_name] = kwargs.get('value')
        logger.info(f"set_var {_name} = {kwargs.get('value')!r}")
        return True

    def _exec_find_all(self, target, kwargs):
        """把 find_all 的结果存进变量（可交给 for_data 使用）"""
        pts = self._all_matches(target, kwargs)
        _var = kwargs.get('var')
        if _var:
            self._vars[str(_var)] = pts
        logger.info(f"find_all({target}) 找到 {len(pts)} 个 → {{{_var}}}")
        print(f"找到 {len(pts)} 个「{target}」")
        return True

    # ---------- 窗口焦点 ----------
    def _exec_activate_window(self, target, kwargs):
        return activate_window(target, timeout=kwargs.get('timeout', 5.0))

    def _exec_assert_foreground(self, target):
        _fg = get_foreground_window_title()
        if title_matches(_fg, target):
            return True
        print(f"✗ 前台窗口不匹配：期望包含「{target}」，实际是「{_fg}」")
        logger.error(f"assert_foreground 失败: 期望 '{target}'，实际 '{_fg}'")
        return False

    # ---------- 通知 / 鼠标位置 ----------
    def _exec_notify(self, target):
        _msg = str(target or "流程执行完毕")
        try:
            def _pop():
                try:
                    ctypes.windll.user32.MessageBoxW(0, _msg, str(self.name), 0x40 | 0x1000)
                except Exception:
                    pass
            threading.Thread(target=_pop, daemon=True).start()
        except Exception as e:
            logger.debug(f"notify 失败: {e}")
        print(f"🔔 {_msg}")
        return True

    def _exec_restore_pos(self):
        _pos = getattr(self, '_saved_pos', None)
        if not _pos:
            return False
        mouse_moveto(int(_pos[0]), int(_pos[1]))
        return True

    # ---------- 逃生舱 ----------
    def _exec_call_python(self, kwargs):
        _fn = kwargs.get('func')
        if not callable(_fn):
            raise ValueError("call_python 需要传入可调用对象")
        _fn(*(kwargs.get('fargs') or ()), **(kwargs.get('fkwargs') or {}))
        return True

    # ---------- 等待界面稳定 ----------
    def _exec_wait_idle(self, kwargs):
        """等指定区域连续 stable 秒不再变化"""
        _stable = float(kwargs.get('stable', 1.0) or 1.0)
        _timeout = float(kwargs.get('timeout', 30) or 30)
        _diff_th = float(kwargs.get('diff', 0.002) or 0.002)
        _rect = kwargs.get('rect')
        _eng = self.locator.image_engine
        _bbox = _eng._bbox(self.locator._area_real(_rect) if _rect else None)
        _t0 = time.time()
        _last = None
        _last_change = time.time()
        while True:
            self._check_stop()
            _cur = np.asarray(ImageGrab.grab(bbox=_bbox))
            if _last is not None and frame_diff_ratio(_last, _cur) > _diff_th:
                _last_change = time.time()
            _last = _cur
            if time.time() - _last_change >= _stable:
                logger.info(f"wait_idle 完成：{_stable}s 内画面无变化"
                            f"（用时 {time.time() - _t0:.1f}s）")
                return True
            if time.time() - _t0 >= _timeout:
                logger.warning(f"wait_idle 超时 {_timeout}s，画面仍在变化，继续往下执行")
                return False
            time.sleep(0.15)

    def _exec_wait_count(self, target, kwargs):
        """等匹配个数满足 op n"""
        _timeout = kwargs.get('timeout') or Config.default_timeout
        _t0 = time.time()
        _n = 0
        while True:
            self._check_stop()
            _pts = self.locator.find_all(target, timeout=0, area=kwargs.get('rect'),
                                         fast_mode=kwargs.get('fast_mode'),
                                         template_dir=kwargs.get('template_dir'))
            _n = len(_pts)
            if _compare_count(_n, kwargs.get('op', '>='), kwargs.get('n', 1)):
                logger.info(f"wait_count({target}) 满足：{_n} 个")
                return True
            if time.time() - _t0 >= _timeout:
                logger.warning(f"wait_count({target}) 超时 {_timeout}s，实际 {_n} 个")
                return False
            time.sleep(Config.retry_interval)

    def _exec_wait_any(self, target, kwargs):
        """等候选里任意一个出现，命中的名字存进 {matched}"""
        _targets = list(target) if isinstance(target, (list, tuple)) else [target]
        _timeout = kwargs.get('timeout') or Config.default_timeout
        _interval = float(kwargs.get('interval', 0.3) or 0.3)
        _t0 = time.time()
        while True:
            self._check_stop()
            for _t in _targets:
                try:
                    _m = self._find_image_match(
                        _t, timeout=0, fast_mode=kwargs.get('fast_mode'),
                        template_dir=kwargs.get('template_dir'),
                        threshold=kwargs.get('similarity'))
                except FileNotFoundError:
                    continue
                if _m:
                    self._last_match = _m
                    self._vars['matched'] = _t
                    self.last_matched = _t
                    logger.info(f"wait_any 命中: {_t}")
                    print(f"✓ wait_any 命中「{_t}」")
                    return True
            if time.time() - _t0 >= _timeout:
                logger.warning(f"wait_any 超时 {_timeout}s，候选都没出现: {_targets}")
                return False
            time.sleep(_interval)

    # ---------- 断言 ----------
    def _exec_expect(self, target, kwargs, negate=False):
        """断言目标可见 / 不可见"""
        _timeout = kwargs.get('timeout')
        m = self._find_image_match(target,
                                   timeout=_timeout if _timeout is not None else 3,
                                   area=kwargs.get('rect'),
                                   fast_mode=kwargs.get('fast_mode'),
                                   template_dir=kwargs.get('template_dir'),
                                   threshold=kwargs.get('similarity'))
        if m:
            self._last_match = m
        if negate:
            if m:
                print(f"✗ 断言失败：不应该出现「{target}」，但它出现在 {m['coords']}")
                return False
            print(f"✓ 断言通过：「{target}」确实没有出现")
            return True
        if not m:
            print(f"✗ 断言失败：期望看到「{target}」，但没有找到")
            return False
        print(f"✓ 断言通过：「{target}」@ {m['coords']} 分数 {m['score']}")
        return True

    def _exec_assert_count(self, target, kwargs):
        _pts = self._all_matches(target, kwargs)
        _n = len(_pts)
        if _compare_count(_n, kwargs.get('op', '=='), kwargs.get('n', 1)):
            print(f"✓ 数量断言通过：「{target}」共 {_n} 个")
            return True
        print(f"✗ 数量断言失败：「{target}」实际 {_n} 个，"
              f"要求 {kwargs.get('op')} {kwargs.get('n')}")
        return False

    # ---------- 调试与创作 ----------
    def _exec_highlight(self, target, kwargs):
        """在屏幕上把目标框出来闪一下"""
        m = self._find_image_match(target, timeout=kwargs.get('timeout', 1),
                                   fast_mode=kwargs.get('fast_mode'),
                                   template_dir=kwargs.get('template_dir'))
        if not m:
            print(f"⚠ highlight: 找不到「{target}」")
            return False
        self._last_match = m
        _x, _y = m['coords']
        _w, _h = m['size']
        print(f"🔍 高亮「{target}」@ {m['coords']} 尺寸 {m['size']} 分数 {m['score']}")
        highlight_on_screen(_x, _y, _w, _h, seconds=kwargs.get('seconds', 2.0),
                            color=kwargs.get('color', 'red'), label=str(target))
        return True

    def _exec_dump_state(self, note="state"):
        """存一张全屏图，把流程里用到的模板匹配位置全标出来"""
        _img = ImageGrab.grab()
        _draw = ImageDraw.Draw(_img)
        _names = []
        _scan = ('click', 'dclick', 'rclick', 'click_robust', 'click_multi', 'click_nth',
                 'click_offset', 'click_all', 'hover', 'wait', 'wait_not', 'expect',
                 'expect_not', 'if_start', 'if_not_start', 'highlight')
        for (_a, _t, _k) in self.steps:
            if _a in _scan and isinstance(_t, str) and _t not in _names:
                _names.append(_t)
        _found = 0
        for _name in _names:
            try:
                _m = self._find_image_match(_name, timeout=0.3)
            except FileNotFoundError:
                continue
            if not _m:
                continue
            _found += 1
            _x, _y = _m['coords']
            _w, _h = _m['size']
            _draw.rectangle([_x - 2, _y - 2, _x + _w + 2, _y + _h + 2], outline='red', width=3)
            _draw.text((_x, max(0, _y - 14)), f"{_name} {_m['score']:.3f}", fill='red')
        _dir = workspace_path(Config.screenshot_dir)
        ensure_dir(_dir)
        _path = os.path.join(_dir, f"state_{note}_{time.strftime('%H%M%S')}.png")
        _img.save(_path)
        print(f"状态快照已保存: {_path}（标注了 {_found} 个模板）")
        return True

    # --- 图像查找辅助（Flow 内部使用，返回逻辑坐标和逻辑尺寸）---
    def _find_image_coords(self, tempname, timeout=None, area=None, fast_mode=None,
                           template_dir=None, threshold=None):
        """找图返回 (lx, ly, logic_w, logic_h) 或 None。

        内部走 _find_image_match，因此所有走这条路的执行器（click/dclick/rclick/
        click_multi/click_seq/click_any/click_robust）都会把匹配详情记进
        self._last_match，trace 里就能带上匹配分数与命中的模板文件。
        """
        m = self._find_image_match(tempname, timeout=timeout, area=area,
                                   fast_mode=fast_mode, template_dir=template_dir,
                                   threshold=threshold)
        if not m:
            return None
        self._last_match = m
        return m['coords'][0], m['coords'][1], m['size'][0], m['size'][1]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    文件查找（升级：os.scandir）                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def find_files(file_path=None, keyword=''):
    """按关键字查找文件夹和文件（使用os.scandir加速）

    file_path 默认取当前工作目录（即调用方脚本所在目录）；
    keyword 可用 , . 。 ， 分隔多个关键字。

    注意：旧版把作者的个人目录写成了默认参数（r'D:\\企划部工作\\02 对外报送'），
    本版改为工作目录，避免把内部目录结构随库分发出去。
    """
    if not file_path:
        file_path = get_workspace_dir()
    if not keyword:
        print("请提供关键字，例如：find_files('.', '报表,xlsx')")
        return []
    file_keywords = re.split(r'[.,。,，,\,]', keyword)
    file_keywords = [k.strip() for k in file_keywords if k.strip()]

    matched_folders = []
    matched_files = []

    def scan(dirpath):
        with os.scandir(dirpath) as it:
            dirs = []
            files = []
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(entry.name)
                elif entry.is_file():
                    files.append(entry.name)

            if all(k in dirpath for k in file_keywords):
                matched_folders.append(dirpath)

            for fname in files:
                full = os.path.join(dirpath, fname)
                if all(k in full for k in file_keywords):
                    matched_files.append(full)

            for d in dirs:
                scan(os.path.join(dirpath, d))

    scan(file_path)
    print(f'找到文件夹 {len(matched_folders)} 个')
    print(f'找到文件 {len(matched_files)} 个')
    return matched_folders, matched_files


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    定时任务（升级：sched模块）                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_scheduler = sched.scheduler(time.time, time.sleep)


def _cron_wrapper(task, run_once, target_int):
    """定时任务包装器"""
    logger.info(f"定时任务执行: {time.strftime('%H:%M:%S')}")
    try:
        task()
    except Exception as e:
        logger.error(f"定时任务异常: {e}")
    if not run_once:
        _scheduler.enter(60, 1, _cron_wrapper, (task, run_once, target_int))


def _calc_delay(target, now):
    """计算从 now 到 target 的秒数（支持跨天）"""
    target_h = target // 10000
    target_m = (target % 10000) // 100
    target_s = target % 100
    now_h = now // 10000
    now_m = (now % 10000) // 100
    now_s = now % 100

    target_seconds = target_h * 3600 + target_m * 60 + target_s
    now_seconds = now_h * 3600 + now_m * 60 + now_s

    if target_seconds > now_seconds:
        return target_seconds - now_seconds
    else:
        return 86400 - now_seconds + target_seconds


def timer_task(timer, task):
    """每天定时执行（非阻塞，使用sched）"""
    target = int(timer)
    now = int(time.strftime("%H%M%S"))
    delay = _calc_delay(target, now)
    logger.info(f"定时任务将在 {delay} 秒后启动")
    _scheduler.enter(delay, 1, _cron_wrapper, (task, False, target))


def timer_task_once(timer, task):
    """一次性定时执行"""
    target = int(timer)
    now = int(time.strftime("%H%M%S"))
    delay = _calc_delay(target, now)
    logger.info(f"一次性定时任务将在 {delay} 秒后启动")
    _scheduler.enter(delay, 1, _cron_wrapper, (task, True, target))


def timer_tasks(**kwargs):
    """多个定时任务"""
    for t_str, task in kwargs.items():
        timer_task(t_str, task)
    _scheduler.run()


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    日期工具                                                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def first_last_day(year=2026):
    """获取某年各月的首日和末日（2月到次年1月，共12个月）"""
    result = []
    for i in range(2, 14):
        if i <= 12:
            month = i
            year_str = str(year)
        else:
            month = 1
            year_str = str(year + 1)
        first = f"{year_str}-{month:02d}-01"
        if month == 12:
            next_first = f"{year+1}-01-01"
        else:
            next_first = f"{year_str}-{month+1:02d}-01"
        last = (datetime.datetime.strptime(next_first, "%Y-%m-%d") - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        result.append([first, last])
    return result


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    调试工具类                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _DebugTools:
    """调试工具"""

    def __init__(self, recorder):
        self.recorder = recorder

    def on(self):
        self.recorder.on()

    def off(self):
        self.recorder.off()

    def trace(self, on=True):
        """打开/关闭结构化操作记录（JSONL，落到 logs/trace_<run_id>.jsonl）"""
        return self.recorder.trace(on)

    def read_trace(self, path=None):
        """读取 trace 记录为 list[dict]"""
        return _StepRecorder.read_trace(path)

    def slowest(self, n=10):
        """最慢的 N 个步骤"""
        return _StepRecorder.slowest(n)

    def weak_templates(self, threshold=0.95):
        """匹配分数长期偏低的模板（建议重新截图）"""
        return _StepRecorder.weak_templates(threshold)

    def failures(self):
        """历史失败点汇总"""
        return _StepRecorder.failures()

    def report(self):
        return self.recorder.generate_report()

    def replay(self):
        if not self.recorder.steps:
            print("没有可回放的记录。")
            return
        print(f"\n执行回放 - {self.recorder.flow_name}")
        print("-" * 50)
        for step in self.recorder.steps:
            icon = 'OK' if step['result'] == 'success' else 'FAIL'
            print(f"  {icon} 步骤{step['step']}: {step['action']}({step['target']}) - {step['duration']}s")
        print("-" * 50)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    截图工具                                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def capture_template_simple(name):
    """截图工具：全屏截图并提示裁剪"""
    print(f"\n截图工具 - 模板名称: {name}")
    print("操作步骤:")
    print("  1. 确保目标按钮在屏幕上可见")
    print("  2. 按回车完成全屏截图")
    print("  3. 用画图工具裁剪到只保留按钮区域")
    input("\n按回车截图...")

    try:
        _dir = workspace_path(Config.template_dir)
        ensure_dir(_dir)
        save_path = os.path.join(_dir, f"{name}.png")
        screenshot = ImageGrab.grab()
        screenshot.save(save_path)
        print(f"\n截图已保存: {save_path}")
        print("请用画图工具裁剪后覆盖该文件")
        return save_path
    except Exception as e:
        logger.error(f"截图失败: {e}")
        return None


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                 调试录像模块 DebugRecorder（自动启动，毫秒时间戳命名）        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class DebugRecorder:
    """调试录像模块：自动截图、水印、焦点标记。总开关：enabled"""

    def __init__(self):
        self.enabled = True
        self.max_screenshots = Config.debug_max_screenshots
        self.max_age_days = Config.debug_max_age_days
        self._lock = threading.Lock()
        self._task_name = None
        self._task_dir = None
        self._counter = 0
        self._is_failure = False
        self._flow_name = None            # 当前流程名（仅用于水印显示）
        self.font_en = None
        self.font_cn = None
        self._font_valid = False

        self._max_failed_tasks = Config.debug_max_failed_tasks
        self.bottom_offset = Config.debug_watermark_bottom_offset   # 新增
        self.right_margin = Config.debug_watermark_right_margin     # 新增
        self._init_font()

    def begin_run(self, flow_name=None):
        """每次流程开始前调用。

        重置失败标记与计数 —— 旧版 `_is_failure` 一旦被置 True 就只会在
        `start()` 里重置，导致「流程 A 失败后，流程 B 的截图水印全都被标成[失败]」。
        """
        self._is_failure = False
        self._counter = 0
        self._flow_name = flow_name
        logger.debug(f"DebugRecorder.begin_run: flow={flow_name}")

    @staticmethod
    def _script_name():
        """调用方脚本名（用于生成"xxx_操作截屏"目录）"""
        _f = None
        _main = sys.modules.get('__main__')
        if _main is not None:
            _f = getattr(_main, '__file__', None)
        if not _f and sys.argv:
            _f = sys.argv[0] or None
        _base = os.path.basename(_f) if _f else 'unknown_script.py'
        return os.path.splitext(_base)[0] or "未命名任务"

    def _auto_start(self):
        _name = self._script_name()
        self._task_name = _name
        # 与工作目录保持一致：默认就是调用方脚本所在目录
        self._task_dir = os.path.join(get_workspace_dir(), f"{_name}_操作截屏")
        ensure_dir(self._task_dir)
        print(f"调试录像已自动启动，截图保存在: {self._task_dir}")

    def start(self, task_name=None):
        if not self.enabled:
            return
        with self._lock:
            # 修复：原实现在显式传 task_name 时没有导入 __main__，会 NameError
            self._task_name = task_name or self._script_name()
            self._task_dir = os.path.join(get_workspace_dir(), f"{self._task_name}_操作截屏")
            ensure_dir(self._task_dir)
            self._counter = 0
            self._is_failure = False
            print(f"调试录像已手动启动: {self._task_dir}")

    def _init_font(self):
        self.font_en = None
        self.font_cn = None
        try:
            self.font_en = ImageFont.truetype("consola.ttf", 16)
        except:
            try:
                self.font_en = ImageFont.truetype("cour.ttf", 16)
            except:
                try:
                    self.font_en = ImageFont.load_default()
                except:
                    pass
        try:
            self.font_cn = ImageFont.truetype("msyh.ttc", 16)
        except:
            try:
                self.font_cn = ImageFont.truetype("simsun.ttc", 16)
            except:
                self.font_cn = self.font_en
        self._font_valid = self.font_en is not None

    def _get_font(self, text):
        if self.font_cn and re.search(r'[\u4e00-\u9fff]', text):
            return self.font_cn
        return self.font_en if self.font_en else ImageFont.load_default()

    def _draw_focus_shape(self, image, shape):
        """根据形状字典在图片上绘制焦点标记：圆圈或矩形"""
        if shape is None:
            return
        scale = get_scale_factor()
        draw = ImageDraw.Draw(image)
        try:
            if shape['type'] == 'circle':
                x, y = shape['center']
                rx, ry = logical_to_real(x, y, scale)
                r_outer = max(1, int(25 * scale))
                r_inner = max(1, int(20 * scale))
                bbox_outer = [rx - r_outer, ry - r_outer, rx + r_outer, ry + r_outer]
                draw.ellipse(bbox_outer, outline='red', width=max(2, int(2*scale)))
                bbox_inner = [rx - r_inner, ry - r_inner, rx + r_inner, ry + r_inner]
                draw.ellipse(bbox_inner, outline='white', width=max(1, int(1*scale)))
            elif shape['type'] == 'rectangle':
                left, top = logical_to_real(shape['left'], shape['top'], scale)
                w = int(shape['width'] * scale)
                h = int(shape['height'] * scale)
                bbox = [left, top, left + w, top + h]
                draw.rectangle(bbox, outline='red', width=max(3, int(3*scale)))
        except:
            pass

    def _draw_search_rect(self, image, rect):
        """
        在截图上用蓝白双线绘制查找区域（rect: left, top, width, height 逻辑坐标）
        """
        if rect is None:
            return
        try:
            scale = get_scale_factor()
            left_log, top_log, w_log, h_log = rect
            left_real, top_real = logical_to_real(left_log, top_log, scale)
            right_real = left_real + int(w_log * scale)
            bottom_real = top_real + int(h_log * scale)

            draw = ImageDraw.Draw(image)
            # 外线：蓝色，较粗
            draw.rectangle(
                [left_real - 1, top_real - 1, right_real + 1, bottom_real + 1],
                outline='blue',
                width=2
            )
            # 内线：白色，较细
            draw.rectangle(
                [left_real + 1, top_real + 1, right_real - 1, bottom_real - 1],
                outline='white',
                width=1
            )
        except Exception as e:
            logger.debug(f"绘制搜索区域失败: {e}")


    def capture(self, action_code, focus_shape=None, focus_shapes=None, search_rect=None):
        if not self.enabled:
            return
        if self._task_dir is None:
            self._auto_start()

        with self._lock:
            try:
                screenshot = ImageGrab.grab()
                if screenshot is None:
                    return

                if search_rect is not None:
                    self._draw_search_rect(screenshot, search_rect)

                # 合并所有需要绘制的焦点形状
                all_shapes = []
                if focus_shape is not None:
                    all_shapes.append(focus_shape)
                if focus_shapes is not None:
                    all_shapes.extend(focus_shapes)

                for shape in all_shapes:
                    self._draw_focus_shape(screenshot, shape)

                now = datetime.datetime.now()
                now_str = now.strftime("%Y-%m-%d %H:%M:%S")
                display_code = action_code if len(action_code) <= 50 else action_code[:47] + "..."
                _tag = (f"{self._task_name}·{self._flow_name}"
                        if getattr(self, '_flow_name', None) else self._task_name)
                if self._is_failure:
                    line2 = f"[{_tag}][失败] {display_code}"
                else:
                    line2 = f"[{_tag}] {display_code}"

                # 定位用户脚本调用行号
                try:
                    this_file = os.path.basename(__file__)
                    frame = sys._getframe()
                    while frame and os.path.basename(frame.f_code.co_filename) == this_file:
                        frame = frame.f_back
                    if frame:
                        caller_file = os.path.basename(frame.f_code.co_filename)
                        caller_lineno = frame.f_lineno
                        line3 = f"{caller_file}:第{caller_lineno}行代码"
                    else:
                        line3 = None
                except:
                    line3 = None

                watermarked = self._add_watermark(screenshot, now_str, line2, line3=line3)

                base_name = f"capture_{now.strftime('%Y%m%d%H%M%S%f')[:-3]}"
                filepath = os.path.join(self._task_dir, f"{base_name}.png")
                suffix = 1
                while os.path.exists(filepath):
                    filepath = os.path.join(self._task_dir, f"{base_name}_{suffix}.png")
                    suffix += 1

                watermarked.save(filepath)
                self._counter += 1

                if not self._is_failure:
                    self._enforce_limit()

            except Exception as e:
                logger.debug(f"调试截图失败: {e}")


    def _add_watermark(self, image, line1, line2, line3=None):
        draw = ImageDraw.Draw(image)
        margin = 10
        # 转换所有可用文本为字体对象
        texts = [(line1, self._get_font(line1)), (line2, self._get_font(line2))]
        if line3:
            texts.append((line3, self._get_font(line3)))

        # 计算每行的宽高
        sizes = []
        total_height = 0
        max_width = 0
        for text, font in texts:
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
            except AttributeError:
                w, h = draw.textsize(text, font=font)
            sizes.append((w, h))
            total_height += h
            if w > max_width:
                max_width = w

        total_height += (len(texts) - 1) * 5  # 行间距 5 像素

        img_w, img_h = image.size
        x = img_w - max_width - self.right_margin
        y = img_h - total_height - self.bottom_offset

        # 逐行绘制（阴影 + 白字）
        current_y = y
        for (text, font), (_, h) in zip(texts, sizes):
            for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                draw.text((x + dx, current_y + dy), text, font=font, fill="black")
            draw.text((x, current_y), text, font=font, fill="white")
            current_y += h + 5

        return image


    def _enforce_limit(self):
        if not self._task_dir:
            return
        try:
            files = [f for f in os.listdir(self._task_dir) if f.startswith("capture_") and f.endswith(".png")]
            files.sort()
            while len(files) > self.max_screenshots:
                oldest = files.pop(0)
                os.remove(os.path.join(self._task_dir, oldest))
        except Exception as e:
            logger.debug(f"清理截图失败: {e}")

    def success(self):
        if not self.enabled or not self._task_dir:
            return
        with self._lock:
            self._enforce_limit()
            print(f"录像任务正常结束，保留最近 {min(self.max_screenshots, self._counter)} 张截图")

    def failure(self, error_msg=""):
        if not self.enabled or not self._task_dir:
            return
        with self._lock:
            self._is_failure = True
            try:
                screenshot = ImageGrab.grab()
                if screenshot:
                    now = datetime.datetime.now()
                    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
                    msg_display = error_msg[:47] + "..." if len(error_msg) > 50 else error_msg
                    line2 = f"[{self._task_name}][失败] {msg_display}" \
                        if not getattr(self, '_flow_name', None) \
                        else f"[{self._task_name}·{self._flow_name}][失败] {msg_display}"
                    watermarked = self._add_watermark(screenshot, now_str, line2)
                    base_name = f"failure_{now.strftime('%Y%m%d%H%M%S%f')[:-3]}"
                    failure_path = os.path.join(self._task_dir, f"{base_name}.png")
                    watermarked.save(failure_path)
            except Exception as e:
                logger.debug(f"失败截图保存失败: {e}")
            self._write_error_summary(error_msg)

    def _write_error_summary(self, error_msg):
        if not self._task_dir:
            return
        summary_path = os.path.join(self._task_dir, "error_summary.txt")
        try:
            with open(summary_path, 'w', encoding='utf-8') as f:
                f.write(f"任务名称: {self._task_name}\n")
                f.write(f"失败时间: {datetime.datetime.now()}\n")
                f.write(f"错误信息: {error_msg}\n")
                f.write(f"截图数量: {self._counter}\n")
        except Exception as e:
            logger.debug(f"写入错误摘要失败: {e}")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    主用户接口类 _Auto (集成录像调用，支持 fast_mode)         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class _Auto:
    """ImgClickFlow 办公助手"""

    def __init__(self):
        self._locator = _LocatorFusion()
        self._recorder = _StepRecorder()
        self._error_handler = _ErrorHandler()
        self._env_checker = _EnvChecker()
        self.debug = _DebugTools(self._recorder)
        self._step_mode = False
        self._step_pace = 0
        self._current_flow = None
        self.recorder = DebugRecorder()

    # ===== 工作目录与模板目录（v2.0 新增）=====
    @property
    def workspace_dir(self):
        """当前工作目录。被其他脚本 import 时，默认就是那个脚本所在目录。

        例如 C:\\Users\\123\\Desktop\\forpython\\amazon\\测试.py 里 import 本模块，
        workspace_dir 就是 C:\\Users\\123\\Desktop\\forpython\\amazon
        """
        return get_workspace_dir()

    def set_workspace_dir(self, path, create=True):
        """指定工作目录（templates/logs/截图都相对它）

        auto.set_workspace_dir(r"D:\\我的项目")
        """
        _p = set_workspace_dir(path, create=create, verbose=True)
        self._locator.image_engine.refresh_screen_info()
        return _p

    @property
    def template_dirs(self):
        """当前模板搜索目录列表（按查找顺序，绝对路径）"""
        return get_template_dirs()

    def set_template_dir(self, path, create=True):
        """设置主模板目录（覆盖默认的 templates/）

        auto.set_template_dir("temp1")            # 相对工作目录
        auto.set_template_dir(r"D:\\按钮图")       # 绝对路径
        """
        _p = set_template_dir(path, create=create, verbose=True)
        self._locator.image_engine._tpl_cache.clear()
        return _p

    def add_template_dir(self, path, create=True):
        """追加一个模板搜索目录（放在查找顺序末尾）

        auto.add_template_dir("temp1")
        auto.add_template_dir("temp2")   # 之后 temp1 与 temp2 里的模板都能找到
        """
        _dirs = add_template_dir(path, create=create, verbose=True)
        self._locator.image_engine._tpl_cache.clear()
        return _dirs

    def set_template_dirs(self, paths, create=True):
        """一次性设置模板搜索顺序：第一个是主目录，其余为追加目录

        auto.set_template_dirs(["templates", "temp1", "temp2"])
        """
        _dirs = set_template_dirs(paths, create=create, verbose=True)
        self._locator.image_engine._tpl_cache.clear()
        return _dirs

    def template_path(self, tempname, template_dir=None):
        """查看某个模板名最终会命中哪个文件（排查"找不到模板"时很有用）"""
        return self._locator.find_template_file(tempname, template_dir)

    def templates(self):
        """列出所有搜索目录里可用的模板名（去重）"""
        _names = []
        for _d in get_template_dirs():
            if os.path.isdir(_d):
                for _f in sorted(os.listdir(_d)):
                    if _f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                        _n = os.path.splitext(_f)[0]
                        if _n not in _names:
                            _names.append(_n)
        return _names

    # ===== 通用暂停 =====
    def delay(self, seconds=1.0):
        """暂停指定秒数"""
        time.sleep(float(seconds))

    # ===== 路径辅助 =====
    def _screenshot_path(self, filename):
        """截图保存路径（相对当前工作目录，目录不存在会自动创建）"""
        _dir = workspace_path(Config.screenshot_dir)
        ensure_dir(_dir)
        return os.path.join(_dir, filename)

    def _template_save_path(self, filename):
        """模板保存路径（相对当前工作目录）"""
        _dir = workspace_path(Config.template_dir)
        ensure_dir(_dir)
        return os.path.join(_dir, filename)

    # ===== 屏幕截图 =====
    def shot(self, filename="screenshot"):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.shot("{filename}")')
        path = self._screenshot_path(f"{filename}_{time.strftime('%H%M%S')}.png")
        ImageGrab.grab().save(path)
        logger.info(f"截图已保存: {path}")
        print(f"截图已保存: {path}")
        return path

    def snip(self, x, y, w, h, filename="region"):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.snip({x},{y},{w},{h},"{filename}")')
        path = self._screenshot_path(f"{filename}_{time.strftime('%H%M%S')}.png")
        ImageGrab.grab(bbox=(int(x), int(y), int(x + w), int(y + h))).save(path)
        logger.info(f"区域截图已保存: {path}")
        print(f"区域截图已保存: {path}")
        return path

    def snap(self, note=""):
        """截取当前屏幕并保存到截图目录"""
        if self.recorder.enabled:
            self.recorder.capture(f'auto.snap("{note}")')
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f"snap_{timestamp}_{note}.png" if note else f"snap_{timestamp}.png"
        path = self._screenshot_path(filename)
        try:
            ImageGrab.grab().save(path)
            logger.info(f"截图已保存: {path}")
            print(f"截图已保存: {path}")
            return path
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    # ===== 鼠标操作 =====
    def moveto(self, x, y):
        """移动鼠标到指定坐标"""
        if self.recorder.enabled:
            self.recorder.capture(f'auto.moveto({x}, {y})', focus_shape=_build_circle_focus_shape(x, y))
        mouse_moveto(int(x), int(y))

    move = moveto

    def click(self, *args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """左键单击。支持 rect 区域、fast_mode 极速模式、template_dir 指定模板目录"""
        if not args:
            if self.recorder.enabled:
                x, y = get_mouse_point()
                self.recorder.capture("auto.click()", focus_shape=_build_circle_focus_shape(x, y))
            x, y = get_mouse_point()
            mouse_left_click(x, y)
        elif len(args) == 1 and isinstance(args[0], str):
            self._click_by_image(args[0], rect=rect, fast_mode=fast_mode,
                                 template_dir=template_dir, similarity=similarity)
        elif len(args) >= 2:
            if self.recorder.enabled:
                self.recorder.capture(f"auto.click({args[0]}, {args[1]})",
                                      focus_shape=_build_circle_focus_shape(args[0], args[1]))
            mouse_left_click(int(args[0]), int(args[1]))

    def dclick(self, *args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """双击。支持 rect 区域和 fast_mode"""
        if not args:
            if self.recorder.enabled:
                x, y = get_mouse_point()
                self.recorder.capture("auto.dclick()", focus_shape=_build_circle_focus_shape(x, y))
            x, y = get_mouse_point()
            mouse_left_click(x, y, k=2)
        elif len(args) == 1 and isinstance(args[0], str):
            self._click_by_image(args[0], clicks=2, rect=rect, fast_mode=fast_mode,
                                 template_dir=template_dir, similarity=similarity)
        elif len(args) >= 2:
            if self.recorder.enabled:
                self.recorder.capture(f"auto.dclick({args[0]}, {args[1]})",
                                      focus_shape=_build_circle_focus_shape(args[0], args[1]))
            mouse_left_click(int(args[0]), int(args[1]), k=2)

    def rclick(self, *args, rect=None, fast_mode=None, template_dir=None, similarity=None):
        """右键单击。支持 rect 区域和 fast_mode"""
        if not args:
            if self.recorder.enabled:
                x, y = get_mouse_point()
                self.recorder.capture("auto.rclick()", focus_shape=_build_circle_focus_shape(x, y))
            x, y = get_mouse_point()
            mouse_right_click(x, y)
        elif len(args) == 1 and isinstance(args[0], str):
            self._click_by_image(args[0], right_click=True, rect=rect, fast_mode=fast_mode,
                                 template_dir=template_dir, similarity=similarity)
        elif len(args) >= 2:
            if self.recorder.enabled:
                self.recorder.capture(f"auto.rclick({args[0]}, {args[1]})",
                                      focus_shape=_build_circle_focus_shape(args[0], args[1]))
            mouse_right_click(int(args[0]), int(args[1]))

    def drag(self, x1, y1, x2, y2, duration=0.5):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.drag({x1},{y1},{x2},{y2})',
                                  focus_shape=_build_circle_focus_shape(x1, y1))
        drag_mouse(int(x1), int(y1), int(x2), int(y2), duration=duration)

    def scroll(self, direction, clicks=3, x=None, y=None):
        if self.recorder.enabled:
            self.recorder.capture(f"auto.scroll('{direction}', clicks={clicks})")
        scroll_mouse(clicks, direction, x, y)

    def click_multi(self, *args, k=None, wait=None, times=None, interval=None,
                    rect=None, fast_mode=None, template_dir=None, similarity=None):
        """多次左键点击（图片仅定位一次）

        k / wait 为推荐写法；同时兼容旧文档里的 times= / interval= 写法。
        """
        k = k if k is not None else (times if times is not None else 1)
        wait = wait if wait is not None else (interval if interval is not None else 0.0)
        action_code = f'auto.click_multi("{args[0] if len(args)==1 else (args[0], args[1])}", k={k}, wait={wait})'
        if self.recorder.enabled:
            self.recorder.capture(action_code)

        if len(args) == 1 and isinstance(args[0], str):
            found = self._find_image_coords(args[0], area=rect, fast_mode=fast_mode,
                                           template_dir=template_dir, threshold=similarity)
            if found is not None:
                lx, ly, logic_w, logic_h = found
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                for i in range(k):
                    mouse_left_click(cx, cy)
                    if i < k - 1 and wait > 0:
                        time.sleep(wait)
            else:
                print(f"✗ 查找{args[0]}图片失败，流程终止")
        elif len(args) >= 2:
            x, y = int(args[0]), int(args[1])
            for i in range(k):
                mouse_left_click(x, y)
                if i < k - 1 and wait > 0:
                    time.sleep(wait)
        else:
            x, y = get_mouse_point()
            for i in range(k):
                mouse_left_click(x, y)
                if i < k - 1 and wait > 0:
                    time.sleep(wait)
        time.sleep(Config.post_click_delay)

    def click_seq(self, targets, wait=0.2, template_dir=None):
        """依次点击多个目标（支持图片名与坐标混合，list/tuple 均可）

        返回 True/False（旧版无返回值，失败只是打印一行）。
        """
        _list = list(targets) if isinstance(targets, (list, tuple)) else [targets]
        for idx, t in enumerate(_list):
            if isinstance(t, str):
                if not self._click_by_image(t, template_dir=template_dir):
                    print(f"✗ 查找{t}图片失败，流程终止")
                    return False
            elif isinstance(t, (tuple, list)) and len(t) >= 2:
                x, y = int(t[0]), int(t[1])
                if self.recorder.enabled:
                    self.recorder.capture(
                        f"auto.click_seq 坐标({x},{y})",
                        focus_shape=_build_circle_focus_shape(x, y)
                    )
                mouse_left_click(x, y)
            else:
                logger.warning(f"click_seq 目标格式不支持: {t}")
                return False
            if idx < len(_list) - 1:
                time.sleep(wait)
        time.sleep(Config.post_click_delay)
        return True

    def click_any(self, targets, timeout=None, wait=0.1, fast_mode=None, template_dir=None):
        """按顺序尝试点击列表中第一个出现的模板或坐标；返回 True/False"""
        if self.recorder.enabled:
            self.recorder.capture(f"auto.click_any({targets!r})")
        timeout_val = timeout or Config.default_timeout
        wait_val = float(wait)
        _list = list(targets) if isinstance(targets, (list, tuple)) else [targets]
        start = time.time()
        found = False
        while True:
            for t in _list:
                if isinstance(t, str):
                    found_coords = self._find_image_coords(t, timeout=0.01,
                                                           fast_mode=fast_mode,
                                                           template_dir=template_dir)
                    if found_coords is not None:
                        lx, ly, logic_w, logic_h = found_coords
                        cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                        mouse_left_click(cx, cy)
                        found = True
                        break
                elif isinstance(t, (tuple, list)) and len(t) >= 2:
                    mouse_left_click(int(t[0]), int(t[1]))
                    found = True
                    break
            if found or time.time() - start >= timeout_val:
                break
            time.sleep(wait_val)
        if not found:
            names = [str(t) for t in _list if isinstance(t, str)]
            if names:
                print(f"✗ 查找 {names} 图片失败，流程终止")
            else:
                print("✗ click_any 未找到任何目标，流程终止")
        time.sleep(Config.post_click_delay)
        return found

    def click_robust(self, tempname, retries=3, interval=0.5, timeout=None,
                     rect=None, fast_mode=None, template_dir=None, similarity=None):
        """鲁棒点击：找不到就等 interval 秒再试，最多 retries 次。返回 True/False。

        （旧文档把它称为"招牌功能"，但原代码里并没有这个 API）
        """
        for attempt in range(int(retries) + 1):
            if self.recorder.enabled:
                self.recorder.capture(f'auto.click_robust("{tempname}", 第{attempt + 1}次)')
            found = self._find_image_coords(tempname, timeout=timeout, area=rect,
                                            fast_mode=fast_mode, template_dir=template_dir,
                                            threshold=similarity)
            if found is not None:
                lx, ly, logic_w, logic_h = found
                cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
                mouse_left_click(cx, cy)
                time.sleep(Config.post_click_delay)
                return True
            if attempt < int(retries):
                logger.info(f"click_robust 第 {attempt + 1}/{retries} 次未找到 {tempname}，"
                            f"{interval}s 后重试")
                time.sleep(float(interval))
        print(f"✗ click_robust 重试 {retries} 次仍未找到 {tempname}")
        return False

    def pos(self):
        print("显示鼠标坐标（按Ctrl+C停止）...")
        try:
            while True:
                x, y = get_mouse_point()
                scale = get_scale_factor()
                print(f"\r鼠标坐标: ({x:>5}, {y:>5})  缩放: {scale:.2f}  ", end="")
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n已停止。")

    # ---- 图像点击内部实现（支持 rect 和 fast_mode）----
    def _click_by_image(self, tempname, clicks=1, right_click=False, rect=None, fast_mode=None,
                        template_dir=None, similarity=None):
        """找图点击，负责录像截图（含矩形标记及搜索范围蓝框）"""
        action_code = f"auto.{'rclick' if right_click else 'dclick' if clicks>1 else 'click'}(\"{tempname}\")"
        start_time = time.time()

        found = self._find_image_coords(tempname, area=rect, fast_mode=fast_mode,
                                        template_dir=template_dir, threshold=similarity)
        if found is not None:
            lx, ly, logic_w, logic_h = found
            shape = _build_rect_focus_shape(lx, ly, logic_w, logic_h)
            if self.recorder.enabled:
                self.recorder.capture(action_code, focus_shape=shape, search_rect=rect)
            cx, cy = _center_from_rect(lx, ly, logic_w, logic_h)
            if right_click:
                mouse_right_click(cx, cy)
            else:
                mouse_left_click(cx, cy, k=clicks)
            time.sleep(Config.post_click_delay)
            self._recorder.record('click', tempname, True, time.time() - start_time)
            return True
        else:
            if self.recorder.enabled:
                self.recorder.capture(action_code, focus_shape=None, search_rect=rect)
            self._error_handler.save_error_screenshot(tempname)
            return False

    def _find_image_coords(self, tempname, timeout=None, area=None, fast_mode=None,
                           template_dir=None, threshold=None):
        """找图并返回 (lx, ly, logic_w, logic_h) 或 None"""
        coords = self._locator.find(tempname, timeout=timeout, area=area, fast_mode=fast_mode,
                                    template_dir=template_dir, threshold=threshold)
        if coords != (-1, -1):
            lx, ly = coords
            tw, th = self._locator.get_template_size()
            scale = self._locator.get_scale_factor()
            logic_w, logic_h = _logic_size_from_phys(tw, th, scale)
            return lx, ly, logic_w, logic_h
        return None

    # ===== 键盘操作 =====
    def write(self, text, times=1):
        if self.recorder.enabled:
            display = str(text)[:47] + '...' if len(str(text)) > 50 else str(text)
            self.recorder.capture(f'auto.write("{display}", times={times})')
        saystring(str(text), k=times)

    def press(self, key_name, times=1):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.press("{key_name}", times={times})')
        key_press(str(key_name), k=times)

    def hotkey(self, *keys):
        if self.recorder.enabled:
            keys_str = '+'.join(keys)
            self.recorder.capture(f'auto.hotkey({keys_str})')
        key_press_plus([str(k).lower() for k in keys])

    def press_sequence(self, *keys):
        if self.recorder.enabled:
            seq = ', '.join(keys)
            self.recorder.capture(f'auto.press_sequence({seq})')
        for k in keys:
            key_press(str(k))
            time.sleep(0.05)

    # ===== 图像识别（支持 rect 和 fast_mode） =====
    def find(self, tempname, timeout=None, similarity=None, rect=None, fast_mode=None,
             template_dir=None, with_score=False):
        """查找图片，返回逻辑坐标 (x, y) 或 (-1, -1)。

        - similarity 现在真正生效（旧版该参数被静默丢弃，调了没用）
        - with_score=True 时返回 ((x, y), 匹配分数)
        - template_dir 可指定本次查找的模板来源目录
        """
        if self.recorder.enabled:
            self.recorder.capture(f'auto.find("{tempname}")', search_rect=rect)
        if with_score:
            return self._locator.find_with_score(tempname, timeout=timeout, area=rect,
                                                 threshold=similarity, fast_mode=fast_mode,
                                                 template_dir=template_dir)
        return self._locator.find(tempname, timeout=timeout, area=rect, fast_mode=fast_mode,
                                  template_dir=template_dir, threshold=similarity)

    def find_all(self, tempname, timeout=None, similarity=None, rect=None, fast_mode=None,
                 template_dir=None):
        """查找所有匹配，返回中心点逻辑坐标列表（按匹配分数从高到低），可直接用于 click(x,y)。
        调试截屏中会圈出所有找到的目标（红框）以及搜索范围（蓝框）。"""
        points = self._locator.find_all(tempname, timeout=timeout, area=rect, fast_mode=fast_mode,
                                        template_dir=template_dir)

        if self.recorder.enabled:
            if points:
                tw, th = self._locator.get_template_size()
                scale = self._locator.get_scale_factor()
                logic_w = int(tw / scale)
                logic_h = int(th / scale)

                # 为每个匹配点生成红框（基于左上角坐标，points 此时为左上角逻辑坐标）
                red_rects = []
                for (lx, ly) in points:
                    red_rects.append({
                        'type': 'rectangle',
                        'left': lx,
                        'top': ly,
                        'width': logic_w,
                        'height': logic_h
                    })

                self.recorder.capture(
                    f'auto.find_all("{tempname}")',
                    search_rect=rect,
                    focus_shapes=red_rects
                )
            else:
                # 即使没找到，依然生成调试截图（只有搜索范围蓝框）
                self.recorder.capture(
                    f'auto.find_all("{tempname}")',
                    search_rect=rect
                )

        if not points:
            return []

        # 转换为便于点击的中心点坐标（基于左上角坐标 points）
        tw, th = self._locator.get_template_size()
        scale = self._locator.get_scale_factor()
        logic_w = int(tw / scale)
        logic_h = int(th / scale)
        centers = [(int(lx + logic_w / 2), int(ly + logic_h / 2)) for (lx, ly) in points]
        return centers


    def wait(self, tempname, timeout=30, fast_mode=None, template_dir=None):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.wait("{tempname}", timeout={timeout})')
        logger.info(f"等待 '{tempname}' 出现（超时={timeout}s）...")
        print(f"等待 '{tempname}' 出现...")
        result = self._locator.find(tempname, timeout=timeout, fast_mode=fast_mode,
                                    template_dir=template_dir)
        if result != (-1, -1):
            print(f"'{tempname}' 已出现")
            return True
        else:
            print(f"✗ 查找{tempname}图片失败，流程终止")
            return False

    def wait_not(self, tempname, timeout=30, fast_mode=None, template_dir=None):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.wait_not("{tempname}", timeout={timeout})')
        logger.info(f"等待 '{tempname}' 消失（超时={timeout}s）...")
        print(f"等待 '{tempname}' 消失...")
        start = time.time()
        while time.time() - start < timeout:
            if self._locator.find(tempname, timeout=1, fast_mode=fast_mode,
                                 template_dir=template_dir) == (-1, -1):
                print(f"'{tempname}' 已消失")
                return True
            time.sleep(0.5)
        print(f"超时: '{tempname}' 未消失")
        return False

    # ===== 定时任务 =====
    def cron(self, time_str, task_func):
        t = time_str.replace(':', '')
        logger.info(f"已设置每日定时任务: {time_str}")
        timer_task(t, task_func)

    def cron1(self, time_str, task_func):
        t = time_str.replace(':', '')
        logger.info(f"已设置一次性定时任务: {time_str}")
        timer_task_once(t, task_func)

    # ===== 流程引擎 =====
    def do(self, name=None):
        """新建一个链式流程。name 用于水印与 trace 区分多个流程。"""
        flow = Flow(self._locator, self._recorder, self._error_handler,
                    debug_recorder=self.recorder, name=name)
        flow._step_mode = self._step_mode
        flow._step_pace = self._step_pace
        self._current_flow = flow
        return flow

    def stop(self):
        """中断当前正在执行的流程（可在回调/其它线程里调用）"""
        if self._current_flow is not None:
            self._current_flow.cancel()
            return True
        print("当前没有正在执行的流程")
        return False

    @property
    def current_flow(self):
        """最近一次由 auto.do() 创建的流程"""
        return self._current_flow

    def _aux_flow(self):
        """给非链式的便捷方法用的临时 Flow（复用同一个定位器与录像）"""
        return Flow(self._locator, self._recorder, self._error_handler, self.recorder)

    # ===== v2.1 新增便捷封装（非链式直接调用）=====
    def activate_window(self, pattern, timeout=5.0):
        """把标题匹配的窗口切到前台"""
        return activate_window(pattern, timeout)

    def foreground_title(self):
        """当前前台窗口标题"""
        return get_foreground_window_title()

    def read_clipboard(self):
        """读取剪贴板文本"""
        return _get_clipboard_text()

    def notify(self, message="流程执行完毕"):
        """弹一个系统提示（不阻塞）"""
        try:
            threading.Thread(
                target=lambda: ctypes.windll.user32.MessageBoxW(
                    0, str(message), "ImgClickFlow", 0x40 | 0x1000),
                daemon=True).start()
        except Exception as e:
            logger.debug(f"notify 失败: {e}")
        print(f"🔔 {message}")
        return True

    def wait_idle(self, rect=None, stable=1.0, timeout=30, diff=0.002):
        """等指定区域连续 stable 秒不再变化（取代瞎等 pause）"""
        return self._aux_flow()._exec_wait_idle(
            {'rect': rect, 'stable': stable, 'timeout': timeout, 'diff': diff})

    def click_all(self, target, wait=0.1, **kwargs):
        """点击所有匹配到的目标"""
        _kw = dict(kwargs)
        _kw['wait'] = wait
        return self._aux_flow()._exec_click_all(target, _kw)

    def click_nth(self, target, index=1, **kwargs):
        """点击第 index 个匹配（从 1 开始）"""
        _kw = dict(kwargs)
        _kw['index'] = index
        return self._aux_flow()._exec_click_nth(target, _kw)

    # ===== 调试 =====
    def step(self):
        self._step_mode = not self._step_mode
        print("单步模式已开启" if self._step_mode else "单步模式已关闭")

    def pace(self, seconds):
        self._step_pace = float(seconds)
        print(f"步骤间隔已设置为 {seconds} 秒")

    # ===== 环境诊断 =====
    def check(self):
        self._env_checker.check()

    def check_templates(self):
        self._env_checker.check_templates()

    # ===== 截图工具 =====
    def capture(self, name):
        if self.recorder.enabled:
            self.recorder.capture(f'auto.capture("{name}")')
        return capture_template_simple(name)

    # ===== 帮助 =====
    def help(self):
        print(__doc__)
        print(f"\n当前版本: {getattr(auto, 'version', '?')}")
        print(f"工作目录: {get_workspace_dir()}")
        print("模板搜索目录:")
        for _i, _d in enumerate(get_template_dirs(), 1):
            print(f"  {_i}. {_d}")
        print("\n作者知乎：https://www.zhihu.com/people/mhaksy")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    创建全局实例                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

auto = _Auto()
auto.version = "2.0.0"

search = find_files
monthends = first_last_day

if __name__ == "__main__":
    auto.help()
    print("\n" + "=" * 60)
    print("ImgClickFlow v2.0 正式发布")
    print("本版重点：工作目录自动识别 + 多模板文件夹来源 + 流程引擎与匹配引擎修复")
    print("详细变更见 CHANGELOG.md")
    print("=" * 60)
    print("\n助手已就绪。")
    print('''
╔══════════════════════════════════════════════════════════════════════╗
║              ImgClickFlow 办公助手 — 完整使用手册                      ║
║                  DPI已修复 · 纯文本 · 即查即用                         ║
╚══════════════════════════════════════════════════════════════════════╝
【目录】
  一、快速上手
  二、配置与参数
  三、API速查表
    3.1 鼠标操作
    3.2 键盘操作
    3.3 图像识别
    3.4 等待操作
    3.5 流程引擎（链式调用）
    3.6 调试工具
    3.7 环境诊断
    3.8 截图工具
    3.9 定时任务
    3.10 文件查找
    3.11 日期工具
    3.12 模板文件夹来源（v2.0 新增）
    3.13 链式指令全集（v2.0 新增）
    3.14 试跑、高亮与操作记录（v2.0 新增）
  四、经典实战案例

═══════════════════════════════════════════════════════════════════════
一、快速上手
═══════════════════════════════════════════════════════════════════════

【文件结构】（v2.0：以下目录都建在【你的脚本所在目录】，不再建在模块目录）
  你的脚本目录/
  ├── ImgClickFlow.py   ← 本模块（放哪都行，import 即可）
  ├── 你的脚本.py        ← 在这里 import ImgClickFlow
  ├── templates/       ← 主模板文件夹（放PNG图片，首次运行自动创建）
  ├── temp1/ temp2/    ← 你自定义的其它模板文件夹（可选，见 3.12）
  ├── logs/            ← 运行日志（自动生成）
  ├── screenshots_author/ ← 用户截图（自动生成）
  └── error_snapshots/ ← 找图失败时的现场截图（自动生成）

【三步开始】
  步骤1：把本模块放到你的项目里任意位置
  步骤2：在你的脚本里写一行代码（templates/ 会自动创建，把按钮PNG截图丢进去即可）
  步骤3：运行

  from ImgClickFlow import auto
  auto.click("按钮名称")   # 找图并点击，就这么简单

【重要提醒】
  ★ 截图必须用 PNG 格式！JPG 会造成像素偏移！
  ★ 目录位置：所有文件夹都建在【你的脚本所在目录】。
    可以用 auto.workspace_dir 查看，auto.set_workspace_dir() 修改。
  ★ 模板来源：默认读 templates/，也可以用 temp1、temp2……
    见 3.12「模板文件夹来源」。
  ★ 默认使用彩色匹配（精准），可通过 fast_mode=True 启用极速模式（灰度）
  ★ 模板只截按钮本体，越小越独特越好；
    但**不要截纯色/纯色块当模板**（v2.0 已能正确处理，但带边框文字更可靠）
  ★ 默认按匹配分数取最高分位置，不会点到屏幕左上角那个相似图案
  ★ 控制块必须配对：if_see()…endif()、for_data()…end_for()，
    漏写会在 .run() 时明确报错（不会再静默跳过）

═══════════════════════════════════════════════════════════════════════
二、配置与参数（修改脚本中的 Config 类）
═══════════════════════════════════════════════════════════════════════

  template_dir = "templates"        # 主模板文件夹（相对工作目录，或绝对路径）
  template_dirs = []                # 追加的模板搜索目录（按顺序查找）
  log_dir = "logs"                  # 日志存放文件夹
  screenshot_dir = "screenshots_author"  # 截图存放文件夹
  error_dir = "error_snapshots"     # 失败截图存放文件夹
  default_similarity = 0.9         # 图像匹配相似度（0~1，越高越严格）
  default_timeout = 10             # 找图默认超时（秒，真实等待时间）
  retry_interval = 0.2             # 找图失败重试间隔（秒）
  post_click_delay = 0.3           # 点击后等待界面反应的时间（秒）
  default_fast_mode = False        # 默认关闭极速模式（使用彩色匹配）
  max_match_candidates = 1000      # 单次匹配保留的候选点上限（防病态输入卡死）
  degenerate_template_std = 1.0    # 模板标准差低于此值视为纯色，改用逐像素差异匹配
  PIP_INDEX_URL = None             # 依赖自动安装所用源（None=官方PyPI）
  AUTO_INSTALL_DEPS = True         # 设为 False 则只提示、不自动安装依赖

═══════════════════════════════════════════════════════════════════════
三、API 速查表
═══════════════════════════════════════════════════════════════════════

3.1 鼠标操作
────────────────────────────────────────────────────────────────────

  auto.moveto(x, y)            移动鼠标到逻辑坐标
  auto.click()               左键单击（无参数/坐标/图片名，可选 rect/fast_mode）
  auto.dclick()              双击
  auto.rclick()              右键单击
  auto.drag(x1,y1,x2,y2)     平滑拖拽
  auto.pos()                 实时显示鼠标坐标（Ctrl+C停止）

3.2 键盘操作
────────────────────────────────────────────────────────────────────

  auto.write(text, times=1)       粘贴文本（支持中文）
  auto.press(key, times=1)        按下并释放单个键
  auto.hotkey(k1,k2,...)          组合键
  auto.press_sequence(*keys)      依次按下多个键
  auto.delay(seconds)             暂停指定秒数（无需导入 time）

  常用键名：enter, tab, esc, spacebar, backspace, del, ins,
           F1~F12, up_arrow, down_arrow, left_arrow, right_arrow,
           ctrl, alt, shift, left_win, right_win,
           numpad_0~numpad_9, 0~9, a~z, + , - . / ` ; [ \\ ] '

3.3 图像识别
────────────────────────────────────────────────────────────────────

  auto.find("按钮名", timeout=None, similarity=None, rect=None, fast_mode=None)
      返回 (x, y) 或 (-1, -1)
  auto.find_all("按钮名", rect=None, fast_mode=None) 
      返回所有匹配坐标列表 [(x,y), ...]
  auto.wait("按钮名", timeout=30, fast_mode=None)
      等待图片出现，成功返回True
  auto.wait_not("按钮名", timeout=30, fast_mode=None)
      等待图片消失

3.4 流程引擎（链式调用）
────────────────────────────────────────────────────────────────────

  创建流程：auto.do() → 添加步骤 → .run()

  可用方法：
    .click(目标, rect=None, fast_mode=None) 
    .dclick(目标, rect=None, fast_mode=None) 
    .rclick(目标, rect=None, fast_mode=None)
    .write(文本) .press(键名) .hotkey(k1,k2,...)
    .wait(目标, fast_mode=None) .wait_not(目标, fast_mode=None) .pause(秒)
    .retry(次数, 间隔)      # 整体重试
    .if_see(目标) .if_not_see(目标) .else_do() .endif()
    .for_data(列表) .end_for()   # 循环体可用{item}和{index}

3.5 调试工具
────────────────────────────────────────────────────────────────────

  auto.debug.on()         开启操作追踪
  auto.debug.off()        关闭追踪
  auto.debug.report()     生成HTML执行报告
  auto.debug.replay()     控制台回放记录

3.6 环境诊断
────────────────────────────────────────────────────────────────────

  auto.check()                全面诊断（分辨率、缩放、依赖）
  auto.check_templates()      检查所有模板是否可见

3.7 截图工具
────────────────────────────────────────────────────────────────────

  auto.shot("文件名")         全屏截图
  auto.snip(x,y,w,h,"文件名") 区域截图
  auto.capture("按钮名")      交互式生成模板截图

3.8 定时任务
────────────────────────────────────────────────────────────────────

  auto.cron("17:30", my_task)      # 每天17:30执行
  auto.cron1("18:00", my_task)     # 一次性定时

3.10 文件查找（需导入 search）
────────────────────────────────────────────────────────────────────

  from imgclickflow import search
  folders, files = search(r"D:\\工作", "报告,2025,doc")

3.11 日期工具（需导入 monthends）
────────────────────────────────────────────────────────────────────

  from imgclickflow import monthends
  days = monthends(2025)   # 返回2月到次年1月的首末日列表

3.12 模板文件夹来源（v2.0 新增）
────────────────────────────────────────────────────────────────────

  【查看】
  auto.workspace_dir          # 当前工作目录（默认=你的脚本所在目录）
  auto.template_dirs          # 当前模板搜索目录（按查找顺序）
  auto.templates()            # 所有目录里可用的模板名
  auto.template_path("登录")   # 这个模板最终会命中哪个文件（排查用）

  【指定】
  auto.set_workspace_dir(r"D:\\我的项目")          # 改工作目录
  auto.set_template_dir("temp1")                  # 改主模板目录
  auto.add_template_dir("temp2")                  # 追加一个搜索目录
  auto.set_template_dirs(["templates","temp1","temp2"])  # 一次设定顺序

  【同一个脚本用不同来源的模板】
  auto.click("保存")                 # → templates/保存.png（第一个命中的）
  auto.click("登录", template_dir="temp1")   # → temp1/登录.png
  auto.click("temp2/确定")                   # 名字带路径 → temp2/确定.png
  auto.click_seq(["temp1/登录", "temp2/确定", "保存"])
  auto.do().click("A", template_dir="temp1").click("B", template_dir="temp2").run()

  【规则】
  · 模板名带路径（含 / 或 \\）时按路径解析，先相对工作目录、再相对主模板目录
  · 不带路径时按 template_dirs 顺序找，第一个命中的生效
  · 找不到会抛 FileNotFoundError，并列出所有已搜索目录
  · 找到过的模板会按「路径+修改时间」缓存，改图后自动重新加载

3.13 链式指令全集（v2.0 新增）
────────────────────────────────────────────────────────────────────

  auto.do(name="流程名")  ...  .run()     创建并执行流程

  【鼠标】click  dclick  rclick  click_multi  click_seq  click_any  click_robust
          click_all  click_nth  click_offset  hover  drag  drag_to  scroll  moveto  snap
  【键盘】write  clear_and_write  type_slowly  press  hotkey
  【条件】if_see  if_not_see  if_window  if_count  if_color  if_python
          else_if  else_do  endif
  【循环】for_data/end_for   loop_n/end_loop
          while_see/end_while   until/end_until   break_if  continue_if
  【等待】wait  wait_not  wait_idle  wait_count  wait_any  pause  delay
  【失败】try_do/on_fail/end_try   expect  expect_not  assert_count
  【窗口】activate_window  assert_foreground
  【数据】set_var  find_all(var=)  read_clipboard(var=)   {名字} 占位符
  【其他】notify  remember_pos  restore_pos  mark  log  highlight  dump_state  call_python
  【控制】retry  opts(retry=/optional=)  timeout  dry_run  cancel  explain  validate

  常用写法：
    .click("保存").opts(retry=3, interval=1)      # 只重试这一步
    .click("广告弹窗").opts(optional=True)         # 点不到也不算失败
    .while_see("加载中", max=60, interval=0.5) ... .end_while()
    .until("完成标志", timeout=120) ... .end_until()
    .try_do().click("保存").on_fail().click("重试").end_try()
    .expect("提交成功")                            # 断言
    .if_window("记事本").write("内容").endif()      # 只在记事本里输入
    .assert_foreground("记事本").write("内容")      # 焦点不对就报错，绝不乱输入
    .wait_idle(rect=(0,0,800,600), stable=1.0)     # 等画面稳定，取代 pause(2)
    .read_clipboard(var="单号").write("单号 {单号}") # 读出来再用
    .call_python(lambda: time.sleep(1))            # DSL 覆盖不到就写 Python

  ★ while_see / until 必须给 max 或 timeout，否则运行前直接报错（防止死循环）
  ★ 控制块必须配对：endif / end_for / end_while / end_try 漏写会明确报错

3.14 试跑、高亮与操作记录（v2.0 新增）
────────────────────────────────────────────────────────────────────

  【试跑，不动鼠标键盘】
    flow = auto.do().click("保存").click("提交")
    flow.explain()              # 先看步骤清单 + 每个模板命中的实际文件
    flow.dry_run().run()        # 只报告"本来会点哪里"
    auto.do().highlight("保存", seconds=2).run()   # 屏幕上框出来闪一下

  【结构化操作记录（默认关，不依赖数据库）】
    auto.debug.trace(True)      # 落盘 logs/trace_<run_id>.jsonl
    auto.debug.slowest(10)      # 哪一步最慢
    auto.debug.weak_templates() # 匹配分数长期偏低的模板 → 该重新截图了
    auto.debug.failures()       # 历史失败点
    每条记录含：时间/步骤/动作/目标/结果/耗时，
    以及匹配分数 score、命中的 template_path 与 template_sha1、屏幕与 DPI 信息

  【中断正在跑的流程】
    flow.cancel()   或   auto.stop()
    flow.last_result # 结构化结果：success/attempts/failed_step/index/error/cancelled/...

═══════════════════════════════════════════════════════════════════════
四、经典实战案例（缩略版，更多见项目主页）
═══════════════════════════════════════════════════════════════════════

# 案例1：保存并关闭
auto.click("保存按钮")
auto.delay(1)
auto.click("关闭按钮")

# 案例2：链式新建文档并保存
auto.do()
    .click("新建按钮")
    .write("文档内容")
    .click("保存")
    .run()

# 案例3：批量循环填写表格
data = ["张三","李四","王五"]
auto.do()
    .for_data(data)
        .click("姓名框")
        .write("{item}")
        .click("保存")
    .end_for()
    .run()

# 案例4：条件分支
auto.do()
    .click("提交")
    .if_see("成功")
        .click("确定")
    .else_do()
        .click("重试")
    .endif()
    .run()

# 案例5：每日定时截图
def daily_job():
    auto.click("刷新")
    auto.shot("日报")
auto.cron("17:30", daily_job)

# 案例6：区域限定点击 + 极速模式
auto.click("链接", rect=(100,200,300,50), fast_mode=True)

═══════════════════════════════════════════════════════════════════════
文档版本：v2.0    适用脚本：ImgClickFlow    最后更新：2026年5月
作者知乎：https://www.zhihu.com/people/mhaksy
作者：FUJIAN·FUZHOU YUSHIHONG
═══════════════════════════════════════════════════════════════════════

''')
