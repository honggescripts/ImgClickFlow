# -*- coding: utf-8 -*-
"""ImgClickFlow 自测脚本（无需 pytest，直接 `python tests/run_tests.py`）

覆盖三块：
  A. 图像匹配引擎（选点策略、纯色模板、rect、超时、缓存）
  B. 流程引擎（条件分支、循环、嵌套、配对校验、retry 模式、占位符）
  C. 工作目录与多模板来源（目录落点、多目录解析）

全部用假 locator / 替换鼠标函数隔离：不产生真实点击，不截取你的屏幕。
"""
import os
import sys
import time
import shutil
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(HERE)
sys.path.insert(0, MODULE_DIR)

import numpy as np                                          # noqa: E402
import cv2                                                  # noqa: E402
import ImgClickFlow as ICF                                  # noqa: E402

# 测试里把默认超时调小，避免"找不到"的用例真的等 10 秒
ICF.Config.default_timeout = 0.3

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


# ---------------------------------------------------------------- 公共桩
clicked = []
ICF.mouse_left_click = lambda *a, **k: clicked.append(a)
ICF.mouse_right_click = lambda *a, **k: clicked.append(a)
ICF.mouse_moveto = lambda *a, **k: None


class Dummy:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _SimpleFakeEngine:
    """给 FakeLocator 补的最小假引擎（新版 _find_image_match 会用到）"""

    def __init__(self, loc):
        self._loc = loc
        self.tempsize = (20, 10)
        self.scale = 1.0
        self.threshold = 0.9

    def best_match_score(self, name, area_real=None, threshold=None,
                         fast_mode=None, template_dir=None):
        _r = self._loc.find(name)
        return _r, (0.95 if _r != (-1, -1) else -1.0)

    def template_path(self, name, template_dir=None):
        return f"C:/templates/{name}.png"


class FakeLocator:
    """假定位器：visible 里的名字才算"看得到" """

    def __init__(self, visible=()):
        self.visible = set(visible)
        self.calls = []
        self.image_engine = _SimpleFakeEngine(self)

    def _area_real(self, area):
        return None

    def find(self, name, timeout=None, area=None, threshold=None, fast_mode=None, template_dir=None):
        self.calls.append(name)
        return (100, 100) if name in self.visible else (-1, -1)

    def find_with_score(self, *a, **k):
        return (-1, -1), 0.0

    def find_all(self, *a, **k):
        return []

    def click(self, *a, **k):
        clicked.append(a)
        return True

    def get_template_size(self):
        return (20, 10)

    def get_scale_factor(self):
        return 1.0

    def find_template_file(self, tempname, template_dir=None):
        return None


def mk_flow(visible=()):
    loc = FakeLocator(visible)
    return ICF.Flow(loc, Dummy(), Dummy(), None), loc


def run_watch(flow, limit=3.0):
    """跑流程；挂死则判定为 HANG（这是旧版最严重的问题）"""
    res = {}

    def _target():
        try:
            res['r'] = flow.run()
        except Exception as exc:                            # noqa: BLE001
            res['e'] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_target, daemon=True)
    clicked.clear()
    t0 = time.time()
    t.start()
    t.join(limit)
    if t.is_alive():
        return 'HANG', res, time.time() - t0
    return ('OK' if 'r' in res else 'EXC'), res, time.time() - t0


# ================================================================ A
print("=" * 76)
print("A. 图像匹配引擎")
print("=" * 76)

WS = os.path.join(HERE, '_tmp_ws')
shutil.rmtree(WS, ignore_errors=True)
os.makedirs(os.path.join(WS, 'templates'), exist_ok=True)
os.makedirs(os.path.join(WS, 'temp2'), exist_ok=True)
ICF.set_workspace_dir(WS, create=True, verbose=False)
ICF.Config.template_dir = 'templates'
ICF.Config.template_dirs = []


def make_tpl(color=(40, 40, 200), text='OK'):
    t = np.full((40, 120, 3), 250, np.uint8)
    cv2.rectangle(t, (0, 0), (119, 39), color, -1)
    cv2.rectangle(t, (2, 2), (117, 37), (250, 250, 250), -1)
    cv2.putText(t, text, (30, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 2)
    return t


def save_tpl(d, name, img):
    cv2.imencode('.png', img)[1].tofile(os.path.join(d, name + '.png'))


def make_screen(w=1600, h=900, seed=7):
    rng = np.random.default_rng(seed)
    s = np.full((h, w, 3), 235, np.uint8)
    for _ in range(300):
        x, y = int(rng.integers(0, w - 100)), int(rng.integers(0, h - 60))
        cv2.rectangle(s, (x, y), (x + int(rng.integers(20, 90)), y + int(rng.integers(10, 40))),
                      tuple(int(v) for v in rng.integers(120, 240, 3)), -1)
    return s


def set_screen(eng, screen_bgr, honor_rect=True):
    """按引擎契约喂 RGB；honor_rect=True 时真的按 area_real 裁剪（否则 rect 用例无效）"""
    rgb = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2RGB)

    def _grab(area_real=None):
        if area_real is None or not honor_rect:
            return rgb
        l, t, w, h = [int(v) for v in area_real]
        return rgb[max(t, 0):t + h, max(l, 0):l + w]

    eng._grab_screen = _grab


tpl = make_tpl()
save_tpl(os.path.join(WS, 'templates'), 'btn', tpl)
save_tpl(os.path.join(WS, 'temp2'), 'only2', make_tpl((200, 40, 40), 'T2'))

s1 = make_screen()
s1[400:440, 700:820] = tpl

e = ICF._ImageEngine(); set_screen(e, s1)
check("单目标精确匹配定位正确", e.find_best('btn', timeout=0.3) == (700, 400),
      f"-> {e.find_best('btn', timeout=0.3)}（期望 (700,400)）")

s2 = make_screen()
s2[80:120, 120:240] = (tpl.astype(np.int16) * 0.88).clip(0, 255).astype(np.uint8)
s2[400:440, 700:820] = tpl
e = ICF._ImageEngine(); set_screen(e, s2)
_r = e.find_best('btn', timeout=0.3)
check("多个相似图案时取最高分而非扫描顺序第一个", _r == (700, 400),
      f"-> {_r}（期望 (700,400) 完美副本）")

s3 = make_screen()
for (_x, _y) in [(200, 150), (700, 400), (1200, 650)]:
    s3[_y:_y + 40, _x:_x + 120] = tpl
e = ICF._ImageEngine(); set_screen(e, s3)
_pts = e.find_all('btn', timeout=0.5)
check("find_all 找到全部 3 个相同按钮", len(_pts) == 3, f"-> {_pts}")

e = ICF._ImageEngine(); set_screen(e, s1)
_ins = e.find_best('btn', timeout=0.3, area_real=(600, 300, 400, 300))
_out = e.find_best('btn', timeout=0.3, area_real=(0, 0, 400, 300))
check("rect 区域内命中、区域外找不到", _ins == (700, 400) and _out == (-1, -1),
      f"inside={_ins} outside={_out}")

e = ICF._ImageEngine(); set_screen(e, s1)
check("灰度极速模式定位正确", e.find_best('btn', timeout=0.3, fast_mode=True) == (700, 400))

sN = make_screen(seed=99)
e = ICF._ImageEngine(); set_screen(e, sN)
_t0 = time.time(); e.find_best('btn', timeout=1.0); _dt = time.time() - _t0
check("find_best 超时时间即真实等待时间", 0.8 <= _dt <= 2.0, f"timeout=1.0 实际 {_dt:.2f}s")

flat = np.full((40, 120, 3), 200, np.uint8)
save_tpl(os.path.join(WS, 'templates'), 'flatbtn', flat)
sf = np.full((600, 800, 3), 128, np.uint8)
sf[100:140, 100:220] = (200, 200, 200)
sf[300:340, 500:620] = (200, 200, 200)
e = ICF._ImageEngine(); set_screen(e, sf, honor_rect=False)
_t0 = time.time()
_pf = e._match_template('flatbtn', (0, 0, 800, 600), 0.9, 'colorful')
_dt = time.time() - _t0
check("纯色模板不挂死且能定位（旧版会算 730 亿次比较）",
      0 < len(_pf) <= 4 and _dt < 3.0, f"{len(_pf)} 个候选点 {_dt:.2f}s -> {_pf}")

_grab_n = []
_real_grab = ICF.ImageGrab.grab
_rgb1 = cv2.cvtColor(s1, cv2.COLOR_BGR2RGB)
_huge = list(_rgb1)                 # 避免真的创建大对象
ICF.ImageGrab.grab = lambda *a, **k: (_grab_n.append(k.get('bbox')), _rgb1)[1]
try:
    e = ICF._ImageEngine()
    e._grab_screen(None)
    e._grab_screen(None)
finally:
    ICF.ImageGrab.grab = _real_grab
check("全屏截图缓存生效（2 次全屏抓屏只真抓 1 次）", len(_grab_n) == 1, f"实际抓屏 {len(_grab_n)} 次")

# ================================================================ B
print()
print("=" * 76)
print("B. 流程引擎")
print("=" * 76)

f, loc = mk_flow(['yes', 'A']); f.if_see('yes').click('A').endif()
_st, _res, _el = run_watch(f)
check("if_see(真) 不挂死且执行分支（旧版原地死循环）",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1,
      f"st={_st} run={_res.get('r')} clicks={len(clicked)} {_el:.2f}s")

f, loc = mk_flow([]); f.if_see('no').click('A').endif()
_st, _res, _el = run_watch(f)
check("if_see(假) 跳过分支", _st == 'OK' and _res.get('r') is True and len(clicked) == 0)

f, loc = mk_flow(['B']); f.if_see('no').click('A').else_do().click('B').endif()
_st, _res, _el = run_watch(f)
check("if_see(假)+else 执行 else 分支（旧版在 endif 死循环）",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1)

f, loc = mk_flow(['yes', 'A']); f.if_see('yes').click('A').else_do().click('B').endif()
_st, _res, _el = run_watch(f)
check("if_see(真)+else 只执行 if 分支", _st == 'OK' and len(clicked) == 1)

f, loc = mk_flow(['yes', 'A', 'C']); f.if_see('yes').if_see('yes').click('A').endif().endif().click('C')
_st, _res, _el = run_watch(f)
check("嵌套 if 正常", _st == 'OK' and _res.get('r') is True and len(clicked) == 2)

f, loc = mk_flow(['X', 'Y']); f.for_data([1, 2, 3]).click('X').click('Y').end_for()
_st, _res, _el = run_watch(f)
check("纯循环 3 项 × 2 步 = 6 次点击", _st == 'OK' and len(clicked) == 6)

f, loc = mk_flow(['X']); f.for_data([1, 2, 3]).click('X').if_see('X').click('X').endif().end_for()
_st, _res, _el = run_watch(f)
check("循环体内含 if → 3 项全部处理（旧版只跑 1 项就放弃）",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 6, f"clicks={len(clicked)}")

f, loc = mk_flow(['A', 'B'])
f.for_data(['a1', 'a2']).click('A').end_for().for_data(['b1', 'b2', 'b3']).click('B').end_for()
_st, _res, _el = run_watch(f)
check("两个串行循环各用各的数据（旧版第一个循环会用第二个的数据）",
      loc.calls.count('A') == 2 and loc.calls.count('B') == 3,
      f"A={loc.calls.count('A')} B={loc.calls.count('B')}")

f, loc = mk_flow(['A'])
f.for_data([['x', 'y'], ['z']]).click('A').for_data([1, 2]).click('A').end_for().end_for()
_st, _res, _el = run_watch(f)
check("嵌套循环正常（外层 2 项 × 3 次点击 = 6）", _st == 'OK' and len(clicked) == 6)

f, loc = mk_flow(['X']); f.for_data([1, 2, 3]).click('X').click('NOTHERE').end_for().click('X')
_st, _res, _el = run_watch(f)
check("循环体失败 → run() 返回 False（旧版返回 True）",
      _st == 'OK' and _res.get('r') is False)

f, loc = mk_flow(['C']); f.if_see('no').click('A').click('C')
_st, _res, _el = run_watch(f)
check("漏写 endif → 明确失败（旧版静默跳过并返回成功）",
      _st == 'OK' and _res.get('r') is False)

f, loc = mk_flow(['A']); f.for_data([1, 2]).click('A')
_st, _res, _el = run_watch(f)
check("漏写 end_for → 明确失败", _st == 'OK' and _res.get('r') is False)

f, loc = mk_flow(['A']); f.endif().click('A')
_st, _res, _el = run_watch(f)
check("多余的 endif → 明确失败", _st == 'OK' and _res.get('r') is False)


class OnceLocator(FakeLocator):
    """'保存' 总能找到，'确认' 永远找不到"""
    def find(self, name, timeout=None, area=None, threshold=None, fast_mode=None, template_dir=None):
        self.calls.append(name)
        return (100, 100) if name == '保存' else (-1, -1)


loc = OnceLocator()
f = ICF.Flow(loc, Dummy(), Dummy(), None)
f.retry(2, wait=0, mode='all').click('保存').click('确认').run()
check("retry(mode='all') 保留旧语义（整体重放，'保存' 点 3 次）",
      loc.calls.count('保存') == 3, f"{loc.calls}")

loc = OnceLocator()
f = ICF.Flow(loc, Dummy(), Dummy(), None)
f.retry(2, wait=0, mode='step').click('保存').click('确认').run()
check("retry(mode='step') 不重复点击已成功的按钮（'保存' 只点 1 次）",
      loc.calls.count('保存') == 1, f"{loc.calls}")

f, loc = mk_flow(['确定', '保存']); f.click_seq(('确定', '保存'))
_st, _res, _el = run_watch(f)
check("click_seq 传 tuple 真正点击（旧版 0 次却返回成功）", len(clicked) == 2)

f, loc = mk_flow(['Z']); f.for_data(['{index}']).click('{item}').end_for()
_st, _res, _el = run_watch(f)
check("{item} 单遍替换，数据里的 {index} 不被二次替换",
      bool(loc.calls) and set(loc.calls) == {'{index}'},
      f"find 收到 {sorted(set(loc.calls))}（旧版会变成 ['0']）")

f, loc = mk_flow(['X']); f.wait('NEVER', timeout=0.2, optional=True).click('X')
_st, _res, _el = run_watch(f)
check("wait(optional=True) 超时后流程继续",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1)

f, loc = mk_flow(['yes']); f.click_robust('yes', retries=2, interval=0)
_st, _res, _el = run_watch(f)
check("click_robust 有执行器可用（旧版报「未知操作」）",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1)

# ================================================================ C
print()
print("=" * 76)
print("C. 工作目录与多模板来源")
print("=" * 76)

check("工作目录 = 调用方脚本所在目录",
      os.path.normcase(ICF.get_workspace_dir()) == os.path.normcase(WS),
      f"{ICF.get_workspace_dir()}")

ICF.set_template_dirs(['templates', 'temp2'], create=False, verbose=False)
_e = ICF._ImageEngine()
check("多目录：能在第二个目录里找到模板",
      _e.template_path('only2') is not None and 'temp2' in _e.template_path('only2'),
      f"-> {_e.template_path('only2')}")
check("模板名带路径解析", _e.template_path('temp2/only2') is not None)
check("显式指定 template_dir 优先", _e.template_path('only2', template_dir='temp2') is not None)
check("搜索顺序正确", [os.path.basename(d) for d in _e.search_dirs] == ['templates', 'temp2'],
      f"{[os.path.basename(d) for d in _e.search_dirs]}")

try:
    _e._load_template('根本不存在的模板')
    check("模板缺失抛出可读错误", False)
except FileNotFoundError as _ex:
    check("模板缺失抛出 FileNotFoundError 并列出搜索目录",
          '已搜索以下目录' in str(_ex), str(_ex).splitlines()[0])

for _d in ('templates', 'temp2'):
    check(f"工作目录下存在 {_d}/", os.path.isdir(os.path.join(WS, _d)))

shutil.rmtree(WS, ignore_errors=True)

# ================================================================ D
print()
print("=" * 76)
print("D. v2.1 新增能力")
print("=" * 76)

ICF.set_workspace_dir(HERE, create=True, verbose=False)
ICF.Config.default_timeout = 0.3      # 让"找不到"的用例快速返回
# 屏蔽真实的键盘副作用，避免测试动到用户的桌面
ICF.auto.recorder.enabled = False
_said = []
ICF.saystring = lambda *a, **k: _said.append(a)
ICF.key_press_plus = lambda *a, **k: None
ICF.key_press = lambda *a, **k: None
ICF._get_clipboard_text = lambda: "测试剪贴板内容"


class FakeEngine:
    """假图像引擎：按预设表返回匹配结果"""

    def __init__(self, matches=None, all_matches=None, score=0.9876):
        self.matches = matches or {}
        self.all_results = all_matches or {}
        self.tempsize = (20, 10)
        self.scale = 1.0
        self.threshold = 0.9
        self._score = score

    def refresh_screen_info(self):
        pass

    def _bbox(self, area_real=None):
        return (0, 0, 200, 200)

    def template_path(self, name, template_dir=None):
        return f"C:/templates/{name}.png"

    def best_match_score(self, name, area_real=None, threshold=None,
                         fast_mode=None, template_dir=None):
        if name in self.matches:
            return self.matches[name], self._score
        return (-1, -1), -1.0

    def find_all(self, name, timeout=None, area_real=None, threshold=None,
                 fast_mode=None, template_dir=None):
        return list(self.all_results.get(name, []))

    def _grab_screen(self, area_real=None):
        return np.zeros((20, 20, 3), dtype=np.uint8)


class FakeLocator2:
    def __init__(self, matches=None, all_matches=None, score=0.9876):
        self.image_engine = FakeEngine(matches, all_matches, score)
        self.calls = []

    def _area_real(self, area):
        return None

    def find(self, name, timeout=None, area=None, threshold=None, fast_mode=None,
             template_dir=None):
        self.calls.append(name)
        return self.image_engine.matches.get(name, (-1, -1))

    def find_with_score(self, name, **k):
        _r = self.find(name)
        return _r, (self.image_engine._score if _r != (-1, -1) else -1.0)

    def find_all(self, name, **k):
        return list(self.image_engine.all_results.get(name, []))

    def click(self, *a, **k):
        clicked.append(a)
        return True

    def get_template_size(self):
        return (20, 10)

    def get_scale_factor(self):
        return 1.0

    def find_template_file(self, name, d=None):
        return f"C:/templates/{name}.png"


def mk2(matches=None, all_matches=None, name="t"):
    loc = FakeLocator2(matches, all_matches)
    return ICF.Flow(loc, Dummy(), Dummy(), None, name=name), loc


# ---- 阶段 0 ----
_f = ICF.auto.do()
check("死状态已清除（_current_if_stack / _data_context / _loop_data）",
      not hasattr(_f, '_current_if_stack') and not hasattr(_f, '_data_context')
      and not hasattr(_f, '_loop_data'))

_dr = ICF.DebugRecorder()
_dr.enabled = False
_dr._is_failure = True
_dr.begin_run("流程B")
check("begin_run 重置失败标记（不再跨流程污染）", _dr._is_failure is False)

# ---- 阶段 1：取消 / trace / mark ----
f, loc = mk2({'X': (100, 100)})
f.call_python(lambda: f.cancel()).pause(5).click('X')
_r = f.run()
check("cancel 后 run() 返回 False 且标记 cancelled",
      _r is False and f.last_result.get('cancelled') is True,
      f"{f.last_result.get('error')}")

check("trace 默认关闭", ICF.auto._recorder.trace_enabled is False)

ICF.auto.debug.trace(True)
f = ICF.auto.do(name="trace测试")
f.locator = FakeLocator2({'X': (100, 100)})
f.click('X').mark("检查点").call_python(lambda: None)
f.run()
_rows = ICF.auto.debug.read_trace()
_tp = ICF.auto._recorder.trace_path(f._run_id)
_click_rows = [r for r in _rows if r.get('action') == 'click']
check("trace 落盘且包含匹配分数/模板路径/屏幕信息",
      os.path.exists(_tp) and bool(_click_rows)
      and all(k in _click_rows[-1] for k in
              ('score', 'template_path', 'template_sha1', 'screen',
               'dpi_scale', 'duration', 'action', 'run_id', 'flow')),
      f"{os.path.basename(_tp) if _tp else None} 共 {len(_rows)} 行")
check("trace 里 mark 也被记录", any(r.get('action') == 'mark' for r in _rows))
check("debug.weak_templates 可查询（不依赖数据库）",
      isinstance(ICF.auto.debug.weak_templates(), list))
ICF.auto.debug.trace(False)

# ---- 阶段 2：窗口 / 等待 ----
check("窗口标题匹配（多关键字 / 大小写不敏感）",
      ICF.title_matches("无标题 - 记事本", "记事本|Notepad")
      and ICF.title_matches("Notepad++", "记事本|Notepad")
      and not ICF.title_matches("Excel", "记事本"))

f, loc = mk2()
f.assert_foreground("绝对不存在的窗口关键字__zzz")
_st, _res, _el = run_watch(f)
check("assert_foreground 不匹配时判失败", _st == 'OK' and _res.get('r') is False)

f, loc = mk2()
f.wait_idle(rect=(0, 0, 200, 200), stable=0.3, timeout=8)
_st, _res, _el = run_watch(f, limit=12)
check("wait_idle 在静止画面上返回成功", _st == 'OK' and _res.get('r') is True)

f, loc = mk2(all_matches={'行': [(10, 10), (10, 30), (10, 50)]})
f.wait_count('行', n=3, op='>=', timeout=2)
_st, _res, _el = run_watch(f)
check("wait_count 达到数量即通过", _st == 'OK' and _res.get('r') is True)

f, loc = mk2({'B': (5, 5)})
f.wait_any(['A', 'B', 'C'], timeout=2)
_st, _res, _el = run_watch(f)
check("wait_any 命中并写入 last_matched",
      _st == 'OK' and _res.get('r') is True and f.last_matched == 'B',
      f"matched={f.last_matched}")

# ---- 阶段 3：条件循环 ----
f, loc = mk2({'加载中': (1, 1)})
f.while_see('加载中', max=3, interval=0).pause(0).end_while()
_st, _res, _el = run_watch(f)
check("while_see 达到 max 上限后退出（不死循环）", _st == 'OK' and _res.get('r') is True)

f, loc = mk2()
f.until('完成', max=2, interval=0).pause(0).end_until()
_st, _res, _el = run_watch(f)
check("until 达到 max 上限后退出", _st == 'OK' and _res.get('r') is True)

f, loc = mk2({'A': (1, 1)})
check("while_see 缺 max/timeout 时构建期报错",
      any('max' in _e or 'timeout' in _e
          for _e in f.while_see('A').end_while().validate()))

f, loc = mk2({'X': (1, 1), 'A': (1, 1), 'C': (1, 1)})
f.while_see('X', max=2, interval=0).if_see('A').click('A').endif() \
 .for_data([1]).click('C').end_for().end_while()
_st, _res, _el = run_watch(f)
check("while 循环体内可嵌 if 与 for", _st == 'OK' and _res.get('r') is True,
      f"clicks={len(clicked)}")

f, loc = mk2({'X': (1, 1)})
f.for_data([1, 2, 3]).click('X').break_if('X').end_for().mark("出去")
_st, _res, _el = run_watch(f)
check("break_if 生效（第一个数据项后即跳出）",
      _st == 'OK' and len(clicked) == 1, f"clicks={len(clicked)}")

f, loc = mk2({'X': (1, 1), 'SKIP': (1, 1)})
f.for_data([1, 2]).continue_if('SKIP').click('X').end_for()
_st, _res, _el = run_watch(f)
check("continue_if 生效（跳过本次剩余步骤）",
      _st == 'OK' and len(clicked) == 0, f"clicks={len(clicked)}")

f, loc = mk2({'X': (1, 1)})
f.loop_n(4).click('X').end_loop()
_st, _res, _el = run_watch(f)
check("loop_n 重复执行 N 次", _st == 'OK' and len(clicked) == 4, f"clicks={len(clicked)}")

# ---- 阶段 3：条件扩展 ----
f, loc = mk2(all_matches={'行': [(1, 1), (1, 2), (1, 3)]}, matches={'A': (9, 9)})
f.if_count('行', '>=', 3).click('A').endif()
_st, _res, _el = run_watch(f)
check("if_count 条件成立", _st == 'OK' and len(clicked) == 1)

f, loc = mk2({'A': (9, 9)})
f.if_python(lambda: True).click('A').endif()
_st, _res, _el = run_watch(f)
check("if_python 条件成立", _st == 'OK' and len(clicked) == 1)

clicked.clear()
f, loc = mk2(matches={'B': (9, 9)})
f.if_see('A').click('A').else_if('B').click('B').else_do().click('Z').endif()
_st, _res, _el = run_watch(f)
check("else_if：A 不可见、B 可见 → 执行 B 分支",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1,
      f"clicks={len(clicked)}")

clicked.clear()
f, loc = mk2(matches={'Z': (5, 5)})
f.if_see('A').click('A').else_if('B').click('B').else_do().click('Z').endif()
_st, _res, _el = run_watch(f)
check("else_if：A、B 都不可见 → 落到 else 分支（验证 else_if 条件真的被判定）",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1,
      f"clicks={len(clicked)}")

f, loc = mk2()
f.if_color(0, 0, "#000000", tol=255).mark("颜色分支").endif()
_st, _res, _el = run_watch(f)
check("if_color 可判定（容差 255 必然命中）", _st == 'OK' and _res.get('r') is True)

# ---- 阶段 3：步骤级重试 / optional ----
class FlakyEngine(FakeEngine):
    """第一次找不到、之后找得到的引擎（用于验证步骤级 retry）"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.n = 0

    def best_match_score(self, name, area_real=None, threshold=None,
                         fast_mode=None, template_dir=None):
        self.n += 1
        return ((100, 100), 0.99) if self.n >= 2 else ((-1, -1), -1.0)


_obj = FakeLocator2()
_obj.image_engine = FlakyEngine()
f = ICF.Flow(_obj, Dummy(), Dummy(), None)
f.click('X').opts(retry=3, interval=0)
_st, _res, _el = run_watch(f)
check("步骤级 retry 生效（第 2 次尝试成功）",
      _st == 'OK' and _res.get('r') is True, f"尝试 {_obj.image_engine.n} 次")

f, loc = mk2()
f.click('不存在').opts(optional=True).mark("继续")
_st, _res, _el = run_watch(f)
check("opts(optional=True) 失败不中止流程",
      _st == 'OK' and _res.get('r') is True)

# ---- 阶段 4：失败分支 / 断言 / 超时 ----
f, loc = mk2({'OK': (1, 1)})
f.try_do().click('不存在').on_fail().click('OK').end_try()
_st, _res, _el = run_watch(f)
check("try_do/on_fail 走失败分支并继续",
      _st == 'OK' and _res.get('r') is True and len(clicked) >= 1,
      f"clicks={len(clicked)}")

f, loc = mk2({'OK': (1, 1)})
f.try_do().click('OK').on_fail().click('不存在').end_try()
_st, _res, _el = run_watch(f)
check("try_do 成功时不走 on_fail",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 1,
      f"clicks={len(clicked)}")

f, loc = mk2({'OK': (1, 1)})
f.expect('OK', timeout=1)
_st, _res, _el = run_watch(f)
check("expect 命中即通过", _st == 'OK' and _res.get('r') is True)

f, loc = mk2()
f.expect('不存在', timeout=0.3)
_st, _res, _el = run_watch(f)
check("expect 未命中判失败", _st == 'OK' and _res.get('r') is False)

f, loc = mk2()
f.expect_not('不存在', timeout=0.3)
_st, _res, _el = run_watch(f)
check("expect_not 未出现即通过", _st == 'OK' and _res.get('r') is True)

f, loc = mk2(all_matches={'行': [(1, 1), (1, 2)]})
f.assert_count('行', n=2, op='==')
_st, _res, _el = run_watch(f)
check("assert_count 数量断言通过", _st == 'OK' and _res.get('r') is True)

f, loc = mk2({'X': (1, 1)})
f.timeout(0.4).pause(2).click('X')
_st, _res, _el = run_watch(f, limit=8)
check("流程整体 timeout 生效",
      _st == 'OK' and _res.get('r') is False
      and '超时' in str(f.last_result.get('error')), f"{f.last_result.get('error')}")

f, loc = mk2()
f.click('不存在')
f.run()
check("last_result 字段完整",
      all(k in (f.last_result or {}) for k in
          ('success', 'attempts', 'failed_step', 'index', 'error', 'cancelled',
           'duration', 'dry_run', 'run_id', 'flow', 'steps')),
      str(list((f.last_result or {}).keys()))[:100])

# ---- 变量 / 逃生舱 / 调试 ----
_seen = []
f, loc = mk2({'X': (1, 1)})
f.call_python(lambda: _seen.append(1))
_st, _res, _el = run_watch(f)
check("call_python 执行了传入的函数", _seen == [1])

f, loc = mk2({'X': (1, 1)})
f.set_var('who', 'Alice').click('X')
_st, _res, _el = run_watch(f)
check("set_var 可用于 {占位符}",
      f._apply_placeholders('hi {who}', {})[0] == 'hi Alice')

f, loc = mk2({'X': (1, 1)})
f.find_all('X', var='pts')
_st, _res, _el = run_watch(f)
check("find_all 结果可存进变量", 'pts' in f._vars, f"{f._vars.get('pts')}")

f, loc = mk2({'X': (1, 1)})
f.dry_run().click('X').write('内容')
_st, _res, _el = run_watch(f)
check("dry_run 不产生真实点击且列出将执行的动作",
      _st == 'OK' and _res.get('r') is True and len(clicked) == 0
      and len(f._dry_run_actions) == 2, f"actions={f._dry_run_actions}")

try:
    mk2({'X': (1, 1)})[0].highlight('X', seconds=0.1)
    mk2({'X': (1, 1)})[0].explain()
    check("explain/highlight 构建不报错", True)
except Exception as _e:
    check("explain/highlight 构建不报错", False, str(_e))

# ---- 批量 / 相对定位 ----
_all = {'行': [(10, 10), (10, 40), (10, 70)]}
f, loc = mk2(all_matches=_all)
f.click_all('行', wait=0)
_st, _res, _el = run_watch(f)
check("click_all 点击了全部 3 个匹配", _st == 'OK' and len(clicked) == 3,
      f"clicks={len(clicked)}")

clicked.clear()
f, loc = mk2(all_matches=_all)
f.click_nth('行', index=2)
_st, _res, _el = run_watch(f)
check("click_nth 点击第 2 个（中心应为 20,45）",
      _st == 'OK' and bool(clicked) and clicked[-1][:2] == (20, 45),
      f"{clicked[-1][:2] if clicked else None}")

clicked.clear()
f, loc = mk2({'X': (100, 100)})
f.click_offset('X', dx=5, dy=-3)
_st, _res, _el = run_watch(f)
check("click_offset 偏移正确（中心 110,105 + 5,-3）",
      _st == 'OK' and bool(clicked) and clicked[-1][:2] == (115, 102),
      f"{clicked[-1][:2] if clicked else None}")

f, loc = mk2({'X': (1, 1), 'Y': (2, 2)})
f.hover('X', wait=0)
_st, _res, _el = run_watch(f)
check("hover 可用", _st == 'OK' and _res.get('r') is True)

f, loc = mk2({'X': (1, 1), 'Y': (2, 2)})
f.drag_to('X', 'Y', duration=0)
_st, _res, _el = run_watch(f)
check("drag_to 可用（两个模板定位）", _st == 'OK' and _res.get('r') is True)

# ---- 输入输出（真实副作用已屏蔽）----
_said.clear()
f, loc = mk2({'X': (1, 1)})
f.clear_and_write('新内容')
_st, _res, _el = run_watch(f)
check("clear_and_write 可用（已屏蔽真实按键）",
      _st == 'OK' and _res.get('r') is True and _said == [('新内容',)])

f, loc = mk2({'X': (1, 1)})
f.type_slowly('abc', interval=0)
_st, _res, _el = run_watch(f)
check("type_slowly 可用（已屏蔽真实按键）", _st == 'OK' and _res.get('r') is True)

f, loc = mk2({'X': (1, 1)})
f.read_clipboard(var='clip')
_st, _res, _el = run_watch(f)
check("read_clipboard 存入变量", f._vars.get('clip') == "测试剪贴板内容",
      f"{f._vars.get('clip')!r}")

f, loc = mk2({'X': (1, 1)})
f.remember_pos().restore_pos()
_st, _res, _el = run_watch(f)
check("remember_pos/restore_pos 可用", _st == 'OK' and _res.get('r') is True)

# ================================================================ 汇总
print()
print("=" * 76)
print(f"汇总：通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for _n in FAIL:
        print("  -", _n)
print("=" * 76)
sys.exit(1 if FAIL else 0)
