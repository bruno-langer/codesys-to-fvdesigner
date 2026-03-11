"""
Modbus Table Generator — Desktop UI
=====================================
Tkinter interface for modbus_generator.py + modbus_to_hmi.py
Keep all 3 files in the same folder.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import sys
import os
import io
from pathlib import Path

# ── Import the two backend scripts from the same folder ──────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modbus_generator as gen
import modbus_to_hmi    as hmi

# ─────────────────────────────────────────────────────────────
# Colours & fonts (industrial dark theme)
# ─────────────────────────────────────────────────────────────
BG          = "#1a1d23"
BG2         = "#22262f"
BG3         = "#2b303c"
ACCENT      = "#00aaff"
ACCENT2     = "#0077cc"
SUCCESS     = "#00cc77"
WARNING     = "#ffaa00"
ERROR       = "#ff4455"
TEXT        = "#e8eaf0"
TEXT_DIM    = "#7a8099"
BORDER      = "#363c4e"

FONT_TITLE  = ("Consolas", 15, "bold")
FONT_HEAD   = ("Consolas", 10, "bold")
FONT_BODY   = ("Consolas",  9)
FONT_MONO   = ("Consolas",  9)
FONT_LOG    = ("Consolas",  8)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def styled_frame(parent, **kw):
    return tk.Frame(parent, bg=BG2, **kw)

def section_label(parent, text):
    tk.Label(parent, text=text, font=FONT_HEAD,
             bg=BG2, fg=ACCENT).pack(anchor="w", pady=(10,2))

def divider(parent):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=6)

def row_frame(parent):
    f = tk.Frame(parent, bg=BG2)
    f.pack(fill="x", pady=2)
    return f

def label(parent, text, width=22):
    tk.Label(parent, text=text, font=FONT_BODY, bg=BG2,
             fg=TEXT_DIM, width=width, anchor="w").pack(side="left")

def entry(parent, var, width=28):
    e = tk.Entry(parent, textvariable=var, font=FONT_MONO,
                 bg=BG3, fg=TEXT, insertbackground=ACCENT,
                 relief="flat", bd=0, width=width,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT)
    e.pack(side="left", ipady=4, padx=(0,8))
    return e

def spinbox(parent, var, from_=0, to=65535, width=8):
    s = tk.Spinbox(parent, textvariable=var, from_=from_, to=to,
                   font=FONT_MONO, bg=BG3, fg=TEXT,
                   buttonbackground=BG3, relief="flat",
                   insertbackground=ACCENT, width=width,
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT)
    s.pack(side="left", ipady=4, padx=(0,8))
    return s

def checkbox(parent, var, text):
    c = tk.Checkbutton(parent, variable=var, text=text,
                       font=FONT_BODY, bg=BG2, fg=TEXT,
                       selectcolor=BG3, activebackground=BG2,
                       activeforeground=ACCENT,
                       relief="flat", bd=0)
    c.pack(side="left", padx=(0,14))
    return c


# ─────────────────────────────────────────────────────────────
# Log redirector
# ─────────────────────────────────────────────────────────────
class LogRedirect(io.TextIOBase):
    """Redirect stdout/stderr to the log widget."""
    def __init__(self, widget: scrolledtext.ScrolledText, tag="info"):
        self.widget = widget
        self.tag    = tag

    def write(self, msg):
        if msg.strip() == "":
            return
        self.widget.configure(state="normal")
        self.widget.insert("end", msg + ("\n" if not msg.endswith("\n") else ""), self.tag)
        self.widget.see("end")
        self.widget.configure(state="disabled")

    def flush(self):
        pass


# ─────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Modbus Table Generator")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(720, 640)

        self._build_vars()
        self._build_ui()
        self._center()

    # ── Variables ────────────────────────────────────────────
    def _build_vars(self):
        self.v_xml          = tk.StringVar()
        self.v_modbus_out   = tk.StringVar(value="modbus_output.csv")
        self.v_hmi_out      = tk.StringVar(value="hmi_tags.csv")

        # Generator
        self.v_server       = tk.StringVar(value="MODBUS_IHM")
        self.v_parent       = tk.StringVar(value="XP325")
        self.v_connector    = tk.StringVar(value="NET 1")
        self.v_port         = tk.IntVar(value=502)
        self.v_hr_start     = tk.IntVar(value=0)
        self.v_coil_start   = tk.IntVar(value=0)
        self.v_filter       = tk.StringVar(value="")

        # HMI converter
        self.v_name_strip   = tk.StringVar(value="UserPrg.")
        self.v_name_sep     = tk.StringVar(value="_")
        self.v_id_start     = tk.IntVar(value=0)
        self.v_w_coils      = tk.IntVar(value=0)
        self.v_w_regs       = tk.IntVar(value=1)

        # Checkboxes
        self.v_gen_modbus   = tk.BooleanVar(value=True)
        self.v_gen_hmi      = tk.BooleanVar(value=True)

    # ── UI layout ────────────────────────────────────────────
    def _build_ui(self):
        # Title bar
        title_bar = tk.Frame(self, bg=BG, pady=0)
        title_bar.pack(fill="x")
        tk.Label(title_bar, text="⚡ MODBUS TABLE GENERATOR",
                 font=FONT_TITLE, bg=BG, fg=ACCENT,
                 pady=14, padx=20).pack(side="left")
        tk.Label(title_bar, text="CoDeSys XML → Modbus CSV → Altus HMI",
                 font=FONT_BODY, bg=BG, fg=TEXT_DIM,
                 pady=14).pack(side="left")

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

        # Main pane: left config + right log
        pane = tk.PanedWindow(self, orient="horizontal",
                              bg=BG, sashwidth=6, sashrelief="flat")
        pane.pack(fill="both", expand=True, padx=0, pady=0)

        left  = self._build_left(pane)
        right = self._build_right(pane)

        pane.add(left,  minsize=380)
        pane.add(right, minsize=260)

    def _build_left(self, parent):
        outer = tk.Frame(parent, bg=BG)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG2, padx=16, pady=10)
        win_id = canvas.create_window((0,0), window=inner, anchor="nw")

        def on_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=canvas.winfo_width())

        inner.bind("<Configure>", on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        self._build_files_section(inner)
        divider(inner)
        self._build_generator_section(inner)
        divider(inner)
        self._build_hmi_section(inner)
        divider(inner)
        self._build_run_section(inner)

        return outer

    def _build_right(self, parent):
        outer = tk.Frame(parent, bg=BG, padx=10, pady=10)

        tk.Label(outer, text="OUTPUT LOG", font=FONT_HEAD,
                 bg=BG, fg=ACCENT).pack(anchor="w", pady=(4,6))

        self.log = scrolledtext.ScrolledText(
            outer, font=FONT_LOG, bg="#0d0f14", fg=TEXT,
            relief="flat", bd=0, state="disabled",
            wrap="word", padx=8, pady=8,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.log.pack(fill="both", expand=True)

        # Tag colours for log messages
        self.log.tag_config("info",    foreground=TEXT)
        self.log.tag_config("success", foreground=SUCCESS)
        self.log.tag_config("warn",    foreground=WARNING)
        self.log.tag_config("error",   foreground=ERROR)
        self.log.tag_config("dim",     foreground=TEXT_DIM)

        # Clear button
        tk.Button(outer, text="Clear log", font=FONT_BODY,
                  bg=BG3, fg=TEXT_DIM, relief="flat", bd=0,
                  activebackground=BORDER, activeforeground=TEXT,
                  cursor="hand2", pady=4,
                  command=self._clear_log).pack(anchor="e", pady=(6,0))

        return outer

    # ── File section ─────────────────────────────────────────
    def _build_files_section(self, parent):
        section_label(parent, "FILES")

        r = row_frame(parent)
        label(r, "XML Symbol Config")
        entry(r, self.v_xml, width=22)
        tk.Button(r, text="Browse…", font=FONT_BODY,
                  bg=ACCENT2, fg="white", relief="flat", bd=0,
                  activebackground=ACCENT, cursor="hand2", padx=8, pady=3,
                  command=self._browse_xml).pack(side="left")

        r = row_frame(parent)
        label(r, "Modbus CSV output")
        entry(r, self.v_modbus_out, width=22)
        tk.Button(r, text="Save as…", font=FONT_BODY,
                  bg=BG3, fg=TEXT_DIM, relief="flat", bd=0,
                  activebackground=BORDER, cursor="hand2", padx=8, pady=3,
                  command=self._browse_modbus_out).pack(side="left")

        r = row_frame(parent)
        label(r, "HMI Tags CSV output")
        entry(r, self.v_hmi_out, width=22)
        tk.Button(r, text="Save as…", font=FONT_BODY,
                  bg=BG3, fg=TEXT_DIM, relief="flat", bd=0,
                  activebackground=BORDER, cursor="hand2", padx=8, pady=3,
                  command=self._browse_hmi_out).pack(side="left")

    # ── Generator section ─────────────────────────────────────
    def _build_generator_section(self, parent):
        section_label(parent, "MODBUS SERVER SETTINGS")

        for lbl, var, spin in [
            ("Server name",      self.v_server,    False),
            ("Parent device",    self.v_parent,    False),
            ("Connector",        self.v_connector, False),
        ]:
            r = row_frame(parent)
            label(r, lbl)
            entry(r, var, width=20)

        r = row_frame(parent)
        label(r, "TCP port")
        spinbox(r, self.v_port, from_=1, to=65535, width=7)
        label(r, "HR start addr", width=14)
        spinbox(r, self.v_hr_start, width=7)

        r = row_frame(parent)
        label(r, "Coil start addr")
        spinbox(r, self.v_coil_start, width=7)

        r = row_frame(parent)
        label(r, "Node filter (prefix)")
        entry(r, self.v_filter, width=24)
        tk.Label(r, text="e.g. UserPrg,GVL", font=FONT_BODY,
                 bg=BG2, fg=TEXT_DIM).pack(side="left")

    # ── HMI section ───────────────────────────────────────────
    def _build_hmi_section(self, parent):
        section_label(parent, "HMI TAG SETTINGS")

        r = row_frame(parent)
        label(r, "Strip name prefix")
        entry(r, self.v_name_strip, width=20)
        tk.Label(r, text="e.g. UserPrg.", font=FONT_BODY,
                 bg=BG2, fg=TEXT_DIM).pack(side="left")

        r = row_frame(parent)
        label(r, "Name separator")
        entry(r, self.v_name_sep, width=4)
        label(r, "Id start", width=10)
        spinbox(r, self.v_id_start, width=6)

        r = row_frame(parent)
        label(r, "Writeable flags")
        checkbox(r, self.v_w_coils, "Coils (BOOL)")
        checkbox(r, self.v_w_regs,  "Registers")

    # ── Run section ───────────────────────────────────────────
    def _build_run_section(self, parent):
        section_label(parent, "RUN")

        toggle_row = row_frame(parent)
        checkbox(toggle_row, self.v_gen_modbus, "Generate Modbus CSV")
        checkbox(toggle_row, self.v_gen_hmi,    "Generate HMI CSV")

        tk.Frame(parent, bg=BG2, height=8).pack()

        self.btn_run = tk.Button(
            parent, text="▶  GENERATE",
            font=("Consolas", 11, "bold"),
            bg=ACCENT, fg="white", relief="flat", bd=0,
            activebackground=ACCENT2, activeforeground="white",
            cursor="hand2", pady=10,
            command=self._run
        )
        self.btn_run.pack(fill="x", pady=(0,8))

        self.status_var = tk.StringVar(value="Ready")
        self.status_lbl = tk.Label(parent, textvariable=self.status_var,
                                   font=FONT_BODY, bg=BG2, fg=TEXT_DIM)
        self.status_lbl.pack(anchor="w")

    # ── File dialogs ─────────────────────────────────────────
    def _browse_xml(self):
        p = filedialog.askopenfilename(
            title="Select CoDeSys Symbol Config XML",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")]
        )
        if p:
            self.v_xml.set(p)
            base = Path(p).stem
            self.v_modbus_out.set(str(Path(p).parent / f"{base}_modbus.csv"))
            self.v_hmi_out.set(str(Path(p).parent / f"{base}_hmi_tags.csv"))

    def _browse_modbus_out(self):
        p = filedialog.asksaveasfilename(
            title="Save Modbus CSV as",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")]
        )
        if p: self.v_modbus_out.set(p)

    def _browse_hmi_out(self):
        p = filedialog.asksaveasfilename(
            title="Save HMI Tags CSV as",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")]
        )
        if p: self.v_hmi_out.set(p)

    # ── Log helpers ───────────────────────────────────────────
    def _log(self, msg, tag="info"):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_status(self, msg, color=TEXT_DIM):
        self.status_var.set(msg)
        self.status_lbl.configure(fg=color)

    # ── Run pipeline ──────────────────────────────────────────
    def _run(self):
        xml = self.v_xml.get().strip()
        if not xml:
            messagebox.showerror("Missing input", "Please select an XML file.")
            return
        if not os.path.isfile(xml):
            messagebox.showerror("File not found", f"Cannot find:\n{xml}")
            return
        if not self.v_gen_modbus.get() and not self.v_gen_hmi.get():
            messagebox.showwarning("Nothing to do", "Enable at least one output.")
            return

        self.btn_run.configure(state="disabled", text="⏳  Running…")
        self._set_status("Running…", ACCENT)
        threading.Thread(target=self._run_thread, daemon=True).start()

    def _run_thread(self):
        # Redirect stdout/stderr to log widget
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = LogRedirect(self.log, "info")
        sys.stderr = LogRedirect(self.log, "warn")

        success = True
        modbus_csv = self.v_modbus_out.get().strip()

        try:
            self._log("─" * 52, "dim")

            # ── Step 1: XML → Modbus CSV ─────────────────────
            if self.v_gen_modbus.get():
                self._log("[ STEP 1 ]  XML → Modbus CSV", "info")

                import xml.etree.ElementTree as ET
                tree = ET.parse(self.v_xml.get())
                root = tree.getroot()

                user_types = gen.load_user_types(root)
                array_types = gen.load_array_types(root)
                state = gen.State(
                    hr_cursor=self.v_hr_start.get(),
                    coil_cursor=self.v_coil_start.get(),
                )
                filter_prefixes = [p.strip() for p in self.v_filter.get().split(",") if p.strip()]

                nodes = root.find("c:NodeList", gen.NS)
                if nodes is None:
                    raise RuntimeError("No NodeList found in XML.")

                for node in nodes.findall("c:Node", gen.NS):
                    gen.walk_node(node, user_types, array_types, state, self.v_server.get(),
                                  path="", filter_prefixes=filter_prefixes)

                gen.write_csv(state, modbus_csv,
                              self.v_server.get(), self.v_parent.get(),
                              self.v_connector.get(), self.v_port.get())
                gen.print_summary(state, modbus_csv)
                self._log(f"✅  Modbus CSV saved: {modbus_csv}", "success")

            # ── Step 2: Modbus CSV → HMI CSV ─────────────────
            if self.v_gen_hmi.get():
                self._log("[ STEP 2 ]  Modbus CSV → HMI Tags", "info")

                src = modbus_csv if self.v_gen_modbus.get() else modbus_csv
                if not os.path.isfile(src):
                    raise RuntimeError(f"Modbus CSV not found: {src}\nRun Step 1 first or provide an existing file.")

                rows = hmi.parse_modbus_csv(src)
                strip = [p.strip() for p in self.v_name_strip.get().split(",") if p.strip()]

                written, skipped = hmi.write_hmi_csv(
                    rows,
                    self.v_hmi_out.get(),
                    id_start=self.v_id_start.get(),
                    writeable_default=1,
                    writeable_coils=self.v_w_coils.get(),
                    writeable_regs=self.v_w_regs.get(),
                    strip_prefixes=strip,
                    name_sep=self.v_name_sep.get(),
                )
                hmi.print_summary(written, skipped, self.v_hmi_out.get())
                self._log(f"✅  HMI Tags CSV saved: {self.v_hmi_out.get()}", "success")

            self._log("─" * 52, "dim")
            self.after(0, lambda: self._set_status("Done ✓", SUCCESS))

        except Exception as e:
            self._log(f"\n❌  ERROR: {e}", "error")
            self.after(0, lambda: self._set_status(f"Error: {e}", ERROR))
            success = False
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            self.after(0, lambda: self.btn_run.configure(
                state="normal",
                text="▶  GENERATE"
            ))

    # ── Center window ─────────────────────────────────────────
    def _center(self):
        self.update_idletasks()
        w, h = 860, 680
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()