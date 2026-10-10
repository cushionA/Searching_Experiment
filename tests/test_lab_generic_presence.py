from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
from trial_generic_presence_v1 import complementary_relation


class GenericPresenceTests(unittest.TestCase):
    def test_positive_requires_opposite_contradiction(self):
        support = {"support": .95, "conflict": .02, "unknown": .03}
        conflict = {"support": .01, "conflict": .98, "unknown": .01}
        self.assertEqual(complementary_relation(support, conflict), "present")
        self.assertEqual(complementary_relation(conflict, support), "absent")
        self.assertEqual(complementary_relation(support, support), "unknown")

    def test_weak_opposite_never_proves_presence(self):
        strong = {"support": .98, "conflict": .01, "unknown": .01}
        weak = {"support": .01, "conflict": .60, "unknown": .39}
        self.assertEqual(complementary_relation(strong, weak), "unknown")
