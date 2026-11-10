# Köpir C3 — ESP32-C3 companion board for FPV / drone flight controllers (rev A)

*Köpir* means "bridge" in Kazakh: the board sits between a Betaflight/INAV flight controller and the outside world
(Wi-Fi/BLE, sensors, a ground station). It is two boards that stack:

| | **Köpir C3** (main board) | **Köpir Wing** (add-on) |
|---|---|---|
| Origin | Derivative of Espressif's **ESP32-C3-DevKit-RUST-2 rev 2.0** (CERN-OHL-P-2.0); no track, pad or via changes | Designed by Ashyq Aspan Foundation |
| Size | 22.86 × 63.50 mm, 2 layers, 1.6 mm | 22.86 × 44.00 mm, 2 layers, 1.6 mm |
| Parts | ESP32-C3-MINI-1-H4X (4 MB flash), native USB-C (USB-Serial-JTAG), ICM-42670-P IMU, WS2812B RGB LED, Li-ion charger, 3.3 V buck, BOOT/RESET | JST-SH FC port with fused, TVS-clamped 5 V input, Qwiic I²C port, AUX UART port with fused 5 V, GPIO2 strapping pull-up |
| Assembly | JLCPCB, top side only, 47 placements + 2 hand-soldered male headers | JLCPCB, top side only, 13 placements + 2 hand-soldered female headers |

Release files (fabrication zips, sources, this README and NOTICE): <https://huzarh.github.io/ashyqaspan-inventar/files/kopir/>

![Köpir C3 and Köpir Wing](art/kopir-renders.png)

## Köpir Wing pinout

The wing mounts **under** Köpir C3 on its 2.54 mm pin rows (Feather-style 16 + 12 pins), component side facing down
(away from the main board). Its headers enter from the back, which is marked MAIN BOARD SIDE. All three connectors
are JST-SH 1.0 mm, 4 pins, and pin 1 is GND on each of them.

| Connector | Pin 1 | Pin 2 | Pin 3 | Pin 4 |
|---|---|---|---|---|
| **FC** (J2) | GND | +5 V **in** from the FC BEC | TX out (GPIO4, 470 Ω) → FC RX | RX in (GPIO5, 1 kΩ) ← FC TX |
| **I2C** (J3, Qwiic order) | GND | 3V3 out | SDA (GPIO7) | SCL (GPIO8) |
| **AUX** (J4) | GND | +5 V out (VBUS through F2) | TX out (GPIO0, 470 Ω) | RX in (GPIO1, 1 kΩ) |

AUX uses the same order as FC, so a straight 1:1 JST-SH cable fits GND/5V/RX/TX sensors such as the MicoAir MTF-01.
The silkscreen legend lists the pins left to right as they appear on the component side (J4 is turned 180°, so its
row reads RX TX 5V G). Logic levels are 3.3 V. Betaflight/INAV UARTs are 3.3 V too. The ESP32-C3 pins are **not
5 V tolerant**.

* **Power path.** FC 5 V → F1 (0.75 A hold, 30 V PTC) → SMF5.0A TVS + 10 µF → B5819W Schottky → main board VBUS
  (≈4.4–4.6 V from a 5.0 V BEC at 0.4 A) → buck → 3.3 V. USB and the FC can be connected at the same time: the main
  board's D6 stops VBUS flowing back into USB, and the wing's D2 stops USB 5 V flowing back into the FC. Köpir C3
  draws up to about 0.4 A in Wi-Fi bursts; keep AUX loads below about 0.2 A. F1 holds 0.75 A at 25 °C and less
  when hot.
* **J2 pin 2 is not battery-proof.** Feed it from a regulated 5 V BEC only. A 2S–6S battery voltage on it makes the
  TVS D1 clamp until F1 trips. D1 is then likely destroyed, and while the fault lasts the main board's charger and
  buck (absolute maximum 7 V and 6 V) can be damaged. F1 (rated 30 V, enough for 6S) only limits the fault current.
  Replace D1 and F1 after such a fault.
* **AUX 5 V** comes from VBUS (USB or FC) through F2 (0.35 A hold PTC). It is off when Köpir C3 runs from its
  battery. A shorted sensor cable trips F2 within about 0.1–1 s; Köpir C3 may reset during that time, and F1 may
  trip too.
* **Series resistors.** 470 Ω on outputs and 1 kΩ on inputs keep currents into the ESP32-C3 clamp diodes to a few mA
  when a powered peripheral meets an unpowered board. They are fine up to well above 1 Mbaud.
* **I²C** is the main board's bus (IMU at 0x68, 10 kΩ pull-ups on the main board). Qwiic modules with their own
  pull-ups are fine. GPIO8 is a strapping pin: no device may pull SCL low at power-up.
* **GPIO0/GPIO1** are the ESP32-C3's 32 kHz crystal pins. The main board's crystal Y1 is not fitted in rev A; keep it
  that way when AUX is used.
* **GPIO2** is a strapping pin that only drives the RGB LED on the main board. The wing adds the 10 kΩ pull-up that
  Espressif's hardware design guidelines recommend.

## Assembly and mounting

1. **Main board headers.** Solder Köpir C3's 1×16 and 1×12 male headers **pins down**: the plastic spacer sits on
   the bottom face (the one with the KÖPIR C3 name), and the pins are soldered on the component side.
2. **Key the stack.** Cut off three of those pins: pin 3 of the 16-pin row (third from the antenna end, the NC pin
   between 3V3 and GND) and pins 17 and 18 (the two NC pins at the USB-C end of the 12-pin row). Then block the
   matching sockets of the wing, marked KEY on its back: J1 socket 3 and J5 sockets 1 and 2. Press a cut-off pin into
   each, or fill it with a drop of epoxy. With both pin rows engaged, the wing then cannot be plugged in rotated,
   shifted along the rows or on top of Köpir C3. Each of those mistakes would put a supply on a signal pin: rotated,
   VBUS lands on GPIO5; offset by one pin, wing GND lands on IO0, or 3V3 on CHIP_PU and VBUS on EN; on top, VBUS lands
   on GPIO2. The key cannot stop a sideways fit in which only one row engages and the wing hangs off the side, so check
   that both rows are engaged and the outlines line up before powering.
3. Solder the 1×16 and 1×12 female headers to the wing: bodies on the back (MAIN BOARD SIDE), pins soldered on the
   component side. The ANTENNA END and USB-C END arrows on the wing's back must point toward Köpir C3's antenna and
   USB-C ends.
4. The AUX plug enters from the antenna end. Bend the cable back under the wing and keep it away from the
   ESP32-C3-MINI-1 antenna, which sits about 3 mm beyond the wing's edge. No copper runs under the antenna.
5. The wing's outer face is the bottom of the stack: insulate it (Kapton tape or foam) before mounting it on a frame.
   Battery leads, if any, leave the main board from the top.

## Battery (optional)

The main board's MCP73831 charger (4.2 V, 100 mA set by R9 = 10 kΩ) charges a single-cell Li-ion/Li-Po connected
to the BAT+/BAT− pads. **Use protected cells only:** the board has no over-discharge, over-current or reverse-polarity
protection. Check the pad polarity before soldering. With the wing fitted, the FC's 5 V also charges the cell; on a
drone, leave the battery pads empty.

## Firmware notes

ESP32-C3-MINI-1-H4X carries chip revision v1.1 (Espressif advisory AR2024-009). Set *Minimum Supported ESP32-C3
Revision* to v1.1, or, for images that must also run on older chips, to the lowest revision they must run on
(ESP-IDF default v0.3; the advisory suggests v0.4 for firmware shared with v1.1).

* Minimum ESP-IDF with the default UART0 console: v4.3.7, v4.4.7, v5.0.5, v5.1.3 or v5.2.
* To use both UARTs (UART1 → FC on GPIO4/5, UART0 → AUX on GPIO0/1), the console must move to USB-Serial-JTAG
  (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`, or none). On chip v1.1 that needs ESP-IDF v5.0.8, v5.1.6, v5.2.4,
  v5.3.2, v5.4 or later (AR2025-006).
* Other frameworks follow the ESP-IDF version they are built on. In Arduino, set *USB CDC On Boot: Enabled*.

A typical FC setup runs MSP on the UART wired to J2 at 115200 baud.

## Ordering at JLCPCB

Fabrication packages are generated into `fab/<board>/` (see *Reproduce*). Each release zip
`<board>-revA-jlcpcb.zip` holds the Gerber zip, the JLC BOM and CPL with the CPL report, the schematic PDF, top and
bottom renders, the STEP model, `summary.json` (sizes, check counts) and `ORDERING.txt` (these steps for that board),
plus this README with its image, NOTICE.md and the licence text. `kopir-revA-source.zip` holds all repository files at
the release commit, without git history (run `git init` and commit before using `scripts/release.py` on it).

1. **PCB**: upload `<board>-revA_gerbers.zip`. 2 layers, 1.6 mm. The design specifies black solder mask, white
   silkscreen and ENIG. JLCPCB's Economic PCBA offers ENIG only with green mask (capabilities page, checked
   2026-11-10), so choose one of:
   * black mask + lead-free HASL, Economic PCBA (the look as designed);
   * green mask + ENIG, Economic PCBA (flattest pads for the LGA module and IMU);
   * black mask + ENIG, Standard PCBA (needs a panel with rails and fiducials at this size; higher fees).

   *Mark on PCB*: leave the default. JLCPCB no longer prints order numbers (since May 2026), and the boards carry
   no marker. Order single boards; if JLCPCB panelizes, keep rails and tabs off the two JST edges of the wing.
2. **Assembly**: top side only. Upload `<board>-revA_bom_jlc.csv` and `<board>-revA_cpl_jlc.csv`. The placement
   file is already corrected for JLCPCB's part orientation (see below). In the preview, still check the polarity of
   the LEDs D2/D3/D9, the Schottky diodes D4/D6 and the USB-C connector on Köpir C3, and of D1/D2 on the wing. The LCSC footprint of the red LEDs (C2286)
   numbers the anode as pad 1, so its pin-1 dot shows on the side opposite the KiCad cathode mark; this is correct.
3. **Not fitted** on Köpir C3 (not in the BOM): U1 SHTC3 (optional temperature/humidity sensor), Y1/C12/C13/R12
   (32 kHz crystal; leave empty when the wing's AUX port uses GPIO0/1), C14 (1 nF on BOOT, not fitted upstream either)
   and test pad TP2.
4. **Hand-soldered**: 1×16 and 1×12 2.54 mm male headers on Köpir C3, 1×16 and 1×12 female headers on the wing
   (`kopir-wing-revA_not_assembled.csv`); see *Assembly and mounting* for their orientation and the key.

Parts cost at 10 boards (JLCPCB assembly stock and prices checked 2026-11-10 with `scripts/jlc_parts.py`):
Köpir C3 ≈ 7.3 USD/board with 12 extended parts; Köpir Wing ≈ 0.9 USD/board with 4 extended parts. JLCPCB adds its own
setup, extended-part and PCB fees. Re-run `jlc_parts.py --qty <boards>` right before ordering. The IMU (C3288646) has
the thinnest stock (426 pieces when checked). ICM-42607-P (C5129967) is a footprint- and pin-compatible alternate, but
its WHO_AM_I is 0x60 instead of 0x67, so the driver must accept it. For larger runs, pre-buy the IMU into the JLC
parts library.

Optional cost cuts that were not applied: R3 22.1 kΩ → 22 kΩ basic part (C25768, 3.3 V rail +0.4 %); Q1 → AO3401A
(C15127, basic). Each saves one extended-part fee. Re-run the CPL check after any part change.

### What was checked

| Check | Köpir C3 | Köpir Wing |
|---|---|---|
| ERC | 0 errors (upstream: 6) | 0 items |
| DRC + schematic parity | 0 errors (upstream: 3), parity clean | 0 errors against the JLCPCB rules (board setup in `kopir-wing.kicad_pro` + `kopir-wing.kicad_dru`), parity clean |
| Copper vs. upstream | all 707 tracks/vias and 240 pads identical; GND pours ≥ 0.3 mm from the routed edge and ≥ 0.25 mm from unplated holes | — |
| Connectivity vs. upstream | identical: 55 components, same nets; only U3's value changed | — |
| Placement vs. LCSC footprints | 47/47 compatible; Q1 +180°, U5/U6 +270°, J1 centre 2.38 mm | 13/13 compatible |
| JLCPCB stock (10 boards) | all in stock | all in stock |

`scripts/jlc_cpl_fix.py` downloads every LCSC part's own footprint and pairs pads by pin function (K/A, VDD/DIN,
G/S/D …). It then solves rotation and centre against the KiCad footprint and checks coverage both ways. It refuses a
polarized part whose orientation it cannot prove by pin names. It caught a mirrored-pinout LED substitute and the red
LEDs' reversed pad numbering. KiCad's DRC does not apply a zone-only edge-clearance rule to existing fills, so
`scripts/check.py` measures the GND fill against the board edge itself (and, as a cross-check, against the unplated
holes, which DRC does check). The remaining DRC warnings are footprints that differ
on purpose from their KiCad library copies (upstream footprints, trimmed silkscreen), three upstream silkscreen
overlaps in the front IMU/T+H labels and the USB-C outline at the board edge. The silkscreen lines added for Köpir
are at least 1.0 mm high. Renders and STEP files show bare boards (no 3D component models).

## Reproduce

Requirements: KiCad 9.0 (kicad-cli + pcbnew Python), Python 3,
`pip install easyeda2kicad opencv-python-headless pillow`.

```sh
python3 scripts/make_wing.py               # regenerate Köpir Wing schematic + board from the script
python3 scripts/make_wing.py check         # ERC + DRC with schematic parity and JLC rules
python3 scripts/check.py hardware/kopir-c3/kopir-c3 --upstream-pcb <esp-rust-board v2.0>/hardware/esp-rust-board/esp-rust-board.kicad_pcb
for b in kopir-c3 kopir-wing; do
  python3 scripts/jlc_export.py hardware/$b/$b.kicad_pro --out fab/$b --name $b-revA --cpl-fix --step
done
python3 scripts/jlc_parts.py fab/*/*_bom_jlc.csv --qty 10 --json fab/jlc_parts.json   # JLCPCB stock / basic-extended / price
python3 scripts/renders.py                 # art/kopir-renders.png from the current boards
python3 scripts/release.py                 # release folder fab/release/: zips, SHA256SUMS, download page (commit first)
```

`check.py` compares the main board with the upstream baseline in `baseline/` (ERC, DRC, netlist) and, with
`--upstream-pcb`, every track, via and pad with the upstream board (ends, widths, drills, via types, pad orientation,
layers and copper outlines); it exits with 1 on new errors, changed copper or a pour that is too close. The other
scripts record the changes to the design files: `bom_refresh.py` (BOM), `rebrand.py` (silkscreen and title blocks),
and `add_pwr_flags.py` with `hygiene.py` (ERC/library hygiene, silkscreen fixes, pour clearances, removed logo
footprints). NOTICE.md lists every modification.

## Repository layout

```
hardware/kopir-c3/     main board (KiCad 9)           hardware/kopir-wing/   add-on board (generated by scripts/make_wing.py)
kicad_libs/            project symbols, footprints, 3D models (upstream + exported footprints)
baseline/              upstream ERC/DRC/netlist used by scripts/check.py
scripts/               generators, checks, renders and JLCPCB export          art/   logo and renders
NOTICE.md              origin and list of modifications            LICENSE-CERN-OHL   licence text
```

## Licence and credits

All files in this repository (KiCad sources, libraries, scripts and documentation) are licensed under the **CERN
Open Hardware Licence Version 2 – Permissive** (`LICENSE-CERN-OHL`, SPDX `CERN-OHL-P-2.0`), except the licence text
itself and the third-party 3D component models in `kicad_libs/3d/*.step`, which come with the upstream design and
keep their original terms. Köpir C3 is derived from **ESP32-C3-DevKit-RUST-2** by Espressif Systems / esp-rs
(<https://github.com/esp-rs/esp-rust-board>). See `NOTICE.md` for the full list of modifications. Espressif and esp-rs
are named only to describe the origin of the design and do not endorse this project. Köpir Wing and the scripts are
by Ashyq Aspan Foundation. If you sell or give away boards, include NOTICE.md or the link to the release files.
Provided without warranty.
