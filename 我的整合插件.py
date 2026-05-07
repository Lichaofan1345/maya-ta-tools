# -*- coding: utf-8 -*-
import maya.cmds as cmds
import maya.mel as mel
import sys
import os

# ===================== 全局配置 & 兼容处理 =====================
# 主窗口名称（保证唯一性）
MAIN_WINDOW = "MayaAssetToolsSuite"
# Python2/3 编码兼容
if sys.version_info.major == 2:
    reload(sys)
    sys.setdefaultencoding('utf-8')

# ===================== 通用工具函数 =====================
def get_short_name(full_path):
    """将全路径转换为短名称，避免路径相关操作异常"""
    if '|' in full_path:
        return full_path.split('|')[-1]
    return full_path

def safe_rename(node, base_name):
    """安全重命名：返回实际名称，避免冲突"""
    node_short = get_short_name(node)
    if cmds.objExists(base_name):
        new_name = cmds.rename(node_short, base_name + '#')
    else:
        new_name = cmds.rename(node_short, base_name)
    return get_short_name(new_name)

def safe_group(name, parent=None):
    """安全创建组：若名称已存在则使用自动编号"""
    parent_short = get_short_name(parent) if parent else None
    if cmds.objExists(name):
        grp = cmds.group(empty=True, name=name + '#', parent=parent_short)
    else:
        grp = cmds.group(empty=True, name=name, parent=parent_short)
    return get_short_name(grp)

# ===================== 功能1：骨骼方向轴开关 =====================
def toggle_joint_orientation(show=True, scale=1.0):
    """显示/隐藏所有骨骼的方向轴"""
    all_joints = cmds.ls(type='joint', long=True)
    
    if not all_joints:
        cmds.warning("场景中没有找到任何骨骼！")
        return
    
    success_count = 0
    for joint in all_joints:
        try:
            cmds.setAttr(joint + '.displayLocalAxis', show)
            
            if show:
                cmds.setAttr(joint + '.jointDisplayScale', scale)
                cmds.setAttr(joint + '.drawStyle', 2)
                cmds.showHidden(joint)
                cmds.setAttr(joint + '.visibility', True)
            else:
                cmds.setAttr(joint + '.drawStyle', 0)
            
            success_count += 1
        except Exception as e:
            cmds.warning(f"处理骨骼 {joint} 失败: {str(e)}")
    
    cmds.refresh()
    status = "显示" if show else "隐藏"
    print(f"成功{status} {success_count}/{len(all_joints)} 个骨骼的方向轴")

def create_joint_orientation_tab(parent_layout):
    """创建骨骼方向轴开关标签页"""
    # 子布局
    sub_layout = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=15,
        columnAlign="center",
        parent=parent_layout
    )
    
    cmds.text(label="骨骼方向轴显示/隐藏开关", font="boldLabelFont", height=30)
    cmds.separator(height=5, style='in')
    
    # 缩放值输入框
    scale_layout = cmds.rowLayout(
        numberOfColumns=2,
        columnWidth2=(80, 200),
        parent=sub_layout
    )
    cmds.text(label="显示大小：", parent=scale_layout)
    scale_field = cmds.floatField(
        value=1.0,
        minValue=0.1,
        maxValue=5.0,
        step=0.1,
        parent=scale_layout
    )
    
    # 按钮布局
    button_layout = cmds.rowLayout(
        numberOfColumns=2,
        columnWidth2=(140, 140),
        parent=sub_layout
    )
    
    # 显示按钮
    cmds.button(
        label="显示所有骨骼方向轴",
        command=lambda *args: toggle_joint_orientation(
            show=True,
            scale=cmds.floatField(scale_field, query=True, value=True)
        ),
        backgroundColor=(0.3, 0.6, 0.3),
        height=40,
        parent=button_layout
    )
    
    # 隐藏按钮
    cmds.button(
        label="隐藏所有骨骼方向轴",
        command=lambda *args: toggle_joint_orientation(show=False),
        backgroundColor=(0.6, 0.3, 0.3),
        height=40,
        parent=button_layout
    )

# ===================== 功能2：资产全自动整理 =====================
def run_clean():
    """执行资产整理核心逻辑"""
    cmds.undoInfo(openChunk=True)
    try:
        meshes = cmds.ls(type='mesh', long=False) or []
        joints = cmds.ls(type='joint', long=False) or []
        curves = cmds.ls(type='nurbsCurve', long=False) or []

        if not (meshes or joints or curves):
            cmds.confirmDialog(title=u'提示', message=u'场景中没有可处理的模型、骨骼或曲线！', button=[u'确定'])
            return

        # 提取变换节点
        mesh_transforms = []
        for mesh in meshes:
            parents = cmds.listRelatives(mesh, parent=True, fullPath=True) or []
            if parents:
                mesh_transforms.append(get_short_name(parents[0]))
        mesh_transforms = list(set(mesh_transforms))

        joint_transforms = [get_short_name(j) for j in joints]

        curve_transforms = []
        for crv in curves:
            parents = cmds.listRelatives(crv, parent=True, fullPath=True) or []
            if parents:
                curve_transforms.append(get_short_name(parents[0]))
        curve_transforms = list(set(curve_transforms))

        all_transforms = mesh_transforms + joint_transforms + curve_transforms
        if not all_transforms:
            cmds.confirmDialog(title=u'提示', message=u'未能找到有效的变换节点！', button=[u'确定'])
            return

        # 冻结变换 + 删除历史
        cmds.select(all_transforms, replace=True)
        cmds.makeIdentity(apply=True, translate=True, rotate=True, scale=True, normal=False)
        cmds.delete(constructionHistory=True)

        # 创建分组结构
        main_group = safe_group('ASSET_CLEAN')
        mesh_grp = safe_group('MESH_GRP', parent=main_group)
        joint_grp = safe_group('JOINT_GRP', parent=main_group)
        curve_grp = safe_group('CRV_GRP', parent=main_group)
        created_groups = {main_group, mesh_grp, joint_grp, curve_grp}

        # 模型分组命名
        for i, tran in enumerate(mesh_transforms, 1):
            if cmds.objExists(tran):
                new_name = safe_rename(tran, f'MSH_{i:03d}')
                cmds.parent(new_name, mesh_grp)

        # 骨骼根节点处理
        root_joints = []
        for jnt in joint_transforms:
            if not cmds.objExists(jnt):
                continue
            parents = cmds.listRelatives(jnt, parent=True, type='transform') or []
            if not parents or cmds.objectType(parents[0]) != 'joint':
                root_joints.append(jnt)

        for i, jnt in enumerate(root_joints, 1):
            new_name = safe_rename(jnt, f'JNT_{i:03d}')
            cmds.parent(new_name, joint_grp)

        # 曲线分组命名
        for i, crv in enumerate(curve_transforms, 1):
            if cmds.objExists(crv):
                new_name = safe_rename(crv, f'CRV_{i:03d}')
                cmds.parent(new_name, curve_grp)

        # 清理空组
        all_transforms = cmds.ls(type='transform', long=False) or []
        for grp in all_transforms:
            if grp in created_groups or not cmds.objExists(grp):
                continue
            children = cmds.listRelatives(grp, children=True, fullPath=False) or []
            if not children:
                cmds.delete(grp)

        cmds.confirmDialog(
            title=u'✅ 整理完成',
            message=u'已执行：\n• 冻结变换（保留法线）\n• 删除冗余历史\n• 自动分类分组\n• 标准化命名\n• 清理无效空组',
            button=[u'太棒了！']
        )

    except Exception as e:
        error_msg = u'整理过程中发生错误：\n{}\n\n排查建议：\n1. 节点是否被锁定/引用\n2. 命名是否含特殊字符\n3. 场景是否有损坏节点'.format(str(e))
        cmds.confirmDialog(title=u'❌ 处理失败', message=error_msg, button=[u'确定'])
    finally:
        cmds.undoInfo(closeChunk=True)

def create_asset_clean_tab(parent_layout):
    """创建资产整理标签页"""
    sub_layout = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=15,
        columnAlign="center",
        parent=parent_layout
    )
    
    cmds.text(label="资产全自动整理工具", font="boldLabelFont", height=30)
    cmds.separator(height=5, style='in')
    cmds.text(label=u"• 冻结变换 • 删除历史 • 自动分组 • 规范命名 • 清理空组", height=25)
    cmds.separator(height=5, style='in')
    
    cmds.button(
        label=u"✅ 开始全自动整理",
        height=55,
        backgroundColor=(0.22, 0.48, 0.96),
        command=lambda x: run_clean(),
        parent=sub_layout
    )

# ===================== 功能3：UE骨骼工具 =====================
def create_ue_skeleton(*args):
    """创建UE标准骨骼"""
    if cmds.ls(type="joint"):
        cmds.warning(u"场景中已存在骨骼，请先删除！")
        return
    
    cmds.select(cl=True)

    # 躯干骨骼
    root        = cmds.joint(name="root",        p=[0, 0, 0])
    pelvis      = cmds.joint(name="pelvis",      p=[0, 15, 0])
    spine_01    = cmds.joint(name="spine_01",    p=[0, 22, 0])
    spine_02    = cmds.joint(name="spine_02",    p=[0, 30, 0])
    spine_03    = cmds.joint(name="spine_03",    p=[0, 38, 0])
    neck_01     = cmds.joint(name="neck_01",     p=[0, 44, 0])
    head        = cmds.joint(name="head",        p=[0, 50, 0])
    head_end    = cmds.joint(name="head_end",    p=[0, 55, 0])

    # 左手
    cmds.select(spine_03)
    clavicle_l  = cmds.joint(name="clavicle_l",  p=[-3, 42, 0])
    upperarm_l  = cmds.joint(name="upperarm_l",  p=[-10, 40, 0])
    forearm_l   = cmds.joint(name="forearm_l",   p=[-22, 38, 0])
    hand_l      = cmds.joint(name="hand_l",      p=[-34, 36, 0])

    # 左手手指
    l_fingers = [
        ("thumb",     [-33, 34, 2.0]),
        ("index",     [-36, 36, 1.5]),
        ("middle",    [-36, 36, 0.5]),
        ("ring",      [-36, 36, -0.5]),
        ("pinky",     [-35, 36, -1.5])
    ]
    for name, pos in l_fingers:
        cmds.select(hand_l)
        cmds.joint(name="{0}_01_l".format(name), p=pos)
        cmds.joint(name="{0}_02_l".format(name), p=[pos[0]-4, pos[1], pos[2]])
        cmds.joint(name="{0}_03_l".format(name), p=[pos[0]-8, pos[1], pos[2]])

    # 左腿
    cmds.select(pelvis)
    thigh_l     = cmds.joint(name="thigh_l",     p=[-4, 12, 0])
    calf_l      = cmds.joint(name="calf_l",      p=[-6, -6, 0])
    foot_l      = cmds.joint(name="foot_l",      p=[-6, -24, 0])
    ball_l      = cmds.joint(name="ball_l",      p=[-6, -28, 4])
    foot_end_l  = cmds.joint(name="foot_end_l",  p=[-6, -30, 8])

    # 右手
    cmds.select(spine_03)
    clavicle_r  = cmds.joint(name="clavicle_r",  p=[3, 42, 0])
    upperarm_r  = cmds.joint(name="upperarm_r",  p=[10, 40, 0])
    forearm_r   = cmds.joint(name="forearm_r",   p=[22, 38, 0])
    hand_r      = cmds.joint(name="hand_r",      p=[34, 36, 0])

    # 右手手指
    r_fingers = [
        ("thumb",     [33, 34, 2.0]),
        ("index",     [36, 36, 1.5]),
        ("middle",    [36, 36, 0.5]),
        ("ring",      [36, 36, -0.5]),
        ("pinky",     [35, 36, -1.5])
    ]
    for name, pos in r_fingers:
        cmds.select(hand_r)
        cmds.joint(name="{0}_01_r".format(name), p=pos)
        cmds.joint(name="{0}_02_r".format(name), p=[pos[0]+4, pos[1], pos[2]])
        cmds.joint(name="{0}_03_r".format(name), p=[pos[0]+8, pos[1], pos[2]])

    # 右腿
    cmds.select(pelvis)
    thigh_r     = cmds.joint(name="thigh_r",     p=[4, 12, 0])
    calf_r      = cmds.joint(name="calf_r",      p=[6, -6, 0])
    foot_r      = cmds.joint(name="foot_r",      p=[6, -24, 0])
    ball_r      = cmds.joint(name="ball_r",      p=[6, -28, 4])
    foot_end_r  = cmds.joint(name="foot_end_r",  p=[6, -30, 8])

    cmds.select(root)
    cmds.confirmDialog(title=u"完成", message=u"UE标准骨骼创建完成！", button=[u"OK"])

def smooth_bind_ue(*args):
    """蒙皮绑定（UE专用）"""
    sel = cmds.ls(selection=True, long=True)
    if len(sel) < 2:
        cmds.warning(u"请先选择模型 + root骨骼！")
        return
    mesh = sel[0]
    root_jnt = sel[1]
    try:
        cmds.skinCluster(root_jnt, mesh, toSelectedBones=True, 
                         bindMethod=0, smoothWeights=1, maximumInfluences=4, 
                         dropoffRate=0.1, name="skinCluster_UE")
        cmds.confirmDialog(title=u"完成", message=u"蒙皮绑定完成！", button=[u"OK"])
    except:
        cmds.warning(u"蒙皮绑定失败，请检查选择！")

def freeze_clean_ue(*args):
    """冻结清理（UE专用）"""
    sel = cmds.ls(selection=True)
    if not sel:
        cmds.warning(u"请先选择需要处理的对象！")
        return
    count = 0
    for obj in sel:
        if cmds.objectType(obj) == "joint":
            continue
        cmds.makeIdentity(obj, apply=True, t=1, r=1, s=1, n=0)
        cmds.delete(obj, ch=True)
        count +=1
    cmds.confirmDialog(title=u"完成", message=u"已处理 {0} 个对象！".format(count), button=[u"OK"])

def export_fbx_ue(*args):
    """导出UE专用FBX"""
    sel = cmds.ls(selection=True)
    if not sel:
        cmds.warning(u"请先选择root骨骼！")
        return
    
    try:
        if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
            cmds.loadPlugin("fbxmaya")
    except:
        cmds.warning(u"FBX插件加载失败！")
        return

    # 收集关联模型
    skin_clusters = cmds.ls(type="skinCluster")
    geo_list = []
    for sc in skin_clusters:
        infs = cmds.skinCluster(sc, q=True, inf=True)
        if any(j in sel for j in infs):
            geo = cmds.skinCluster(sc, q=True, geometry=True)
            if geo:
                geo_list.extend(geo)
    
    final = list(set(sel + geo_list))
    cmds.select(final)

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    path = os.path.join(desktop, "UE_Character_Final.fbx")

    cmds.FBXResetExport()
    cmds.FBXExportSmoothingGroups("-v", True)
    cmds.FBXExportSmoothMesh("-v", False)
    cmds.FBXExportAnimation("-v", False)
    cmds.FBXExportShapeKeys("-v", False)
    cmds.FBXExportSkeletonDefinitions("-v", True)
    cmds.FBXExportUpAxis("-v", "z")
    cmds.FBXExport("-f", path, "-s")

    cmds.confirmDialog(title=u"导出完成", message=u"FBX已导出至：\n{0}".format(path), button=[u"OK"])

def create_ue_skeleton_tab(parent_layout):
    """创建UE骨骼工具标签页"""
    sub_layout = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
        columnAlign="center",
        parent=parent_layout
    )
    
    cmds.text(label="UE骨骼工具 V3.0", font="boldLabelFont", height=30)
    cmds.separator(height=5, style='in')
    
    # UE工具按钮组
    cmds.button(label=u"1. 创建适配UE的骨骼", command=create_ue_skeleton, height=40, bgc=[0.22, 0.38, 0.65], parent=sub_layout)
    cmds.button(label=u"2. 模型蒙皮+Root绑定", command=smooth_bind_ue, height=40, bgc=[0.22, 0.50, 0.32], parent=sub_layout)
    cmds.button(label=u"3. 冻结+清理冗余历史", command=freeze_clean_ue, height=40, bgc=[0.55, 0.28, 0.22], parent=sub_layout)
    cmds.button(label=u"4. 导出UE专用FBX", command=export_fbx_ue, height=40, bgc=[0.65, 0.38, 0.20], parent=sub_layout)
    
    cmds.separator(height=8, style='in')
    cmds.text(u"适配UE mannequin标准 | 自动关联蒙皮模型", font="tinyBoldLabelFont", height=20)

# ===================== 主UI创建（彻底修复tabLayout问题） =====================
def create_main_ui():
    """创建主UI窗口（整合所有功能）"""
    # 关闭已存在的窗口
    if cmds.window(MAIN_WINDOW, exists=True):
        cmds.deleteUI(MAIN_WINDOW, window=True)
    
    # 创建主窗口
    window = cmds.window(
        MAIN_WINDOW,
        title="Maya资产工具套件 V1.0",
        widthHeight=(420, 520),
        sizeable=True,
        menuBar=False
    )
    
    # 主布局：标签页（简化写法，去掉容易出错的tabWidth自定义）
    tab_layout = cmds.tabLayout(parent=window)
    
    # 标签1：骨骼方向轴
    joint_tab = cmds.columnLayout(parent=tab_layout)
    create_joint_orientation_tab(joint_tab)
    
    # 标签2：资产整理
    clean_tab = cmds.columnLayout(parent=tab_layout)
    create_asset_clean_tab(clean_tab)
    
    # 标签3：UE骨骼工具
    ue_tab = cmds.columnLayout(parent=tab_layout)
    create_ue_skeleton_tab(ue_tab)
    
    # 设置标签页名称（兼容所有Maya版本的写法）
    cmds.tabLayout(tab_layout, edit=True, 
                   tabLabel=[(joint_tab, "骨骼方向轴"), 
                             (clean_tab, "资产整理"), 
                             (ue_tab, "UE骨骼工具")])
    
    # 显示窗口并居中
    cmds.showWindow(window)
    mel.eval('centerWindow "' + MAIN_WINDOW + '"')

# ===================== 运行主UI =====================
if __name__ == "__main__":
    create_main_ui()