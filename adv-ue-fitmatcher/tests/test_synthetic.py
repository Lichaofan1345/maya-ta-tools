"""隔离 mayapy 合成场景验证。不加载 ADV、不打开用户场景、不执行任何场景脚本。

运行（必须隔离偏好）：
  env -u PYTHONPATH MAYA_APP_DIR=<隔离目录> MAYA_SKIP_USERSETUP_PY=1 \
      mayapy test_synthetic.py
"""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback

BASE = Path(__file__).resolve().parent
assert "mayapy" in sys.executable.lower(), "必须用 mayapy 运行"
assert os.environ.get("MAYA_SKIP_USERSETUP_PY") == "1"
assert not os.environ.get("PYTHONPATH")
assert not os.environ.get("MAYA_SCRIPT_PATH")

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as c

PRODUCT = BASE.parents[0] / "adv_ue_deform_link.py"
spec = importlib.util.spec_from_file_location("deform_link", PRODUCT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _ue_names():
    names = ["root", "pelvis", "neck_01", "neck_02", "head"]
    names += ["spine_%02d" % i for i in range(1, 6)]
    for s in ("r", "l"):
        names += [b + "_" + s for b in ("clavicle", "upperarm", "lowerarm", "hand",
                                        "thigh", "calf", "foot", "ball")]
        for _, f in m.FINGERS:
            for i in (range(1, 4) if f == "thumb" else range(4)):
                names.append(f + ("_metacarpal" if i == 0 else "_%02d" % i) + "_" + s)
        for _, seg, _ in m.TWISTS:
            names += [seg + "_twist_01_" + s, seg + "_twist_02_" + s]
    names += ["ik_foot_root", "ik_foot_l", "ik_foot_r", "ik_hand_root",
              "ik_hand_gun", "ik_hand_l", "ik_hand_r", "interaction", "center_of_mass"]
    return names


def fixture(namespace="", drop_adv=(), drop_ue=(), skinned=False, foreign=False,
            locked=False):
    """合成两棵树。非真实 ADV 输出，仅验证本工具逻辑。"""
    c.file(new=True, force=True)
    c.currentUnit(linear="cm", angle="deg")
    adv_names = [r["adv"] for r in m.deform_mapping() if r["adv"] not in drop_adv]
    group = c.createNode("transform", name="Group")
    deform = c.createNode("transform", name="DeformationSystem", parent=group)
    c.createNode("transform", name="Main", parent=group)
    for i, name in enumerate(adv_names):
        j = c.createNode("joint", name=name, parent=deform)
        c.setAttr(j + ".translate", i * 0.5, 10 + i * 0.1, -i * 0.2)
        c.setAttr(j + ".jointOrient", 5 + i, 10 - i * 0.3, i * 0.7)
    ue_root_name = namespace + ":root" if namespace else "root"
    root = c.createNode("joint", name=ue_root_name)
    for i, name in enumerate(_ue_names()):
        if name == "root" or name in drop_ue:
            continue
        short = namespace + ":" + name if namespace else name
        j = c.createNode("joint", name=short, parent=root)
        c.setAttr(j + ".translate", 1 + i * 0.3, 5 - i * 0.1, i * 0.2)
        c.setAttr(j + ".jointOrient", -8 + i * 0.2, 3 + i * 0.5, -i * 0.4)
        c.setAttr(j + ".rotate", 2, -3, 1.5)
    if foreign:
        other = c.createNode("transform", name="foreignDriver")
        target = (namespace + ":hand_r") if namespace else "hand_r"
        c.pointConstraint(other, target, maintainOffset=True)
    if locked:
        target = (namespace + ":hand_r") if namespace else "hand_r"
        c.setAttr(target + ".translate", lock=True)
    if skinned:
        mesh = c.polySphere()[0]
        target = (namespace + ":hand_r") if namespace else "hand_r"
        c.skinCluster(target, mesh, toSelectedBones=True)
    return "|" + group, "|" + root


def require(result, status):
    assert result["status"] == status, json.dumps(result, ensure_ascii=False)[:2000]


def constraint_count():
    return len(c.ls(type="constraint") or [])


def run():
    result = {"scope": "synthetic only; not a real ADV Build output",
              "product_sha256": hashlib.sha256(PRODUCT.read_bytes()).hexdigest(),
              "checks": []}

    def case(name, fn):
        try:
            fn()
            result["checks"].append({"name": name, "passed": True})
        except Exception:
            result["checks"].append({"name": name, "passed": False,
                                     "error": traceback.format_exc()})

    def preview_readonly():
        a, u = fixture()
        sel = c.ls(sl=True, long=True)
        t = c.currentTime(q=True)
        r = m.preview(a, u)
        require(r, "ready")
        assert r["counts"]["connect"] == 85  # 79 deform + Main->root + 5 IK follow
        assert constraint_count() == 0
        assert c.ls(sl=True, long=True) == sel and c.currentTime(q=True) == t

    def apply_happy():
        a, u = fixture()
        before = {n: c.xform(n, q=True, ws=True, matrix=True)
                  for n in c.listRelatives(u, ad=True, fullPath=True) or []}
        r = m.apply(a, u, confirm=lambda _: True)
        require(r, "applied")
        assert constraint_count() == 85 * 3
        for n, w in before.items():
            assert all(abs(x - y) < 1e-6 for x, y in
                       zip(c.xform(n, q=True, ws=True, matrix=True), w)), n
        # 驱动正确性：肘反序 Part1 -> twist_02；膝反序；颈中间骨；root<-Main
        def driver_of(target, kind):
            node = c.ls("UELink_%s_%s" % (target, kind), long=True)[0]
            fn = {"pt": c.pointConstraint, "or": c.orientConstraint,
                  "sc": c.scaleConstraint}[kind]
            return fn(node, q=True, targetList=True)[0]
        assert driver_of("lowerarm_twist_02_r", "or").endswith("ElbowPart1_R")
        assert driver_of("lowerarm_twist_01_r", "or").endswith("ElbowPart2_R")
        assert driver_of("calf_twist_02_l", "pt").endswith("KneePart1_L")
        assert driver_of("upperarm_twist_01_r", "sc").endswith("ShoulderPart1_R")
        assert driver_of("neck_02", "or").endswith("NeckPart1_M")
        assert driver_of("root", "or") == "Main"
        assert driver_of("ik_hand_gun", "pt").endswith("hand_r")
        # 幂等：重复执行零新增
        r2 = m.apply(a, u, confirm=lambda _: True)
        require(r2, "unchanged")
        assert constraint_count() == 85 * 3
        # 单次撤销整体恢复
        c.undo()
        assert constraint_count() == 0

    def cancel_zero_write():
        a, u = fixture()
        r = m.apply(a, u, confirm=lambda _: False)
        require(r, "blocked")
        assert constraint_count() == 0

    def foreign_blocked():
        a, u = fixture(foreign=True)
        r = m.preview(a, u)
        require(r, "blocked")
        assert "不覆盖" in r["issues"][0]
        assert constraint_count() == 1

    def skinned_blocked():
        a, u = fixture(skinned=True)
        require(m.preview(a, u), "blocked")

    def locked_blocked():
        a, u = fixture(locked=True)
        r = m.preview(a, u)
        require(r, "blocked")
        assert "锁定" in r["issues"][0]

    def twist_needs_part():
        # UE 有前臂 twist 但 ADV 没建 ElbowPart1（Build 时 twistJoints=0）：
        # 不再整体阻断，只跳过这一对，并给出「Build 时开 Twist Joints」的建议。
        a, u = fixture(drop_adv=("ElbowPart1_R",))
        r = m.preview(a, u)
        require(r, "ready")
        gap = r["details"]["unavailable"]
        assert [i["ue"] for i in gap] == ["lowerarm_twist_02_r"], gap
        assert "ElbowPart1_R" in gap[0]["reason"] and "Twist Joints" in gap[0]["reason"]
        assert r["counts"]["connect"] == 84
        assert r["counts"]["unavailable"] == 1

    def ue_without_twist_skips():
        # UE 没有小腿 twist：该对跳过并提示，不当作缺项失败
        a, u = fixture(drop_ue=("calf_twist_01_r", "calf_twist_02_r",
                                "calf_twist_01_l", "calf_twist_02_l"),
                       drop_adv=("KneePart1_R", "KneePart2_R",
                                 "KneePart1_L", "KneePart2_L"))
        r = m.preview(a, u)
        require(r, "ready")
        assert r["counts"]["connect"] == 81

    def partial_skeleton_runs():
        """真实残缺场景：UE 缺整条左臂+左手指+左侧扭转骨，ADV 脊柱 4 节且无 KneePart。

        结论必须是「能跑通、不阻断」，缺骨分组进 skipped / unavailable。
        """
        drop_ue = ["upperarm_l", "lowerarm_l", "hand_l", "thigh_l", "calf_l",
                   "foot_l", "ball_l"]
        for _, f in m.FINGERS:  # 左侧手指整排没有
            for i in (range(1, 4) if f == "thumb" else range(4)):
                drop_ue.append(f + ("_metacarpal" if i == 0 else "_%02d" % i) + "_l")
        for _, seg, _ in m.TWISTS:
            drop_ue += [seg + "_twist_01_l", seg + "_twist_02_l"]
        # ADV：脊柱只有 4 节（Spine1~3 + Chest），且 Build 时没开小腿扭转骨
        a, u = fixture(drop_ue=drop_ue, drop_adv=("Spine4_M", "KneePart1_R", "KneePart2_R"))
        c.rename("Spine5_M", "Chest_M")
        r = m.preview(a, u)
        require(r, "ready")
        assert not r["issues"], r["issues"]
        cnt = r["counts"]
        assert cnt["connect"] > 0
        # 主骨覆盖率在安全阀之上（62 根主骨的 40%）
        assert cnt["connect"] >= 0.4 * 62
        skipped = {i["ue"] for i in r["details"]["skipped"]}
        assert {"hand_l", "index_01_l", "upperarm_twist_01_l"} <= skipped, sorted(skipped)[:5]
        gap = {i["ue"]: i["reason"] for i in r["details"]["unavailable"]}
        assert "spine_04" in gap and "4 节" in gap["spine_04"] and "ReSample" in gap["spine_04"]
        assert "calf_twist_01_r" in gap and "Twist Joints" in gap["calf_twist_01_r"]
        assert "calf_twist_02_r" in gap
        # 脊柱缺节只提示 ReSample，绝不引导改脊柱节数
        assert not any("改成 5 节" in t or "改为 5 节" in t for t in gap.values())
        # 真能连：约束数 = 对数*3，且左臂那几根一个都没建
        r2 = m.apply(a, u, confirm=lambda _: True)
        require(r2, "applied")
        assert constraint_count() == cnt["connect"] * 3
        assert not c.ls("UELink_hand_l_pt", long=True)
        assert c.ls("UELink_hand_r_pt", long=True)
        node = c.ls("UELink_spine_05_or", long=True)[0]
        assert c.orientConstraint(node, q=True, targetList=True)[0] == "Chest_M"

    def structure_mismatch_blocked():
        """只看得到 1 根主骨：安全阀给明确结论，而不是含糊的「映射缺项」。"""
        drop_adv = tuple(r_["adv"] for r_ in m.deform_mapping() if r_["adv"] != "Root_M")
        a, u = fixture(drop_adv=drop_adv)
        r = m.preview(a, u)
        require(r, "blocked")
        assert "不是一对配套骨架" in r["issues"][0], r["issues"][0]
        assert "40%" in r["issues"][0]

    def auto_detect_and_resolve():
        """载入选中：选任意节点都要能反推出 ADV 组 / UE root。"""
        a, u = fixture()
        adv_hits, ue_hits = m.auto_detect()
        assert adv_hits == [a], adv_hits
        assert ue_hits == [u], ue_hits
        # 选中 UE 树里的手指骨 -> 反推出 UE root，且不会误认成 ADV
        ra, ru = m.resolve_selection("|root|hand_r")
        assert ra == [] and ru == [u], (ra, ru)
        # 选中 ADV 里的 DeformationSystem -> 反推出 ADV 组
        ra, ru = m.resolve_selection(a + "|DeformationSystem")
        assert ra == [a] and ru == [], (ra, ru)
        # 选中 ADV 里一根变形骨 -> 沿父链反推出 ADV 组
        ra, ru = m.resolve_selection(a + "|DeformationSystem|Wrist_R")
        assert ra == [a] and ru == [], (ra, ru)

    def namespace_ok():
        a, u = fixture(namespace="cha")
        r = m.apply(a, u, confirm=lambda _: True)
        require(r, "applied")
        node = c.ls("UELink_hand_r_or", long=True)[0]
        assert c.orientConstraint(node, q=True, targetList=True)[0].endswith("Wrist_R")

    def duplicate_short_name_blocked():
        a, u = fixture()
        sub = c.createNode("joint", name="extra_sub", parent=u)
        c.createNode("joint", name="hand_r", parent=sub)  # 不同父级下的同名骨
        r = m.preview(a, u)
        require(r, "blocked")

    def spine5_chest_variant():
        a, u = fixture()
        c.rename("Spine5_M", "Chest_M")
        r = m.apply(a, u, confirm=lambda _: True)
        require(r, "applied")
        node = c.ls("UELink_spine_05_or", long=True)[0]
        assert c.orientConstraint(node, q=True, targetList=True)[0] == "Chest_M"

    def fit_neck_set_from_default_two():
        """用户真实情况：Neck.inbetweenJoints 已经是 ADV 默认的 2，要改成 1（UE 只有 neck_02）。"""
        c.file(new=True, force=True)
        fit = c.createNode("transform", name="FitSkeleton")
        root = c.createNode("joint", name="Root", parent=fit)
        prev = root
        for name in ("Spine2", "Spine3", "Chest", "Neck", "Head"):
            prev = c.createNode("joint", name=name, parent=prev)
            c.setAttr(prev + ".translate", 0, 3, 0)
        neck = c.ls("Neck", long=True)[0]
        c.addAttr(neck, longName="inbetweenJoints", attributeType="long", minValue=0,
                  defaultValue=2, keyable=True)
        c.addAttr(neck, longName="unTwister", attributeType="bool", defaultValue=False, keyable=True)
        for n in ("Shoulder", "Elbow", "Hip", "Knee"):
            j = c.createNode("joint", name=n, parent=root)
            c.addAttr(j, longName="twistJoints", attributeType="long", minValue=0, defaultValue=2)
        c.createNode("joint", name="Spine4", parent=c.ls("Spine3", long=True)[0])
        path = "|" + fit
        r = m.preview_fit(path)
        # Spine4 已存在但 Chest 仍在 Spine3 下 -> 本用例只关心 Neck，所以允许 Spine4 动作报错之外的情况
        if r["issues"]:
            c.delete(c.ls("Spine4", long=True)[0])
            r = m.preview_fit(path)
        neck_act = [a for a in r["actions"] if a["kind"] == "neck"][0]
        assert neck_act["state"] == "create" and neck_act["mode"] == "set", neck_act
        assert "2 改成 1" in neck_act["note"] and "重新 Build" in neck_act["note"], neck_act["note"]
        r = m.apply_fit(path, confirm=lambda _: True)
        require(r, "applied")
        assert c.getAttr(c.ls("Neck", long=True)[0] + ".inbetweenJoints") == 1
        # 已经是 1 -> 跳过
        again = [a for a in m.preview_fit(path)["actions"] if a["kind"] == "neck"][0]
        assert again["state"] == "skip", again
        # 撤销 -> 回到 2
        c.undo()
        assert c.getAttr(c.ls("Neck", long=True)[0] + ".inbetweenJoints") == 2

    def stray_suffix_bone_blocked():
        """复现用户真实翻车：clavicle_l1 带着整条左臂，clavicle_l 是空骨。

        工具按名字只认 clavicle_l，clavicle_l1 既不匹配位置也不约束，位置一直是错的。
        必须阻断并点名，而不是放行出一个"看起来成功"的结果。
        """
        a, u = fixture()
        clav = c.ls("|root|clavicle_l", long=True)[0]
        arm = c.ls("|root|upperarm_l", long=True)[0]
        twin = c.duplicate(clav, parentOnly=True, name="clavicle_l1")[0]
        twin = c.ls(twin, long=True)[0]
        arm = c.parent(arm, twin)[0]  # 整条左臂改挂到撞名骨下，原名骨成空骨
        arm = c.ls(arm, long=True)[0]
        r = m.preview(a, u)
        require(r, "blocked")
        msg = r["issues"][0]
        assert "clavicle_l1" in msg and "clavicle_l" in msg and "撞名" in msg, msg
        assert constraint_count() == 0
        # 修复后（挂回原名骨、删掉多余的）应恢复可用
        c.parent(arm, c.ls("|root|clavicle_l", long=True)[0])
        c.delete(c.ls("|root|clavicle_l1", long=True)[0])
        require(m.preview(a, u), "ready")

    def clean_scene_has_no_stray():
        a, u = fixture()
        r = m.preview(a, u)
        require(r, "ready")

    def match_position_and_orient():
        """Build 后把 UE 搬到 ADV 位置：位置必须等于 ADV 变形骨，朝向按定稿规则。"""
        a, u = fixture()
        by_name = {n.rsplit("|", 1)[-1]: n for n in
                   (c.listRelatives(u, ad=True, fullPath=True) or [])}
        adv_w = {n.rsplit("|", 1)[-1]: c.xform(n, q=True, ws=True, matrix=True)
                 for n in (c.listRelatives(a + "|DeformationSystem", ad=True, fullPath=True) or [])}
        unmapped_before = {k: c.xform(by_name[k], q=True, ws=True, matrix=True)
                           for k in ("ik_hand_gun", "interaction", "center_of_mass")}
        # 左臂未设朝向规则：位置跟随 ADV，朝向必须保持原样
        left_rot_before = m._rot3(c.xform(by_name["upperarm_l"], q=True, ws=True, matrix=True))
        saved = m.ORIENT_OFFSETS["arm_l"]
        m.ORIENT_OFFSETS["arm_l"] = None  # 本用例验证"区域置 None = 只搬位置不转朝向"
        r = m.preview(a, u, match=True, orient=True, link=False)
        require(r, "ready")
        assert r["counts"]["move"] == 79, r["counts"]
        r = m.apply(a, u, confirm=lambda _: True, match=True, orient=True, link=False)
        require(r, "applied")
        assert constraint_count() == 0

        def rot_of(mat):
            return m._rot3(mat)

        cases = [("pelvis", "Root_M", "spine"), ("spine_03", "Spine3_M", "spine"),
                 ("upperarm_r", "Shoulder_R", "arm_r"), ("lowerarm_r", "Elbow_R", "arm_r"),
                 ("hand_r", "Wrist_R", "hand_r"), ("thigh_r", "Hip_R", "leg_r"),
                 ("calf_r", "Knee_R", "leg_r"), ("head", "Head_M", "spine")]
        for ue, adv, _region in cases:
            w = c.xform(by_name[ue], q=True, ws=True, matrix=True)
            src = adv_w[adv]
            assert all(abs(w[i] - src[i]) < 1e-4 for i in (12, 13, 14)), (ue, w[12:15], src[12:15])
        # 朝向：UE = 偏移 × ADV
        for ue, adv, region in cases:
            off = m.ORIENT_OFFSETS[region]
            want = m._rot3(adv_w[adv]) if off is None else m._mm3(m._euler_rot(off), m._rot3(adv_w[adv]))
            got = m._rot3(c.xform(by_name[ue], q=True, ws=True, matrix=True))
            assert all(abs(got[i][j] - want[i][j]) < 1e-4 for i in range(3) for j in range(3)), \
                (ue, got, want)
        # 左臂未设规则：位置跟随、朝向保持原样
        lw = c.xform(by_name["upperarm_l"], q=True, ws=True, matrix=True)
        assert all(abs(lw[i] - adv_w["Shoulder_L"][i]) < 1e-4 for i in (12, 13, 14))
        got_l = m._rot3(lw)
        assert all(abs(got_l[i][j] - left_rot_before[i][j]) < 1e-4
                   for i in range(3) for j in range(3)), "左臂朝向应保持不变"
        m.ORIENT_OFFSETS["arm_l"] = saved
        # 未映射骨（参考骨）世界矩阵完全不变
        for k, w0 in unmapped_before.items():
            assert all(abs(x - y) < 1e-6 for x, y in
                       zip(c.xform(by_name[k], q=True, ws=True, matrix=True), w0)), k

    def match_then_link_together():
        """位置匹配 + 约束在一个撤销块里；Ctrl+Z 一次全回。"""
        a, u = fixture()
        before = {n: c.xform(n, q=True, ws=True, matrix=True)
                  for n in (c.listRelatives(u, ad=True, fullPath=True) or [])}
        r = m.apply(a, u, confirm=lambda _: True, match=True, orient=True)
        require(r, "applied")
        assert constraint_count() == 85 * 3
        # 约束建好后仍是 ADV 的位置（保偏移没有把它拉回去）
        adv = c.xform(a + "|DeformationSystem|Shoulder_R", q=True, ws=True, matrix=True)
        ue = c.xform("|root|upperarm_r", q=True, ws=True, matrix=True)
        assert all(abs(adv[i] - ue[i]) < 1e-4 for i in (12, 13, 14))
        c.undo()
        assert constraint_count() == 0
        for n, w in before.items():
            assert all(abs(x - y) < 1e-6 for x, y in
                       zip(c.xform(n, q=True, ws=True, matrix=True), w)), n
        # 幂等：再跑一次不应再报 ready
        r2 = m.apply(a, u, confirm=lambda _: True, match=True, orient=True)
        require(r2, "applied")
        r3 = m.preview(a, u, match=True, orient=True)
        require(r3, "unchanged")

    def fit_prepare_idempotent():
        """Build 前补骨：Spine4 / Neck 中段 / 四肢扭转，重复执行不重复建。"""
        c.file(new=True, force=True)
        c.currentUnit(linear="cm", angle="deg")
        fit = c.createNode("transform", name="FitSkeleton")
        root = c.createNode("joint", name="Root", parent=fit)
        chain = {"Spine1": root}
        for name in ("Spine2", "Spine3", "Chest", "Neck", "Head"):
            chain[name] = c.createNode("joint", name=name, parent=chain[list(chain)[-1]])
            c.setAttr(chain[name] + ".translate", 0, 3, 0)
        for name in ("Shoulder", "Elbow", "Hip", "Knee"):
            c.createNode("joint", name=name, parent=root)
        path = "|" + fit
        r = m.preview_fit(path)
        require(r, "ready")
        kinds = sorted(a["kind"] for a in r["actions"] if a["state"] == "create")
        assert kinds == ["neck", "spine4", "twist", "twist", "twist", "twist"], kinds
        chest_before = c.xform(chain["Chest"], q=True, ws=True, matrix=True)
        r = m.apply_fit(path, confirm=lambda _: True)
        require(r, "applied")
        assert c.objExists("Spine4"), "Spine4 未创建"
        assert c.listRelatives(chain["Chest"], parent=True)[0].endswith("Spine4")
        # Chest 世界位置不动
        assert all(abs(x - y) < 1e-6 for x, y in
                   zip(c.xform(chain["Chest"], q=True, ws=True, matrix=True), chest_before))
        assert c.getAttr(chain["Neck"] + ".inbetweenJoints") == 1
        for name in ("Shoulder", "Elbow", "Hip", "Knee"):
            assert c.objExists("|%s|Root|%s.twistJoints" % (fit, name)), name
        # 幂等
        require(m.preview_fit(path), "unchanged")
        # 单次撤销全回
        c.undo()
        assert not c.objExists("Spine4")
        assert not c.objExists(chain["Neck"] + ".inbetweenJoints")

    for name, fn in [("preview_readonly", preview_readonly),
                     ("apply_happy_reverse_twist_idempotent_undo", apply_happy),
                     ("cancel_zero_write", cancel_zero_write),
                     ("foreign_blocked", foreign_blocked),
                     ("skinned_blocked", skinned_blocked),
                     ("locked_blocked", locked_blocked),
                     ("twist_needs_part", twist_needs_part),
                     ("ue_without_twist_skips", ue_without_twist_skips),
                     ("partial_skeleton_runs", partial_skeleton_runs),
                     ("structure_mismatch_blocked", structure_mismatch_blocked),
                     ("auto_detect_and_resolve", auto_detect_and_resolve),
                     ("namespace_ok", namespace_ok),
                     ("duplicate_short_name_blocked", duplicate_short_name_blocked),
                     ("spine5_chest_variant", spine5_chest_variant),
                     ("fit_neck_set_from_default_two", fit_neck_set_from_default_two),
                     ("stray_suffix_bone_blocked", stray_suffix_bone_blocked),
                     ("clean_scene_has_no_stray", clean_scene_has_no_stray),
                     ("match_position_and_orient", match_position_and_orient),
                     ("match_then_link_together", match_then_link_together),
                     ("fit_prepare_idempotent", fit_prepare_idempotent)]:
        case(name, fn)
    result["passed"] = sum(x["passed"] for x in result["checks"])
    result["total"] = len(result["checks"])
    out = BASE / ("synthetic_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return result["passed"] == result["total"]


ok = run()
maya.standalone.uninitialize()
sys.exit(0 if ok else 1)
