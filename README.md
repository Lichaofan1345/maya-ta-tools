# Maya TA 工具集 / Maya TA Toolkit

动画 / 绑定方向 → 技术美术（TA）。这里放 Maya 绑定与 DCC 流程自动化的自写工具，
偏向「把重复的手工步骤变成可预览、可撤销的一键操作」。

## 项目

| 项目 | 一句话 | 位置 |
| --- | --- | --- |
| **ADV × UE 骨架对接工具 v3.1** | ADV 绑定骨架 ↔ Unreal Engine Mannequin 骨架自动补骨、位置匹配、轴向对齐与约束，含只读预检和一键撤销 | [adv-ue-fitmatcher](./adv-ue-fitmatcher/README.md) |
| 我的整合插件 | 早期的 Maya 绑定辅助脚本集合 | [我的整合插件.py](./我的整合插件.py) |

## 技术栈

Maya 2025 / 2026 · Python 3 · PySide6 · `maya.cmds` / `maya.api.OpenMaya` ·
Unreal Engine 5 · Advanced Skeleton 6.915 · FBX 骨骼导出链路

## 关注方向

角色绑定（IK/FK、Twist、脊柱）、DCC 工具开发、绑定资产检查与批处理、
Maya → UE 的骨骼 / 动画导出与重定向流程。

---

代码与文档为个人学习与工作整理，如发现问题欢迎提 Issue。
