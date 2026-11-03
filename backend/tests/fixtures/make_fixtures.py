"""Generate synthetic marine manuals for automated tests.

The content is invented for testing only (not copied from any real manual) but
mirrors real layouts: ruled schedule tables, interval matrices, prose
schedules, spare-parts lists, multi-page procedures far from the schedule,
diagram pages, scanned (image-only) pages, corrupt and encrypted files.

Run: python -m tests.fixtures.make_fixtures <out_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

W, H = 595, 842  # A4 points
MARGIN = 56


class Writer:
    def __init__(self, doc: pymupdf.Document, running_header: str):
        self.doc = doc
        self.header = running_header
        self.page: pymupdf.Page | None = None
        self.y = 0.0

    def new_page(self) -> pymupdf.Page:
        self.page = self.doc.new_page(width=W, height=H)
        n = self.doc.page_count
        self.page.insert_text((MARGIN, 30), self.header, fontsize=8, fontname="helv")
        self.page.insert_text((W - MARGIN - 40, H - 25), f"Page {n}", fontsize=8, fontname="helv")
        self.y = 70
        return self.page

    def ensure(self, needed: float) -> None:
        if self.page is None or self.y + needed > H - 60:
            self.new_page()

    def heading(self, text: str, size: float = 15) -> None:
        self.ensure(size * 3)
        self.y += size * 0.6
        self.page.insert_text((MARGIN, self.y), text, fontsize=size, fontname="hebo")
        self.y += size * 1.2

    def para(self, text: str, size: float = 10, bold: bool = False, indent: float = 0) -> None:
        font = "hebo" if bold else "helv"
        width = W - 2 * MARGIN - indent
        words = text.split()
        line = ""
        for w in words:
            trial = (line + " " + w).strip()
            if pymupdf.get_text_length(trial, fontname=font, fontsize=size) > width:
                self.ensure(size * 1.5)
                self.page.insert_text((MARGIN + indent, self.y), line, fontsize=size, fontname=font)
                self.y += size * 1.45
                line = w
            else:
                line = trial
        if line:
            self.ensure(size * 1.5)
            self.page.insert_text((MARGIN + indent, self.y), line, fontsize=size, fontname=font)
            self.y += size * 1.45
        self.y += size * 0.4

    def table(self, rows: list[list[str]], widths: list[float], size: float = 9) -> None:
        row_h = size * 3.4
        x0 = MARGIN
        for r_i, row in enumerate(rows):
            self.ensure(row_h + 4)
            x = x0
            for c_i, cell in enumerate(row):
                rect = pymupdf.Rect(x, self.y, x + widths[c_i], self.y + row_h)
                self.page.draw_rect(rect, color=(0, 0, 0), width=0.6)
                font = "hebo" if r_i == 0 else "helv"
                rc = self.page.insert_textbox(rect + (3, 3, -3, -2), cell, fontsize=size, fontname=font)
                if rc < 0:  # PyMuPDF silently drops text that does not fit; fixtures must be exact
                    raise ValueError(f"Fixture cell text does not fit: {cell!r}")
                x += widths[c_i]
            self.y += row_h
        self.y += 12

    def filler(self, topic: str, n_pages: int) -> None:
        for i in range(n_pages):
            self.new_page()
            self.heading(f"{topic} - description {i + 1}", 13)
            for _ in range(5):
                self.para(
                    f"This section describes the general arrangement and working principle of the {topic.lower()}. "
                    "The design ensures reliable operation under normal service conditions. Operating data are "
                    "listed in the technical data sheet. The system is monitored by the alarm and control system."
                )


def main_engine_manual(path: Path) -> None:
    doc = pymupdf.open()
    w = Writer(doc, "MARINER 6M50 Main Engine - Instruction Manual (TEST FIXTURE)")
    p = w.new_page()
    p.insert_text((MARGIN, 200), "MARINER 6M50 Main Engine", fontsize=26, fontname="hebo")
    p.insert_text((MARGIN, 240), "Instruction Manual - Operation and Maintenance", fontsize=14, fontname="helv")
    doc.set_metadata({"title": "MARINER 6M50 Main Engine Instruction Manual"})

    w.new_page()
    w.heading("Contents", 16)
    for line in ["1 General ........................ 3", "2 Maintenance Schedule ......... 4", "3 Spare Parts .................... 5",
                 "909 Fuel Valve ................... 40", "912 Crosshead Bearing ......... 70"]:  # fmt: skip
        w.para(line)

    w.new_page()
    w.heading("1 General", 16)
    w.para("All maintenance work must be carried out by qualified personnel. Observe the safety instructions in chapter 0.")

    w.new_page()
    w.heading("2 Maintenance Schedule - Main Engine", 16)
    w.para("Intervals are given in operating hours unless otherwise stated.")
    w.table(
        [
            ["Component", "Maintenance task", "Interval", "Procedure"],
            ["Fuel valve", "Overhaul", "3,000 operating hours", "909-1.2"],
            ["Fuel valve", "Pressure test", "1,500 h", "909-1.3"],
            ["Crosshead bearing", "Check bearing clearance", "8,000 h", "912-2.1"],
            ["Starting air valve", "Inspect and clean", "Every 6 months", ""],
            ["Cylinder liner", "Measure liner wear", "At every dry docking", ""],
            ["Lubrication system", "", "", ""],
            ["Lub. oil filter", "Replace filter element", "When diff. pressure exceeds 0.8 bar", ""],
            ["Exhaust valve", "Overhaul exhaust valve", "", ""],
        ],
        [110, 150, 170, 53],
    )

    w.new_page()
    w.heading("3 Spare Parts - Fuel Valve", 16)
    w.table(
        [
            ["Item no.", "Designation", "Part number", "Qty"],
            ["001", "Valve body", "909-0012-01", "1"],
            ["002", "Spindle guide", "909-0013-07", "1"],
            ["003", "O-ring 45x3", "909-0099-11", "4"],
            ["004", "Nozzle", "909-0101-03", "1"],
        ],
        [60, 200, 140, 83],
    )

    w.filler("Fuel injection system", 34)  # pushes procedures far away from the schedule

    w.new_page()
    w.heading("909-1.2 Fuel Valve - Overhaul", 15)
    w.para("WARNING: Before dismantling, shut off the fuel supply and release the fuel pressure. Hot fuel can cause severe burns.", bold=True)
    w.para("Tools required: Torque wrench 50-300 Nm, special tool 909-T1 (spindle extractor), test pump.")
    w.para("Materials: Clean diesel oil, molybdenum disulphide paste, new O-rings.")
    steps = [
        "Remove the fuel valve from the cylinder cover using the lifting tool.",
        "Clean the outside of the valve and place it in the dismantling fixture.",
        "Loosen the union nut and remove the nozzle. Note the position of the spindle guide.",
        "Inspect the nozzle holes for carbon deposits and erosion. Measure the hole diameter; renew the nozzle if the diameter exceeds 0.62 mm.",
        "Check the spindle guide for wear. Maximum clearance between spindle and guide: 0.015 mm.",
        "Clean all parts in clean diesel oil. Do not use steel brushes on the nozzle tip.",
        "Renew all O-rings and apply molybdenum disulphide paste to the threads.",
        "Reassemble the valve in reverse order. Tighten the union nut to 180 Nm.",
        "Carry out a pressure test according to procedure 909-1.3 before refitting the valve.",
        "Refit the valve and tighten the fixing nuts crosswise to 120 Nm.",
    ]
    for i, s in enumerate(steps, 1):
        w.para(f"{i}. {s}")
        if i == 5:
            w.new_page()  # forces the procedure to continue on a second page
            w.para("(continued)", size=9)
    w.para("CAUTION: Never test the valve with the nozzle pointing towards persons.", bold=True)

    w.new_page()
    w.heading("909-1.3 Fuel Valve - Pressure Testing", 15)
    w.para("1. Mount the valve on the test pump. 2. Raise pressure slowly to 350 bar. 3. Check the opening pressure and spray pattern.")

    w.filler("Crankshaft and bearings", 25)

    w.new_page()
    w.heading("912-2.1 Crosshead Bearing - Clearance Check", 15)
    w.para("DANGER: Engage the turning gear and block the starting air before entering the crankcase.", bold=True)
    w.para("1. Open the crankcase doors and ventilate the crankcase for at least 15 minutes.")
    w.para("2. Turn the crank to bottom dead centre.")
    w.para("3. Measure the bearing clearance with a feeler gauge at the positions shown in Fig. 912-1.")
    # Diagram page in the middle of the procedure
    dp = w.new_page()
    dp.draw_rect(pymupdf.Rect(100, 150, 495, 600), color=(0, 0, 0), width=2)
    dp.draw_circle((297, 375), 120, color=(0, 0, 0), width=2)
    dp.insert_text((240, 640), "Fig. 912-1 Measuring positions", fontsize=9, fontname="helv")
    w.new_page()
    w.para("4. Record the clearances in the engine log. Permissible clearance: 0.30 - 0.45 mm.")
    w.para("5. If the clearance exceeds 0.50 mm, the bearing must be opened up for inspection.")
    w.para("6. Close the crankcase doors and remove the turning gear blocking.")

    w.new_page()
    w.heading("912-2.2 Crosshead Bearing - Dismantling", 15)
    w.para("This procedure describes removal of the crosshead bearing shells.")
    w.filler("Starting air system", 5)
    w.new_page()
    w.heading("Starting Air Valve - Inspection and Cleaning", 15)
    w.para("WARNING: Shut off and vent the starting air system before work.", bold=True)
    w.para("1. Remove the starting air valve. 2. Dismantle and clean all parts. 3. Inspect the valve seat for scoring; lap if necessary.")
    w.para("4. Reassemble with new O-rings and tighten the cover bolts to 90 Nm.")
    w.new_page()
    w.heading("6.4 Cleaning of Turbocharger Air Filter", 15)
    w.para("NOTE: The filter may be cleaned with the engine running at reduced load.", bold=True)
    w.para("1. Remove the filter mats from the silencer housing.")
    w.para("2. Wash the mats in warm water with a mild detergent (max. 40 °C).")
    w.para("3. Dry the mats and refit them. Renew mats that are damaged.")
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def burner_manual(path: Path) -> None:
    doc = pymupdf.open()
    w = Writer(doc, "AQUATHERM Auxiliary Boiler Burner - Manual (TEST FIXTURE)")
    p = w.new_page()
    p.insert_text((MARGIN, 200), "AQUATHERM Auxiliary Boiler Burner", fontsize=24, fontname="hebo")
    w.new_page()
    w.heading("Maintenance Intervals - Auxiliary Boiler Burner", 16)
    w.para("Every month:", bold=True)
    w.para("- General cleaning of the burner and air register")
    w.para("- Check ignition electrodes")
    w.para("Every 3 months:", bold=True)
    w.para("- Replace fuel oil filter cartridge")
    w.para("Flame scanner: clean lens weekly")
    w.heading("Turbocharger Maintenance (Main Engine)", 13)
    w.table(
        [["Task", "Daily", "500 h", "3000 h"],
         ["Clean turbocharger air filter", "", "X", ""],
         ["Check turbocharger vibration", "X", "", ""],
         ["Inspect turbine blades", "", "", "X"]],
        [210, 90, 90, 93],
    )  # fmt: skip
    w.filler("Burner control", 6)
    w.new_page()
    w.heading("5.1 General Cleaning", 15)
    w.para("CAUTION: Switch off the burner and isolate the electrical supply before cleaning.", bold=True)
    w.para("1. Remove the burner head from the boiler front.")
    w.para("2. Clean the air register and swirler with a soft brush and compressed air.")
    w.para("3. Clean the nozzle holder externally. Do not clean nozzle orifices with metal objects.")
    w.para("4. Check the electrode gap: 3-4 mm.")
    w.para("5. Refit the burner head and perform a start test.")
    w.heading("5.2 Ignition Electrodes", 15)
    w.para("1. Check the ignition electrodes for wear and adjust the electrode gap to 3-4 mm.")
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def scanned_manual(path: Path) -> None:
    """A page rendered to an image and re-inserted, so it has no text layer."""
    src = pymupdf.open()
    w = Writer(src, "Purifier Manual")
    w.new_page()
    w.heading("Separator Maintenance Schedule", 18)
    w.para("Clean the separator bowl every 1000 operating hours.", size=13)
    w.para("Replace the bowl seal rings every 4000 operating hours.", size=13)
    w.new_page()
    w.heading("Separator Bowl - Cleaning", 18)
    w.para("WARNING: Stop the separator and wait until the bowl has come to a complete standstill.", size=13, bold=True)
    w.para("1. Remove the bowl hood and lift out the bowl.", size=13)
    w.para("2. Clean the disc stack and bowl body with a soft brush.", size=13)
    w.para("3. Reassemble and tighten the lock ring to the alignment mark.", size=13)
    out = pymupdf.open()
    for src_page in src:  # every page becomes an image without a text layer
        pix = src_page.get_pixmap(dpi=150)
        page = out.new_page(width=W, height=H)
        page.insert_image(page.rect, pixmap=pix)
    out.save(path)
    out.close()
    src.close()


def corrupt_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\nthis is not a valid pdf\n")


def encrypted_pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Secret")
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()


def build_all(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "main_engine": out / "main_engine_manual.pdf",
        "burner": out / "aux_boiler_burner_manual.pdf",
        "scanned": out / "purifier_scanned.pdf",
        "corrupt": out / "corrupt.pdf",
        "encrypted": out / "encrypted.pdf",
        "doc_coded": out / "doc_coded_manual.pdf",
    }
    main_engine_manual(files["main_engine"])
    burner_manual(files["burner"])
    scanned_manual(files["scanned"])
    corrupt_pdf(files["corrupt"])
    encrypted_pdf(files["encrypted"])
    doc_coded_manual(files["doc_coded"])
    return files



def doc_coded_manual(path: Path) -> None:
    """A manual in the 'document code' style: every page carries a code + counter in the header/footer and
    rotated margin labels (title / type / section); the schedule is a landscape matrix with bare-hour headers,
    a 'B' (based on observations) column, superscript comment numbers, a continuation page and a legend."""
    doc = pymupdf.open()

    def coded_page(code: str, n: int, m: int, labels: list[str], landscape: bool = False) -> pymupdf.Page:
        w, h = (H, W) if landscape else (W, H)
        page = doc.new_page(width=w, height=h)
        page.insert_text((60, 45), "ACME Engines", fontsize=14)
        page.insert_text((w - 180, 45), code, fontsize=14)
        page.insert_text((w / 2 - 40, h - 25), code, fontsize=9)
        page.insert_text((w - 70, h - 25), f"{n} ({m})", fontsize=9)
        for i, lb in enumerate(labels):  # rotated labels in the right margin
            page.insert_text((w - 30 + i * 12, h / 2 + 80), lb, fontsize=9, rotate=90)
        return page

    intro = coded_page("0760-0300-0001", 1, 1, ["Maintenance Schedule", "Introduction"])
    intro.insert_text((60, 120), "Maintenance Schedule - Introduction", fontsize=14, fontname="hebo")
    intro.insert_text((60, 150), "Running hours indicate tasks carried out after a number of running hours.", fontsize=10)
    intro.insert_text((60, 165), "Based on observations (B): the interval depends on the condition of the component.", fontsize=10)

    widths = [55, 260, 80, 50, 50, 50]
    header = ["Task ID", "Tasks", "Doc ID", "2000", "8000", "B"]
    rows = [
        [("20010", ""), ("Relief Valve - Checking", "24"), ("1065-0101", ""), "", "x", ""],
        [("20020", ""), ("Relief Valve - Overhaul", ""), ("1065-0101", ""), "", "", "x"],
        [("20030", ""), ("Top Bracing - Checking", ""), ("N/A", ""), "x", "", ""],
    ]
    more = [[("20040", ""), ("Relief Valve - Replacement", "40"), ("1065-0101", ""), "", "", "x"]]

    def table(page: pymupdf.Page, title: str | None, data: list, legend: list[tuple[str, str]] | None = None) -> None:
        y = 80
        if title:
            page.insert_text((40, y), title, fontsize=14, fontname="hebo")
            y += 20
        for r_i, row in enumerate([header] + data):
            x = 40
            for c_i, cell in enumerate(row):
                rect = pymupdf.Rect(x, y, x + widths[c_i], y + 22)
                page.draw_rect(rect, color=(0, 0, 0), width=0.6)
                text, sup = (cell if isinstance(cell, tuple) else (cell, "")) if r_i else (cell, "")
                page.insert_text((x + 4, y + 15), text, fontsize=9, fontname="hebo" if r_i == 0 else "helv")
                if sup:  # superscript comment number
                    tw = pymupdf.get_text_length(text, fontname="helv", fontsize=9)
                    page.insert_text((x + 5 + tw, y + 10), sup, fontsize=5.5)
                x += widths[c_i]
            y += 22
        for num, txt in legend or []:
            y += 16
            page.insert_text((40, y), num, fontsize=9)
            page.insert_text((70, y), txt, fontsize=9)

    table(coded_page("0760-0301-0001", 1, 2, ["Major Overhaul", "Maintenance Schedule"], True), "Maintenance Schedule - Major Overhaul", rows)
    table(coded_page("0760-0301-0001", 2, 2, ["Major Overhaul", "Maintenance Schedule"], True), None, more,
          [("24", "Check new/overhauled parts after 500 and 1000 hours"), ("40", "See service letter on overhaul intervals")])

    for n, section, body in [
        (1, "Checking", ["1. Leaks during running", "During running, check the valve for leaks.", "CAUTION Replace the flame arrester after a crankcase explosion."]),
        (2, "Overhaul", ["1. Remove screws", "Remove the screws and lift away the valve.", "2. Clean the plates", "Clean the plates and tighten the cover to 45 Nm."]),
    ]:
        page = coded_page("1065-0101-0003", n, 2, ["Relief valve", section, "Work Card"])
        page.insert_text((60, 110), section, fontsize=13, fontname="hebo")
        y = 140
        for line in body:
            x = 60 if line[:2].strip().rstrip(".").isdigit() else 190
            page.insert_text((x, y), line, fontsize=10)
            if x == 190 or line.startswith("CAUTION"):
                y += 16
    doc.save(path)
    doc.close()


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/out")
    for name, p in build_all(target).items():
        print(name, p)
