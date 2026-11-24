import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beam_designer import (
    BeamInput,
    CATALOG,
    PointLoad,
    Section,
    UniformLoad,
    beam_from_document,
    beam_to_document,
    calculation_sheet_pdf,
    csv_rows,
    solve_beam,
)


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
        self.assertAlmostEqual(result.fixed_moment_kip_ft, 10.0, places=6)
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

    def test_custom_overhang_file_round_trip(self):
        beam = BeamInput(
            support="overhang",
            span_ft=18,
            left_overhang_ft=3,
            right_overhang_ft=4,
            section=Section("Custom timber", 20, 90, 1600, 0.9, 0.02, "Test source"),
            loads=(
                PointLoad(2.5, 2, -1),
                UniformLoad(0.15, 3, 21, 1),
            ),
            include_self_weight=False,
            deflection_limit_denominator=240,
        )
        document = beam_to_document(beam, "Custom", "metric", "Both")
        restored, unit, section_name, side = beam_from_document(document)

        self.assertEqual(unit, "metric")
        self.assertEqual(section_name, "Custom")
        self.assertEqual(side, "Both")
        self.assertEqual(restored.support, beam.support)
        self.assertAlmostEqual(restored.span_ft, beam.span_ft)
        self.assertAlmostEqual(restored.left_overhang_ft, beam.left_overhang_ft)
        self.assertAlmostEqual(restored.right_overhang_ft, beam.right_overhang_ft)
        self.assertEqual(restored.section, beam.section)
        self.assertEqual(restored.loads, beam.loads)
        self.assertEqual(restored.deflection_limit_denominator, 240)

    def test_csv_has_unit_headers_and_metric_values(self):
        beam = BeamInput(
            support="simple",
            span_ft=20,
            section=CATALOG["W12x26"],
            loads=(UniformLoad(1, 0, 20, -1),),
            include_self_weight=False,
        )
        result = solve_beam(beam)
        headers, rows = csv_rows(result, "metric")

        self.assertEqual(headers, ["x (m)", "V (kN)", "M (kN-m)", "deflection (mm)"])
        self.assertAlmostEqual(rows[-1][0], 20 / 3.280839895013123)
        self.assertEqual(len(rows), len(result.xs_ft))

    def test_calculation_sheet_is_a_pdf_with_inputs_and_diagrams(self):
        beam = BeamInput(
            support="simple",
            span_ft=20,
            section=CATALOG["W12x26"],
            loads=(UniformLoad(1, 0, 20, -1),),
            include_self_weight=False,
        )
        pdf = calculation_sheet_pdf(beam, solve_beam(beam), "us")

        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"BEAM INPUTS", pdf)
        self.assertIn(b"RESULTS AND NUMERICAL CHECKS", pdf)
        self.assertIn(b"SHEAR V", pdf)
        self.assertIn(b"MOMENT M", pdf)
        self.assertIn(b"DEFLECTION", pdf)


if __name__ == "__main__":
    unittest.main()
