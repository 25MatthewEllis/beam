# Beam Designer

## Product

Beam Designer is a Python desktop application built with Tkinter. It analyzes
simply supported, cantilever, and overhanging beams and displays shear, moment,
and deflection results in one native application window.

The application is educational and preliminary. It is not for approving a real
beam design and does not check shear capacity, buckling, connections, or code
load combinations.

## Platform

- Use Python and the standard-library Tkinter toolkit.
- Use Tk or Ttk widgets, and the Tkinter canvas for beam and response diagrams.
- Avoid a browser UI or web framework.
- Support a fixed, resizable desktop window with a practical minimum size.
- Do not require third-party runtime packages for the core implementation.
- Package and run the application as a normal local Python desktop program.

## Window Layout

- The top of the window contains the application title, a prominent US
  customary/metric unit toggle, and an example beam loaded when the app opens.
- Put beam, support, section, and load controls in a left input column.
- Put the loaded-beam sketch and support reactions in the main/right area.
- Stack shear, moment, and deflection diagrams directly below the beam sketch.
- Put maximum response values, bending stress, deflection, applicable limits,
  and ratios to limits below the input controls.
- Use a clean, high-contrast warehouse-industrial theme with concrete/steel
  grays, safety yellow, rust accents, and restrained wood-grain or timber
  details.
- Use a condensed industrial font stack when available and readable Tk fonts
  otherwise.
- Keep all controls and labels legible as the window is resized.

## Inputs

- Unit system: US customary is the default, with ft, kip, kip/ft, kip-ft, in,
  ksi, in^3, and in^4. Metric uses m, kN, kN/m, kN-m, mm, MPa, mm^3, and mm^4.
  Switching units converts entered values and displayed results without changing
  the physical beam.
- Support type: simply supported, cantilever, or overhanging beam with two
  simple supports. Default to simply supported.
- Span: numeric field greater than zero. The opening example uses 20 ft.
- Overhangs: for an overhanging beam, support the left, right, or both sides
  and enter each active overhang length independently.
- Loads: repeatable point-load and uniform-load rows with add/remove controls.
  Point loads include magnitude, position from the left beam end, and up/down
  direction. Uniform loads include intensity, start and end positions, and
  up/down direction. Downward is the default.
- Beam section: catalog steel W-shapes and lumber sizes, plus a custom section.
  Catalog entries must provide Sx, Ix, E, supplied Fb, material identity, grade,
  and weight per length.
- Beam self-weight: checkbox enabled by default. Include catalog or custom
  section weight per length in analysis when enabled and report it separately
  from applied loads.

## Section Data

- The starter catalog includes W12x26 steel grade A992 and Douglas Fir-Larch
  No. 2 nominal 2x4 through 2x12 lumber sizes.
- Custom sections require Sx, Ix, E, Fb, and a material label.
- Record the source and edition for catalog properties. Do not calculate
  code-specific allowable values.
- Use the AISC Shapes Database v16.0 for steel geometry and weight.
- Source lumber properties from the AWC 2024 NDS Supplement and its
  2026-09-17 addendum. Use reference E = 1,600 ksi and Fb = 0.90 ksi for
  Douglas Fir-Larch No. 2 from Table 4A, with dimensions based on assumed
  dressed sizes.

## Analysis

- Use upward force as positive and downward force as negative.
- Use positive internal shear as upward on the left face and positive bending
  moment as sagging.
- Calculate reactions and internal force diagrams from statics.
- Sum enabled self-weight linearly with entered loads.
- Use a linear-elastic Euler-Bernoulli model and ignore shear deformation.
- Enforce zero deflection at simple supports.
- Calculate bending stress as fb = M/Sx and compare maximum absolute stress
  with supplied Fb.
- Compare maximum absolute deflection with L/360. Use support-to-support span
  for simply supported and overhanging beams, and fixed-end-to-free-end length
  for cantilevers.
- Do not apply load factors or show an overall pass/fail verdict.

## Visualization

- Use the Tkinter canvas to draw the loaded beam, supports, load markers,
  reactions, and an exaggerated deflected shape.
- Draw distinct shear, moment, and deflection curves with filled areas from
  each curve to its zero axis.
- Label diagram units and maximum/minimum values.
- Show the cursor position and corresponding response value while interacting
  with a diagram.
- Update all diagrams immediately after valid input changes.
- Provide a print or save-as-PDF workflow using Tk-compatible printing/export
  support or a standard library/process-based system workflow.

## Results

The application must show:

- Support reactions.
- Maximum absolute shear and moment.
- Maximum absolute deflection.
- Calculated bending stress and applicable limit.
- Ratios of bending stress and deflection to their respective limits.
- A prominent educational/preliminary disclaimer.

## Validation

- 20 ft simply supported W12x26 beam with 1 kip/ft uniform load and self-weight
  off: maximum moment 50.0 kip-ft and maximum deflection 0.609 in.
- Add independently checked cases for self-weight, point loads, cantilever,
  lumber sections, custom properties, overhangs, and metric inputs.
