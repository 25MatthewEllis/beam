#!/usr/bin/env python3
"""Beam Designer - a Tkinter desktop beam analysis application."""

from __future__ import annotations

import math
import shutil
import subprocess
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, Sequence


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
        fixed_moment,
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
        ttk.Button(intro, text="PRINT / SAVE PDF", command=self.export_pdf).pack(side="right")

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
        )

    def validate(self, beam: BeamInput) -> list[str]:
        errors = []
        length = beam_length_ft(beam)
        if not math.isfinite(beam.span_ft) or beam.span_ft <= 0:
            errors.append("Enter a span greater than zero.")
        if beam.support == "overhang" and (
            beam.left_overhang_ft < 0 or beam.right_overhang_ft < 0
        ):
            errors.append("Overhang lengths must be nonnegative.")
        if beam.section.sx_in3 <= 0:
            errors.append("Section modulus Sx must be greater than zero.")
        if beam.section.ix_in4 <= 0:
            errors.append("Moment of inertia Ix must be greater than zero.")
        if beam.section.e_ksi <= 0:
            errors.append("Elastic modulus E must be greater than zero.")
        if beam.include_self_weight and beam.section.weight_kip_ft < 0:
            errors.append("Section weight per length must be nonnegative.")
        for i, load in enumerate(beam.loads, 1):
            name = f"{'Point' if isinstance(load, PointLoad) else 'Uniform'} load {i}"
            if isinstance(load, PointLoad):
                if load.magnitude_kip <= 0:
                    errors.append(f"{name}: enter a magnitude greater than zero.")
                if load.position_ft < 0 or load.position_ft > length:
                    errors.append(f"{name}: position must be within the beam.")
            else:
                if load.intensity_kip_ft <= 0:
                    errors.append(f"{name}: enter an intensity greater than zero.")
                if load.start_ft < 0 or load.end_ft > length or load.end_ft <= load.start_ft:
                    errors.append(f"{name}: interval must be within the beam.")
        return errors

    def update_results(self) -> None:
        self._update_support_visibility()
        beam = self._current_beam()
        errors = self.validate(beam)
        self.error_var.set("\n".join(errors))
        self.error_label.configure(height=0)
        if errors:
            for var in self.result_vars.values():
                var.set("—")
            self.beam_canvas.delete("all")
            self.deflect_canvas.delete("all")
            for canvas in self.chart_canvases.values():
                canvas.delete("all")
            self.comparison_var.set("Resolve the inputs above to calculate results.")
            return

        result = solve_beam(beam)
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
        if result.reaction_b is not None:
            self.result_vars["right_reaction"].set(f"{to_force(result.reaction_b_kip):.3f} {force_unit}")
        else:
            self.result_vars["right_reaction"].set(
                f"{to_moment(result.fixed_moment_kip_ft or 0.0):.3f} {moment_unit}"
            )
        self.result_vars["peak_shear"].set(f"{to_force(result.peak_shear_kip):.3f} {force_unit}")
        self.result_vars["peak_moment"].set(f"{to_moment(result.peak_moment_kip_ft):.3f} {moment_unit}")
        self.result_vars["peak_deflection"].set(f"{to_defl(result.peak_deflection_in):.3f} {defl_unit}")
        self.result_vars["stress"].set(f"{to_stress(result.peak_stress_ksi):.3f} {stress_unit}")

        limit_length_ft = beam.span_ft if beam.support in ("simple", "overhang") else beam.span_ft
        limit_in = limit_length_ft * 12.0 / 360.0
        ratio = result.peak_deflection_in / limit_in
        stress_ratio = result.peak_stress_ksi / beam.section.fb_ksi if beam.section.fb_ksi else None
        text = (
            f"Deflection vs L/360: ratio {ratio:.3f} "
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

    def export_pdf(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save Beam Designer output",
            defaultextension=".pdf",
            filetypes=[("PDF document", "*.pdf"), ("PostScript", "*.ps")],
        )
        if not path:
            return
        if path.lower().endswith(".pdf"):
            ps_path = path[:-4] + ".ps"
            try:
                self.postscript(file=ps_path, colormode="color")
            except tk.TclError as exc:
                messagebox.showerror("Export failed", str(exc))
                return
            if shutil.which("ps2pdf"):
                try:
                    subprocess.run(["ps2pdf", ps_path, path], check=True)
                    if ps_path != path:
                        import os
                        os.unlink(ps_path)
                except (OSError, subprocess.CalledProcessError) as exc:
                    messagebox.showerror("PDF export failed", str(exc))
                    return
            elif shutil.which("pstopdf"):
                try:
                    subprocess.run(["pstopdf", "-o", path, ps_path], check=True)
                    import os
                    os.unlink(ps_path)
                except (OSError, subprocess.CalledProcessError) as exc:
                    messagebox.showerror("PDF export failed", str(exc))
                    return
            else:
                messagebox.showinfo(
                    "PostScript saved",
                    f"No PDF converter is available. PostScript was saved as:\n{ps_path}",
                )
                return
            messagebox.showinfo("Export complete", f"Saved PDF:\n{path}")
        else:
            try:
                self.postscript(file=path, colormode="color")
                messagebox.showinfo("Export complete", f"Saved PostScript:\n{path}")
            except tk.TclError as exc:
                messagebox.showerror("Export failed", str(exc))


def main() -> None:
    app = BeamDesigner()
    app.mainloop()


if __name__ == "__main__":
    main()
