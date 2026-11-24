#!/usr/bin/env python3
"""Beam Designer - a Tkinter desktop beam analysis application."""

from __future__ import annotations

import csv
import json
import math
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, ttk
from typing import Sequence


FT_PER_M = 3.280839895013123
KIP_PER_KN = 0.22480894387096
KSI_PER_MPA = 1.0 / 6.894757293168
MM_PER_IN = 25.4
MM3_PER_IN3 = 25.4 ** 3
MM4_PER_IN4 = 25.4 ** 4


@dataclass
class Section:
    name: str
    sx_in3: float
    ix_in4: float
    e_ksi: float
    fb_ksi: float | None
    weight_kip_ft: float
    source: str


@dataclass(frozen=True)
class PointLoad:
    magnitude_kip: float
    position_ft: float
    direction: int = -1


@dataclass(frozen=True)
class UniformLoad:
    intensity_kip_ft: float
    start_ft: float
    end_ft: float
    direction: int = -1


Load = PointLoad | UniformLoad


CATALOG = {
    "W12x26": Section(
        "W12x26 - A992 steel",
        33.4,
        204.0,
        29000.0,
        None,
        0.026,
        "AISC Shapes Database v16.0 for geometry and nominal weight. "
        "No code-specific Fb is supplied.",
    ),
}

LUMBER_DIMS = {
    "DFL 2x4": (1.5, 3.5),
    "DFL 2x6": (1.5, 5.5),
    "DFL 2x8": (1.5, 7.25),
    "DFL 2x10": (1.5, 9.25),
    "DFL 2x12": (1.5, 11.25),
}
DFL_DENSITY_PCF = 62.4 * 0.50
for _name, (_width, _depth) in LUMBER_DIMS.items():
    _sx = _width * _depth * _depth / 6.0
    _ix = _width * _depth ** 3 / 12.0
    CATALOG[_name] = Section(
        f"Douglas Fir-Larch No. 2 nominal {_name.removeprefix('DFL ')}",
        _sx,
        _ix,
        1600.0,
        0.90,
        DFL_DENSITY_PCF * _width * _depth / 144.0 / 1000.0,
        "AWC 2024 NDS Supplement Table 4A and 2026-09-17 addendum reference "
        "values: E = 1,600 ksi and Fb = 0.90 ksi. Sx/Ix use assumed dressed dimensions.",
    )


@dataclass
class BeamResult:
    xs_ft: list[float]
    shear_kip: list[float]
    moment_kip_ft: list[float]
    deflection_in: list[float]
    reaction_a_kip: float
    reaction_b_kip: float | None
    fixed_moment_kip_ft: float | None
    peak_shear_kip: float
    peak_moment_kip_ft: float
    peak_moment_x_ft: float
    peak_deflection_in: float
    peak_deflection_x_ft: float
    peak_stress_ksi: float


@dataclass
class BeamInput:
    support: str
    span_ft: float
    left_overhang_ft: float = 0.0
    right_overhang_ft: float = 0.0
    section: Section = field(default_factory=lambda: CATALOG["W12x26"])
    loads: tuple[Load, ...] = ()
    include_self_weight: bool = True
    deflection_limit_denominator: float = 360.0


def support_positions(beam: BeamInput) -> tuple[float, float | None]:
    if beam.support == "overhang":
        left = beam.left_overhang_ft
        return left, left + beam.span_ft
    if beam.support == "cantilever":
        return 0.0, None
    return 0.0, beam.span_ft


def beam_length_ft(beam: BeamInput) -> float:
    if beam.support == "overhang":
        return beam.left_overhang_ft + beam.span_ft + beam.right_overhang_ft
    return beam.span_ft


def validate_beam(beam: BeamInput) -> list[str]:
    errors = []
    length = beam_length_ft(beam)
    if not math.isfinite(beam.span_ft) or beam.span_ft <= 0:
        errors.append("Enter a span greater than zero.")
    if not math.isfinite(length) or length <= 0:
        errors.append("Total beam length must be finite and greater than zero.")
    if beam.support == "overhang":
        if (not math.isfinite(beam.left_overhang_ft) or beam.left_overhang_ft < 0
                or not math.isfinite(beam.right_overhang_ft) or beam.right_overhang_ft < 0):
            errors.append("Overhang lengths must be finite and nonnegative.")
        elif beam.left_overhang_ft + beam.right_overhang_ft <= 0:
            errors.append("Set at least one overhang length greater than zero.")
    if not math.isfinite(beam.section.sx_in3) or beam.section.sx_in3 <= 0:
        errors.append("Section modulus Sx must be greater than zero.")
    if not math.isfinite(beam.section.ix_in4) or beam.section.ix_in4 <= 0:
        errors.append("Moment of inertia Ix must be greater than zero.")
    if not math.isfinite(beam.section.e_ksi) or beam.section.e_ksi <= 0:
        errors.append("Elastic modulus E must be greater than zero.")
    if beam.section.fb_ksi is not None and (
        not math.isfinite(beam.section.fb_ksi) or beam.section.fb_ksi <= 0
    ):
        errors.append("Supplied Fb must be greater than zero.")
    if not math.isfinite(beam.section.weight_kip_ft) or beam.section.weight_kip_ft < 0:
        errors.append("Section weight per length must be nonnegative.")
    if (not math.isfinite(beam.deflection_limit_denominator)
            or beam.deflection_limit_denominator <= 0):
        errors.append("Deflection limit denominator must be greater than zero.")
    for i, load in enumerate(beam.loads, 1):
        name = f"{'Point' if isinstance(load, PointLoad) else 'Uniform'} load {i}"
        if isinstance(load, PointLoad):
            if not math.isfinite(load.magnitude_kip) or load.magnitude_kip <= 0:
                errors.append(f"{name}: enter a magnitude greater than zero.")
            if (not math.isfinite(load.position_ft) or load.position_ft < 0
                    or load.position_ft > length):
                errors.append(f"{name}: position must be within the beam.")
        else:
            if not math.isfinite(load.intensity_kip_ft) or load.intensity_kip_ft <= 0:
                errors.append(f"{name}: enter an intensity greater than zero.")
            if (not math.isfinite(load.start_ft) or not math.isfinite(load.end_ft)
                    or load.start_ft < 0 or load.end_ft > length
                    or load.end_ft <= load.start_ft):
                errors.append(f"{name}: interval must be within the beam.")
    return errors


def beam_to_document(
    beam: BeamInput,
    section_key: str,
    unit: str,
    overhang_side: str,
) -> dict:
    if unit not in ("us", "metric"):
        raise ValueError("Unsupported unit system.")
    if section_key in CATALOG:
        section = {"catalog_key": section_key}
    else:
        section = {
            "catalog_key": None,
            "name": beam.section.name,
            "sx_in3": beam.section.sx_in3,
            "ix_in4": beam.section.ix_in4,
            "e_ksi": beam.section.e_ksi,
            "fb_ksi": beam.section.fb_ksi,
            "weight_kip_ft": beam.section.weight_kip_ft,
            "source": beam.section.source,
        }
    loads = []
    for load in beam.loads:
        if isinstance(load, PointLoad):
            loads.append({
                "type": "point",
                "magnitude_kip": load.magnitude_kip,
                "position_ft": load.position_ft,
                "direction": load.direction,
            })
        else:
            loads.append({
                "type": "uniform",
                "intensity_kip_ft": load.intensity_kip_ft,
                "start_ft": load.start_ft,
                "end_ft": load.end_ft,
                "direction": load.direction,
            })
    document = {
        "format": "beam-designer",
        "version": 1,
        "unit": unit,
        "support": beam.support,
        "span_ft": beam.span_ft,
        "left_overhang_ft": beam.left_overhang_ft,
        "right_overhang_ft": beam.right_overhang_ft,
        "overhang_side": overhang_side,
        "include_self_weight": beam.include_self_weight,
        "deflection_limit_denominator": beam.deflection_limit_denominator,
        "section": section,
        "loads": loads,
    }
    return document


def beam_from_document(document: object) -> tuple[BeamInput, str, str, str]:
    if not isinstance(document, dict) or document.get("format") != "beam-designer":
        raise ValueError("This is not a Beam Designer file.")
    if type(document.get("version")) is not int or document.get("version") != 1:
        raise ValueError("Unsupported Beam Designer file version.")

    unit = document.get("unit")
    if unit not in ("us", "metric"):
        raise ValueError("Saved unit system is invalid.")
    support = document.get("support")
    if support not in ("simple", "cantilever", "overhang"):
        raise ValueError("Saved support type is invalid.")

    def number(value: object, label: str, optional: bool = False) -> float | None:
        if value is None and optional:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Saved {label} must be a number.")
        try:
            result = float(value)
        except OverflowError as exc:
            raise ValueError(f"Saved {label} must be finite.") from exc
        if not math.isfinite(result):
            raise ValueError(f"Saved {label} must be finite.")
        return result

    section_data = document.get("section")
    if not isinstance(section_data, dict):
        raise ValueError("Saved section data is missing.")
    section_key = section_data.get("catalog_key")
    if isinstance(section_key, str):
        if section_key not in CATALOG:
            raise ValueError("Saved catalog section is not available.")
        section = CATALOG[section_key]
        section_name = section_key
    elif section_key is None:
        name = section_data.get("name")
        source = section_data.get("source", "Custom properties supplied by the user.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Saved custom section label is missing.")
        if not isinstance(source, str):
            raise ValueError("Saved custom section source must be text.")
        section = Section(
            name,
            number(section_data.get("sx_in3"), "Sx"),
            number(section_data.get("ix_in4"), "Ix"),
            number(section_data.get("e_ksi"), "E"),
            number(section_data.get("fb_ksi"), "Fb", optional=True),
            number(section_data.get("weight_kip_ft"), "weight per length"),
            source,
        )
        section_name = "Custom"
    else:
        raise ValueError("Saved section key is invalid.")

    raw_loads = document.get("loads")
    if not isinstance(raw_loads, list):
        raise ValueError("Saved loads must be a list.")
    loads: list[Load] = []
    for index, item in enumerate(raw_loads, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Saved load {index} is invalid.")
        direction = item.get("direction")
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError(f"Saved load {index} direction is invalid.")
        if item.get("type") == "point":
            loads.append(PointLoad(
                number(item.get("magnitude_kip"), f"load {index} magnitude"),
                number(item.get("position_ft"), f"load {index} position"),
                direction,
            ))
        elif item.get("type") == "uniform":
            loads.append(UniformLoad(
                number(item.get("intensity_kip_ft"), f"load {index} intensity"),
                number(item.get("start_ft"), f"load {index} start"),
                number(item.get("end_ft"), f"load {index} end"),
                direction,
            ))
        else:
            raise ValueError(f"Saved load {index} type is invalid.")

    include_self_weight = document.get("include_self_weight")
    if not isinstance(include_self_weight, bool):
        raise ValueError("Saved self-weight setting is invalid.")
    beam = BeamInput(
        support=support,
        span_ft=number(document.get("span_ft"), "span"),
        left_overhang_ft=number(document.get("left_overhang_ft", 0), "left overhang"),
        right_overhang_ft=number(document.get("right_overhang_ft", 0), "right overhang"),
        section=section,
        loads=tuple(loads),
        include_self_weight=include_self_weight,
        deflection_limit_denominator=number(
            document.get("deflection_limit_denominator"), "deflection limit"
        ),
    )
    errors = validate_beam(beam)
    if errors:
        raise ValueError("Saved beam inputs are invalid:\n" + "\n".join(errors))
    side = document.get("overhang_side", "Right")
    if side not in ("Left", "Right", "Both"):
        raise ValueError("Saved overhang side is invalid.")
    return beam, unit, section_name, side


def csv_rows(result: BeamResult, unit: str) -> tuple[list[str], list[list[float]]]:
    if unit == "metric":
        headers = ["x (m)", "V (kN)", "M (kN-m)", "deflection (mm)"]
        rows = [
            [
                x / FT_PER_M,
                v / KIP_PER_KN,
                m / (KIP_PER_KN * FT_PER_M),
                d * MM_PER_IN,
            ]
            for x, v, m, d in zip(
                result.xs_ft, result.shear_kip, result.moment_kip_ft, result.deflection_in
            )
        ]
    else:
        headers = ["x (ft)", "V (kip)", "M (kip-ft)", "deflection (in)"]
        rows = [
            list(values)
            for values in zip(
                result.xs_ft, result.shear_kip, result.moment_kip_ft, result.deflection_in
            )
        ]
    return headers, rows


def _pdf_safe_text(value: object) -> str:
    text = str(value).encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_text(x: float, y: float, text: object, size: float = 10) -> str:
    return f"BT /F1 {size:.1f} Tf {x:.1f} {y:.1f} Td ({_pdf_safe_text(text)}) Tj ET\n"


def _pdf_document(pages: Sequence[str]) -> bytes:
    objects: list[bytes | None] = [None]

    def add(data: bytes) -> int:
        objects.append(data)
        return len(objects) - 1

    catalog_id = add(b"")
    pages_id = add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for page in pages:
        stream = page.encode("ascii")
        content_id = add(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii")
        ))
    objects[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects[1:], 1):
        assert obj is not None
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects)} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def calculation_sheet_pdf(beam: BeamInput, result: BeamResult, unit: str) -> bytes:
    length_unit = "m" if unit == "metric" else "ft"
    force_unit = "kN" if unit == "metric" else "kip"
    moment_unit = "kN-m" if unit == "metric" else "kip-ft"
    deflection_unit = "mm" if unit == "metric" else "in"
    stress_unit = "MPa" if unit == "metric" else "ksi"
    display_length = (lambda x: x / FT_PER_M) if unit == "metric" else (lambda x: x)
    display_force = (lambda x: x / KIP_PER_KN) if unit == "metric" else (lambda x: x)
    display_moment = (
        (lambda x: x / (KIP_PER_KN * FT_PER_M)) if unit == "metric" else (lambda x: x)
    )
    display_deflection = (lambda x: x * MM_PER_IN) if unit == "metric" else (lambda x: x)
    display_stress = (lambda x: x / KSI_PER_MPA) if unit == "metric" else (lambda x: x)
    display_sx = (lambda x: x * MM3_PER_IN3) if unit == "metric" else (lambda x: x)
    display_ix = (lambda x: x * MM4_PER_IN4) if unit == "metric" else (lambda x: x)
    sx_unit = "mm^3" if unit == "metric" else "in^3"
    ix_unit = "mm^4" if unit == "metric" else "in^4"
    line_unit = "kN/m" if unit == "metric" else "kip/ft"
    display_weight = (
        (lambda x: x * FT_PER_M / KIP_PER_KN) if unit == "metric" else (lambda x: x)
    )
    limit_in = beam.span_ft * 12.0 / beam.deflection_limit_denominator
    deflection_ratio = result.peak_deflection_in / limit_in

    input_pages: list[str] = []
    page = ""

    def new_input_page(continued: bool = False) -> None:
        nonlocal page
        page = ""
        page += _pdf_text(48, 752, "BEAM DESIGNER - CALCULATION SHEET", 16)
        page += _pdf_text(48, 734, "Educational / preliminary only - not for approving real construction.", 9)
        page += _pdf_text(48, 708, "BEAM INPUTS" + (" (continued)" if continued else ""), 12)
        input_pages.append("")

    new_input_page()
    current_y = 687

    def input_row(text: str) -> None:
        nonlocal page, current_y
        if current_y < 55:
            input_pages[-1] = page
            new_input_page(True)
            current_y = 687
        page += _pdf_text(54, current_y, text[:105], 9)
        current_y -= 14

    support_names = {"simple": "Simply supported", "cantilever": "Cantilever", "overhang": "Overhanging"}
    input_row(f"Support: {support_names[beam.support]}")
    input_row(f"Support span: {display_length(beam.span_ft):.4g} {length_unit}")
    if beam.support == "overhang":
        input_row(
            f"Overhangs: left {display_length(beam.left_overhang_ft):.4g} {length_unit}; "
            f"right {display_length(beam.right_overhang_ft):.4g} {length_unit}"
        )
    input_row(f"Section: {beam.section.name}")
    input_row(
        f"Sx {display_sx(beam.section.sx_in3):.4g} {sx_unit}; "
        f"Ix {display_ix(beam.section.ix_in4):.4g} {ix_unit}; "
        f"E {display_stress(beam.section.e_ksi):.4g} {stress_unit}"
    )
    input_row(
        "Fb: " + (
            f"{display_stress(beam.section.fb_ksi):.4g} {stress_unit}"
            if beam.section.fb_ksi is not None else "not supplied"
        )
    )
    input_row(
        "Self-weight: " + (
            f"included ({display_weight(beam.section.weight_kip_ft):.4g} {line_unit})"
            if beam.include_self_weight else "excluded"
        )
    )
    input_row(f"Deflection limit: L/{beam.deflection_limit_denominator:g}")
    input_row(f"Applied loads: {len(beam.loads)}")
    for index, load in enumerate(beam.loads, 1):
        direction = "up" if load.direction > 0 else "down"
        if isinstance(load, PointLoad):
            magnitude = display_force(load.magnitude_kip)
            magnitude_unit = force_unit
            line = (
                f"Point {index}: {magnitude:.4g} {magnitude_unit} {direction} at "
                f"{display_length(load.position_ft):.4g} {length_unit} from left"
            )
        else:
            magnitude = (
                load.intensity_kip_ft / (KIP_PER_KN / FT_PER_M)
                if unit == "metric" else load.intensity_kip_ft
            )
            magnitude_unit = "kN/m" if unit == "metric" else "kip/ft"
            line = (
                f"Uniform {index}: {magnitude:.4g} {magnitude_unit} {direction}, "
                f"{display_length(load.start_ft):.4g}-{display_length(load.end_ft):.4g} {length_unit}"
            )
        input_row(line)
    input_pages[-1] = page

    summary = _pdf_text(48, 752, "RESULTS AND NUMERICAL CHECKS", 16)
    summary += _pdf_text(48, 730, "Numerical references only; no overall pass/fail verdict.", 9)
    summary += _pdf_text(48, 694, "SUPPORT REACTIONS", 12)
    summary += _pdf_text(
        54, 674,
        f"Reaction A: {display_force(result.reaction_a_kip):.4g} {force_unit}", 10,
    )
    if result.reaction_b_kip is not None:
        reaction_b = f"Reaction B: {display_force(result.reaction_b_kip):.4g} {force_unit}"
    else:
        reaction_b = (
            f"Fixed-end moment reaction: {display_moment(result.fixed_moment_kip_ft or 0):.4g} "
            f"{moment_unit}"
        )
    summary += _pdf_text(54, 658, reaction_b, 10)
    summary += _pdf_text(48, 624, "RESPONSE", 12)
    summary += _pdf_text(
        54, 604,
        f"Maximum |V|: {display_force(result.peak_shear_kip):.4g} {force_unit}", 10,
    )
    summary += _pdf_text(
        54, 588,
        f"Maximum |M|: {display_moment(result.peak_moment_kip_ft):.4g} {moment_unit}", 10,
    )
    summary += _pdf_text(
        54, 572,
        f"Maximum |deflection|: {display_deflection(result.peak_deflection_in):.4g} "
        f"{deflection_unit} at {display_length(result.peak_deflection_x_ft):.4g} {length_unit}", 10,
    )
    summary += _pdf_text(
        54, 556,
        f"Maximum bending stress: {display_stress(result.peak_stress_ksi):.4g} {stress_unit}", 10,
    )
    summary += _pdf_text(48, 520, "COMPARISONS", 12)
    summary += _pdf_text(
        54, 500,
        f"Deflection reference L/{beam.deflection_limit_denominator:g}: "
        f"{display_deflection(limit_in):.4g} {deflection_unit}; ratio {deflection_ratio:.4g}", 10,
    )
    if beam.section.fb_ksi is not None and beam.section.fb_ksi > 0:
        stress_ratio = result.peak_stress_ksi / beam.section.fb_ksi
        summary += _pdf_text(
            54, 484,
            f"Supplied Fb: {display_stress(beam.section.fb_ksi):.4g} {stress_unit}; "
            f"stress ratio {stress_ratio:.4g}", 10,
        )
    else:
        summary += _pdf_text(54, 484, "Supplied Fb: unavailable; stress ratio not calculated.", 10)
    summary += _pdf_text(
        48, 40,
        "Educational / preliminary only. Does not check capacity, buckling, connections, or code requirements.",
        8,
    )

    charts = _pdf_text(48, 752, "BEAM RESPONSE DIAGRAMS", 16)
    chart_specs = [
        ("SHEAR V", result.shear_kip, force_unit, display_force, (0.25, 0.37, 0.43)),
        ("MOMENT M", result.moment_kip_ft, moment_unit, display_moment, (0.65, 0.31, 0.14)),
        ("DEFLECTION", result.deflection_in, deflection_unit, display_deflection, (0.35, 0.43, 0.20)),
    ]
    x_left, x_right = 65.0, 545.0
    length = beam_length_ft(beam)
    for chart_index, (title, raw_values, value_unit, convert, color) in enumerate(chart_specs):
        box_top = 720.0 - chart_index * 225.0
        box_bottom = box_top - 195.0
        plot_left, plot_right = x_left + 40, x_right - 12
        plot_bottom, plot_top = box_bottom + 25, box_top - 35
        values = [convert(value) for value in raw_values]
        low = min(0.0, *values)
        high = max(0.0, *values)
        if high == low:
            high, low = max(1.0, high), min(-1.0, low)
        padding = (high - low) * 0.1
        low -= padding
        high += padding
        x_map = lambda x: plot_left + x / length * (plot_right - plot_left)
        y_map = lambda value: plot_bottom + (value - low) / (high - low) * (plot_top - plot_bottom)
        zero_y = y_map(0)
        charts += f"{x_left:.1f} {box_bottom:.1f} 490 195 re S\n"
        charts += _pdf_text(x_left + 8, box_top - 18, f"{title} ({value_unit})", 11)
        charts += f"0.55 0.62 0.65 RG 0.7 w {plot_left:.1f} {zero_y:.1f} m {plot_right:.1f} {zero_y:.1f} l S\n"
        curve = [(x_map(x), y_map(value)) for x, value in zip(result.xs_ft, values)]
        if curve:
            charts += f"q 0.82 0.85 0.83 rg {curve[0][0]:.2f} {zero_y:.2f} m\n"
            charts += " ".join(f"{x:.2f} {y:.2f} l" for x, y in curve)
            charts += f" {curve[-1][0]:.2f} {zero_y:.2f} l h f Q\n"
            charts += f"{color[0]:.2f} {color[1]:.2f} {color[2]:.2f} RG 1.4 w "
            charts += f"{curve[0][0]:.2f} {curve[0][1]:.2f} m\n"
            charts += " ".join(f"{x:.2f} {y:.2f} l" for x, y in curve[1:])
            charts += " S\n"
        charts += _pdf_text(10, plot_top - 3, f"{high:.3g}", 8)
        charts += _pdf_text(10, plot_bottom, f"{low:.3g}", 8)
        charts += _pdf_text(plot_left, box_bottom + 7, f"0 {length_unit}", 8)
        charts += _pdf_text(plot_right - 30, box_bottom + 7,
                            f"{display_length(length):.3g} {length_unit}", 8)
    charts += _pdf_text(
        48, 28,
        "Positive internal shear is upward on the left face; positive moment is sagging.", 8,
    )
    return _pdf_document([*input_pages, summary, charts])


def resolve_loads(beam: BeamInput) -> tuple[Load, ...]:
    loads = list(beam.loads)
    if beam.include_self_weight and beam.section.weight_kip_ft > 0:
        loads.append(UniformLoad(
            beam.section.weight_kip_ft,
            0.0,
            beam_length_ft(beam),
            -1,
        ))
    return tuple(loads)


def solve_beam(beam: BeamInput) -> BeamResult:
    """Solve statics and linearly integrate Euler-Bernoulli curvature."""
    length = beam_length_ft(beam)
    left_support, right_support = support_positions(beam)
    loads = resolve_loads(beam)

    force_sum = 0.0
    moment_sum = 0.0
    for load in loads:
        if isinstance(load, PointLoad):
            force = load.magnitude_kip * load.direction
            force_sum += force
            moment_sum += force * (load.position_ft - left_support)
        else:
            force = load.intensity_kip_ft * load.direction * (load.end_ft - load.start_ft)
            center = (load.start_ft + load.end_ft) / 2.0
            force_sum += force
            moment_sum += force * (center - left_support)

    if beam.support == "cantilever":
        reaction_a = -force_sum
        reaction_b = None
        fixed_moment = moment_sum
    else:
        assert right_support is not None
        reaction_b = -moment_sum / (right_support - left_support)
        reaction_a = -force_sum - reaction_b
        fixed_moment = None

    def shear_at(x: float) -> float:
        shear = 0.0
        if beam.support == "cantilever" or x >= left_support:
            shear += reaction_a
        if right_support is not None and x >= right_support:
            shear += reaction_b or 0.0
        for load in loads:
            if isinstance(load, PointLoad):
                if x >= load.position_ft:
                    shear += load.magnitude_kip * load.direction
            else:
                overlap = max(0.0, min(x, load.end_ft) - load.start_ft)
                shear += overlap * load.intensity_kip_ft * load.direction
        return shear

    def moment_at(x: float) -> float:
        moment = fixed_moment or 0.0
        if beam.support == "cantilever" or x >= left_support:
            moment += reaction_a * (x - left_support)
        if right_support is not None and x >= right_support:
            moment += (reaction_b or 0.0) * (x - right_support)
        for load in loads:
            if isinstance(load, PointLoad):
                moment += load.magnitude_kip * load.direction * max(0.0, x - load.position_ft)
            else:
                a = max(0.0, x - load.start_ft)
                b = max(0.0, x - load.end_ft)
                moment += load.intensity_kip_ft * load.direction * (a * a - b * b) / 2.0
        return moment

    breaks = {0.0, length}
    if right_support is not None:
        breaks.add(right_support)
    breaks.add(left_support)
    for load in loads:
        if isinstance(load, PointLoad):
            breaks.add(load.position_ft)
        else:
            breaks.add(load.start_ft)
            breaks.add(load.end_ft)
    xs = {length * i / 800.0 for i in range(801)}
    xs.update(point for point in breaks if 0.0 <= point <= length)

    # Add exact roots where uniform load makes shear pass through zero.
    ordered = sorted(x for x in breaks if 0.0 <= x <= length)
    for a, b in zip(ordered, ordered[1:]):
        if b <= a:
            continue
        mid = (a + b) / 2.0
        q = sum(
            load.intensity_kip_ft * load.direction
            for load in loads
            if isinstance(load, UniformLoad)
            and mid > load.start_ft
            and mid < load.end_ft
        )
        if abs(q) < 1e-12:
            continue
        va = shear_at(a)
        vb = shear_at(b)
        if va == 0:
            xs.add(a)
        if va * vb < 0:
            root = a - va / q
            if a < root < b:
                xs.add(root)

    xs_sorted = sorted(xs)
    shear = [shear_at(x) for x in xs_sorted]
    moment = [moment_at(x) for x in xs_sorted]
    peak_shear = max(abs(v) for v in shear)
    peak_moment_index = max(range(len(moment)), key=lambda i: abs(moment[i]))
    peak_moment = abs(moment[peak_moment_index])
    peak_moment_x = xs_sorted[peak_moment_index]

    ei = beam.section.e_ksi * beam.section.ix_in4
    curvature = [m * 12.0 / ei for m in moment]
    slope = [0.0]
    deflection = [0.0]
    for i in range(1, len(xs_sorted)):
        dx_in = (xs_sorted[i] - xs_sorted[i - 1]) * 12.0
        avg_curvature = (curvature[i - 1] + curvature[i]) / 2.0
        new_slope = slope[-1] + avg_curvature * dx_in
        slope.append(new_slope)
        deflection.append(deflection[-1] + (slope[-2] + new_slope) * dx_in / 2.0)

    if beam.support != "cantilever":
        assert left_support is not None and right_support is not None
        ia = min(range(len(xs_sorted)), key=lambda i: abs(xs_sorted[i] - left_support))
        ib = min(range(len(xs_sorted)), key=lambda i: abs(xs_sorted[i] - right_support))
        slope_correction = (deflection[ia] - deflection[ib]) / (
            (right_support - left_support) * 12.0
        )
        for i, x in enumerate(xs_sorted):
            deflection[i] += -deflection[ia] + slope_correction * (x - left_support) * 12.0

    peak_index = max(range(len(deflection)), key=lambda i: abs(deflection[i]))
    stress = abs(peak_moment) * 12.0 / beam.section.sx_in3
    return BeamResult(
        xs_sorted,
        shear,
        moment,
        deflection,
        reaction_a,
        reaction_b,
        -fixed_moment if fixed_moment is not None else None,
        peak_shear,
        peak_moment,
        peak_moment_x,
        abs(deflection[peak_index]),
        xs_sorted[peak_index],
        stress,
    )


class ToolTip:
    def __init__(self, widget: tk.Widget):
        self.widget = widget
        self.tip: tk.Toplevel | None = None

    def show(self, text: str, x: int, y: int) -> None:
        self.hide()
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x + 10}+{y + 10}")
        tk.Label(
            self.tip,
            text=text,
            bg="#243038",
            fg="#fff",
            justify="left",
            padx=8,
            pady=5,
            relief="solid",
            borderwidth=1,
        ).pack()

    def hide(self) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class BeamDesigner(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Beam Designer")
        self.geometry("1280x820")
        self.minsize(980, 640)
        self.configure(bg="#9aa2a2")

        self.unit = "us"
        self.support = "simple"
        self.section_name = "W12x26"
        self.section = CATALOG[self.section_name]
        self.include_self_weight = tk.BooleanVar(value=True)
        self.span_var = tk.StringVar(value="20")
        self.deflection_limit_var = tk.StringVar(value="360")
        self.left_overhang_var = tk.StringVar(value="0")
        self.right_overhang_var = tk.StringVar(value="5")
        self.overhang_side = "right"
        self.loads: list[dict] = [{
            "id": 1,
            "type": "uniform",
            "magnitude": "1",
            "start": "0",
            "end": "20",
            "direction": "-1",
        }]
        self.next_load_id = 2
        self.last_analysis: tuple[BeamInput, BeamResult] | None = None
        self.current_file_path: str | None = None

        self._build_style()
        self._build_ui()
        self.bind("<Configure>", lambda _event: self.after_idle(self.update_results))

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Arial Narrow", 10), background="#c49661", foreground="#252d31")
        style.configure("TFrame", background="#c49661")
        style.configure("TLabel", background="#c49661", foreground="#252d31")
        style.configure("Title.TLabel", font=("Arial Narrow", 18, "bold"))
        style.configure("Heading.TLabel", font=("Arial Narrow", 12, "bold"))
        style.configure("Top.TFrame", background="#39444a")
        style.configure("Top.TLabel", background="#39444a", foreground="#f3eee2")
        style.configure("Yellow.TButton", background="#f2c439", foreground="#252d31")

    def _build_ui(self) -> None:
        menu_bar = tk.Menu(self)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Open...", accelerator="Cmd+O", command=self.open_beam)
        file_menu.add_command(label="Save...", accelerator="Cmd+S", command=self.save_beam)
        menu_bar.add_cascade(label="File", menu=file_menu)
        self.configure(menu=menu_bar)
        self.bind("<Command-o>", lambda _event: self.open_beam())
        self.bind("<Command-s>", lambda _event: self.save_beam())
        self.bind("<Control-o>", lambda _event: self.open_beam())
        self.bind("<Control-s>", lambda _event: self.save_beam())

        top = ttk.Frame(self, style="Top.TFrame", height=72)
        top.pack(fill="x")
        top.pack_propagate(False)
        ttk.Label(top, text="BD", style="Top.TLabel", font=("Arial Narrow", 13, "bold"),
                  relief="solid", borderwidth=2, padding=(8, 7)).pack(side="left", padx=16, pady=12)
        ttk.Label(top, text="BEAM DESIGNER", style="Top.TLabel",
                  font=("Arial Narrow", 16, "bold")).pack(side="left")

        unit_frame = ttk.Frame(top, style="Top.TFrame")
        unit_frame.pack(side="right", padx=16)
        ttk.Label(unit_frame, text="UNITS", style="Top.TLabel").pack(side="left", padx=(0, 8))
        self.us_button = tk.Button(unit_frame, text="US CUSTOMARY", command=lambda: self.set_unit("us"),
                                   bg="#f2c439", fg="#252d31", relief="flat", padx=10)
        self.us_button.pack(side="left", padx=2)
        self.metric_button = tk.Button(unit_frame, text="METRIC", command=lambda: self.set_unit("metric"),
                                       bg="#68747a", fg="#f3eee2", relief="flat", padx=10)
        self.metric_button.pack(side="left", padx=2)

        intro = ttk.Frame(self)
        intro.pack(fill="x", padx=20, pady=(14, 8))
        ttk.Label(intro, text="BEAM ANALYSIS", font=("Arial Narrow", 9, "bold"),
                  foreground="#252d31").pack(anchor="w")
        ttk.Label(intro, text="Understand how your beam responds.", style="Title.TLabel").pack(side="left")
        actions = ttk.Frame(intro)
        actions.pack(side="right")
        ttk.Button(actions, text="EXPORT RESULTS CSV",
                   command=self.export_results_csv).pack(side="left", padx=3)
        ttk.Button(actions, text="CALCULATION SHEET PDF",
                   command=self.export_calculation_sheet).pack(side="left", padx=3)

        disclaimer = tk.Frame(self, bg="#f0d3a2", highlightbackground="#70502d",
                              highlightthickness=1, padx=10, pady=7)
        disclaimer.pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(disclaimer, text="Educational / preliminary only.",
                 bg="#f0d3a2", fg="#532c10", font=("Arial Narrow", 10, "bold")).pack(anchor="w")
        tk.Label(disclaimer,
                 text="This tool does not approve real construction and does not check shear capacity, buckling, "
                      "connections, load combinations, or code requirements.",
                 bg="#f0d3a2", fg="#49351f", wraplength=1150, justify="left").pack(anchor="w")

        workspace = ttk.Panedwindow(self, orient="horizontal")
        workspace.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        left_outer = ttk.Frame(workspace)
        workspace.add(left_outer, weight=1)
        left_canvas = tk.Canvas(left_outer, bg="#c49661", highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_outer, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)
        left = tk.Frame(left_canvas, bg="#c49661", padx=14, pady=12,
                        highlightbackground="#634326", highlightthickness=2)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>", lambda e: left_canvas.itemconfigure(left_window, width=e.width))

        right_outer = ttk.Frame(workspace)
        workspace.add(right_outer, weight=3)
        right_canvas = tk.Canvas(right_outer, bg="#c49661", highlightthickness=0)
        right_scroll = ttk.Scrollbar(right_outer, orient="vertical", command=right_canvas.yview)
        right_canvas.configure(yscrollcommand=right_scroll.set)
        right_scroll.pack(side="right", fill="y")
        right_canvas.pack(side="left", fill="both", expand=True)
        right = tk.Frame(right_canvas, bg="#c49661", padx=14, pady=12,
                         highlightbackground="#634326", highlightthickness=2)
        right_window = right_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda _e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>", lambda e: right_canvas.itemconfigure(right_window, width=e.width))

        self._build_input_column(left)
        self._build_result_column(right)
        self.update_results()

    def _section(self, parent: tk.Widget, title: str) -> tk.LabelFrame:
        frame = tk.LabelFrame(parent, text=title, bg="#c49661", fg="#252d31",
                              font=("Arial Narrow", 11, "bold"),
                              highlightbackground="#74502f", highlightthickness=1, padx=8, pady=8)
        frame.pack(fill="x", pady=(0, 10))
        return frame

    def _entry(self, parent: tk.Widget, label: str, var: tk.StringVar,
               unit: str = "", width: int = 12) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label).pack(side="left")
        entry = ttk.Entry(row, textvariable=var, width=width)
        entry.pack(side="right")
        if unit:
            ttk.Label(row, text=f" {unit}", width=7).pack(side="right")
        entry.bind("<KeyRelease>", lambda _e: self.after_idle(self.update_results))

    def _build_input_column(self, parent: tk.Widget) -> None:
        basic = self._section(parent, "BEAM INPUTS")
        ttk.Label(basic, text="Support condition").pack(anchor="w")
        self.support_var = tk.StringVar(value="Simply supported")
        support = ttk.Combobox(basic, textvariable=self.support_var, state="readonly",
                               values=["Simply supported", "Cantilever", "Overhanging"])
        support.pack(fill="x", pady=(2, 6))
        support.bind("<<ComboboxSelected>>", lambda _e: self.update_results())

        self.span_label = ttk.Label(basic, text="Span")
        self.span_label.pack(anchor="w")
        span_entry = ttk.Entry(basic, textvariable=self.span_var)
        span_entry.pack(fill="x", pady=(2, 6))
        span_entry.bind("<KeyRelease>", lambda _e: self.after_idle(self.update_results))

        self._entry(basic, "Deflection limit (L / n)", self.deflection_limit_var)

        self.overhang_frame = self._section(basic, "OVERHANG")
        ttk.Label(self.overhang_frame, text="Side").pack(anchor="w")
        self.overhang_side_var = tk.StringVar(value="Right")
        side = ttk.Combobox(self.overhang_side_var.master if False else self.overhang_frame,
                            textvariable=self.overhang_side_var, state="readonly",
                            values=["Left", "Right", "Both"])
        side.pack(fill="x")
        side.bind("<<ComboboxSelected>>", lambda _e: self.update_results())
        self.left_overhang_entry = ttk.Entry(self.overhang_frame, textvariable=self.left_overhang_var)
        self.right_overhang_entry = ttk.Entry(self.overhang_frame, textvariable=self.right_overhang_var)
        for entry in (self.left_overhang_entry, self.right_overhang_entry):
            entry.bind("<KeyRelease>", lambda _e: self.after_idle(self.update_results))

        section_frame = self._section(parent, "SECTION")
        ttk.Label(section_frame, text="Catalog or custom").pack(anchor="w")
        self.section_var = tk.StringVar(value="W12x26")
        section_names = list(CATALOG.keys()) + ["Custom"]
        section_combo = ttk.Combobox(section_frame, textvariable=self.section_var,
                                     state="readonly", values=section_names)
        section_combo.pack(fill="x")
        section_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_section_change())

        self.custom_frame = tk.Frame(section_frame, bg="#c49661")
        self.custom_name = tk.StringVar(value="Custom section")
        self.sx_var = tk.StringVar(value="")
        self.ix_var = tk.StringVar(value="")
        self.e_var = tk.StringVar(value="")
        self.fb_var = tk.StringVar(value="")
        self.weight_var = tk.StringVar(value="")
        self._entry(self.custom_frame, "Section label", self.custom_name)
        self._entry(self.custom_frame, "Sx", self.sx_var)
        self._entry(self.custom_frame, "Ix", self.ix_var)
        self._entry(self.custom_frame, "E", self.e_var)
        self._entry(self.custom_frame, "Fb", self.fb_var)
        self._entry(self.custom_frame, "Weight", self.weight_var)

        self.property_label = ttk.Label(section_frame, text="", wraplength=300)
        self.property_label.pack(anchor="w", pady=(6, 0))

        weight_frame = tk.Frame(parent, bg="#c49661", highlightbackground="#74502f",
                                highlightthickness=1, padx=8, pady=8)
        weight_frame.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(weight_frame, text="Include beam self-weight",
                        variable=self.include_self_weight,
                        command=self.update_results).pack(anchor="w")
        self.weight_status = ttk.Label(weight_frame, text="")
        self.weight_status.pack(anchor="w")

        loads_frame = self._section(parent, "APPLIED LOADS")
        self.loads_container = tk.Frame(loads_frame, bg="#c49661")
        self.loads_container.pack(fill="x")
        button_row = ttk.Frame(loads_frame)
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="+ Point load", command=self.add_point_load).pack(side="left")
        ttk.Button(button_row, text="+ Uniform load", command=self.add_uniform_load).pack(side="left", padx=5)
        ttk.Button(button_row, text="Load example", command=self.load_example).pack(side="right")
        self._render_loads()

        results = self._section(parent, "REACTIONS & RESULTS")
        self.result_vars = {}
        grid = tk.Frame(results, bg="#c49661")
        grid.pack(fill="x")
        labels = [
            ("left_reaction", "Left reaction"),
            ("right_reaction", "Right reaction"),
            ("peak_shear", "Maximum |shear|"),
            ("peak_moment", "Maximum |moment|"),
            ("peak_deflection", "Maximum |deflection|"),
            ("stress", "Maximum bending stress"),
        ]
        for i, (key, label) in enumerate(labels):
            box = tk.Frame(grid, bg="#e6d7bb", highlightbackground="#74502f", highlightthickness=1,
                           padx=6, pady=6)
            box.grid(row=i // 2, column=i % 2, sticky="nsew", padx=3, pady=3)
            tk.Label(box, text=label, bg="#e6d7bb", fg="#343c40",
                     font=("Arial Narrow", 8, "bold")).pack(anchor="w")
            var = tk.StringVar(value="—")
            self.result_vars[key] = var
            tk.Label(box, textvariable=var, bg="#e6d7bb", fg="#22292d",
                     font=("Arial Narrow", 12, "bold")).pack(anchor="w")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self.comparison_var = tk.StringVar(value="")
        tk.Label(results, textvariable=self.comparison_var, bg="#c49661", fg="#343c40",
                 justify="left", wraplength=300).pack(anchor="w", pady=(6, 0))

        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(parent, textvariable=self.error_var, bg="#fff5f5", fg="#812c2c",
                                    justify="left", wraplength=300, padx=8, pady=6,
                                    highlightbackground="#9c3d32", highlightthickness=1)
        self.error_label.pack(fill="x", pady=(0, 10))

    def _build_result_column(self, parent: tk.Widget) -> None:
        beam_frame = self._section(parent, "BEAM AND LOADS")
        self.beam_canvas = tk.Canvas(beam_frame, height=190, bg="#dfc9a1",
                                     highlightbackground="#74502f", highlightthickness=1)
        self.beam_canvas.pack(fill="x", pady=(0, 4))

        deflect_frame = self._section(parent, "DEFLECTED SHAPE")
        self.deflect_canvas = tk.Canvas(deflect_frame, height=150, bg="#dfc9a1",
                                        highlightbackground="#74502f", highlightthickness=1)
        self.deflect_canvas.pack(fill="x", pady=(0, 4))

        self.chart_canvases = {}
        for title, key, color in (
            ("SHEAR FORCE", "shear", "#4d6572"),
            ("BENDING MOMENT", "moment", "#a64f24"),
            ("DEFLECTION", "deflection", "#596e32"),
        ):
            frame = self._section(parent, title)
            canvas = tk.Canvas(frame, height=170, bg="#f5ecd9",
                               highlightbackground="#74502f", highlightthickness=1)
            canvas.pack(fill="x")
            self.chart_canvases[key] = canvas

    def set_unit(self, unit: str) -> None:
        if unit == self.unit:
            return
        beam = self._current_beam()
        self.unit = unit
        self.us_button.configure(bg="#f2c439" if unit == "us" else "#68747a",
                                 fg="#252d31" if unit == "us" else "#f3eee2")
        self.metric_button.configure(bg="#f2c439" if unit == "metric" else "#68747a",
                                     fg="#252d31" if unit == "metric" else "#f3eee2")

        def length_display(ft: float) -> str:
            value = ft if unit == "us" else ft / FT_PER_M
            return f"{value:.6g}"

        def force_display(kip: float) -> str:
            value = kip if unit == "us" else kip / KIP_PER_KN
            return f"{value:.6g}"

        def line_display(kip_ft: float) -> str:
            value = kip_ft if unit == "us" else kip_ft * FT_PER_M / KIP_PER_KN
            return f"{value:.6g}"

        self.span_var.set(length_display(beam.span_ft))
        self.left_overhang_var.set(length_display(beam.left_overhang_ft))
        self.right_overhang_var.set(length_display(beam.right_overhang_ft))
        # Rebuild loads from canonical values captured by _current_beam before switching.
        for i, load in enumerate(beam.loads):
            target = self.loads[i]
            if isinstance(load, PointLoad):
                target["magnitude"] = force_display(load.magnitude_kip)
                target["start"] = length_display(load.position_ft)
            else:
                target["magnitude"] = line_display(load.intensity_kip_ft)
                target["start"] = length_display(load.start_ft)
                target["end"] = length_display(load.end_ft)

        if self.section_var.get() == "Custom":
            s = beam.section
            self.sx_var.set(f"{s.sx_in3 if unit == 'us' else s.sx_in3 * MM3_PER_IN3:.6g}")
            self.ix_var.set(f"{s.ix_in4 if unit == 'us' else s.ix_in4 * MM4_PER_IN4:.6g}")
            self.e_var.set(f"{s.e_ksi if unit == 'us' else s.e_ksi / KSI_PER_MPA:.6g}")
            self.fb_var.set("" if s.fb_ksi is None else f"{s.fb_ksi if unit == 'us' else s.fb_ksi / KSI_PER_MPA:.6g}")
            self.weight_var.set(f"{s.weight_kip_ft if unit == 'us' else s.weight_kip_ft * FT_PER_M / KIP_PER_KN:.6g}")

        self.on_section_change()
        self._render_loads()
        self.update_results()

    def on_section_change(self) -> None:
        name = self.section_var.get()
        if name == "Custom":
            self.custom_frame.pack(fill="x", pady=(6, 0))
            self._sync_custom_section()
        else:
            self.custom_frame.pack_forget()
            self.section = CATALOG[name]
            self.property_label.configure(text=self.section.source)
        self.update_results()

    def _sync_custom_section(self) -> None:
        def val(var: tk.StringVar) -> float:
            try:
                value = float(var.get())
                return value
            except ValueError:
                return 0.0

        if self.unit == "us":
            sx = val(self.sx_var)
            ix = val(self.ix_var)
            e = val(self.e_var)
            fb = val(self.fb_var) if self.fb_var.get() else None
            weight = val(self.weight_var)
        else:
            sx = val(self.sx_var) / MM3_PER_IN3
            ix = val(self.ix_var) / MM4_PER_IN4
            e = val(self.e_var) * KSI_PER_MPA
            fb = val(self.fb_var) * KSI_PER_MPA if self.fb_var.get() else None
            weight = val(self.weight_var) * 0.22480894387096 / 3.280839895013123
        self.section = Section(
            self.custom_name.get() or "Custom section",
            sx,
            ix,
            e,
            fb,
            weight,
            "Custom properties supplied by the user.",
        )

    def _render_loads(self) -> None:
        for child in self.loads_container.winfo_children():
            child.destroy()
        for index, load in enumerate(self.loads, 1):
            card = tk.Frame(self.loads_container, bg="#e6d7bb", highlightbackground="#74502f",
                            highlightthickness=1, padx=6, pady=6)
            card.pack(fill="x", pady=3)
            title = "Point load" if load["type"] == "point" else "Uniform load"
            tk.Label(card, text=f"{title} {index}", bg="#e6d7bb",
                     font=("Arial Narrow", 9, "bold")).pack(anchor="w")
            remove = tk.Button(card, text="×", command=lambda i=load["id"]: self.remove_load(i),
                               bg="#e6d7bb", relief="flat", fg="#9c3d32")
            remove.place(relx=1.0, x=-2, y=2, anchor="ne")
            fields = [("Magnitude", "magnitude")]
            if load["type"] == "point":
                fields.append(("Position", "start"))
            else:
                fields.extend([("Start", "start"), ("End", "end")])
            for label, key in fields:
                row = tk.Frame(card, bg="#e6d7bb")
                row.pack(fill="x", pady=1)
                tk.Label(row, text=label, bg="#e6d7bb", width=10, anchor="w").pack(side="left")
                var = tk.StringVar(value=load[key])
                entry = ttk.Entry(row, textvariable=var, width=10)
                entry.pack(side="right")
                entry.bind("<KeyRelease>",
                           lambda _e, item=load, k=key, v=var: self._load_field_changed(item, k, v))
            row = tk.Frame(card, bg="#e6d7bb")
            row.pack(fill="x", pady=1)
            tk.Label(row, text="Direction", bg="#e6d7bb", width=10, anchor="w").pack(side="left")
            direction = tk.StringVar(value="Down" if load["direction"] == "-1" else "Up")
            combo = ttk.Combobox(row, textvariable=direction, state="readonly",
                                 values=["Down", "Up"], width=8)
            combo.pack(side="right")
            combo.bind("<<ComboboxSelected>>",
                       lambda _e, item=load, v=direction: self._direction_changed(item, v))

    def _load_field_changed(self, load: dict, key: str, var: tk.StringVar) -> None:
        load[key] = var.get()
        self.update_results()

    def _direction_changed(self, load: dict, var: tk.StringVar) -> None:
        load["direction"] = "-1" if var.get() == "Down" else "1"
        self.update_results()

    def remove_load(self, load_id: int) -> None:
        self.loads = [load for load in self.loads if load["id"] != load_id]
        self._render_loads()
        self.update_results()

    def add_point_load(self) -> None:
        length = beam_length_ft(self._current_beam())
        self.loads.append({
            "id": self.next_load_id,
            "type": "point",
            "magnitude": "",
            "start": f"{length / 2:.6g}",
            "direction": "-1",
        })
        self.next_load_id += 1
        self._render_loads()
        self.update_results()

    def add_uniform_load(self) -> None:
        length = beam_length_ft(self._current_beam())
        self.loads.append({
            "id": self.next_load_id,
            "type": "uniform",
            "magnitude": "",
            "start": "0",
            "end": f"{length:.6g}",
            "direction": "-1",
        })
        self.next_load_id += 1
        self._render_loads()
        self.update_results()

    def load_example(self) -> None:
        self.support_var.set("Simply supported")
        self.span_var.set("20")
        self.deflection_limit_var.set("360")
        self.section_var.set("W12x26")
        self.include_self_weight.set(True)
        self.loads = [{
            "id": 1,
            "type": "uniform",
            "magnitude": "1",
            "start": "0",
            "end": "20",
            "direction": "-1",
        }]
        self.next_load_id = 2
        self.on_section_change()
        self._render_loads()
        self.update_results()

    def _style_unit_buttons(self) -> None:
        self.us_button.configure(bg="#f2c439" if self.unit == "us" else "#68747a",
                                 fg="#252d31" if self.unit == "us" else "#f3eee2")
        self.metric_button.configure(bg="#f2c439" if self.unit == "metric" else "#68747a",
                                     fg="#252d31" if self.unit == "metric" else "#f3eee2")

    def save_beam(self) -> None:
        beam = self._current_beam()
        errors = self.validate(beam)
        if errors:
            messagebox.showerror("Cannot save beam", "\n".join(errors), parent=self)
            return
        path = self.current_file_path or filedialog.asksaveasfilename(
            parent=self,
            title="Save Beam Designer file",
            defaultextension=".beam.json",
            filetypes=[("Beam Designer files", "*.beam.json"), ("JSON files", "*.json")],
        )
        if not path:
            return
        document = beam_to_document(
            beam,
            self.section_var.get(),
            self.unit,
            self.overhang_side_var.get(),
        )
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(document, file, indent=2, allow_nan=False)
                file.write("\n")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        self.current_file_path = path
        messagebox.showinfo("Beam saved", f"Saved beam to:\n{path}", parent=self)

    def open_beam(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Open Beam Designer file",
            filetypes=[("Beam Designer files", "*.beam.json"), ("JSON files", "*.json")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                document = json.load(file)
            beam, unit, section_name, overhang_side = beam_from_document(document)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            messagebox.showerror("Open failed", str(exc), parent=self)
            return

        self.unit = unit
        self.current_file_path = path
        self._style_unit_buttons()
        self.support_var.set({
            "simple": "Simply supported",
            "cantilever": "Cantilever",
            "overhang": "Overhanging",
        }[beam.support])
        self.span_var.set(self._display_length(beam.span_ft))
        self.left_overhang_var.set(self._display_length(beam.left_overhang_ft))
        self.right_overhang_var.set(self._display_length(beam.right_overhang_ft))
        self.overhang_side_var.set(overhang_side)
        self.deflection_limit_var.set(f"{beam.deflection_limit_denominator:g}")
        self.include_self_weight.set(beam.include_self_weight)
        self.section_var.set(section_name)
        self.section_name = section_name
        self.section = beam.section
        if section_name == "Custom":
            self.custom_name.set(beam.section.name)
            self.sx_var.set(self._display_section_modulus(beam.section.sx_in3))
            self.ix_var.set(self._display_inertia(beam.section.ix_in4))
            self.e_var.set(self._display_stress(beam.section.e_ksi))
            self.fb_var.set(
                "" if beam.section.fb_ksi is None else self._display_stress(beam.section.fb_ksi)
            )
            self.weight_var.set(self._display_line_load(beam.section.weight_kip_ft))

        restored_loads = []
        for index, load in enumerate(beam.loads, 1):
            item = {"id": index, "direction": str(load.direction)}
            if isinstance(load, PointLoad):
                item.update({
                    "type": "point",
                    "magnitude": self._display_force(load.magnitude_kip),
                    "start": self._display_length(load.position_ft),
                })
            else:
                item.update({
                    "type": "uniform",
                    "magnitude": self._display_line_load(load.intensity_kip_ft),
                    "start": self._display_length(load.start_ft),
                    "end": self._display_length(load.end_ft),
                })
            restored_loads.append(item)
        self.loads = restored_loads
        self.next_load_id = len(restored_loads) + 1
        self.on_section_change()
        self._render_loads()
        self.update_results()

    def _display_length(self, feet: float) -> str:
        value = feet if self.unit == "us" else feet / FT_PER_M
        return f"{value:.8g}"

    def _display_force(self, kip: float) -> str:
        value = kip if self.unit == "us" else kip / KIP_PER_KN
        return f"{value:.8g}"

    def _display_line_load(self, kip_ft: float) -> str:
        value = kip_ft if self.unit == "us" else kip_ft * FT_PER_M / KIP_PER_KN
        return f"{value:.8g}"

    def _display_section_modulus(self, in3: float) -> str:
        value = in3 if self.unit == "us" else in3 * MM3_PER_IN3
        return f"{value:.8g}"

    def _display_inertia(self, in4: float) -> str:
        value = in4 if self.unit == "us" else in4 * MM4_PER_IN4
        return f"{value:.8g}"

    def _display_stress(self, ksi: float) -> str:
        value = ksi if self.unit == "us" else ksi / KSI_PER_MPA
        return f"{value:.8g}"

    def export_results_csv(self) -> None:
        if self.last_analysis is None:
            messagebox.showerror(
                "Cannot export results", "Enter valid beam inputs before exporting.", parent=self
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export beam results",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        _, result = self.last_analysis
        headers, rows = csv_rows(result, self.unit)
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(headers)
                writer.writerows([[f"{value:.9g}" for value in row] for row in rows])
        except OSError as exc:
            messagebox.showerror("CSV export failed", str(exc), parent=self)
            return
        messagebox.showinfo("Export complete", f"Saved results to:\n{path}", parent=self)

    def export_calculation_sheet(self) -> None:
        if self.last_analysis is None:
            messagebox.showerror(
                "Cannot export calculation sheet",
                "Enter valid beam inputs before exporting.",
                parent=self,
            )
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save calculation sheet",
            defaultextension=".pdf",
            filetypes=[("PDF document", "*.pdf")],
        )
        if not path:
            return
        beam, result = self.last_analysis
        try:
            report = calculation_sheet_pdf(beam, result, self.unit)
            with open(path, "wb") as file:
                file.write(report)
        except (OSError, ValueError) as exc:
            messagebox.showerror("PDF export failed", str(exc), parent=self)
            return
        messagebox.showinfo("Export complete", f"Saved calculation sheet to:\n{path}", parent=self)

    def _number(self, text: str) -> float | None:
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    def _length_to_ft(self, value: float) -> float:
        return value if self.unit == "us" else value * FT_PER_M

    def _force_to_kip(self, value: float) -> float:
        return value if self.unit == "us" else value * KIP_PER_KN

    def _line_to_kip_ft(self, value: float) -> float:
        return value if self.unit == "us" else value * KIP_PER_KN / FT_PER_M

    def _current_beam(self) -> BeamInput:
        if self.support_var.get() == "Cantilever":
            support = "cantilever"
        elif self.support_var.get() == "Overhanging":
            support = "overhang"
        else:
            support = "simple"

        span = self._length_to_ft(self._number(self.span_var.get()) or 0.0)
        left = self._length_to_ft(self._number(self.left_overhang_var.get()) or 0.0)
        right = self._length_to_ft(self._number(self.right_overhang_var.get()) or 0.0)
        if self.section_var.get() == "Custom":
            self._sync_custom_section()
        elif self.section_name != self.section_var.get():
            self.section_name = self.section_var.get()
            self.section = CATALOG[self.section_name]

        loads: list[Load] = []
        for load in self.loads:
            magnitude = self._number(load["magnitude"])
            direction = int(load["direction"])
            if magnitude is None:
                magnitude = 0.0
            if load["type"] == "point":
                loads.append(PointLoad(
                    self._force_to_kip(magnitude),
                    self._length_to_ft(self._number(load["start"]) or 0.0),
                    direction,
                ))
            else:
                loads.append(UniformLoad(
                    self._line_to_kip_ft(magnitude),
                    self._length_to_ft(self._number(load["start"]) or 0.0),
                    self._length_to_ft(self._number(load["end"]) or 0.0),
                    direction,
                ))
        return BeamInput(
            support=support,
            span_ft=span,
            left_overhang_ft=left,
            right_overhang_ft=right,
            section=self.section,
            loads=tuple(loads),
            include_self_weight=self.include_self_weight.get(),
            deflection_limit_denominator=self._number(self.deflection_limit_var.get()) or 0.0,
        )

    def validate(self, beam: BeamInput) -> list[str]:
        return validate_beam(beam)

    def update_results(self) -> None:
        self._update_support_visibility()
        beam = self._current_beam()
        errors = self.validate(beam)
        self.error_var.set("\n".join(errors))
        self.error_label.configure(height=0)
        if errors:
            self.last_analysis = None
            for var in self.result_vars.values():
                var.set("—")
            self.beam_canvas.delete("all")
            self.deflect_canvas.delete("all")
            for canvas in self.chart_canvases.values():
                canvas.delete("all")
            self.comparison_var.set("Resolve the inputs above to calculate results.")
            return

        result = solve_beam(beam)
        self.last_analysis = (beam, result)
        force_unit = "kN" if self.unit == "metric" else "kip"
        moment_unit = "kN-m" if self.unit == "metric" else "kip-ft"
        defl_unit = "mm" if self.unit == "metric" else "in"
        length_unit = "m" if self.unit == "metric" else "ft"
        stress_unit = "MPa" if self.unit == "metric" else "ksi"

        to_force = lambda x: x / KIP_PER_KN if self.unit == "metric" else x
        to_moment = lambda x: x / (KIP_PER_KN * FT_PER_M) if self.unit == "metric" else x
        to_defl = lambda x: x * MM_PER_IN if self.unit == "metric" else x
        to_length = lambda x: x / FT_PER_M if self.unit == "metric" else x
        to_stress = lambda x: x / KSI_PER_MPA if self.unit == "metric" else x

        self.result_vars["left_reaction"].set(f"{to_force(result.reaction_a_kip):.3f} {force_unit}")
        if result.reaction_b_kip is not None:
            self.result_vars["right_reaction"].set(f"{to_force(result.reaction_b_kip):.3f} {force_unit}")
        else:
            self.result_vars["right_reaction"].set(
                f"{to_moment(result.fixed_moment_kip_ft or 0.0):.3f} {moment_unit}"
            )
        self.result_vars["peak_shear"].set(f"{to_force(result.peak_shear_kip):.3f} {force_unit}")
        self.result_vars["peak_moment"].set(f"{to_moment(result.peak_moment_kip_ft):.3f} {moment_unit}")
        self.result_vars["peak_deflection"].set(f"{to_defl(result.peak_deflection_in):.3f} {defl_unit}")
        self.result_vars["stress"].set(f"{to_stress(result.peak_stress_ksi):.3f} {stress_unit}")

        limit_in = beam.span_ft * 12.0 / beam.deflection_limit_denominator
        ratio = result.peak_deflection_in / limit_in
        stress_ratio = result.peak_stress_ksi / beam.section.fb_ksi if beam.section.fb_ksi else None
        text = (
            f"Deflection vs L/{beam.deflection_limit_denominator:g}: ratio {ratio:.3f} "
            f"(limit {to_defl(limit_in):.3f} {defl_unit}).\n"
        )
        if stress_ratio is not None:
            text += f"Bending stress vs supplied Fb: ratio {stress_ratio:.3f}. "
        else:
            text += "No supplied Fb: bending-stress comparison unavailable."
        text += "\nNot a safety verdict."
        self.comparison_var.set(text)

        self._draw_beam(beam)
        self._draw_deflection(beam, result)
        self._draw_chart("shear", beam, result.xs_ft, result.shear_kip,
                         force_unit, lambda x: x / KIP_PER_KN if self.unit == "metric" else x)
        self._draw_chart("moment", beam, result.xs_ft, result.moment_kip_ft,
                         moment_unit,
                         lambda x: x / (KIP_PER_KN * FT_PER_M) if self.unit == "metric" else x)
        self._draw_chart("deflection", beam, result.xs_ft, result.deflection_in,
                         defl_unit,
                         lambda x: x * MM_PER_IN if self.unit == "metric" else x)

        line_unit = "kN/m" if self.unit == "metric" else "kip/ft"
        weight = beam.section.weight_kip_ft
        if beam.include_self_weight:
            weight_display = weight / (KIP_PER_KN * FT_PER_M) if self.unit == "metric" else weight
            self.weight_status.configure(text=f"Included at {weight_display:.3f} {line_unit}")
        else:
            self.weight_status.configure(text="Excluded")

    def _update_support_visibility(self) -> None:
        support = self.support_var.get()
        if support != "Overhanging":
            self.overhang_frame.pack_forget()
        else:
            self.overhang_frame.pack(fill="x", pady=(0, 10))
            side = self.overhang_side_var.get()
            if side == "Left":
                self.right_overhang_var.set("0")
            elif side == "Right":
                self.left_overhang_var.set("0")
            for entry in (self.left_overhang_entry, self.right_overhang_entry):
                entry.pack_forget()
            if side in ("Left", "Both"):
                tk.Label(self.overhang_frame, text="Left overhang",
                         bg="#c49661").pack(anchor="w")
                self.left_overhang_entry.pack(fill="x")
            if side in ("Right", "Both"):
                tk.Label(self.overhang_frame, text="Right overhang",
                         bg="#c49661").pack(anchor="w")
                self.right_overhang_entry.pack(fill="x")

    def _draw_beam(self, beam: BeamInput) -> None:
        canvas = self.beam_canvas
        canvas.delete("all")
        width = max(500, canvas.winfo_width())
        height = 190
        x0, x1 = 75, width - 75
        y = 100
        length = beam_length_ft(beam)
        x_of = lambda x: x0 + x / length * (x1 - x0)
        left_support, right_support = support_positions(beam)
        canvas.create_line(x0, y, x1, y, fill="#35444c", width=7)

        if beam.support == "cantilever":
            canvas.create_rectangle(x0 - 20, y - 24, x0 - 5, y + 24,
                                    fill="#b6c0c2", outline="#354149", width=2)
            for dy in range(-18, 19, 9):
                canvas.create_line(x0 - 20, y + dy, x0 - 30, y + dy + 9,
                                   fill="#66747b")
            canvas.create_text(x0, y + 45, text="Fixed")
        else:
            for support_x in (x_of(left_support), x_of(right_support)):
                canvas.create_polygon(support_x - 12, y + 24, support_x + 12, y + 24,
                                      support_x, y + 4, fill="#b6c0c2",
                                      outline="#354149", width=2)
                canvas.create_line(support_x - 18, y + 27, support_x + 18, y + 27,
                                   fill="#354149")

        for index, load in enumerate(resolve_loads(beam)):
            if isinstance(load, PointLoad):
                px = x_of(load.position_ft)
                if load.direction < 0:
                    canvas.create_line(px, y - 48, px, y - 8,
                                       fill="#a64f24", width=3, arrow="last")
                else:
                    canvas.create_line(px, y + 48, px, y + 8,
                                       fill="#4f704f", width=3, arrow="last")
            else:
                start = x_of(load.start_ft)
                end = x_of(load.end_ft)
                count = 8
                for i in range(count):
                    px = start + (end - start) * i / max(1, count - 1)
                    if load.direction < 0:
                        canvas.create_line(px, y - 42, px, y - 8,
                                           fill="#a64f24", width=2, arrow="last")
                    else:
                        canvas.create_line(px, y + 42, px, y + 8,
                                           fill="#4f704f", width=2, arrow="last")

        length_unit = "m" if self.unit == "metric" else "ft"
        canvas.create_text((x0 + x1) / 2, y + 58,
                           text=f"{beam_length_ft(beam) / FT_PER_M if self.unit == 'metric' else beam_length_ft(beam):.3f} {length_unit}",
                           fill="#4a5357")

    def _draw_deflection(self, beam: BeamInput, result: BeamResult) -> None:
        canvas = self.deflect_canvas
        canvas.delete("all")
        width = max(500, canvas.winfo_width())
        x0, x1 = 60, width - 60
        y = 70
        length = beam_length_ft(beam)
        peak = max((abs(v) for v in result.deflection_in), default=0.0)
        scale = 38 / peak if peak else 0.0
        x_of = lambda x: x0 + x / length * (x1 - x0)
        y_of = lambda v: y - v * scale
        canvas.create_line(x0, y, x1, y, fill="#7d8587", dash=(5, 4))
        points = []
        for x, v in zip(result.xs_ft, result.deflection_in):
            points.extend((x_of(x), y_of(v)))
        if len(points) >= 4:
            canvas.create_line(points, fill="#596e32", width=3, smooth=True)
        left_support, right_support = support_positions(beam)
        if beam.support == "cantilever":
            canvas.create_rectangle(x0 - 12, y - 18, x0 - 4, y + 18,
                                    fill="#b6c0c2", outline="#354149")
        else:
            for sx in (left_support, right_support):
                px = x_of(sx)
                canvas.create_polygon(px - 10, y + 18, px + 10, y + 18,
                                      px, y + 3, fill="#b6c0c2", outline="#354149")

    def _draw_chart(self, key: str, beam: BeamInput, xs: Sequence[float],
                    values: Sequence[float], unit: str,
                    converter) -> None:
        canvas = self.chart_canvases[key]
        canvas.delete("all")
        width = max(500, canvas.winfo_width())
        height = 170
        left, right, top, bottom = 55, 18, 14, 25
        pw, ph = width - left - right, height - top - bottom
        length = beam_length_ft(beam)
        converted = [converter(v) for v in values]
        low = min(0.0, *converted)
        high = max(0.0, *converted)
        spread = high - low
        if spread == 0:
            spread = max(abs(high), 1.0)
        low -= spread * 0.1
        high += spread * 0.1
        x_of = lambda x: left + x / length * pw
        y_of = lambda v: top + (high - v) / (high - low) * ph

        canvas.create_line(left, top + ph / 2, left + pw, top + ph / 2,
                           fill="#c3bcaa")
        zero_y = y_of(0)
        canvas.create_line(left, zero_y, left + pw, zero_y, fill="#7d8587")
        points = []
        for x, v in zip(xs, converted):
            points.extend((x_of(x), y_of(v)))
        if len(points) >= 4:
            color = "#4d6572" if key == "shear" else "#a64f24" if key == "moment" else "#596e32"
            canvas.create_line(points, fill=color, width=2.5, smooth=True)

        canvas.create_text(left, top - 2, text=f"{high:.3g}", anchor="w", fill="#465057")
        canvas.create_text(left, top + ph, text=f"{low:.3g}", anchor="w", fill="#465057")
        length_unit = "m" if self.unit == "metric" else "ft"
        canvas.create_text(left, height - 5, text=f"0 {length_unit}", anchor="sw", fill="#465057")
        canvas.create_text(left + pw, height - 5,
                           text=f"{length / FT_PER_M if self.unit == 'metric' else length:.3f} {length_unit}",
                           anchor="se", fill="#465057")

        tooltip = ToolTip(canvas)
        data_x = list(xs)
        data_v = list(converted)

        def on_move(event: tk.Event) -> None:
            if not data_x:
                return
            target = (event.x - left) / pw * length
            index = min(range(len(data_x)), key=lambda i: abs(data_x[i] - target))
            x = data_x[index]
            v = data_v[index]
            display_x = x / FT_PER_M if self.unit == "metric" else x
            tooltip.show(f"x = {display_x:.3f} {length_unit}\nvalue = {v:.3f} {unit}",
                         canvas.winfo_rootx() + event.x,
                         canvas.winfo_rooty() + event.y)

        def on_leave(_event: tk.Event) -> None:
            tooltip.hide()

        canvas.bind("<Motion>", on_move)
        canvas.bind("<Leave>", on_leave)

def main() -> None:
    app = BeamDesigner()
    app.mainloop()


if __name__ == "__main__":
    main()
