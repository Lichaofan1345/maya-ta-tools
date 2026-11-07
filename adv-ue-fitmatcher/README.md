# ADV × UE 骨架对接工具 · v3.1.0

> 把 Advanced Skeleton（ADV）绑定骨架与 Unreal Engine Mannequin 骨架的对接流程，
> 从十几步手工操作压缩成「两个按钮」，并且每一步改动前先给一份只读体检报告。

## 解决什么问题

ADV 生成的变形骨与 UE Mannequin 骨架在**数量、命名、轴向**上都不一致：UE 有 `spine_04`、
`neck_02`、四肢的 `twist_01 / twist_02`，ADV 默认的 FitSkeleton 里没有；直接导 FBX 会出现
缺骨、朝向翻转、动画重定向错位。过去要靠手工插骨、逐根搬位置、逐根建约束，一次几十分钟且容易漏。

这个工具把流程拆成两步，每步都是 **预览 → 执行 → 复核**：

| 阶段 | 做什么 | 对应函数 |
| --- | --- | --- |
| ① Build 前补骨 | 在 FitSkeleton 的 Spine3 与 Chest 之间插 `Spine4`；给 Neck 开 1 根 inbetween；给 Shoulder / Elbow / Hip / Knee 各开 2 根 twist | `preview_fit` / `apply_fit` |
| ② Build 后对接 | 把 UE 原骨搬到 ADV 变形骨的位置 → 按定稿轴向表对齐朝向 → 建立 point / orient / scale 约束（Maintain Offset） | `preview` / `apply` |

位置匹配、朝向对齐、建立约束三个开关互相独立，可只连约束不改位置。

## 技术要点

- **映射表驱动**：`deform_mapping()` 生成 ADV ↔ UE 全表（含 twist 与颈中段）；侧别只替换整段后缀
  token（`_R` / `_r`），不做全文替换，避免误伤骨名。
- **轴向绝对规则**：`ORIENT_OFFSETS` 按身体区域定义旋转偏移，语义是
  `UE 世界旋转 = 偏移矩阵 × ADV 世界旋转`（左乘）。不读取当前状态，因此重复执行结果一致；
  某区域只想搬位置不改朝向时，把对应行改成 `None` 即可。
- **三层安全网**：所有写操作前先由 `_plan` 出体检报告；主骨匹配覆盖率低于阈值（0.4）判定
  「选错根 / 结构不配套」并停止；`apply*` 缺省**拒绝执行**（需显式 `confirm` 回调），
  整套改动包在一个命名 undo 块内，可 Ctrl+Z 一次撤回。
- **矩阵数学自己实现**：3×3 矩阵乘、旋转提取、欧拉偏移在
  `_mm3` / `_rot3` / `_euler_rot` 中，不依赖 pymel 或 numpy。
- **零外部依赖**：只用 `maya.cmds`、`maya.api.OpenMaya` 与 Maya 内置的 PySide6，
  无第三方包、无网络请求、不写用户启动配置、不安装插件。

## 目录

```
adv-ue-fitmatcher/
├── adv_ue_deform_link.py    主实现（映射 / 规划 / 执行 / UI，约 1500 行）
├── launch_deform_link.py    拖入 Maya 视口的入口（带模块热重载）
├── 先看这里.txt             快速使用说明与轴向定稿表
├── 代码思路.md              10 节设计文档：手工步骤 → 代码 → 坑 → 函数速查
├── 思维导图.html            离线单文件思维导图，浏览器直接打开，无 CDN
├── MANIFEST.json            文件清单与 sha256 校验
└── tests/
    ├── test_mapping.py      纯 Python 映射契约测试（无 Maya 依赖）
    └── test_synthetic.py    mayapy 合成场景隔离验证（不加载用户场景）
```

## 使用

1. 把 `launch_deform_link.py` **拖进 Maya 视口**（不安装、不改启动配置）。
2. 点「补骨」→ 去 ADV 里正常 Build。
3. 点「执行」→ 勾选位置匹配 / 朝向对齐 / 建立约束，先看预览报告再确认。

详细参数与轴向表见 [先看这里.txt](./先看这里.txt)。

## 测试

```bash
# 纯 Python 契约测试（不需要 Maya）
python -m unittest discover -s tests -p "test_mapping.py"

# 合成场景验证（需要 mayapy，须隔离 MAYA_APP_DIR 与用户启动脚本）
mayapy tests/test_synthetic.py
```

`test_synthetic.py` 不加载 ADV、不打开用户场景、不执行任何场景脚本。

## 环境与版本

| 项 | 版本 |
| --- | --- |
| Maya | 2025（开发环境），Python 3 / PySide6 |
| Advanced Skeleton | 6.915 |
| 目标骨架 | Unreal Engine 5 Mannequin 命名 |
| 工具版本 | 3.1.0（2026-10-19） |

Maya 2026 及其他 ADV 版本尚未完整验证，使用前请在副本场景里跑一次预览。

## 已知限制

- 只处理映射表覆盖的骨；`tests` 之外的自定义骨需自行扩展 `deform_mapping()`。
- ADV 不允许 `twistJoints` 与 `inbetweenJoints` 加在同一根骨上，遇到冲突会明确报错停下。
- 朝向表来自真实场景只读实测，换一套骨架（非 UE Mannequin）需重新核对 `ORIENT_OFFSETS`。
