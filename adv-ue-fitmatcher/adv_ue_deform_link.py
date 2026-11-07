# -*- coding: utf-8 -*-
"""ADV × UE 骨架对接（两步）— Maya 2025 / ADV 6.915 配套小工具。

① Build 前补骨（preview_fit/apply_fit）：在 FitSkeleton 上补 UE 有而 ADV 缺的骨
   ——Spine4（插在 Spine3 与 Chest 之间）、Neck 中段（inbetweenJoints=1，
   Build 后生成 NeckPart1 → neck_02）、四肢扭转骨（twistJoints=2）。
   只改成 Fit，不动 UE，不 Build。
② Build 后匹配 + 约束（preview/apply）：先把 UE 原骨的位置搬到 ADV 变形骨上、
   朝向按 v2.2.7 定稿规则对齐（UE = 偏移 × ADV，见 ORIENT_OFFSETS），
   再逐对建立 point/orient/scale 约束（Maintain Offset，含 twist 与颈中段）。
   位置匹配与朝向对齐都可单独关掉，只连约束也照样对。

为什么不直接按 ADV 的 Constraint to Joints（asMappingUIFunction 21382–21412）：
1) 它只连接当前 nameMatcher 表里有行的骨；Unreal5.txt 不含 twist/Part 行，
   upperarm/lowerarm/thigh/calf twist 与 neck_02 不会被连接；
2) 它会解锁目标属性并删除目标上已有的同名约束（21393–21401），属于覆盖式
   行为。本工具改为「冲突即停止，绝不覆盖」，twist 反序规则内置：
   ElbowPart1/2→lowerarm_twist_02/01、KneePart1/2→calf_twist_02/01（6.915
   asExportRenameToUnreal 24800–24821 核实），肩/髋为正序。

容错：UE 侧没有的骨、ADV 侧没有驱动骨的骨都只跳过并在报告里给出原因与可操作
建议，不再整体阻断；只有当能匹配到的主骨少于 MAIN_COVERAGE_MIN（40%）时才判定
为「选错根 / 结构不配套」。单侧骨架、缺几根骨都是正常场景，能连多少连多少。

公开入口：preview/apply（第二步）、preview_fit/apply_fit（第一步）、
show() / auto_detect() / auto_detect_fit() / resolve_selection(node)。
导入模块本身不触碰场景。不调用 ADV 任何函数，不 Build/ReBuild。
"""
from __future__ import annotations
import json
import math
import uuid

VERSION = "3.1.0"
TOL = 1e-4
# 安全阀：能匹配到的主骨比例低于此值，才认为是「选错根 / 结构不配套」并明确提示。
# 注意：这里的「主骨」指映射表里非 twist / 非 neck_02 的行（62 根）。
MAIN_COVERAGE_MIN = 0.4

# ADV 骨名 -> UE 骨名。侧别只换整段后缀 token（_R/_r），不做全文替换。
LIMBS = (("Scapula", "clavicle"), ("Shoulder", "upperarm"), ("Elbow", "lowerarm"),
         ("Wrist", "hand"), ("Hip", "thigh"), ("Knee", "calf"),
         ("Ankle", "foot"), ("Toes", "ball"))
FINGERS = (("Index", "index"), ("Middle", "middle"), ("Ring", "ring"),
           ("Pinky", "pinky"), ("Thumb", "thumb"))
# (ADV前缀, UE段名, 是否反序)。反序依据：Mannequin 前臂/小腿 twist 编号反向。
TWISTS = (("Shoulder", "upperarm", False), ("Elbow", "lowerarm", True),
          ("Hip", "thigh", False), ("Knee", "calf", True))
# UE 参考骨跟随（驱动者与目标都在 UE 树内，保偏移）。存在才连接，缺失只提示。
IK_FOLLOW = (("foot_l", "ik_foot_l"), ("foot_r", "ik_foot_r"),
             ("hand_r", "ik_hand_gun"), ("hand_l", "ik_hand_l"),
             ("hand_r", "ik_hand_r"))
# 纯参考骨：保留原层级继承，不连接，报告中标明。
REFERENCES = ("ik_foot_root", "ik_hand_root", "interaction", "center_of_mass")

# ---------------------------------------------------------------
# Build 后「UE 骨匹配 ADV」的朝向规则（沿用 v2.2.7 最终定稿，可直接改这里）。
# 语义（左乘）：UE 世界旋转 = R_offset × R_ADV变形骨；位置直接取 ADV 变形骨位置。
#   spine : (0,0,0)    脊柱/颈/头与 ADV 同向（头骨朝上，v2.2.7 修正）
#   arm_r : (0,180,0)  rotY180：X 反 / Y 不变 / Z 反（用户逐轴指认，v2.2.6）
#   hand_r: (90,0,180) X 反 / Y<->Z 互换（手与手指同约定）
#   leg_r : (0,0,0)    右腿与 ADV 同向
# 扭转骨沿用所在肢体的规则。
# 左侧依据（2026-10-19 在用户真实场景只读实测，M = R_ue × R_adv^T）：左侧未被动过的
# upperarm/lowerarm 是 rotY180、thigh/calf/foot/ball 是单位阵、index_01 是 X 反+Y<->Z，
# 与右侧逐项一致，所以左侧直接沿用右侧的度数。被手动拧过的骨（如 hand_l、thumb_02_l、
# upperarm_twist_01_l）会被这个规则纠正回来。想让某区域"只搬位置、朝向保持原样"，把它改成 None。
ORIENT_OFFSETS = {
    "spine": (0.0, 0.0, 0.0),
    "arm_r": (0.0, 180.0, 0.0), "hand_r": (90.0, 0.0, 180.0), "leg_r": (0.0, 0.0, 0.0),
    "arm_l": (0.0, 180.0, 0.0), "hand_l": (90.0, 0.0, 180.0), "leg_l": (0.0, 0.0, 0.0),
}
# Build 前补骨：这几个 Fit 骨需要的扭转/细分属性（ADV 6.915 的 Elbow 同款）。
FIT_TWIST_BONES = ("Shoulder", "Elbow", "Hip", "Knee")
FIT_NECK_BONE = "Neck"
FIT_NECK_INBETWEEN = 1  # UE 只有 neck_02 一根中间骨，多开一根没有 UE 骨可对应
FIT_TWIST_COUNT = 2


class LinkError(RuntimeError):
    pass


def _maya():
    import maya.cmds as c
    return c


def deform_mapping():
    """79 对 ADV DeformationSystem 变形骨 -> UE 骨（62 主骨+16 twist+neck_02）。"""
    rows = []
    def add(a, u, group):
        rows.append({"adv": a, "ue": u, "group": group})
    add("Root_M", "pelvis", "root")
    for i in range(1, 6):
        add("Spine%d_M" % i, "spine_%02d" % i, "spine")
    add("Neck_M", "neck_01", "spine")
    add("Head_M", "head", "spine")
    for side in ("R", "L"):
        s = side.lower()
        for a, u in LIMBS:
            add(a + "_" + side, u + "_" + s, "limb")
        for a, u in FINGERS:
            for i in (range(1, 4) if a == "Thumb" else range(4)):
                ue = u + ("_metacarpal" if i == 0 else "_%02d" % i) + "_" + s
                add(a + "Finger" + str(i) + "_" + side, ue, "finger")
        for a, u, reverse in TWISTS:
            for i in (1, 2):
                ue_i = 3 - i if reverse else i
                add(a + "Part" + str(i) + "_" + side,
                    u + "_twist_%02d_" % ue_i + s, "twist")
    add("NeckPart1_M", "neck_02", "neck")
    return rows


def _base(node):
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def _parent(node):
    return (_maya().listRelatives(node, parent=True, fullPath=True) or [None])[0]


def _exact(node, what):
    c = _maya()
    if not isinstance(node, str) or not node.startswith("|"):
        raise LinkError("请用“载入选中”提供 %s 的完整路径" % what)
    if (c.ls(node, long=True) or []) != [node]:
        raise LinkError("%s 路径不存在或不唯一：%s" % (what, node))
    return node


def _tree(root):
    """root 及其所有变换类子孙（transform/joint）。

    必须过滤：ADV 的 Group 下还有 constraint / ikHandle / nurbsCurve 形状等
    非变换节点，对它们调用 xform 会直接报 "No valid objects supplied"。
    """
    c = _maya()
    nodes = [root] + (c.listRelatives(root, ad=True, fullPath=True) or [])
    return [n for n in nodes if c.nodeType(n) in ("transform", "joint")]


def _index(nodes):
    out = {}
    for n in nodes:
        key = _base(n)
        if key in out:
            raise LinkError("子树内短名重复，无法安全定位：" + key)
        out[key] = n
    return out


def _world(node):
    return _maya().xform(node, q=True, ws=True, matrix=True)


def _near(a, b, tol=TOL):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _link_names(ue_name):
    return ("UELink_%s_pt" % ue_name, "UELink_%s_or" % ue_name, "UELink_%s_sc" % ue_name)


def _constraint_driver(node):
    c = _maya()
    fn = {"pointConstraint": c.pointConstraint,
          "orientConstraint": c.orientConstraint,
          "scaleConstraint": c.scaleConstraint}.get(c.nodeType(node))
    if fn is None:
        return None
    drivers = fn(node, q=True, targetList=True) or []
    return _base(drivers[0]) if len(drivers) == 1 else None


def _link_state(target, ue_name, adv_name):
    """返回 'ok'（本工具已正确连接）/ 'none' / 'foreign' / 'partial'。"""
    c = _maya()
    states = []
    for name in _link_names(ue_name):
        hits = c.ls(name, long=True) or []
        if len(hits) > 1:
            return "foreign"
        if not hits:
            states.append("none")
            continue
        node = hits[0]
        if _constraint_driver(node) != adv_name:
            return "foreign"
        states.append("ok")
    if states == ["ok"] * 3:
        return "ok"
    if states == ["none"] * 3:
        return "none"
    return "partial"


def _find_adv(adv_root):
    c = _maya()
    root = _exact(adv_root, "ADV 系统")
    if _base(root) == "DeformationSystem":
        root = _parent(root) or root
    nodes = _tree(root)
    index = _index(nodes)
    if "DeformationSystem" not in index or "Main" not in index:
        raise LinkError("未找到 DeformationSystem/Main；请先完成 ADV Build")
    deform = _index([n for n in _tree(index["DeformationSystem"]) if c.nodeType(n) == "joint"])
    return {"group": root, "deform": deform, "main": index["Main"], "nodes": nodes}


def _find_ue(ue_root):
    c = _maya()
    root = _exact(ue_root, "UE 骨架根")
    if c.nodeType(root) != "joint" or _base(root) != "root":
        raise LinkError("请载入原 UE 的 root 关节")
    nodes = _tree(root)
    return {"root": root, "index": _index(nodes), "nodes": nodes}


def _check_ue_clean(ue):
    """首次连接前提：未蒙皮、无引用/实例、目标通道无外来驱动。只读。"""
    c = _maya()
    for node in ue["nodes"]:
        if c.referenceQuery(node, isNodeReferenced=True):
            raise LinkError("UE 骨为引用节点，不支持：" + node)
        if len(c.ls(node, allPaths=True) or []) != 1:
            raise LinkError("UE 骨被实例化，不支持：" + node)
        if c.nodeType(node) == "joint" and c.listConnections(node, type="skinCluster"):
            raise LinkError("UE 已蒙皮，本工具只用于蒙皮前：" + node)


def _adv_hint(row, chest_variant):
    """ADV 侧缺驱动骨时的可操作建议。

    长期约定：绝不引导用户去改 ADV 脊柱节数（改成 5 节或 1 节都会破坏映射），
    脊柱只提示 Body → Fit → ReSample 加一节，或直接接受跳过。
    """
    group, adv = row["group"], row["adv"]
    if group == "twist":
        return "ADV 缺 %s：Build 时开启 Twist Joints（扭转骨）后重新 Build 会自动补上" % adv
    if group == "spine" and adv in ("Spine4_M", "Spine5_M"):
        if chest_variant:
            return ("ADV 脊柱为 4 节，没有 %s：可用 ADV 的 Body → Fit → ReSample 加一节后再连，"
                    "也可以接受跳过（不要改脊柱节数设置，会破坏映射）" % adv)
        return ("ADV 缺 %s：可用 ADV 的 Body → Fit → ReSample 补一节后再连，也可以接受跳过" % adv)
    if group == "neck":
        return "ADV 缺 %s：Build 时开启颈部细分后重新 Build 会自动补上" % adv
    return "ADV 没有对应的变形骨 %s，可跳过" % adv


# ---- 朝向数学：纯 3x3，row-vector（与 Maya 的 world = local * parent_world 一致）----

def _mm3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _rot3(matrix):
    return [list(matrix[i:i + 3]) for i in (0, 4, 8)]


def _euler_rot(degrees):
    """XYZ 欧拉（度）-> 3x3，后乘顺序 Rx·Ry·Rz。"""
    rx, ry, rz = (math.radians(v) for v in degrees)
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry),
                              math.sin(ry), math.cos(rz), math.sin(rz))
    return _mm3(_mm3([[1., 0., 0.], [0., cx, sx], [0., -sx, cx]],
                     [[cy, 0., -sy], [0., 1., 0.], [sy, 0., cy]]),
                [[cz, sz, 0.], [-sz, cz, 0.], [0., 0., 1.]])


def _is_rigid(rot, tol=1e-3):
    for i in range(3):
        for j in range(3):
            dot = sum(rot[i][k] * rot[j][k] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > tol:
                return False
    return True


def _orient_region(ue):
    """UE 骨名 -> ORIENT_OFFSETS 的键；不在规则内返回 None。"""
    if ue == "pelvis" or ue == "head" or ue.startswith(("spine_", "neck_")):
        return "spine"
    if not ue.endswith(("_r", "_l")):
        return None
    side, seg = ue[-1], ue[:-2]
    if seg.startswith(("clavicle", "upperarm", "lowerarm")):
        return "arm_" + side
    if seg == "hand" or seg.startswith(("index", "middle", "ring", "pinky", "thumb")):
        return "hand_" + side
    if seg.startswith(("thigh", "calf", "foot", "ball")):
        return "leg_" + side
    return None


def _match_targets(plan, orient):
    """每根待匹配 UE 骨的目标世界矩阵：位置=ADV 变形骨，朝向=偏移×ADV（或保持原样）。只读。"""
    out, hold = {}, []
    for row in plan["pairs"]:
        if row["kind"] != "deform" or row["state"] != "none":
            continue
        adv_w, ue_w = _world(row["driver"]), _world(row["target"])
        rot = _rot3(ue_w)
        if orient:
            region = _orient_region(row["ue"])
            offset = ORIENT_OFFSETS.get(region) if region else None
            if offset is not None:
                rot_adv = _rot3(adv_w)
                if not _is_rigid(rot_adv):
                    raise LinkError("ADV 变形骨 %s 带缩放/错切，无法对齐朝向（检查 Main 缩放是否为 1）"
                                    % _base(row["driver"]))
                rot = _mm3(_euler_rot(offset), rot_adv)
            else:
                hold.append(row["ue"])
        out[row["target"]] = (rot[0] + [0.] + rot[1] + [0.] + rot[2] + [0.]
                              + [adv_w[12], adv_w[13], adv_w[14], 1.])
    return out, hold


def _write_world(node, desired):
    """把 joint 的世界矩阵写成 desired：rotate 归零，朝向进 jointOrient，写后自检。"""
    c = _maya()
    import maya.api.OpenMaya as om
    parent = _parent(node)
    parent_world = om.MMatrix(_world(parent)) if parent else om.MMatrix()
    local = om.MMatrix(desired) * parent_world.inverse()
    euler = om.MTransformationMatrix(local).rotation(asQuaternion=True).asEulerRotation()
    euler.reorderIt(om.MEulerRotation.kXYZ)  # jointOrient 永远按 XYZ 解释
    c.setAttr(node + ".rotate", 0., 0., 0., type="double3")
    c.setAttr(node + ".jointOrient", *[math.degrees(a) for a in (euler.x, euler.y, euler.z)],
              type="double3")
    c.setAttr(node + ".translate", *list(local)[12:15], type="double3")
    if not _near(_world(node), desired):
        raise LinkError("世界矩阵写后校验失败（骨是否带缩放/RotateAxis？）：" + node)


def _depth(node):
    return node.count("|")


def _stray_bones(ue_index, pairs):
    """找出「映射表之外」的 UE 骨，专门抓撞名残留（clavicle_l1、upperarm_r2 这类）。

    背景：复制/镜像/重复导入会让骨名自动加数字后缀。原名那根可能变成没挂任何东西的
    空骨，真正带着整条肢体的是带后缀的那根——工具按名字只认原名，后缀骨既不会被
    匹配位置也不会被约束，于是位置一直是错的。
    返回 [(杂散骨短名, 疑似对应的原名, 子级数)]。
    """
    # 判定基准是「完整映射表」，不是「这次配成对的」：ADV 缺驱动骨（如没开 Twist 的
    # calf_twist、脊柱少一节的 spine_04）是合法的，已由 unavailable 单独报告，不算杂散。
    known = {r["ue"] for r in deform_mapping()} | set(REFERENCES) | {"root"}
    known |= {r["ue"] for r in pairs}
    for driver, target in IK_FOLLOW:
        known.add(target)
    c = _maya()
    out = []
    for short, node in ue_index.items():
        if short in known or c.nodeType(node) != "joint":
            continue
        base = short.rstrip("0123456789")
        twin = base if base != short and base in ue_index else None
        kids = len(c.listRelatives(node, children=True, type="joint") or [])
        out.append((short, twin, kids))
    return out


def _plan(adv_root, ue_root):
    c = _maya()
    adv = _find_adv(adv_root)
    ue = _find_ue(ue_root)
    if ue["root"].startswith(adv["group"] + "|") or adv["group"].startswith(ue["root"] + "|"):
        raise LinkError("ADV 与 UE 必须是互不包含的两棵树")
    _check_ue_clean(ue)
    deform, ue_index = adv["deform"], ue["index"]
    rows = deform_mapping()
    # 脊柱第五节：Spine5_M 或旧命名 Chest_M 唯一存在（4 节变体即 Chest 变体）。
    chest_variant = "Spine5_M" not in deform and "Chest_M" in deform
    if chest_variant:
        rows = [dict(r, adv="Chest_M") if r["adv"] == "Spine5_M" else r for r in rows]
    skipped, unavailable, pairs = [], [], []
    for row in rows:
        has_adv = row["adv"] in deform
        has_ue = row["ue"] in ue_index
        if has_adv and has_ue:
            pairs.append(dict(row, kind="deform"))
        elif has_ue:
            # UE 有这根骨、ADV 没有驱动骨（典型：Build 时没开 twistJoints、
            # 脊柱比 UE 少一节）。只跳过这一对并给建议，绝不整体阻断。
            unavailable.append({"ue": row["ue"], "adv": row["adv"], "group": row["group"],
                                "reason": _adv_hint(row, chest_variant)})
        else:
            # UE 骨架里根本没有这根骨（单侧/简化骨架常见），直接跳过。
            skipped.append({"ue": row["ue"], "adv": row["adv"], "group": row["group"],
                            "reason": "UE 骨架里没有这根骨"})
    # 安全阀：匹配到的主骨太少，只可能是选错根或两棵树不配套，才明确阻断。
    main_rows = [r for r in rows if r["group"] not in ("twist", "neck")]
    linked_main = sum(1 for r in pairs if r["group"] not in ("twist", "neck"))
    if main_rows and linked_main < MAIN_COVERAGE_MIN * len(main_rows):
        raise LinkError(
            "只匹配到 %d/%d 根主骨（低于 %d%%），基本可以确定不是一对配套骨架：\n"
            "上框应填 ADV 系统组（里面要有 DeformationSystem），下框应填 UE 的 root 关节。\n"
            "请重新「载入选中」后再检查。"
            % (linked_main, len(main_rows), int(MAIN_COVERAGE_MIN * 100)))
    pairs.insert(0, {"adv": "Main", "ue": "root", "group": "root", "kind": "root"})
    for driver, target in IK_FOLLOW:
        if driver in ue_index and target in ue_index:
            pairs.append({"adv": driver, "ue": target, "group": "ik", "kind": "ik"})
    # 逐对判定：已正确连接 / 新建 / 外来冲突 / 残缺。
    locked = []
    for row in pairs:
        target = ue_index[row["ue"]]
        row["target"] = target
        row["driver"] = adv["main"] if row["adv"] == "Main" else (
            deform[row["adv"]] if row["kind"] == "deform" else ue_index[row["adv"]])
        row["state"] = _link_state(target, row["ue"], _base(row["driver"]))
        if row["state"] in ("foreign", "partial"):
            raise LinkError("目标已有外来/残缺约束，不覆盖：%s（%s）" % (target, row["state"]))
        if row["state"] == "none":
            for attr in ("translate", "rotate", "scale"):
                if c.getAttr(target + "." + attr, lock=True):
                    locked.append(target + "." + attr)
            # 逐通道检查：Maya 把约束连到 tx/ty/tz 子属性，查父级 compound 拿不到。
            for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
                for plug in c.listConnections(target + "." + attr, s=True, d=False, plugs=True) or []:
                    raise LinkError("目标已有动画/驱动输入，不覆盖：%s.%s <- %s" % (target, attr, plug))
    if locked:
        raise LinkError("以下通道被锁定，请先手动解锁后重试：\n" + "\n".join(locked[:20]))
    stray = _stray_bones(ue_index, pairs)
    if stray:
        lines = []
        for short, twin, kids in stray[:8]:
            if twin:
                lines.append("%s（疑似 %s 的撞名残留，下面挂了 %d 根骨；"
                             "%s 本身大概是空骨）" % (short, twin, kids, twin))
            else:
                lines.append("%s（不在映射表内，下面挂了 %d 根骨）" % (short, kids))
        raise LinkError(
            "UE 骨架里有映射表之外的骨，匹配后它们的位置会是错的，请先处理：\n"
            + "\n".join(lines)
            + "\n多半是复制/镜像/重复导入留下的带数字后缀的骨。把它的子骨挂回原名骨、"
              "再删掉多余的那根即可（保持世界位置的 Parent，不要加 -relative）。")
    return {"adv": adv, "ue": ue, "pairs": pairs, "skipped": skipped,
            "unavailable": unavailable,
            "references": [n for n in REFERENCES if n in ue_index]}


def _probe_adv(node):
    """复用 _find_adv 的判定（必须有 DeformationSystem + Main）判断 node 是不是 ADV 系统。"""
    try:
        return _find_adv(node)["group"]
    except Exception:
        return None


def _probe_ue(node):
    """复用 _find_ue 的判定（joint + 短名 root）判断 node 是不是 UE 骨架根。"""
    try:
        return _find_ue(node)["root"]
    except Exception:
        return None


def _dedup(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def _up_chain(node):
    """选中节点 -> 一路向上到顶层。"""
    chain, cur = [], node
    while cur:
        chain.append(cur)
        cur = _parent(cur)
    return chain


def _down_hints(node):
    """向下只在「有希望」的子孙上试，避免对整棵子树逐个试错。"""
    c = _maya()
    adv_hits, ue_hits = [], []
    for n in c.listRelatives(node, ad=True, fullPath=True) or []:
        base = _base(n)
        if base == "DeformationSystem":
            up = _parent(n)
            if up:
                adv_hits.append(_probe_adv(up))
        elif base == "root" and c.nodeType(n) == "joint":
            ue_hits.append(_probe_ue(n))
    return _dedup(adv_hits), _dedup(ue_hits)


def resolve_selection(node):
    """从任意节点反推 ADV 组 / UE 骨架根：先看自己和父链，再看子孙。只读。"""
    adv_hits, ue_hits = [], []
    for n in _up_chain(node):
        adv_hits.append(_probe_adv(n))
        ue_hits.append(_probe_ue(n))
    d_adv, d_ue = _down_hints(node)
    return _dedup(adv_hits + d_adv), _dedup(ue_hits + d_ue)


def auto_detect():
    """扫描场景顶层，返回 (ADV 候选列表, UE 候选列表)。只读，不弹窗。"""
    c = _maya()
    adv_hits, ue_hits = [], []
    for n in c.ls(assemblies=True, long=True) or []:
        adv_hits.append(_probe_adv(n))
        ue_hits.append(_probe_ue(n))
    return _dedup(adv_hits), _dedup(ue_hits)


def _guard():
    c = _maya()
    return {"selection": c.ls(sl=True, long=True) or [],
            "time": c.currentTime(q=True),
            "scene": c.file(q=True, sn=True)}


def _result(status):
    return {"status": status, "issues": [], "warnings": [], "pairs": [],
            "counts": {}, "undo_chunk": None,
            "details": {"skipped": [], "unavailable": []}}


def _ssc_on(ue_root):
    """只读：UE 树里 segmentScaleCompensate 仍为开的关节（长路径）。"""
    c = _maya()
    return [n for n in _tree(ue_root)
            if c.nodeType(n) == "joint" and c.getAttr(n + ".segmentScaleCompensate")]


def preview(adv_root, ue_root, match=False, orient=False, link=True):
    """纯只读：不写场景、不弹窗、不写磁盘。

    match=True ：UE 骨位置匹配到 ADV 变形骨；orient=True 再按定稿轴向规则对齐朝向。
    link=True  ：建立 point/orient/scale 约束（Maintain Offset）。
    """
    result = _result("blocked")
    before = _guard()
    try:
        plan = _plan(adv_root, ue_root)
        todo = [r for r in plan["pairs"] if r["state"] == "none"]
        done = [r for r in plan["pairs"] if r["state"] == "ok"]
        result["pairs"] = [{"adv": r["adv"], "ue": r["ue"], "group": r["group"],
                            "state": r["state"]} for r in plan["pairs"]]
        result["counts"] = {"connect": len(todo) if link else 0, "already": len(done),
                            "total": len(plan["pairs"]),
                            "skipped": len(plan["skipped"]),
                            "unavailable": len(plan["unavailable"]),
                            "move": 0, "turn": 0}
        move_todo = False
        hold = []
        if match:
            targets, hold = _match_targets(plan, orient)
            moves = turns = 0
            for node, desired in targets.items():
                cur = _world(node)
                if not _near(cur[12:15], desired[12:15], 1e-4):
                    moves += 1
                if not _near(cur[:12], desired[:12], 1e-4):
                    turns += 1
            result["counts"].update(move=moves, turn=turns)
            move_todo = bool(moves or turns)
            result["details_match"] = sorted(_base(n) for n in targets)
        result["status"] = "ready" if ((link and todo) or move_todo) else "unchanged"
        result["details"] = {"skipped": plan["skipped"],
                             "unavailable": plan["unavailable"]}
        spine_gap = [i["ue"] for i in plan["unavailable"] if i["group"] == "spine"]
        if spine_gap:
            result["warnings"].append(
                "ADV 脊柱比 UE 少：%s 没有对应的 ADV 骨；Build 前用「补 Spine4」补上，"
                "也可以接受跳过（不要改脊柱节数设置）" % "、".join(spine_gap))
        twist_gap = [i["adv"] for i in plan["unavailable"] if i["group"] == "twist"]
        if twist_gap:
            result["warnings"].append(
                "ADV 缺 %d 根扭转骨：Build 前用「补颈中段/扭转」开启后重新 Build 会自动补上（%s…）"
                % (len(twist_gap), "、".join(twist_gap[:3])))
        if hold:
            result["warnings"].append(
                "朝向未设规则（只匹配位置、朝向保持原样）：%d 根，例如 %s。"
                "左侧未实测，确认后把度数填进 ORIENT_OFFSETS" % (len(hold), "、".join(hold[:3])))
        for name in plan["references"]:
            result["warnings"].append("参考骨保留原层级继承，不连接：" + name)
        if link:
            result["warnings"].append("约束为 Maintain Offset，匹配后偏移≈0，之后 UE 骨由 ADV 驱动")
        ssc = _ssc_on(ue_root)
        result["ssc_on"] = ssc
        if ssc:
            result["warnings"].append(
                "UE 骨里有 %d 根开着 Segment Scale Compensate（例如 %s）。绑定阶段可以先不管；"
                "烘焙动画、导出 FBX 进引擎前要关掉，引擎不认这个属性，带缩放的动画会对不上。"
                % (len(ssc), "、".join(_base(n) for n in ssc[:3])))
    except Exception as exc:
        result["issues"].append(str(exc))
    if _guard() != before:
        result["status"] = "blocked"
        result["issues"].append("检查期间场景状态发生变化，请重试")
    return result


def _create(row):
    c = _maya()
    driver, target = row["driver"], row["target"]
    names = _link_names(row["ue"])
    c.pointConstraint(driver, target, maintainOffset=True, name=":" + names[0])
    c.orientConstraint(driver, target, maintainOffset=True, name=":" + names[1])
    c.scaleConstraint(driver, target, maintainOffset=True, name=":" + names[2])


def _do_match(plan, targets):
    """根到叶写入目标世界矩阵；被带跑的未映射 UE 骨恢复原世界。返回写入根数。"""
    ue_nodes = sorted(plan["ue"]["nodes"], key=_depth)
    before = {n: _world(n) for n in ue_nodes}
    writes = 0
    for node in ue_nodes:
        if node in targets:
            if not _near(_world(node), targets[node]):
                _write_world(node, targets[node])
                writes += 1
        elif _maya().nodeType(node) == "joint" and not _near(_world(node), before[node]):
            _write_world(node, before[node])  # 未映射骨（参考骨等）保持原世界
    for node, desired in targets.items():
        if not _near(_world(node), desired):
            raise LinkError("匹配后位置/朝向复查失败：" + node)
    for node in ue_nodes:
        if node not in targets and not _near(_world(node), before[node]):
            raise LinkError("未映射骨被意外带动：" + node)
    return writes


def apply(adv_root, ue_root, confirm=None, match=False, orient=False, link=True):
    """匹配位置/朝向 + 建约束，整套一个撤销块。confirm(context)->bool：缺省=拒绝，绝不静默执行。"""
    c = _maya()
    if not (match or link):
        return dict(_result("blocked"), issues=["至少选择一项：匹配或建立约束"])
    result = preview(adv_root, ue_root, match=match, orient=orient, link=link)
    result.update({"undo_chunk": None})
    if result["status"] != "ready":
        return result
    if not c.undoInfo(q=True, state=True):
        result.update(status="blocked", issues=["请先启用 Maya Undo"])
        return result
    context = {"count": result["counts"]["connect"],
               "move": result["counts"].get("move", 0),
               "turn": result["counts"].get("turn", 0),
               "skipped": result["counts"].get("skipped", 0),
               "unavailable": result["counts"].get("unavailable", 0)}
    if confirm is None or confirm(context) is not True:
        result.update(status="blocked", issues=["已取消，未做任何修改"])
        return result
    plan = _plan(adv_root, ue_root)
    _guard_before = _guard()
    if any(r["state"] != "none" for r in plan["pairs"] if r["ue"] in
           {p["ue"] for p in result["pairs"] if p["state"] == "none"}):
        result.update(status="blocked", issues=["确认期间连接状态变化，请重新检查"])
        return result
    todo = [r for r in plan["pairs"] if r["state"] == "none"] if link else []
    targets, _hold = _match_targets(plan, orient) if match else ({}, [])
    if match:
        locked = []
        for node in targets:
            for attr in ("translate", "rotate", "jointOrient"):
                if c.getAttr(node + "." + attr, lock=True):
                    locked.append(node + "." + attr)
        if locked:
            result.update(status="blocked", issues=[
                "以下通道被锁定，请先手动解锁后重试：\n" + "\n".join(locked[:20])])
            return result
    ue_original = {n: _world(n) for n in plan["ue"]["nodes"]}
    adv_before = {n: _world(n) for n in plan["adv"]["nodes"]}
    chunk = "UE_DeformLink_" + uuid.uuid4().hex
    result["undo_chunk"] = chunk
    opened = closed = wrote = False
    try:
        c.undoInfo(openChunk=True, chunkName=chunk)
        opened = True
        if match:
            wrote = True
            _do_match(plan, targets)
        ue_before = {n: _world(n) for n in plan["ue"]["nodes"]}
        for row in todo:
            _create(row)
            wrote = True
        # 复查：约束后目标世界矩阵不动（Maintain Offset 语义），ADV 未变。
        for node, before in ue_before.items():
            if not _near(_world(node), before):
                raise LinkError("约束后发生跳位：" + node)
        for node, before in adv_before.items():
            if not _near(_world(node), before):
                raise LinkError("ADV 侧被意外改动：" + node)
        for row in todo:
            if _link_state(row["target"], row["ue"], _base(row["driver"])) != "ok":
                raise LinkError("约束复查失败：" + row["ue"])
        state = _guard()
        if state["selection"] != _guard_before["selection"] or state["scene"] != _guard_before["scene"]:
            raise LinkError("执行期间选区/场景路径意外改变，可能有外部回调")
    except Exception as exc:
        result.update(status="blocked")
        result["issues"].append(str(exc))
    finally:
        if opened:
            try:
                c.undoInfo(closeChunk=True)
                closed = True
            except Exception as exc:
                result["issues"].append("关闭撤销块失败：" + str(exc))
    if result["status"] == "blocked" and closed and wrote:
        if c.undoInfo(q=True, undoName=True) == chunk:
            c.undo()
            ok = all(_near(_world(n), w) for n, w in ue_original.items())
            result["status"] = "rolled_back" if ok else "blocked"
            if not ok:
                result["issues"].append("回滚校验失败，请用保存副本恢复")
        else:
            result["issues"].append("栈顶不是本次操作，未撤销其他工作")
    else:
        result["status"] = "applied"
        result["warnings"].append("完成。Ctrl+Z 可整体撤销（匹配+约束一并撤销）")
    return result


# ---------------------------------------------------------------
# Build 前：给 Fit 补骨（Spine4 / 颈部中段 / 四肢扭转骨）
# 只动 FitSkeleton，不读也不改 UE 骨。全部动作一个撤销块，可 Ctrl+Z 整体撤销。

# --- Spine4 插入：自 v2.2.7 已验证实现并入（v3 自包含，不再依赖 v2 文件夹）---
# 核心难点：Chest 被重挂到新 Spine4 下后，所有缓存的长路径都会失效，所以这里
# 一律用 UUID 追踪节点，用到时再用 _path(uuid) 现取当前路径；
# 还要把 Chest 原有的 inverseScale 补偿线迁到新父级，且遇到外来接线就停止，不乱拆。

def _long(node):
    c = _maya()
    hits = c.ls(node, long=True) or []
    if len(hits) != 1:
        raise LinkError("节点必须唯一，不能取第一个结果：%s (%d 个)" % (node, len(hits)))
    return hits[0]


def _namespace(node):
    return node.rsplit("|", 1)[-1].rpartition(":")[0]


def _uid(node):
    c = _maya()
    ids = c.ls(node, uuid=True) or []
    if len(ids) != 1:
        raise LinkError("无法取得唯一 UUID：" + node)
    return ids[0]


def _path(uid):
    # Maya ls 支持 UUID 查询。重挂 Chest 后绝不能继续使用缓存的旧长路径。
    return _long(uid)


def _plug(plug):
    node, attr = plug.split(".", 1)
    return _long(node) + "." + attr


def _selection_identity(selection=None):
    """有序选区身份：UUID + 组件后缀。重挂前后用它确认选区没被悄悄改变。"""
    c = _maya()
    import maya.api.OpenMaya as om
    if selection is None:
        selection = c.ls(selection=True, long=True) or []
    result = []
    for item in selection:
        node, separator, suffix = item.partition(".")
        sel = om.MSelectionList()
        sel.add(node)
        if sel.getDependNode(0).hasFn(om.MFn.kDagNode) and sel.getDagPath(0).isInstanced():
            raise LinkError("不支持实例选区的自动恢复，请改为非实例选区：" + item)
        node_id = _uid(node)
        if _path(node_id) != _long(node):
            raise LinkError("当前选区不能由 UUID 唯一还原，请先改为非实例选区：" + item)
        result.append([node_id, separator + suffix])
    return result


def _parent_preserving_selection(child_id, parent_id, expected_selection):
    """parent 会顺手改选区；这里只修复这一个已知副作用，其他异常改选一律停止。"""
    c = _maya()
    before = _selection_identity()
    if before != expected_selection:
        raise LinkError("parent 前选区已意外改变，停止而不强制恢复。")
    c.parent(_path(child_id), _path(parent_id), relative=True)
    after = _selection_identity()
    if after not in (before, [[child_id, ""]]):
        raise LinkError("parent 后出现非已知的选区变化，可能有外部回调；停止。")
    if after != before:
        items = [_path(node_id) + suffix for node_id, suffix in before]
        if items:
            c.select(items, replace=True, noExpand=True)
        else:
            c.select(clear=True)
        if _selection_identity() != before:
            raise LinkError("parent 的选区副作用未精确恢复，停止。")


def _inverse_scale_inputs(node):
    """读 inverseScale 的实际入线；只认完整、同源、同轴的标准补偿，否则拒绝。
    每条为 (源 UUID, 源属性, 目标属性)。"""
    c = _maya()
    links = set()
    for attr in ("inverseScale", "inverseScaleX", "inverseScaleY", "inverseScaleZ"):
        plug = node + "." + attr
        if c.connectionInfo(plug, isExactDestination=True):
            source = _plug(c.connectionInfo(plug, sourceFromDestination=True))
            source_node, source_attr = source.split(".", 1)
            links.add((_uid(source_node), source_attr, attr))
    result = sorted(links)
    pairs = {(src, dst) for _, src, dst in result}
    valid_pairs = [set(), {("scale", "inverseScale")},
                   {("scale" + a, "inverseScale" + a) for a in "XYZ"}]
    if pairs not in valid_pairs or len({uid for uid, _, _ in result}) > 1:
        raise LinkError("inverseScale 不是完整同源同轴标准补偿：" + node)
    return result


def _insert_spine4(plan):
    """在 Spine3 与 Chest 之间插入 Spine4，保持 Chest 的兄弟顺序与补偿接线。"""
    c = _maya()
    parent_id = _uid(plan["fit_index"]["Spine3"])
    chest_id = _uid(plan["fit_index"]["Chest"])
    old_inputs = _inverse_scale_inputs(_path(chest_id))
    if _parent(_path(chest_id)) != _path(parent_id) or any(uid != parent_id for uid, _, _ in old_inputs):
        raise LinkError("插入前 Chest 父级/标准补偿来源已改变，拒绝迁移。")
    old_siblings = c.listRelatives(_path(parent_id), children=True, fullPath=True) or []
    chest_order = [_uid(n) for n in old_siblings].index(chest_id)
    namespace = _namespace(_path(parent_id))
    name = (namespace + ":" if namespace else "") + "Spine4"
    # createNode(skipSelect) 不改选区。相对重挂避免 Maya 自动插入“补偿 transform”。
    new = c.createNode("joint", name=":" + name, parent=_path(parent_id), skipSelect=True)
    new_id = _uid(new)
    if _base(_path(new_id)) != "Spine4" or _parent(_path(new_id)) != _path(parent_id):
        raise LinkError("Spine4 创建名称/父级不符合计划。")
    c.setAttr(_path(new_id) + ".radius", c.getAttr(_path(parent_id) + ".radius"))
    if not _inverse_scale_inputs(_path(new_id)):
        c.connectAttr(_path(parent_id) + ".scale", _path(new_id) + ".inverseScale")
    if _inverse_scale_inputs(_path(new_id)) != [(parent_id, "scale", "inverseScale")]:
        raise LinkError("新增 Spine4 的父级补偿接线不符合计划。")
    _parent_preserving_selection(chest_id, new_id, plan["selection_identity"])
    if _parent(_path(chest_id)) != _path(new_id):
        raise LinkError("重挂后 Chest 不是 Spine4 直接子级。")
    desired_inputs = sorted((new_id, src, dst) for _, src, dst in old_inputs)
    current_inputs = _inverse_scale_inputs(_path(chest_id))
    if not old_inputs and current_inputs == [(new_id, "scale", "inverseScale")]:
        desired_inputs = current_inputs  # Maya 原生 parent 为原先无入线的 Chest 新连了父补偿
    if current_inputs not in (old_inputs, desired_inputs):
        raise LinkError("重挂后 Chest 补偿出现非计划输入，停止而不拆外来线。")
    if current_inputs != desired_inputs:
        for source_id, source_attr, dest_attr in old_inputs:
            source = _path(source_id) + "." + source_attr
            dest = _path(chest_id) + "." + dest_attr
            if not c.isConnected(source, dest):
                raise LinkError("迁移前标准补偿接线再次变化，已停止。")
            c.disconnectAttr(source, dest)
            c.connectAttr(_path(new_id) + "." + source_attr, dest)
    if _inverse_scale_inputs(_path(chest_id)) != desired_inputs:
        raise LinkError("Chest 新父补偿接线迁移验证失败。")
    siblings = c.listRelatives(_path(parent_id), children=True, fullPath=True) or []
    new_order = [_uid(n) for n in siblings].index(new_id)
    if new_order != chest_order:
        c.reorder(_path(new_id), relative=chest_order - new_order)
    if _parent(_path(chest_id)) != _path(new_id):
        raise LinkError("插入后发现意外中间 transform；停止。")
    return new_id


def _find_fit(fit_root):
    c = _maya()
    root = _exact(fit_root, "FitSkeleton")
    if _base(root) != "FitSkeleton":
        raise LinkError("请载入 FitSkeleton 本身（Build 前 ADV 的 Fit 根）")
    index = {}
    for n in _tree(root):
        key = _base(n)
        index.setdefault(key, n)  # ADV 的 HeadEnd 重名，取第一个
    return {"root": root, "index": index}


def _fit_plan(fit):
    """只读：算清楚 Build 前要补什么。返回动作列表。"""
    c = _maya()
    idx = fit["index"]
    actions = []
    if "Spine3" not in idx or "Chest" not in idx:
        raise LinkError("Fit 里找不到 Spine3/Chest，无法判断 Spine4")
    if "Spine4" in idx:
        actions.append({"kind": "spine4", "state": "skip", "target": idx["Spine4"],
                        "note": "已有 Spine4，不重复建"})
    elif _parent(idx["Chest"]) != idx["Spine3"]:
        raise LinkError("Chest 不是 Spine3 的直接子级，结构非标准，拒绝自动插 Spine4")
    else:
        actions.append({"kind": "spine4", "state": "create", "target": None,
                        "note": "在 Spine3 与 Chest 之间插入 Spine4（位置取两者中点）"})
    neck = idx.get(FIT_NECK_BONE)
    if neck is None:
        raise LinkError("Fit 里找不到 Neck 骨")
    has_ib = c.objExists(neck + ".inbetweenJoints")
    has_tw = c.objExists(neck + ".twistJoints")
    if has_tw or c.objExists(neck + ".bendyJoints"):
        raise LinkError("Neck 上已有 twistJoints/bendyJoints，ADV 不允许与 inbetweenJoints 混用")
    current = c.getAttr(neck + ".inbetweenJoints") if has_ib else None
    if current == FIT_NECK_INBETWEEN:
        actions.append({"kind": "neck", "name": "Neck", "state": "skip", "target": neck,
                        "note": "颈部中段已是 %d 根（inbetweenJoints=%d）"
                                % (FIT_NECK_INBETWEEN, current)})
    elif current is not None:
        # 已经开过但数量不对（ADV 默认是 2）：只改数值。已 Build 的场景需要重新 Build 才生效。
        actions.append({"kind": "neck", "name": "Neck", "state": "create", "target": neck,
                        "mode": "set",
                        "note": "Neck 的 inbetweenJoints 从 %d 改成 %d（UE 只有 neck_02 一根）；"
                                "已 Build 的场景需要重新 Build 才生效" % (current, FIT_NECK_INBETWEEN)})
    else:
        actions.append({"kind": "neck", "name": "Neck", "state": "create", "target": neck,
                        "mode": "add",
                        "note": "Neck 开 %d 段细分：Build 后生成 NeckPart1 → neck_02"
                                % FIT_NECK_INBETWEEN})
    for name in FIT_TWIST_BONES:
        bone = idx.get(name)
        if bone is None:
            continue
        if c.objExists(bone + ".twistJoints"):
            actions.append({"kind": "twist", "name": name, "state": "skip", "target": bone,
                            "note": "已有 twistJoints=%d" % c.getAttr(bone + ".twistJoints")})
        elif c.objExists(bone + ".inbetweenJoints") or c.objExists(bone + ".bendyJoints"):
            raise LinkError("%s 上已有 inbetweenJoints/bendyJoints，ADV 不允许与 twistJoints 混用" % name)
        else:
            actions.append({"kind": "twist", "name": name, "state": "create", "target": bone,
                            "note": "开 %d 节扭转骨（Build 后生成 %sPart1/2）"
                                    % (FIT_TWIST_COUNT, name)})
    return actions


def preview_fit(fit_root):
    """只读预览 Build 前补骨动作。"""
    result = _result("blocked")
    try:
        fit = _find_fit(fit_root)
        actions = _fit_plan(fit)
        result["actions"] = actions
        todo = [a for a in actions if a["state"] == "create"]
        result["counts"] = {"create": len(todo), "skip": len(actions) - len(todo)}
        result["status"] = "ready" if todo else "unchanged"
        result["warnings"].append("只改 FitSkeleton，不动 UE；补完再 Build，Build 后才有对应变形骨")
    except Exception as exc:
        result["issues"].append(str(exc))
    return result


def apply_fit(fit_root, confirm=None):
    """执行 Build 前补骨。confirm(context)->bool：缺省=拒绝。"""
    c = _maya()
    result = preview_fit(fit_root)
    if result["status"] != "ready":
        return result
    if not c.undoInfo(q=True, state=True):
        result.update(status="blocked", issues=["请先启用 Maya Undo"])
        return result
    context = {"count": result["counts"]["create"],
               "names": [a["kind"] for a in result["actions"] if a["state"] == "create"]}
    if confirm is None or confirm(context) is not True:
        result.update(status="blocked", issues=["已取消，未做任何修改"])
        return result
    fit = _find_fit(fit_root)
    root = fit["root"]
    spine3 = fit["index"]["Spine3"]
    chest = fit["index"]["Chest"]
    chest_world = _world(chest)
    # 借来的 _insert_spine4 会重挂 Chest，所以传入独立副本：它刷新副本，我们按骨名重解析。
    shadow = dict(fit["index"])
    chunk = "UE_FitPrepare_" + uuid.uuid4().hex
    result["undo_chunk"] = chunk
    opened = closed = wrote = False
    done = []
    try:
        c.undoInfo(openChunk=True, chunkName=chunk)
        opened = True
        for action in result["actions"]:
            if action["state"] != "create":
                continue
            wrote = True
            if action["kind"] == "spine4":
                _insert_spine4({"fit_index": shadow,
                                "selection_identity": _selection_identity()})
                new = [n for n in (c.ls("Spine4", long=True) or [])
                       if n.startswith(root + "|")][0]
                # Chest 被重挂到 Spine4 下，整棵树的路径都变了：按根重扫一遍。
                fit["index"] = _find_fit(root)["index"]
                if fit["index"].get("Spine4") != new:
                    raise LinkError("Spine4 插入后根索引不一致")
                # 摆到 Spine3 与 Chest 中点（与 Build 后 Spine4_M 的位置一致）。
                # 注意：Chest 已被重挂，必须用刷新后的路径，不能再用开头缓存的旧路径。
                chest = fit["index"]["Chest"]
                spine3 = fit["index"]["Spine3"]
                a, b = _world(spine3), _world(chest)
                # 朝向沿用 Spine3（平均两个旋转会破坏正交），位置取中点。
                mid = list(a[:12]) + [(a[12] + b[12]) * 0.5, (a[13] + b[13]) * 0.5,
                                      (a[14] + b[14]) * 0.5, 1.]
                _write_world(new, mid)
                _write_world(chest, chest_world)  # Chest 位置不动，只改它的局部值
                done.append("Spine4")
            elif action["kind"] == "neck":
                bone = fit["index"]["Neck"]
                if not c.objExists(bone + ".inbetweenJoints"):
                    c.addAttr(bone, longName="inbetweenJoints", attributeType="long",
                              minValue=0, defaultValue=2, keyable=True)
                c.setAttr(bone + ".inbetweenJoints", FIT_NECK_INBETWEEN)
                if not c.objExists(bone + ".unTwister"):
                    c.addAttr(bone, longName="unTwister", attributeType="bool",
                              defaultValue=False, keyable=True)
                done.append("Neck 中段=%d" % FIT_NECK_INBETWEEN)
            elif action["kind"] == "twist":
                bone = fit["index"][action["name"]]
                c.addAttr(bone, longName="twistJoints", attributeType="long",
                          minValue=0, defaultValue=2, keyable=True)
                c.setAttr(bone + ".twistJoints", FIT_TWIST_COUNT)
                if not c.objExists(bone + ".bendyCtrls"):  # 6.915 与 Twist 同时添加
                    c.addAttr(bone, longName="bendyCtrls", attributeType="long",
                              minValue=0, defaultValue=0, keyable=True)
                done.append("%s 扭转" % _base(bone))
        if not _near(_world(fit["index"]["Chest"]), chest_world):
            raise LinkError("Chest 世界位置被改动，已停止")
    except Exception as exc:
        result.update(status="blocked")
        result["issues"].append(str(exc))
    finally:
        if opened:
            try:
                c.undoInfo(closeChunk=True)
                closed = True
            except Exception as exc:
                result["issues"].append("关闭撤销块失败：" + str(exc))
    if result["status"] == "blocked" and closed and wrote:
        if c.undoInfo(q=True, undoName=True) == chunk:
            c.undo()
            result["status"] = "rolled_back"
        else:
            result["issues"].append("栈顶不是本次操作，未撤销其他工作")
    elif result["status"] != "blocked":
        result["status"] = "applied"
        result["done"] = done
        result["warnings"].append("已补：%s。现在去 ADV 里 Build，再回到「已 Build 后」执行匹配"
                                  % "、".join(done))
    return result


def auto_detect_fit():
    """找场景里的 FitSkeleton（Build 前用）。"""
    c = _maya()
    hits = c.ls("*FitSkeleton", long=True) or []
    return [n for n in hits if _base(n) == "FitSkeleton"]


_WINDOW = None
_TAG = "[ADV_UE_Link]"


def _report(level, title, lines=()):
    """把结果打到 Maya 命令行（底部那条）和 Script Editor，像 ADV 一样。

    level: "error" 红 / "warning" 黄 / "info" 普通。
    命令行只放一句话；完整条目逐行打印到 Script Editor，方便复制给别人看。
    """
    import maya.api.OpenMaya as om
    lines = [str(x) for x in lines if x]
    bar = "-" * 64
    print("\n".join([bar, "%s %s" % (_TAG, title)] + ["    " + x for x in lines] + [bar]))
    short = "%s %s" % (_TAG, title)
    if lines:
        short += "（%d 条，详见 Script Editor）" % len(lines)
    {"error": om.MGlobal.displayError,
     "warning": om.MGlobal.displayWarning}.get(level, om.MGlobal.displayInfo)(short)


# 深色配色：贴近 Maya 2025 默认界面，不另起一套"网页风"。
_UI_CSS = """
QDialog { background:#444444; }
QLabel { color:#dddddd; font-size:13px; }
QLabel#note { color:#bdbdbd; }
QLineEdit { font-size:13px; padding:4px 6px; border:1px solid #2e2e2e; border-radius:2px; }
QLineEdit[readOnly="true"] { background:#5a5a5a; color:#e6e6e6; }
QPushButton { font-size:13px; min-height:26px; padding:0 10px; color:#eeeeee;
              background:#5d5d5d; border:1px solid #2e2e2e; border-radius:2px; }
QPushButton:hover { background:#6b6b6b; }
QPushButton:pressed { background:#4a4a4a; }
QPushButton:disabled { color:#8a8a8a; background:#4f4f4f; }
QPushButton#run { font-weight:600; }
QPushButton#run:enabled { background:#4f7a99; }
QPushButton#run:enabled:hover { background:#5b8bad; }
QCheckBox { color:#dddddd; font-size:13px; spacing:6px; }
QToolButton#head { color:#eeeeee; font-size:13px; font-weight:600; text-align:left;
                   background:#5d5d5d; border:0; border-radius:2px; padding:4px 6px; }
QToolButton#head:hover { background:#686868; }
QFrame#body { background:#4a4a4a; border:1px solid #393939; border-top:0; }
QTreeWidget { background:#3c3c3c; color:#dddddd; font-size:12px; border:1px solid #2e2e2e; }
QHeaderView::section { background:#555555; color:#dddddd; border:0; padding:3px; }
QLabel#status { font-size:14px; font-weight:600; padding:6px 8px; border-radius:2px;
                background:#383838; }
"""
_STATUS_COLORS = {"ok": "#8fd18f", "warning": "#f0c674", "error": "#ff8a8a", "info": "#dddddd"}


def show():
    """两步窗口：补骨 / 匹配与约束 / 详细结果，三个分区框，导入本模块不创建 UI。"""
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance
    import maya.OpenMayaUI as omui
    c = _maya()
    if c.about(batch=True):
        raise LinkError("界面仅在交互 Maya 内打开；无头环境请调用 preview/apply")
    global _WINDOW
    if _WINDOW is not None:
        _WINDOW.close()
        _WINDOW.deleteLater()

    class Frame(QtWidgets.QWidget):
        """仿 Maya frameLayout：灰色标题条，点一下折叠/展开。"""

        def __init__(self, title, expanded=True):
            super().__init__()
            box = QtWidgets.QVBoxLayout(self)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(0)
            self.head = QtWidgets.QToolButton()
            self.head.setObjectName("head")
            self.head.setText(title)
            self.head.setCheckable(True)
            self.head.setChecked(expanded)
            self.head.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.head.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.head.toggled.connect(self.set_open)
            box.addWidget(self.head)
            self.body = QtWidgets.QFrame()
            self.body.setObjectName("body")
            self.inner = QtWidgets.QVBoxLayout(self.body)
            self.inner.setContentsMargins(10, 8, 10, 10)
            self.inner.setSpacing(6)
            box.addWidget(self.body)
            self.set_open(expanded)

        def set_open(self, on):
            self.body.setVisible(on)
            self.head.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)
            if self.head.isChecked() != on:
                self.head.setChecked(on)

    class Window(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget))
            self.setWindowTitle("ADV UE Link  v" + VERSION)
            self.resize(520, 600)
            self.setMinimumWidth(460)
            self.setStyleSheet(_UI_CSS)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)

            # ---- 分区 1：Build 前补骨 ----
            f1 = Frame("1  Build 前 · 补骨（FitSkeleton）")
            self.fit_field = self.path_row(f1.inner, "Fit 骨架", self.load_fit,
                                           "Build 前 ADV 的 FitSkeleton 根")
            note = QtWidgets.QLabel("补 Spine4 · Neck 中段 1 根 · 肩/肘/髋/膝 twist 各 2 根。"
                                    "只改 Fit，不碰 UE。")
            note.setObjectName("note")
            note.setWordWrap(True)
            f1.inner.addWidget(note)
            self.fit_btn = QtWidgets.QPushButton("补骨")
            self.fit_btn.clicked.connect(self.run_fit)
            f1.inner.addWidget(self.fit_btn)
            layout.addWidget(f1)

            # ---- 分区 2：Build 后匹配与约束 ----
            f2 = Frame("2  Build 后 · 匹配与约束")
            self.fields = [
                self.path_row(f2.inner, "ADV 系统", lambda: self.load(0),
                              "已 Build 的 ADV 组，里面要有 DeformationSystem"),
                self.path_row(f2.inner, "UE 根骨", lambda: self.load(1),
                              "原 UE 骨架最上面那根 root"),
            ]
            opts = QtWidgets.QHBoxLayout()
            opts.setContentsMargins(0, 4, 0, 0)
            self.cb_move = QtWidgets.QCheckBox("位置匹配")
            self.cb_move.setToolTip("UE 骨的位置搬到对应 ADV 变形骨上")
            self.cb_turn = QtWidgets.QCheckBox("朝向对齐")
            self.cb_turn.setToolTip("按 ORIENT_OFFSETS 定稿规则对齐轴向")
            self.cb_link = QtWidgets.QCheckBox("建立约束")
            self.cb_link.setToolTip("point/orient/scale 约束，Maintain Offset，含 twist 与颈中段")
            for cb in (self.cb_move, self.cb_turn, self.cb_link):
                cb.setChecked(True)
                cb.stateChanged.connect(self.on_option)
                opts.addWidget(cb)
            opts.addStretch(1)
            f2.inner.addLayout(opts)
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setStyleSheet("color:#393939;")
            f2.inner.addWidget(line)
            self.summary = QtWidgets.QLabel("未检查")
            self.summary.setObjectName("note")
            self.summary.setWordWrap(True)
            f2.inner.addWidget(self.summary)
            actions = QtWidgets.QHBoxLayout()
            self.check_btn = QtWidgets.QPushButton("检查")
            self.check_btn.setToolTip("只读，不改场景")
            self.check_btn.clicked.connect(self.inspect)
            actions.addWidget(self.check_btn, 1)
            self.apply_btn = QtWidgets.QPushButton("执行")
            self.apply_btn.setObjectName("run")
            self.apply_btn.setEnabled(False)
            self.apply_btn.setToolTip("检查通过后才能点；一次执行 = 一个撤销块")
            self.apply_btn.clicked.connect(self.execute)
            actions.addWidget(self.apply_btn, 2)
            f2.inner.addLayout(actions)
            layout.addWidget(f2)

            # ---- 分区 3：详细结果（默认折叠）----
            self.f3 = Frame("详细结果", expanded=False)
            self.details = QtWidgets.QTreeWidget()
            self.details.setHeaderLabels(["对象", "说明"])
            self.details.setMinimumHeight(150)
            self.f3.inner.addWidget(self.details)
            layout.addWidget(self.f3, 1)
            layout.addStretch(0)

            # ---- 底部状态条：一句话，颜色区分；详情去 Script Editor ----
            self.status = QtWidgets.QLabel()
            self.status.setObjectName("status")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.set_status("info", "就绪")
            self.autofill()

        # ---------- 小部件 ----------
        def path_row(self, parent, label, on_load, tip):
            row = QtWidgets.QHBoxLayout()
            name = QtWidgets.QLabel(label)
            name.setFixedWidth(64)
            row.addWidget(name)
            field = QtWidgets.QLineEdit()
            field.setReadOnly(True)  # 只能用「载入」填，灰底表示不能手改
            field.setFocusPolicy(QtCore.Qt.NoFocus)
            field.setPlaceholderText("选中后点「<<」载入")
            field.setToolTip(tip)
            row.addWidget(field, 1)
            btn = QtWidgets.QPushButton("<<")
            btn.setFixedWidth(40)
            btn.setToolTip("用当前选择载入（选这棵树里任意一根骨也行）")
            btn.clicked.connect(on_load)
            row.addWidget(btn)
            parent.addLayout(row)
            return field

        def set_status(self, level, text):
            self.status.setText(text)
            self.status.setStyleSheet("color:%s;" % _STATUS_COLORS.get(level, "#dddddd"))

        def tell(self, level, text, lines=()):
            """界面只放一句话，完整内容进 Maya 命令行 + Script Editor。"""
            lines = list(lines)
            self.set_status(level, text + ("　→ 详见 Script Editor" if lines and level != "ok" else ""))
            if level != "info" or lines:
                _report(level if level != "ok" else "info", text, lines)

        def section(self, title):
            item = QtWidgets.QTreeWidgetItem(self.details, [title, ""])
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            return item

        def child(self, parent, first, second):
            item = QtWidgets.QTreeWidgetItem(parent, [first, second])
            item.setToolTip(1, second)
            return item

        def show_field(self, field, value):
            field.setText(value)
            field.setToolTip(value)
            field.setCursorPosition(0)

        def on_option(self):
            self.apply_btn.setEnabled(False)
            self.details.clear()
            self.summary.setText("选项已改，请重新检查")
            self.set_status("info", "选项已改，请重新检查")

        # ---------- 分区 1 ----------
        def load_fit(self):
            sel = [s for s in (c.ls(sl=True, long=True) or []) if "." not in s]
            hits = []
            for node in sel:
                parts = node.split("|")
                for i in range(len(parts), 1, -1):
                    if _base(parts[i - 1]) == "FitSkeleton":
                        hits.append("|".join(parts[:i]))
                        break
            hits = _dedup(hits) or ([] if sel else auto_detect_fit())
            if len(hits) != 1:
                self.tell("error", "没认出 FitSkeleton" if not hits else "认出多个 FitSkeleton",
                          ["请在 Outliner 选中 FitSkeleton（或它下面任意一根 Fit 骨）再点 <<"] + hits)
                return
            self.show_field(self.fit_field, hits[0])
            self.set_status("info", "Fit 已载入：" + _base(hits[0]))

        def run_fit(self):
            if not self.fit_field.text():
                fits = auto_detect_fit()
                if len(fits) == 1:
                    self.show_field(self.fit_field, fits[0])
                else:
                    self.tell("error", "没有可用的 FitSkeleton",
                              ["场景里没有 Build 前的 FitSkeleton，或者有多个；选中要用的那个点 <<"])
                    return
            path = self.fit_field.text()
            try:
                r = preview_fit(path)
            except Exception as exc:
                self.tell("error", "补骨检查失败", [str(exc)])
                return
            if r["issues"]:
                self.tell("error", "补骨被阻断", r["issues"])
                return
            if r["status"] == "unchanged":
                self.tell("ok", "Fit 已经补齐，不用重复", [a["note"] for a in r["actions"]])
                return
            if not self.ask("build 前补骨", context={"count": r["counts"]["create"],
                                                    "acts": r["actions"]}):
                self.set_status("info", "已取消，未做任何修改")
                return
            try:
                r = apply_fit(path, confirm=lambda _: True)
            except Exception as exc:
                self.tell("error", "补骨失败", [str(exc)])
                return
            if r["status"] == "applied":
                self.tell("ok", "已补：" + "、".join(r.get("done", [])) + "。去 ADV 里 Build",
                          r.get("warnings", []))
            else:
                self.tell("error", "补骨未完成", r["issues"] or ["未知原因"])

        # ---------- 分区 2 ----------
        def autofill(self):
            fits = auto_detect_fit()
            if len(fits) == 1:
                self.show_field(self.fit_field, fits[0])
            try:
                adv_hits, ue_hits = auto_detect()
            except Exception:
                adv_hits, ue_hits = [], []
            if len(adv_hits) == 1:
                self.fill(0, adv_hits[0])
            if len(ue_hits) == 1:
                self.fill(1, ue_hits[0])
            many = []
            if len(adv_hits) > 1:
                many.append("ADV 系统 %d 个：%s" % (len(adv_hits), "、".join(map(_base, adv_hits))))
            if len(ue_hits) > 1:
                many.append("root 关节 %d 个：%s" % (len(ue_hits), "、".join(ue_hits)))
            if many:
                self.tell("warning", "场景里有多个候选，请选中要用的再点 <<", many)
            elif all(f.text() for f in self.fields):
                self.inspect()
            elif not adv_hits:
                self.set_status("info", "还没 Build：先做第 1 步补骨，在 ADV 里 Build 后再来第 2 步")

        def fill(self, index, value):
            self.show_field(self.fields[index], value)
            self.details.clear()
            self.summary.setText("未检查")
            self.apply_btn.setEnabled(False)

        def load(self, index):
            """从当前选择反推该填哪一栏；选错栏也会自动放到对的那栏。"""
            sel = [s for s in (c.ls(sl=True, long=True) or []) if "." not in s]
            adv_hits, ue_hits = [], []
            for node in sel:
                a, u = resolve_selection(node)
                adv_hits = _dedup(adv_hits + a)
                ue_hits = _dedup(ue_hits + u)
            if not sel:
                self.tell("error", "没有选中任何节点",
                          ["在 Outliner 里选 ADV 组或 UE 骨架里的任意一根骨，再点 <<"])
                return
            if not adv_hits and not ue_hits:
                self.tell("error", "没认出：" + _base(sel[0]),
                          ["ADV 要先 Build（场景里得有 DeformationSystem）",
                           "UE 侧请选原骨架里的关节（最上面那根叫 root）"])
                return
            filled = []
            want_adv = index == 0
            if (want_adv and adv_hits) or not ue_hits:
                self.fill(0, adv_hits[0])
                filled.append("ADV 系统 ← " + _base(adv_hits[0]))
            else:
                self.fill(1, ue_hits[0])
                filled.append("UE 根骨 ← " + _base(ue_hits[0]))
            if (want_adv and not adv_hits) or (not want_adv and not ue_hits):
                filled.append("（选中的属于另一棵树，已放到对应那一栏）")
            self.set_status("info", "；".join(filled))
            if all(f.text() for f in self.fields):
                self.inspect()

        def options(self):
            return {"match": self.cb_move.isChecked(),
                    "orient": self.cb_turn.isChecked(),
                    "link": self.cb_link.isChecked()}

        def render(self, r):
            self.details.clear()
            issues = r.get("issues", [])
            warnings = r.get("warnings", [])
            cnt = r.get("counts", {})
            det = r.get("details") or {}
            skipped = det.get("skipped", [])
            unavailable = det.get("unavailable", [])
            bits = []
            if self.cb_move.isChecked():
                bits.append("搬位置 %d" % cnt.get("move", 0))
                if self.cb_turn.isChecked():
                    bits.append("转朝向 %d" % cnt.get("turn", 0))
            if self.cb_link.isChecked():
                bits.append("连约束 %d" % cnt.get("connect", 0))
            if cnt.get("already"):
                bits.append("已连 %d" % cnt["already"])
            if skipped or unavailable:
                bits.append("跳过 %d" % (len(skipped) + len(unavailable)))
            self.summary.setText("　·　".join(bits) if bits else "没有要处理的")

            if issues:
                head = self.section("需要处理（%d）" % len(issues))
                for text in issues:
                    self.child(head, "阻断", text)
            if skipped:
                head = self.section("跳过：UE 没这根骨（%d）" % len(skipped))
                for item in skipped:
                    self.child(head, item["ue"], "%s（原本由 %s 驱动）" % (item["reason"], item["adv"]))
            if unavailable:
                head = self.section("跳过：ADV 没驱动骨（%d）" % len(unavailable))
                for item in unavailable:
                    self.child(head, item["ue"], "%s：%s" % (item["adv"], item["reason"]))
            if warnings:
                head = self.section("提示（%d）" % len(warnings))
                for text in warnings:
                    self.child(head, "提示", text)
            if r.get("pairs"):
                head = self.section("对应表（%d 对）" % len(r["pairs"]))
                for row in r["pairs"]:
                    self.child(head, row["ue"], row["adv"] + ("（已连接）" if row["state"] == "ok" else ""))
                head.setExpanded(False)
            self.details.setColumnWidth(0, 170)
            for i in range(self.details.topLevelItemCount() - (1 if r.get("pairs") else 0)):
                self.details.topLevelItem(i).setExpanded(True)

            status = r["status"]
            lines = issues + ["跳过 %s：%s" % (i["ue"], i["reason"]) for i in skipped] + \
                ["跳过 %s：%s" % (i["ue"], i["reason"]) for i in unavailable] + warnings
            if status == "blocked" and issues == ["已取消，未做任何修改"]:
                self.set_status("info", "已取消，未做任何修改")
            elif issues:
                self.f3.set_open(True)
                self.tell("error", "需要处理：" + issues[0][:60], lines)
            elif status == "applied":
                self.tell("ok", "完成。Ctrl+Z 可整体撤销", lines)
            elif status == "unchanged":
                self.tell("ok", "UE 已和 ADV 对齐/连好，不用重复", lines)
            elif status == "rolled_back":
                self.tell("warning", "已撤销本次操作", lines)
            elif skipped or unavailable or r.get("ssc_on"):
                self.tell("warning", "检查通过，可以执行（有提示）", lines)
            else:
                self.tell("ok", "检查通过，可以执行", lines)
            self.apply_btn.setEnabled(status == "ready")

        def inspect(self):
            opt = self.options()
            if not (opt["match"] or opt["link"]):
                self.tell("error", "至少勾「位置匹配」或「建立约束」其中一项")
                return
            if not all(f.text() for f in self.fields):
                self.tell("error", "两栏都要先载入", ["ADV 系统、UE 根骨都用 << 载入"])
                return
            try:
                self.render(preview(*(f.text() for f in self.fields), **opt))
            except Exception as exc:
                self.tell("error", "检查失败", [str(exc)])

        def ask(self, kind, context):
            box = QtWidgets.QMessageBox(self)
            yes = box.addButton("确定", QtWidgets.QMessageBox.AcceptRole)
            cancel = box.addButton("取消", QtWidgets.QMessageBox.RejectRole)
            box.setDefaultButton(cancel)
            box.setEscapeButton(cancel)
            if kind == "build 前补骨":
                box.setWindowTitle("确认补骨")
                box.setText("将修改 FitSkeleton：%d 项" % context["count"])
                box.setInformativeText("\n".join(
                    "· " + a["note"] for a in context["acts"] if a["state"] == "create")
                    + "\n\n不动 UE；Ctrl+Z 可撤销。补完需要 Build。")
            else:
                box.setWindowTitle("确认执行")
                box.setText("将处理 %d 对骨骼" % context["count"])
                lines = []
                if context.get("move"):
                    lines.append("搬 UE 骨位置 %d 根" % context["move"])
                if context.get("turn"):
                    lines.append("调 UE 骨朝向 %d 根" % context["turn"])
                if context.get("count"):
                    lines.append("建立约束 %d 对（Maintain Offset）" % context["count"])
                rest = int(context.get("skipped", 0)) + int(context.get("unavailable", 0))
                if rest:
                    lines.append("缺骨跳过 %d 对，不影响其余" % rest)
                box.setInformativeText("\n".join(lines) + "\n\nCtrl+Z 可整体撤销。")
            box.exec()
            return box.clickedButton() == yes

        def execute(self):
            self.set_status("info", "处理中…")
            self.apply_btn.setEnabled(False)
            self.check_btn.setEnabled(False)
            QtWidgets.QApplication.processEvents()
            try:
                self.render(apply(*(f.text() for f in self.fields),
                                  confirm=lambda ctx: self.ask("执行", ctx), **self.options()))
            except Exception as exc:
                self.tell("error", "执行失败", [str(exc)])
            finally:
                self.check_btn.setEnabled(True)

    _WINDOW = Window()
    _WINDOW.show()
    return _WINDOW
