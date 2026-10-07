
# ImgClickFlow v2.0
> 桌面图像自动化领域的「单兵作战神兵利器」—— 一个 Python 脚本，就能超越商业 RPA 的丝滑体验。

[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)
[![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-blue)](https://www.python.org/downloads/)
[![Platform Windows](https://img.shields.io/badge/platform-Windows-blue)](https://www.microsoft.com/windows)

**作者知乎**：[https://www.zhihu.com/people/mhaksy](https://www.zhihu.com/people/mhaksy)
**作者**：YUSHIHONG
**位置**：FUJIAN·FUZHOU

## 设计哲学：像初代 iPhone 一样极简

作者相信，真正的力量源于极致的简单。ImgClickFlow 不依赖臃肿的编辑器、复杂的模块或繁琐的配置，**一切皆为 `auto`**。作者已将 DPI 适配、超时重试、智能去重、多模板目录等所有复杂逻辑内化，留给用户的只有和说话一样自然的自动化代码。

**核心代码约 240KB（含大量注释与内置手册），单一 Python 文件即开即用。真正的零配置：目录会自动创建，模板可以放在任意多个文件夹里。**

不仅是个人的单兵作战神兵利器，更是**中小企业节省宝贵商业RPA经费、提升效率的有利武器**。无需购买昂贵的商业许可证，无需培训专职 RPA 工程师，一个脚本即可本地部署，一切尽在用户掌控，全员可用。

## 🆕 v2.0 新能力总览

### 1. 工作目录自动识别：你的脚本在哪，目录就建在哪

被其他脚本 `import` 时，`templates/`、`logs/`、截图等目录一律建在**调用方脚本的同级目录**，而不是本模块所在目录。首次运行自动创建并打印提示：

```
你的脚本目录/
├── ImgClickFlow.py         # 本模块（放哪都行，能 import 即可）
├── 我的脚本.py             # 在这里 from ImgClickFlow import auto
├── templates/              # 主模板文件夹（首次运行自动创建）
├── temp1/  temp2/          # 你自定义的其它模板文件夹（可选）
├── logs/                   # 运行日志
├── screenshots_author/     # auto.shot()/auto.snap() 的截图
└── error_snapshots/        # 找图失败时的现场截图

[ImgClickFlow] 工作目录：C:\...\forpython\amazon
  已创建 templates/  —— 把按钮截图（PNG）放进来
  已创建 logs/  —— 运行日志
  ...
```

```python
auto.workspace_dir                  # 查看当前工作目录
auto.set_workspace_dir(r"D:\项目")   # 或手动指定
```

### 2. 多模板文件夹来源：不同模板可以放在不同文件夹

```python
auto.set_template_dirs(["templates", "temp1", "temp2"])   # 设定查找顺序

auto.click("保存")                          # → templates/保存.png
auto.click("登录", template_dir="temp1")     # → temp1/登录.png
auto.click("temp2/确定")                     # 名字带路径 → temp2/确定.png
auto.click_seq(["temp1/登录", "temp2/确定", "保存"])

auto.do().click("A", template_dir="temp1").click("B", template_dir="temp2").run()
```

查找规则：模板名带路径时按路径解析，否则按 `auto.template_dirs` 顺序取第一个命中的；
找不到会抛 `FileNotFoundError` 并**列出所有已搜索目录**。

```python
auto.template_dirs          # 当前搜索顺序
auto.templates()            # 所有目录里可用的模板名
auto.template_path("登录")   # 这个模板最终命中哪个文件（排查用）
auto.add_template_dir("temp3")   # 再追加一个
```

### 3. 链式指令大扩充（v2.0 新增）

旧版只有 `click / write / if_see / for_data` 几个动作，现在补上了自动化真正需要的部分：

```python
auto.do(name="日报流程")
    # —— 条件：窗口 / 数量 / 颜色 / 任意 Python ——
    .if_window("记事本|Notepad").log("在记事本里").endif()
    .if_count("未读", ">=", 1).click("全部已读").endif()
    .if_python(lambda: os.path.exists(r"D:\报表.xlsx")).click("下载").endif()
    .else_if("另一种情况").click("B")          # 多分支（旧版只有 if/else）

    # —— 条件循环：旧版完全写不出来 ——
    .while_see("加载中", max=60, interval=0.5).pause(0.5).end_while()
    .until("完成标志", timeout=120, interval=2).click("刷新").end_until()
    .loop_n(3).click("下一步").end_loop()
    .break_if("已经到底了")                     # 循环内提前跳出

    # —— 等待界面稳定，取代瞎等 pause(n) ——
    .click("查询").wait_idle(rect=(400, 200, 1200, 600), stable=1.0, timeout=30)
    .wait_count("数据行", n=5, timeout=30)
    .wait_any(["成功", "失败"], timeout=60)     # 命中名存进 {matched}

    # —— 失败不再只有"中止"一条路 ——
    .try_do().click("保存").on_fail().click("关闭弹窗").click("保存").end_try()
    .expect("提交成功")                          # 断言，看不到就算失败

    # —— 安全：拒绝在错误的窗口里输入 ——
    .activate_window("记事本").assert_foreground("记事本").write("内容")

    # —— 批量与相对定位 ——
    .click_all("复选框")                         # 一次点掉所有匹配
    .click_nth("行", index=3)
    .click_offset("保存", dx=10, dy=0)            # 点"保存"右边 10 像素处
    .hover("菜单").click("子项")                  # 悬停展开再点
    .drag_to("卡片", "目标列")

    # —— 输入输出闭环（旧版只能写、不能读）——
    .clear_and_write("内容")                      # Ctrl+A 清空再粘贴
    .read_clipboard(var="单号")                   # 把屏幕上的值读出来
    .write("单号是 {单号}")                        # 用 {名字} 引用

    # —— 步骤级控制 ——
    .click("保存").opts(retry=3, interval=1)      # 只重试这一步
    .click("广告弹窗").opts(optional=True)         # 点不到也不算失败

    # —— 逃生舱：DSL 覆盖不到就写 Python ——
    .call_python(lambda: time.sleep(2))
    .run()
```

**`while_see` / `until` 必须给 `max` 或 `timeout`**，否则运行前直接报错 —— 不允许写出无上限死循环。

### 4. 让人敢按第一次运行：试跑与高亮

```python
flow = auto.do().click("保存").click("提交")
flow.explain()                  # 先打印步骤清单 + 每个模板命中的实际文件
flow.dry_run().run()            # 试跑：只报告"本来会点哪里"，不动鼠标键盘
auto.do().highlight("保存", seconds=2).run()   # 在屏幕上框出来闪一下，肉眼确认认对了
```

### 5. 结构化操作记录（JSONL，不需要数据库）

调试截图默认只留 20 张，超出就删——**截图本身是有损记录**。打开 trace 就能永久保留可检索的操作历史：

```python
auto.debug.trace(True)          # 或 Config.trace_enabled = True
# 落盘到 logs/trace_<run_id>.jsonl，每行含：
#   action / target / result / duration
#   score(匹配分数) / template_path / template_sha1 / screen / dpi_scale ...

auto.debug.slowest(10)          # 哪一步最慢
auto.debug.weak_templates()     # 匹配分数长期偏低的模板 → 该重新截图了
auto.debug.failures()           # 历史失败点
```

## 安装

需要第三方库（首次 `import` 会自动安装，也可手动执行）：

```bash
python -m pip install opencv-python numpy Pillow pywin32
```

* 不想自动安装：把脚本里的 `AUTO_INSTALL_DEPS = False`，它只会打印命令。
* 想用国内镜像：把 `PIP_INDEX_URL` 改成你的镜像地址（默认走官方 PyPI）。

> 平台要求：**Windows 8.1 及以上**。模块启动时会声明 DPI 感知（Per-Monitor V2），
> Windows 7 及更早系统缺少 `shcore.dll` 会导入失败。

## 10 秒上手

1. 把 `ImgClickFlow.py` 放到你的项目里（和你的脚本同目录最省事）。
2. 写一行代码并运行：

```python
from ImgClickFlow import auto
auto.click("你的按钮")
```

3. 首次运行会在你脚本的同级目录自动创建 `templates/`，把按钮截图（PNG）丢进去。
4. 再跑一次，完成。

> 连快捷键都不用配。

## ⚙️ 配置参数（修改 `Config` 类）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `template_dir` | `"templates"` | 主模板文件夹（相对工作目录或绝对路径） |
| `template_dirs` | `[]` | 追加的模板搜索目录（按顺序查找） |
| `default_similarity` | `0.9` | 匹配相似度（0~1，越高越严格） |
| `default_timeout` | `10` | 找图超时（秒）。**这就是真实等待时间** |
| `default_fast_mode` | `False` | `False` 彩色精准匹配，`True` 灰度极速 |
| `post_click_delay` | `0.3` | 点击后让界面缓一缓 |
| `max_match_candidates` | `1000` | 单次匹配保留的候选点上限（防病态输入卡死） |
| `degenerate_template_std` | `1.0` | 模板标准差低于此值视为纯色，改用逐像素差异匹配 |
| `AUTO_INSTALL_DEPS` | `True` | 缺依赖时是否自动 pip 安装 |
| `PIP_INDEX_URL` | `None` | 自动安装所用的源（`None`=官方 PyPI） |
| `auto.recorder.enabled` | `True` | 调试截图总开关 |

## API 速查表

### 工作目录与模板目录（v2.0）
| 方法 | 说明 |
|------|------|
| `auto.workspace_dir` | 当前工作目录（= 你脚本所在目录） |
| `auto.set_workspace_dir(path)` | 指定工作目录 |
| `auto.template_dirs` | 模板搜索目录列表 |
| `auto.set_template_dir(path)` | 设置主模板目录 |
| `auto.add_template_dir(path)` | 追加搜索目录 |
| `auto.set_template_dirs([...])` | 一次设定查找顺序 |
| `auto.template_path(name)` | 查看模板命中的实际文件 |
| `auto.templates()` | 列出所有可用模板名 |

### 鼠标操作（万物皆可点）
| 方法 | 说明 | 示例 |
|------|------|------|
| `auto.click()` | 左键单击（无参/坐标/图片名，支持 `rect`,`fast_mode`,`template_dir`,`similarity`） | `auto.click("保存")` |
| `auto.dclick()` | 双击 | `auto.dclick("文件", rect=(0,0,500,500))` |
| `auto.rclick()` | 右键 | `auto.rclick("菜单")` |
| `auto.click_multi()` | 多次点击 | `auto.click_multi("加号", k=5, wait=0.2)` |
| `auto.click_seq()` | 依次点击列表（`list`/`tuple` 均可） | `auto.click_seq(["登录","确定"])` |
| `auto.click_any()` | 候补点击（任意命中即点） | `auto.click_any(["蓝色登录","红色登录"])` |
| `auto.click_robust()` | 鲁棒点击（失败后独立重试） | `auto.click_robust("保存", retries=3, interval=0.5)` |
| `auto.drag()` | 拖拽 | `auto.drag(100,100,500,400)` |
| `auto.scroll()` | 滚轮 | `auto.scroll('down', 5)` |

### 键盘操作（粘贴即输入）
| 方法 | 说明 | 示例 |
|------|------|------|
| `auto.write()` | 剪贴板粘贴（支持中文） | `auto.write("报表.docx")` |
| `auto.press()` | 单键 | `auto.press("enter", 2)` |
| `auto.hotkey()` | 组合键 | `auto.hotkey("ctrl","v")` |

### 图像识别（眼睛与大脑）
| 方法 | 说明 |
|------|------|
| `auto.find("名", timeout, similarity, rect, fast_mode, template_dir)` | 返回坐标 `(x,y)`，找不到返回 `(-1,-1)`；`with_score=True` 时返回 `((x,y), 分数)` |
| `auto.find_all("名", ...)` | 返回所有匹配坐标列表（**按匹配分数从高到低**） |
| `auto.wait("名", timeout, fast_mode, template_dir)` | 等待图片出现 |
| `auto.wait_not("名", timeout, fast_mode, template_dir)` | 等待图片消失 |

> 匹配策略：默认彩色优先，**按匹配得分取最高分位置**（不会再因为屏幕上有相似图案而点到靠左上角的那个）。

### 流程引擎（真正的魔法都在这里）
```python
auto.do()
    .retry(3, wait=2)                # 整体失败重试 3 次（旧行为，注意会重复已成功的点击）
    # .retry(3, wait=2, mode="step") # 只重跑失败步骤及其之后，不重复点击已成功的按钮
    .click("提交")
    .if_see("成功")                   # 条件分支（v2.0 已修复挂死问题）
        .click("确定")
    .else_do()
        .click("重试")
    .endif()
    .for_data(["张三","李四"])        # 循环，可用 {item} / {index}，支持嵌套
        .click("姓名框")
        .write("{item}")
        .click("保存", template_dir="temp1")   # 循环里也能指定模板来源
    .end_for()
    .run()
```

> 控制块必须配对：漏写 `endif()` / `end_for()` / `end_while()` / `end_try()` 会在 `.run()` 时
> **明确报错并指出是第几条指令**，不会再静默跳过剩余流程。
> `while_see` / `until` 必须给 `max` 或 `timeout`，否则运行前报错。

**链式可用指令全集**

| 分类 | 方法 |
|---|---|
| 鼠标 | `click` `dclick` `rclick` `click_multi` `click_seq` `click_any` `click_robust` `click_all` `click_nth` `click_offset` `hover` `drag` `drag_to` `scroll` `moveto` `snap` |
| 键盘 | `write` `clear_and_write` `type_slowly` `press` `hotkey` |
| 条件 | `if_see` `if_not_see` `if_window` `if_count` `if_color` `if_python` `else_if` `else_do` `endif` |
| 循环 | `for_data` `end_for` `loop_n` `end_loop` `while_see` `end_while` `until` `end_until` `break_if` `continue_if` |
| 等待 | `wait` `wait_not` `wait_idle` `wait_count` `wait_any` `pause` `delay` |
| 失败与断言 | `try_do` `on_fail` `end_try` `expect` `expect_not` `assert_count` |
| 窗口安全 | `activate_window` `assert_foreground` |
| 数据 | `set_var` `find_all` `read_clipboard` |
| 其他 | `notify` `remember_pos` `restore_pos` `mark` `log` `highlight` `dump_state` `call_python` |
| 控制 | `retry` `opts` `timeout` `dry_run` `cancel` `explain` `validate` `run` |

### 调试与诊断
| 方法 | 说明 |
|------|------|
| `auto.debug.on()` / `.off()` | 开启/关闭操作追踪（内存） |
| **`auto.debug.trace(True)`** | **开启结构化落盘**（JSONL，含匹配分数/模板哈希/屏幕信息） |
| `auto.debug.slowest(10)` | 最慢的步骤 |
| `auto.debug.weak_templates()` | 匹配分数长期偏低的模板 → 建议重新截图 |
| `auto.debug.failures()` | 历史失败点汇总 |
| `auto.debug.report()` | 生成 HTML 执行报告 |
| **`flow.dry_run().run()`** | **试跑：只报告"本来会点哪里"，不动鼠标键盘** |
| **`flow.highlight("保存")`** | 在屏幕上把目标框出来闪一下，确认引擎认对了 |
| `flow.explain()` | 运行前打印步骤清单 + 每个模板命中的实际文件 |
| `flow.dump_state()` | 存一张全屏图，标出所有模板的匹配位置 |
| `flow.cancel()` / `auto.stop()` | 中断正在执行的流程 |
| `auto.step()` | 开启单步模式，每次操作前按回车 |
| `auto.check()` | 一键诊断环境（含工作目录、全部模板目录、DPI 真实状态） |
| `auto.check_templates()` | 检查所有模板目录下的模板当前是否可见 |
| `auto.recorder.enabled = False` | 关闭调试截图以加速 |

## 常见问题

**Q: 为什么截图必须是 PNG？**
A: JPG 压缩会造成像素偏移，导致匹配失败。建议统一用 PNG。

**Q: 我的脚本在别的目录，模板应该放哪？**
A: 放在**你的脚本同级目录**下的 `templates/`（首次运行会自动创建）。
用 `auto.workspace_dir` 可以确认位置，`auto.set_workspace_dir()` 可以改。

**Q: 不同功能的模板想分开放，怎么办？**
A: 用多模板目录：`auto.set_template_dirs(["templates","temp1","temp2"])`，
然后 `auto.click("登录", template_dir="temp1")` 或 `auto.click("temp2/确定")`。

**Q: 换了一台 4K 屏电脑，脚本需要改吗？**
A: 不需要。在同一台机器上重新截取 `templates` 里的截图即可，DPI 缩放会被自动处理。

**Q: 脚本跑着跑着就停了，怎么找原因？**
A: 打开 `[脚本名]_操作截屏` 文件夹，最后一张带红框/红圈的截图就是案发现场；
重试耗尽后还会生成 `error_summary.txt`。水印上印着是哪一行代码执行的。

**Q: 会不会点到屏幕左上角那个相似的按钮？**
A: v2.0 起不会：默认按**匹配得分最高**的位置点击，而不是扫描顺序第一个过阈值的点。

## 本版修复了什么

v2.0 修掉了一批实质缺陷（全部有实测复现），重点包括：

* **条件分支挂死**：`if_see` 条件成立时会在原地死循环（实测 3 秒空转 434 万次 `find`），现已修复。
* **循环丢数据**：循环体里写 `if` 会导致只处理第一个数据项却返回成功，现已支持 `if`、`while` 与嵌套循环。
* **`else_if` 条件从未被判定**：多分支会直接进入分支体，条件形同虚设（本轮自查发现）。
* **点错位置**：旧版返回扫描顺序第一个过阈值点，而非最高分点（对应 issue "click wrong place"）。
* **纯色模板卡死**：模板为纯色时匹配算法退化，会产生 38 万个候选点、约 730 亿次比较把进程拖死数小时。
* **高 DPI 下只搜到屏幕左上角**：缩放非 100% 时截图框只覆盖屏幕的 44%~75%。
* **超时不准**：`click_by_img` 声明 1 秒实际等 5 秒以上。
* **失败标记跨流程污染**：一个流程失败后，后续流程的调试截图水印全被标成 `[失败]`。
* **`wait` 失败静默中止**：超时会终止流程但没有任何提示。
* **`click_robust` 不存在**、`Flow.delay()` 不存在、`click_multi(times=)` 报错 —— 文档写了但代码里没有的 API 都已补齐。

完整清单（含每一项的旧行为实测数据）见 [CHANGELOG.md](CHANGELOG.md)。

## 验证

```bash
python tests/run_tests.py
```

**83 项断言全部通过**，覆盖匹配引擎、流程引擎、工作目录/多模板、以及本版全部新指令；
测试用假定位器与替换过的鼠标键盘函数隔离，**不会真的点你的鼠标、也不会截你的屏**。

此外还做过「旧版 vs 新版引擎、同一进程、同一输入」的差分回归测试：
**常规用法 5/5 一致，无回归**，差异项均为上表所列的预期修复。

## 许可证

本项目采用 **Mozilla Public License 2.0（MPL-2.0）**，详见 [LICENSE](LICENSE)。

**一行代码，让图像自动化回归它本应有的样子。**
