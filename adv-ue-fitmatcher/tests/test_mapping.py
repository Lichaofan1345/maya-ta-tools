"""纯 Python 映射契约测试（无 Maya 依赖）。先写测试，后改实现。"""
import importlib.util
from pathlib import Path
import unittest

PRODUCT = Path(__file__).resolve().parents[1] / "adv_ue_deform_link.py"
spec = importlib.util.spec_from_file_location("deform_link", PRODUCT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Mapping(unittest.TestCase):
    def test_count_and_uniqueness(self):
        rows = m.deform_mapping()
        self.assertEqual(len(rows), 79)
        self.assertEqual(len({r["adv"] for r in rows}), 79)
        self.assertEqual(len({r["ue"] for r in rows}), 79)

    def test_twist_reverse_rules(self):
        pairs = {r["adv"]: r["ue"] for r in m.deform_mapping()}
        # 肩/髋正序
        self.assertEqual(pairs["ShoulderPart1_R"], "upperarm_twist_01_r")
        self.assertEqual(pairs["HipPart2_L"], "thigh_twist_02_l")
        # 肘/膝反序（6.915 asExportRenameToUnreal 24800-24821）
        self.assertEqual(pairs["ElbowPart1_R"], "lowerarm_twist_02_r")
        self.assertEqual(pairs["ElbowPart2_R"], "lowerarm_twist_01_r")
        self.assertEqual(pairs["KneePart1_L"], "calf_twist_02_l")
        self.assertEqual(pairs["KneePart2_L"], "calf_twist_01_l")
        self.assertEqual(pairs["NeckPart1_M"], "neck_02")

    def test_main_bones_and_fingers(self):
        pairs = {r["adv"]: r["ue"] for r in m.deform_mapping()}
        self.assertEqual(pairs["Root_M"], "pelvis")
        self.assertEqual(pairs["Spine5_M"], "spine_05")
        self.assertEqual(pairs["Wrist_L"], "hand_l")
        self.assertEqual(pairs["Toes_R"], "ball_r")
        self.assertEqual(pairs["IndexFinger0_R"], "index_metacarpal_r")
        self.assertEqual(pairs["ThumbFinger3_L"], "thumb_03_l")
        self.assertEqual(sum(1 for r in m.deform_mapping() if r["group"] == "finger"), 38)

    def test_spine_hint_never_says_change_spine_count(self):
        # 长期约定：脊柱缺节只能提示 ReSample 加一节或接受跳过，
        # 绝不能引导用户去改 ADV 脊柱节数（会破坏映射）。
        hint = m._adv_hint({"adv": "Spine4_M", "ue": "spine_04", "group": "spine"}, True)
        assert "4 节" in hint and "ReSample" in hint
        for bad in ("改成 5 节", "改为 5 节", "调成 5 节", "设为 5 节", "改成 1 节"):
            assert bad not in hint, bad
        # 扭转骨要给出可操作建议
        twist = m._adv_hint({"adv": "KneePart1_R", "ue": "calf_twist_02_r",
                             "group": "twist"}, False)
        assert "Twist Joints" in twist

    def test_no_fullword_replace_bug(self):
        # 经典 bug：全文 r->l 会把 ring 变 ling。映射必须只换后缀。
        ues = {r["ue"] for r in m.deform_mapping()}
        self.assertIn("ring_01_l", ues)
        self.assertNotIn("ling_01_l", ues)


if __name__ == "__main__":
    unittest.main()
