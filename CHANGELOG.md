# Changelog

本项目所有值得注意的变更都记录在此。
版本号格式：`主版本.次版本.修订号`。代码内 `auto.version` 与 README 标题保持一致。

---

## [2.0.0] — 2026-10-07

> 一次性发布版：**修复 + 能力大幅扩充**。因为 1.2.0 之后没有发布过其它版本，
> 本轮所有修复与新功能合并为 **2.0.0** 一个版本。
>
> ⚠️ **请先读「八、行为变化」一节。**

### 一、新增：工作目录自动识别

* **被其他脚本 import 时，一切目录都在「调用方脚本所在目录」下创建与管理**，
  不再受模块自身所在目录影响。
  例如 `C:\Users\123\Desktop\forpython\amazon\测试.py` 里 `import ImgClickFlow`，
  则 `templates/`、`logs/`、`screenshots_author/`、`error_snapshots/` 都建在
  `...\forpython\amazon\` 下。
* 首次运行**自动创建缺失目录并打印提示**（只提示本次新建的）：

  ```
  [ImgClickFlow] 工作目录：...\forpython\amazon
    已创建 templates/  —— 把按钮截图（PNG）放进来
    已创建 logs/  —— 运行日志
    ...
    提示：可用 auto.set_template_dir() / auto.add_template_dir() 指定其它模板目录
  ```
* 新增 API：`auto.workspace_dir`、`auto.set_workspace_dir(path)`。
* 目录创建失败（只读目录等）只告警，不再让 `import` 直接抛异常。
* 调试录像目录 `{脚本名}_操作截屏/` 同样跟随工作目录。

### 二、新增：多模板文件夹来源

* `Config.template_dirs`（追加搜索目录，按顺序查找）+ 新增 API：

  | API | 说明 |
  |---|---|
  | `auto.set_template_dir(path)` | 设置主模板目录 |
  | `auto.add_template_dir(path)` | 追加一个搜索目录 |
  | `auto.set_template_dirs([...])` | 一次性设置查找顺序 |
  | `auto.template_dirs` | 查看当前顺序 |
  | `auto.templates()` | 列出所有目录里可用的模板名 |
  | `auto.template_path(name)` | 查看某模板最终命中的文件 |

* **同一个脚本可用不同来源的模板**：

  ```python
  auto.click("保存")                        # → templates/保存.png
  auto.click("登录", template_dir="temp1")   # → temp1/登录.png
  auto.click("temp2/确定")                   # 名字带路径 → temp2/确定.png
  auto.click_seq(["temp1/登录", "temp2/确定", "保存"])
  auto.do().click("A", template_dir="temp1").click("B", template_dir="temp2").run()
  ```
* 相对路径解析：先相对工作目录，不存在再相对主模板目录。
* 找不到模板时抛 `FileNotFoundError` 并**列出所有已搜索目录**。
* 模板图片按「路径 + 修改时间」缓存，改图后自动重新加载。

### 三、新增：链式指令大幅扩充

#### 条件与分支
| 指令 | 说明 |
|---|---|
| `if_window("记事本\|Notepad")` | 当前前台窗口标题匹配时进入分支 |
| `if_count("行", ">=", 3)` | 匹配个数满足条件时进入分支（`>= <= == != > <`） |
| `if_color(x, y, "#FF0000", tol=12)` | 像素颜色接近时进入分支 |
| `if_python(lambda: ...)` | 任意 Python 表达式为真时进入分支 |
| `else_if(...)` | **多分支**（旧版只有 if/else 二选一） |

#### 条件循环（旧版完全写不出来）
| 指令 | 说明 |
|---|---|
| `while_see("加载中", max=60, timeout=30, interval=0.5)` … `end_while()` | 只要能看到就循环 |
| `until("完成标志", timeout=120, interval=2)` … `end_until()` | 只要看不到就循环 |
| `loop_n(5)` … `end_loop()` | 单纯重复 N 次 |
| `break_if("弹窗")` / `continue_if("跳过条件")` | 循环内提前跳出 / 跳过本次剩余步骤 |

> `while_see` / `until` **必须给 `max` 或 `timeout`**，否则 `.run()` 前直接报错 —— 不允许写出无上限死循环。

#### 失败处理与断言
| 指令 | 说明 |
|---|---|
| `try_do()` … `on_fail()` … `end_try()` | 块内任一步失败则走 `on_fail` 分支，失败不再只有"中止"一条路 |
| `expect("提交成功")` / `expect_not("错误提示")` | 断言目标出现 / 不出现，不满足即判失败并留证 |
| `assert_count("未读", n=0, op="==")` | 断言匹配个数 |

#### 步骤级控制
| 指令 | 说明 |
|---|---|
| `.opts(retry=3, interval=1)` | **只重试这一步**（对所有动作统一生效） |
| `.opts(optional=True)` | 这一步失败也不算失败，标记后继续 |
| `timeout(120)` | **整个流程**的最长执行时间，超时中止 |
| `cancel()` / `auto.stop()` | 中断正在执行的流程（可跨线程调用） |
| `flow.last_result` | 结构化结果：`success/attempts/failed_step/index/error/cancelled/duration/run_id/...` |

#### 窗口焦点安全（防止把输入打进错误窗口）
| 指令 | 说明 |
|---|---|
| `activate_window("记事本")` | 把匹配窗口切到前台（含绕过前台锁定的实现） |
| `assert_foreground("记事本")` | 断言前台窗口，不匹配即失败 —— **拒绝在错误窗口输入** |
| `auto.foreground_title()` | 读取当前前台窗口标题 |

#### 等待界面稳定（取代瞎等 `pause(n)`）
| 指令 | 说明 |
|---|---|
| `wait_idle(rect=None, stable=1.0, timeout=30)` | 等区域**连续 stable 秒不再变化**再继续 |
| `wait_count("数据行", n=5, timeout=30)` | 等匹配个数达标 |
| `wait_any(["成功","失败","超时"], timeout=60)` | 等候选之一出现，命中名存入 `{matched}` |

#### 批量与相对定位
| 指令 | 说明 |
|---|---|
| `click_all("复选框", wait=0.1)` | 点击**所有**匹配（批量勾选/关闭） |
| `click_nth("行", index=3)` | 点第 N 个匹配 |
| `click_offset("保存", dx=10, dy=0)` | 点命中位置偏移处（点标签右边的输入框） |
| `hover("菜单项", wait=0.5)` | 悬停（展开悬停菜单） |
| `drag_to("源", "目标")` | 用两个模板做拖拽 |

#### 输入输出闭环
| 指令 | 说明 |
|---|---|
| `clear_and_write("内容")` | 先 `Ctrl+A` 清空再粘贴，避免与旧内容拼接 |
| `type_slowly("abc", interval=0.05)` | 逐字键入（老 ERP / 远程桌面拒绝粘贴时用） |
| `read_clipboard(var="x")` | **把屏幕上的值读出来**存入 `{x}` |
| `set_var("n", 5)` | 设置运行时变量，后续用 `{n}` 引用 |
| `find_all("行", var="pts")` | 把所有匹配坐标存进变量 |
| `for_data(lambda: ...)` / `for_data("pts")` | 循环数据支持**运行时求值**与变量名 |
| `{item}` `{index}` `{名字}` | 占位符可作用于目标名**和** kwargs；`{{x}}` 转义为字面量 |
| `notify("完成")` / `remember_pos()` / `restore_pos()` | 系统提示 / 记住与恢复鼠标位置 |

#### 调试与创作（让人敢按第一次运行）
| 指令 | 说明 |
|---|---|
| **`run(dry_run=True)` / `dry_run()`** | **只报告"本来会点哪里"，不真的操作鼠标键盘** |
| **`highlight("保存", seconds=2)`** | 在屏幕上把目标框出来闪一下，肉眼确认引擎认的是不是它 |
| `explain()` | 运行前打印可读步骤清单 + 每个模板命中的实际文件 |
| `dump_state()` | 存一张全屏图，把流程里用到的模板匹配位置全标出来 |
| `mark("检查点")` / `log("...")` | 在 trace 与截图水印里插入标记 |
| **`call_python(func)`** | 链式里插任意 Python（DSL 覆盖不到时的逃生舱） |

### 四、新增：结构化操作记录（JSONL，不依赖数据库）

**默认关闭**，`auto.debug.trace(on=True)` 或 `Config.trace_enabled = True` 打开。

* 落盘：`logs/trace_<run_id>.jsonl`，一行一个操作，追加写
* 每行包含：`action / target / kwargs / result / duration / timestamp`，
  以及图像动作的 **`score`（匹配分数）/ `template_path` / `template_sha1`**，
  再加 `screen / dpi_scale / dpi_aware / run_id / flow / script / pid`
* 查询（直接读 JSONL，**不引入数据库**）：
  ```python
  auto.debug.slowest(10)          # 最慢的步骤
  auto.debug.weak_templates()     # 匹配分数长期偏低的模板 → 建议重新截图
  auto.debug.failures()           # 历史失败点汇总
  auto.debug.read_trace()         # 原始记录
  ```

> **为什么需要它**：调试截图默认只保留 20 张，超出就删 —— **截图本身是有损记录**；
> 而 `_StepRecorder` 默认又是关闭的，所以旧版跑完一趟手上什么都没留下。
> `score` 字段尤其有用：长期在 0.90~0.93 边缘徘徊的模板就是"迟早找错"的定时炸弹。

### 五、修复：流程引擎

| 缺陷 | 旧行为（实测） | 新行为 |
|---|---|---|
| **条件分支挂死** | `if_see` 条件成立时在 `if_start` 上**原地死循环**（实测 3 秒空转 434 万次 `find`）；条件不成立且有 `else` 时在 `endif` 上死循环 | 正常执行，不再挂死 |
| **循环体含 `if` 丢数据** | 循环体里的 `if` 被当成"未知操作"，**只跑第 1 个数据项就放弃**，且 `run()` 返回 `True` | 循环体支持 `if`、`while`、嵌套 `for` |
| **循环失败上报成功** | 循环体内某步失败 → 静默中止循环，`run()` 仍返回 `True` | 立即中止整个流程，返回 `False` |
| **多个循环串数据** | 循环数据存在实例单槽位，**第一个循环会用第二个循环的数据** | 数据快照进指令本身 |
| **控制块零校验** | 漏写 `endif`/`end_for` → 静默跳过剩余全部流程并返回 `True` | `.run()` 前静态校验并报错，指明是第几条指令 |
| **`else_if` 条件从未被判定** | （本轮自查发现）`else_if` 会直接进入其分支体，条件形同虚设 | 正确逐分支判定 |
| **`wait` 失败静默中止** | 超时会终止流程但无任何提示 | 明确打印原因；`.opts(optional=True)` 可忽略 |
| **`{item}` 二次替换** | `"{item}"` + 数据 `"{index}"` → 被替换成行号 | 单遍替换；`{{item}}` 可转义 |
| **`{item}` 不作用于 kwargs** | 循环里 `template_dir="{item}"` 无效 | 占位符同时作用于 kwargs |
| `click_seq` 传元组 | 得到空列表 → **一次都没点却返回成功** | 兼容 `list`/`tuple`/单值；空列表明确报错 |
| `click_robust` | 文档称"招牌功能"，**代码里不存在** | 已实现（`auto.click_robust()` 与链式 `.click_robust()`） |
| `Flow.delay()` | 文档链式示例里的 `.delay()` 不存在 | 补为 `pause()` 别名 |
| `click_multi` 参数 | 文档写 `times=/interval=`，实际只认 `k=/wait=` → `TypeError` | 两套写法都支持 |
| `retry` 无法选语义 | 只能整体重放（已成功的按钮被重复点） | 新增 `mode="step"`，只重跑失败步骤及其之后 |
| 调试录像 | 循环体每步重复截图；**最常见的"找图失败"不生成失败快照** | 去掉重复截图；任何失败都会生成失败快照与 `error_summary.txt` |
| **失败标记跨流程污染** | `_is_failure` 一旦置真，**后续流程的截图水印全被标成 `[失败]`** | 新增 `begin_run()`，每次流程开始重置 |
| `DebugRecorder.start("名字")` | 显式传任务名时 `NameError` | 已修 |
| 未知动作 | 记一行日志后继续 | 抛错并明确提示 |
| `run()` 重复调用 | 静默完整重放（副作用重复） | 打印警告 |

### 六、修复：图像匹配引擎

| 缺陷 | 旧行为（实测） | 新行为 |
|---|---|---|
| **点错位置**（对应 issue #1 "click wrong place"） | 返回**扫描顺序第一个**过阈值点。实测完美副本得分 1.0000 在 (700,400)，却返回了 (120,80) 的劣质副本 | 按匹配得分取最高分（`cv2.minMaxLoc`） |
| **纯色模板挂死** | 零方差模板使 `TM_CCOEFF_NORMED` 退化成"整幅图恒为 1.0"，800×600 屏幕产生 **382,041 个候选点** → O(n²) 去重约 **730 亿次比较（数小时）** | 自动改用 `TM_SQDIFF_NORMED`；实测 **0.07s** 完成且定位正确 |
| 候选点无上限 | 病态输入可直接拖死进程 | 新增 `Config.max_match_candidates`，超限截断 |
| 去重耗时 | 全部过阈值点直接进 O(n²) 双重循环 | 先用局部极大值（膨胀法）筛选，再邻近合并；去重函数本身未改 |
| **DPI 取景框** | `scale>1` 时把物理尺寸再除以缩放系数当截图框，实测 `scale=1.5` 只覆盖屏幕 **44.4%**、`scale=2.0` 只剩 **25%** | 直接使用物理框；`scale=1.0` 时与旧版完全一致 |
| 截图缓存 | 是死代码（条件互相矛盾），从未写入也从未命中 | 正确生效，0.1s 内复用同一张截图 |
| `click_by_img` 超时 | `max(timeout*5, 25)` 次尝试，每次内部再等 1 秒 → **声明 1s 实际等 5s+** | 超时时间即真实最长等待时间 |
| `_Auto.find(similarity=)` | 参数被静默丢弃，调了没用 | 真正生效；另加 `with_score=True` 返回匹配分数 |
| `find_all` 顺序 | `np.where` 扫描顺序 | 按匹配分数从高到低 |
| 模板比搜索区域还大 | OpenCV 直接报错 | 提前挡掉并给出可读提示 |

### 七、修复：DPI、依赖与其他

* **DPI 感知声明改为可靠实现**：`SetProcessDpiAwarenessContext(PMv2)` →
  `shcore.SetProcessDpiAwareness(2)` → `SetProcessDPIAware()` 依次回退，
  并且**设置后主动查询真实状态**（旧版假设"调用成功=已感知"，宿主已设置时会误判）。
* 依赖自动安装：**移除硬编码的第三方镜像**，默认走官方 PyPI；
  新增 `PIP_INDEX_URL`（想用国内镜像自行填写）与 `AUTO_INSTALL_DEPS`（设为 `False` 只提示不安装）；
  安装过程不再完全静默，失败会给出可复制命令。
* `Config.auto_screenshot_on_error`（旧版拼写为 `uto_screenshot_on_error`，实际从未生效）。
* **移除源码里硬编码的作者内部目录** `r'D:\企划部工作\02 对外报送'`
  （`find_files` 默认参数改为工作目录，默认关键字清空）。
* `_StepRecorder` / `_ErrorHandler` / `_EnvChecker` / `auto.shot/snip/snap`
  统一使用工作目录（旧版用静态 `_base_dir`，`set_workspace_dir` 后会失效）。
* `_EnvChecker.check()` 现在列出**全部**模板搜索目录、模板数量与 DPI 真实状态。
* 错误截图文件名做了非法字符净化（模板名带路径时不再拼出非法文件名）。
* 清理三处**死状态**（`_current_if_stack`、`_data_context`、`_loop_data`，均只写不读）。

### 八、行为变化（可能影响现有脚本）

1. **`find_best` 的选点策略变了**：屏幕上有多个相似图案时，现在返回**最像**的那个而不是最靠左上的那个。
   这是本版最重要的修复，但如果你过去"依赖"了先到先得的顺序，行为会变。
2. **`retry()` 默认语义不变**（仍是整体重放，会重复执行已成功的步骤）；
   想要安全语义请显式写 `.retry(3, mode="step")`。
3. **控制块必须配对**：漏写 `endif()`/`end_for()` 从"静默跳过并返回成功"变为**明确报错并返回 False**。
4. **循环体内某步失败会让整个流程失败**（旧版返回 `True`）。
5. **`Flow` 现在需要 `locator` 提供 `image_engine` / `_area_real`**（即 `_LocatorFusion`）。
   正常使用 `auto.do()` 不受影响；只有自行注入自定义定位器的用法需要留意。
6. **`scale=1.0`（绝大多数机器）下匹配与截图行为与旧版完全一致**；
   差异只出现在 `scale>1` 的场景，且都是修复方向。
7. 新增开关**默认都是关闭 / 旧行为**：`Config.trace_enabled`、步骤 `optional`、步骤 `retry`、
   `dry_run` 不设置就是原行为。

### 九、本版不做（明确排除）

| 项 | 原因 |
|---|---|
| 运行时实时写 SQLite 作为核心存储 | 破坏"单文件、零配置、放哪都能跑"的定位；它解决的是"事后分析"，而分析用 JSONL 就够 |
| `export_db()` 等 SQLite 分析层 | 等 trace 积累真实数据后再按需加 |
| 流程序列化 + `run(resume=True)` 断点续跑 | 成本大，且桌面自动化做不到 exactly-once（"点一下"与"记一笔"无法原子完成） |
| `_make_action_str` 改可执行代码生成 | 依赖流程序列化 |
| 单步模式 `input()` 改回调/热键 | 只有无人值守才需要 |
| 子流程 `auto.do().call(flow)` | 等真实需求 |
| 给 221 个旧 def 全量补类型注解 | 收益不抵改动量（只给新增方法加了注解） |
| **`parallel()` 并行** | 桌面自动化只有一个鼠标一个键盘，并行点击物理上不可能，加了只会误导 |
| OCR / AI 文字识别 | 需要 tesseract/paddleocr 等重依赖，破坏"单文件零依赖" |
| 通用 `run_cmd` / 任意 `goto`-`label` 跳转 | 安全边界问题 / 会写出面条代码，用 `while` + `try_do` 即可表达 |
| 录制宏（鼠标轨迹回放） | 坐标回放换分辨率即废，与图像识别路线自相矛盾 |

### 十、验证

```bash
python tests/run_tests.py      # 无外部依赖，直接跑
```

**83 项断言全部通过**，覆盖：

1. **图像匹配引擎**：单目标匹配、多相似图案取最高分、`find_all`、`rect`、灰度模式、
   超时语义、**纯色模板不挂死**、截图缓存。
2. **流程引擎**：条件分支真值表、循环/嵌套循环、配对校验、`retry` 两种模式、
   `click_seq`、`{item}` 单遍替换、`wait(optional=True)`、`click_robust`。
3. **工作目录与多模板来源**：工作目录落点、多目录解析、路径写法、显式指定、缺失报错。
4. **本轮新增能力**：死状态清除、`begin_run` 重置、`cancel`、trace 字段完整性、
   窗口标题匹配、`assert_foreground`、`wait_idle`、`wait_count`、`wait_any`、
   `while_see`/`until`/`loop_n`/`break_if`/`continue_if`、
   `if_count`/`if_python`/`if_color`/`else_if`、步骤级 `retry` 与 `optional`、
   `try_do`/`on_fail`、`expect`/`expect_not`/`assert_count`、整体 `timeout`、
   `last_result`、`call_python`、`set_var`、`find_all` 变量、`dry_run`、
   `click_all`/`click_nth`/`click_offset`/`hover`/`drag_to`、
   `clear_and_write`/`type_slowly`/`read_clipboard`、`remember_pos`/`restore_pos`。

测试全部用假定位器与替换过的鼠标/键盘函数隔离：**不会产生真实点击，也不会截取你的屏幕**。

此外还做过一次「旧版 vs 新版引擎、同一进程、同一输入」的**差分回归测试**：
常规用法 5/5 一致（单目标匹配、`find_all`、灰度模式、`rect`、超时语义）**无回归**，
差异项均为上表所列的预期修复。

---

## [1.2.0] — 2026-05-07（历史版本，仅作存档）

* 区域搜索（`rect`）、彩色优先匹配、极速灰度模式、调试录像与红框标注。

## [1.1.0] — 2026-05-05

* 操作自动截图（调试录像）功能。

## [1.0.0] — 2026-05-02

* 首个版本：单文件图像识别自动化，`auto` API，链式流程雏形。
