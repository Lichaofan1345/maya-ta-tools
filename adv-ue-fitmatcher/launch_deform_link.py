# -*- coding: utf-8 -*-
"""拖入 Maya 2025 视口打开「UE 骨架连接」；不安装、不改启动配置。

同路径源码更新后再次拖入会重新加载模块并关闭旧窗口（旧会话无法提供撤销句柄，
但本次连接的约束都在一个命名 undo 块内，仍可用 Ctrl+Z 撤销）。
"""
import hashlib
import importlib.util
from pathlib import Path
import sys


def launch():
    # 拖入 Maya 时没有 __file__，就按固定位置找主工具（v3 优先）。
    if "__file__" in globals():
        here = Path(__file__).resolve().parent
    else:
        root = Path.home() / "Documents/maya/outputs"
        here = next((d for d in (root / "ADV_UE_FitMatcher_v3", root / "ADV_UE_FitMatcher_v2")
                     if (d / "adv_ue_deform_link.py").is_file()), root / "ADV_UE_FitMatcher_v3")
    path = here / "adv_ue_deform_link.py"
    if not path.is_file():
        raise RuntimeError("请把启动器与 adv_ue_deform_link.py 放在同一目录（应在 ADV_UE_FitMatcher_v3 里）：" + str(here))
    name = "adv_ue_deform_link"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    module = sys.modules.get(name)
    stale = (module is None or Path(module.__file__).resolve() != path.resolve()
             or getattr(module, "_SOURCE_SHA", None) != digest)
    if stale:
        # 源码变化：不复用旧模块；关闭旧窗口，旧 undo 句柄随模块失效（UI 撤销按钮不可用，
        # 但 Maya Ctrl+Z 仍可撤销当时的命名块，不会误撤用户后续操作）。
        if module is not None and getattr(module, "_WINDOW", None) is not None:
            try:
                module._WINDOW.close()
                module._WINDOW.deleteLater()
            except Exception:
                pass
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._SOURCE_SHA = digest
        sys.modules[name] = module
    return module.show()


def onMayaDroppedPythonFile(*_):
    return launch()


if __name__ == "__main__":
    launch()
