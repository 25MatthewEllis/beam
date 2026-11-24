import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beam_designer import BeamInput, CATALOG, PointLoad, UniformLoad, solve_beam


class BeamValidationTests(unittest.TestCase):
    def test_uniform_load_reference(self):
        beam = BeamInput(
            support="simple",
            span_ft=20,
            section=CATALOG["W12x26"],
            loads=(UniformLoad(1.0, 0.0, 20.0, -1),),
            include_self_weight=False,
        )
        result = solve_beam(beam)
        self.assertAlmostEqual(result.peak_moment_kip_ft, 50.0, places=2)
        self.assertAlmostEqual(result.peak_deflection_in, 0.609, places=2)

    def test_point_load_and_reactions(self):
        beam = BeamInput(
            support="simple",
            span_ft=20,
            section=CATALOG["W12x26"],
            loads=(PointLoad(10.0, 10.0, -1),),
            include_self_weight=False,
        )
        result = solve_beam(beam)
        self.assertAlmostEqual(result.reaction_a_kip, 5.0, places=6)
        self.assertAlmostEqual(result.reaction_b_kip, 5.0, places=6)
        self.assertAlmostEqual(result.peak_moment_kip_ft, 50.0, places=6)

    def test_cantilever_free_end_deflection_sign(self):
        beam = BeamInput(
            support="cantilever",
            span_ft=10,
            section=CATALOG["W12x26"],
            loads=(PointLoad(1.0, 10.0, -1),),
            include_self_weight=False,
        )
        result = solve_beam(beam)
        self.assertAlmostEqual(result.reaction_a_kip, 1.0, places=6)
        self.assertAlmostEqual(result.peak_moment_kip_ft, 10.0, places=6)
        self.assertAlmostEqual(result.peak_deflection_x_ft, 10.0, places=3)

    def test_overhang_reactions(self):
        beam = BeamInput(
            support="overhang",
            span_ft=20,
            left_overhang_ft=5,
            right_overhang_ft=5,
            section=CATALOG["W12x26"],
            loads=(PointLoad(10.0, 30.0, -1),),
            include_self_weight=False,
        )
        result = solve_beam(beam)
        self.assertTrue(math.isfinite(result.reaction_a_kip))
        self.assertTrue(math.isfinite(result.reaction_b_kip))

    def test_lumber_properties_and_custom_section(self):
        lumber = CATALOG["DFL 2x4"]
        self.assertAlmostEqual(lumber.e_ksi, 1600.0)
        self.assertAlmostEqual(lumber.fb_ksi, 0.90)
        self.assertGreater(lumber.sx_in3, 0)
        custom = BeamInput(
            support="simple",
            span_ft=10,
            section=lumber,
            loads=(UniformLoad(1.0, 0.0, 10.0, -1),),
            include_self_weight=False,
        )
        result = solve_beam(custom)
        self.assertGreater(result.peak_deflection_in, 0)


if __name__ == "__main__":
    unittest.main()
