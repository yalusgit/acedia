#!/usr/bin/env python3
"""
HABIT — terminal habit tracker + calendar + journal
Run: python3 habittracker.py
"""

import curses
import json
import sys
import subprocess
import threading
import time
import csv
import calendar
import textwrap
from datetime import datetime, date, timedelta
from pathlib import Path

DATA_DIR     = Path.home() / ".local" / "share" / "habit"
DATA_FILE    = DATA_DIR / "habits.json"
SCHED_FILE   = DATA_DIR / "schedule.json"
EVENTS_FILE  = DATA_DIR / "events.json"
JOURNAL_FILE = DATA_DIR / "journal.json"

# ─── Data ─────────────────────────────────────────────────────────────────────

def load_data():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        return {"habits": [], "log": {}}
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(d):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

def load_schedule():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SCHED_FILE.exists():
        return {"reminders": []}
    with open(SCHED_FILE) as f:
        return json.load(f)

def save_schedule(s):
    with open(SCHED_FILE, "w") as f:
        json.dump(s, f, indent=2)

def load_events():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not EVENTS_FILE.exists():
        return {}
    with open(EVENTS_FILE) as f:
        return json.load(f)

def save_events(e):
    with open(EVENTS_FILE, "w") as f:
        json.dump(e, f, indent=2)

def load_journal():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        return {}
    with open(JOURNAL_FILE) as f:
        return json.load(f)

def save_journal(j):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(j, f, indent=2)

def today_str():
    return date.today().isoformat()

def get_streak(log, name):
    streak = 0
    d = date.today()
    while True:
        ds = d.isoformat()
        if ds in log and log[ds].get(name):
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    return streak

def checked_today(log, name):
    return log.get(today_str(), {}).get(name, False)

def completion_ratio(log, habits, ds):
    if not habits:
        return 0.0
    day = log.get(ds, {})
    return sum(1 for h in habits if day.get(h)) / len(habits)

def export_csv():
    data = load_data()
    p = Path.home() / f"habits_export_{today_str()}.csv"
    habits = data.get("habits", [])
    log = data.get("log", {})
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + habits)
        for d in sorted(log.keys()):
            w.writerow([d] + [("1" if log[d].get(h) else "0") for h in habits])
    return p

# ─── Notifications ────────────────────────────────────────────────────────────

def send_notification(title, body):
    try:
        subprocess.Popen(["notify-send", "-a", "HABIT", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass
    for cmd in [["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                ["aplay",  "/usr/share/sounds/alsa/Front_Center.wav"]]:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except FileNotFoundError:
            continue

def launch_notification():
    data = load_data()
    habits = data.get("habits", [])
    if not habits:
        return
    log = data.get("log", {})
    unchecked = [h for h in habits if not checked_today(log, h)]
    if unchecked:
        send_notification("HABIT — daily check-in",
                          f"{len(unchecked)} pending: {', '.join(unchecked)}")
    else:
        send_notification("HABIT — all done", "All habits checked ✓")

def reminder_daemon():
    fired = set()
    while True:
        now = datetime.now().strftime("%H:%M")
        for t in load_schedule().get("reminders", []):
            key = f"{today_str()}-{t}"
            if t == now and key not in fired:
                data = load_data()
                habits = data.get("habits", [])
                unchecked = [h for h in habits if not checked_today(data.get("log", {}), h)]
                send_notification(
                    f"HABIT reminder ({t})",
                    f"Pending: {', '.join(unchecked)}" if unchecked else "All done ✓"
                )
                fired.add(key)
        for ev in load_events().get(today_str(), []):
            ekey = f"ev-{today_str()}-{ev.get('time','')}-{ev.get('title','')}"
            if ev.get("time") == now and ekey not in fired:
                send_notification(f"Event: {ev.get('title','')}", ev.get("notes", ""))
                fired.add(ekey)
        time.sleep(10)

# ─── Drawing helpers ──────────────────────────────────────────────────────────

def safestr(scr, y, x, text, attr=0):
    h, w = scr.getmaxyx()
    if y < 0 or y >= h - 1 or x < 0 or x >= w - 1:
        return
    room = w - x - 1
    if room <= 0:
        return
    try:
        if attr:
            scr.attron(attr)
        scr.addstr(y, x, str(text)[:room])
        if attr:
            scr.attroff(attr)
    except curses.error:
        pass

def hline(scr, y, w, char="─"):
    safestr(scr, y, 0, char * (w - 1))

def box(scr, y, x, bh, bw, attr=curses.A_REVERSE):
    for r in range(bh):
        safestr(scr, y + r, x, " " * bw, attr)

# ─── App ──────────────────────────────────────────────────────────────────────

TABS = ["  HABITS  ", "  CALENDAR  "]

class App:
    def __init__(self, stdscr):
        self.scr     = stdscr
        self.data    = load_data()
        self.sched   = load_schedule()
        self.events  = load_events()
        self.journal = load_journal()

        self.tab    = 0
        self.mode   = "main"
        self.status = ""
        self.input_buf = ""

        # habits state
        self.h_cursor       = 0
        self.remind_cursor  = 0

        # calendar state
        self.cal_year   = date.today().year
        self.cal_month  = date.today().month
        self.cal_cursor = date.today().day - 1
        self.ev_cursor  = 0
        self.cal_panel  = "events"   # "events" | "journal"

        # multi-step event creation
        self._ev_title = ""
        self._ev_time  = ""
        self._ev_notes = ""

        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # selected
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)  # event row (bold white on black = "grey" feel)

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self):
        self.scr.timeout(20000)
        while True:
            try:
                self.draw()
                key = self.scr.getch()
                if key == -1:
                    continue
                # Tab switch with Tab key from browse modes
                if key == ord('\t') and self.mode in ("main", "cal_browse"):
                    self.tab  = 1 - self.tab
                    self.mode = "main" if self.tab == 0 else "cal_browse"
                    self.status = ""
                    continue
                if self.tab == 0:
                    self._handle_habits(key)
                else:
                    self._handle_calendar(key)
            except KeyboardInterrupt:
                sys.exit(0)

    # ── draw ──────────────────────────────────────────────────────────────────

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self._draw_topbar(h, w)
        if self.tab == 0:
            self._draw_habits(h, w)
        else:
            self._draw_calendar(h, w)
        self.scr.refresh()

    def _draw_topbar(self, h, w):
        # full reverse bar
        safestr(self.scr, 0, 0, " " * (w - 1), curses.A_REVERSE)
        x = 3
        for i, label in enumerate(TABS):
            if i == self.tab:
                safestr(self.scr, 0, x, label, curses.A_REVERSE | curses.A_BOLD)
            else:
                safestr(self.scr, 0, x, label, curses.A_DIM)
            x += len(label) + 2
        dt = f"  {datetime.now().strftime('%a  %d %b  %H:%M')}  "
        safestr(self.scr, 0, w - len(dt) - 1, dt, curses.A_REVERSE | curses.A_DIM)
        hline(self.scr, 1, w)

    # ─────────────────────────────────────────────────────────────────────────
    # HABITS
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_habits(self, h, w):
        habits = self.data.get("habits", [])
        log    = self.data.get("log", {})
        PAD    = 6   # left padding for bigger feel

        # section label
        welcome = f"Welcome  —  {datetime.now().strftime('%A  %d %B %Y')}"
        safestr(self.scr, 3, PAD, welcome, curses.A_BOLD)
        safestr(self.scr, 4, PAD, "─" * len(welcome), curses.A_DIM)

        if not habits:
            safestr(self.scr, 7, PAD, "No habits yet.", curses.A_DIM)
            safestr(self.scr, 9, PAD, "Press  A  to add your first habit.", curses.A_DIM)
        else:
            row = 5
            for i, habit in enumerate(habits):
                if row >= h - 8:
                    break
                done   = checked_today(log, habit)
                streak = get_streak(log, habit)
                check  = "■" if done else "□"

                # streak label
                if streak >= 7:
                    streak_txt = f"   🔥 {streak} days"
                elif streak > 1:
                    streak_txt = f"   {streak} days"
                elif streak == 1:
                    streak_txt = "   1 day"
                else:
                    streak_txt = ""

                line = f"  {check}   {habit}{streak_txt}"

                if i == self.h_cursor:
                    # selected row — full width highlight
                    safestr(self.scr, row, PAD - 2,
                            (" " * 2 + line).ljust(w - PAD - 2),
                            curses.color_pair(1) | curses.A_BOLD)
                elif done:
                    safestr(self.scr, row, PAD, line, curses.A_DIM)
                else:
                    safestr(self.scr, row, PAD, line)

                row += 3  # triple spacing = very roomy

        # bottom bar
        hline(self.scr, h - 5, w)
        if self.status:
            safestr(self.scr, h - 4, PAD, self.status, curses.A_BOLD)

        keys = [
            ("SPACE", "check/uncheck"),
            ("A", "add habit"),
            ("D", "delete"),
            ("R", "reminders"),
            ("E", "export CSV"),
            ("TAB", "calendar"),
            ("Q", "quit"),
        ]
        kx = 2
        for key, label in keys:
            safestr(self.scr, h - 3, kx, f"[{key}]", curses.A_REVERSE)
            kx += len(key) + 2
            safestr(self.scr, h - 3, kx, f" {label}  ", curses.A_DIM)
            kx += len(label) + 3
            if kx > w - 10:
                break
        hline(self.scr, h - 2, w, "▁")

        # overlays
        if self.mode == "add":
            self._prompt(h, w, "  ADD HABIT  ",
                         f"  Name:  {self.input_buf}▌")
        elif self.mode == "delete" and habits:
            self._prompt(h, w, "  DELETE HABIT  ",
                         f"  Delete  '{habits[self.h_cursor]}'?   Y  /  N  ")
        elif self.mode == "remind":
            self._draw_remind_overlay(h, w)
        elif self.mode == "remind_add":
            self._prompt(h, w, "  ADD REMINDER  ",
                         f"  Time (HH:MM, 24h):  {self.input_buf}▌")

    def _handle_habits(self, key):
        if   self.mode == "main":       self._h_main(key)
        elif self.mode == "add":        self._h_text_input(key, self._finish_add)
        elif self.mode == "delete":     self._h_del_confirm(key)
        elif self.mode == "remind":     self._h_remind_nav(key)
        elif self.mode == "remind_add": self._h_text_input(key, self._finish_remind_add)

    def _h_main(self, key):
        habits = self.data.get("habits", [])
        if key in (ord('q'), ord('Q')):
            sys.exit(0)
        elif key == curses.KEY_DOWN:
            if habits: self.h_cursor = (self.h_cursor + 1) % len(habits)
        elif key == curses.KEY_UP:
            if habits: self.h_cursor = (self.h_cursor - 1) % len(habits)
        elif key == ord(' '):
            self._toggle_habit()
        elif key in (ord('a'), ord('A')):
            self.mode = "add"; self.input_buf = ""; self.status = ""
        elif key in (ord('d'), ord('D')):
            if habits: self.mode = "delete"
        elif key in (ord('r'), ord('R')):
            self.mode = "remind"; self.remind_cursor = 0
        elif key in (ord('e'), ord('E')):
            try:    self.status = f"Exported  →  {export_csv()}"
            except Exception as ex: self.status = f"Export failed: {ex}"

    def _toggle_habit(self):
        habits = self.data.get("habits", [])
        if not habits: return
        name = habits[self.h_cursor]
        log  = self.data.setdefault("log", {})
        day  = log.setdefault(today_str(), {})
        day[name] = not day.get(name, False)
        self.status = ("✓  Checked:  " if day[name] else "✗  Unchecked:  ") + name
        save_data(self.data)

    def _h_text_input(self, key, on_enter):
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.input_buf = self.input_buf[:-1]
        elif key == 27:
            self.mode = "main" if self.tab == 0 else "cal_browse"
            self.input_buf = ""
        elif key in (curses.KEY_ENTER, 10, 13):
            on_enter()
        elif 32 <= key <= 126:
            self.input_buf += chr(key)

    def _finish_add(self):
        name = self.input_buf.strip()
        if name:
            habits = self.data.setdefault("habits", [])
            if name not in habits:
                habits.append(name); save_data(self.data)
                self.h_cursor = len(habits) - 1
                self.status = f"Added:  {name}"
            else:
                self.status = f"'{name}' already exists"
        self.mode = "main"; self.input_buf = ""

    def _h_del_confirm(self, key):
        if key in (ord('y'), ord('Y')):
            habits = self.data.get("habits", [])
            if habits:
                name = habits.pop(self.h_cursor)
                self.h_cursor = max(0, self.h_cursor - 1)
                save_data(self.data)
                self.status = f"Deleted:  {name}"
            self.mode = "main"
        elif key in (ord('n'), ord('N'), 27):
            self.mode = "main"

    def _h_remind_nav(self, key):
        reminders = self.sched.get("reminders", [])
        if key in (27, ord('q'), ord('Q')):
            self.mode = "main"
        elif key == curses.KEY_DOWN:
            if reminders: self.remind_cursor = (self.remind_cursor + 1) % len(reminders)
        elif key == curses.KEY_UP:
            if reminders: self.remind_cursor = (self.remind_cursor - 1) % len(reminders)
        elif key in (ord('a'), ord('A')):
            self.mode = "remind_add"; self.input_buf = ""
        elif key in (ord('d'), ord('D')):
            if reminders:
                reminders.pop(self.remind_cursor)
                self.remind_cursor = max(0, self.remind_cursor - 1)
                save_schedule(self.sched)

    def _finish_remind_add(self):
        t = self.input_buf.strip()
        try:
            datetime.strptime(t, "%H:%M")
            r = self.sched.setdefault("reminders", [])
            if t not in r:
                r.append(t); r.sort(); save_schedule(self.sched)
                self.status = f"Reminder set:  {t}"
            else:
                self.status = f"{t} already exists"
        except ValueError:
            self.status = "Invalid — use HH:MM (24h)"
        self.mode = "remind"; self.input_buf = ""

    def _draw_remind_overlay(self, h, w):
        reminders = self.sched.get("reminders", [])
        bh = max(14, len(reminders) * 3 + 9)
        bw = 48
        y  = (h - bh) // 2
        x  = (w - bw) // 2
        box(self.scr, y, x, bh, bw)
        title = "  REMINDERS  "
        safestr(self.scr, y + 1, x + (bw - len(title)) // 2, title,
                curses.A_REVERSE | curses.A_BOLD)
        safestr(self.scr, y + 2, x + 2, "─" * (bw - 4), curses.A_REVERSE)
        if not reminders:
            safestr(self.scr, y + 4, x + (bw - 18) // 2,
                    "no reminders set", curses.A_REVERSE | curses.A_DIM)
        else:
            for i, t in enumerate(reminders):
                marker = "▶   " if i == self.remind_cursor else "    "
                safestr(self.scr, y + 4 + i * 3, x + 6,
                        f"{marker}{t}", curses.A_REVERSE)
        safestr(self.scr, y + bh - 2, x + 3,
                "  [A] add    [D] delete    [ESC] close  ",
                curses.A_REVERSE | curses.A_DIM)

    # ─────────────────────────────────────────────────────────────────────────
    # CALENDAR
    # ─────────────────────────────────────────────────────────────────────────

    def cal_day_date(self):
        days = calendar.monthrange(self.cal_year, self.cal_month)[1]
        d    = self.cal_cursor + 1
        return date(self.cal_year, self.cal_month, d) if 1 <= d <= days else None

    def cal_date_str(self):
        d = self.cal_day_date()
        return d.isoformat() if d else None

    def _draw_calendar(self, h, w):
        habits = self.data.get("habits", [])
        log    = self.data.get("log", {})
        today  = date.today()

        # ── sizes ──
        CELL_W   = 7    # wider cells
        CAL_W    = CELL_W * 7 + 2
        left_x   = max(2, (w // 2) - (CAL_W // 2) - 10)
        panel_x  = left_x + CAL_W + 4
        panel_w  = max(24, w - panel_x - 2)

        # ── month header ──
        month_lbl = f"{calendar.month_name[self.cal_month].upper()}   {self.cal_year}"
        safestr(self.scr, 2, left_x, "◀  [  ", curses.A_DIM)
        safestr(self.scr, 2, left_x + 6, month_lbl, curses.A_BOLD)
        safestr(self.scr, 2, left_x + 6 + len(month_lbl) + 2, "  ]  ▶", curses.A_DIM)

        # ── day headers ──
        days_hdr = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for col_i, dname in enumerate(days_hdr):
            safestr(self.scr, 3, left_x + col_i * CELL_W + 2,
                    dname.center(CELL_W - 1), curses.A_DIM | curses.A_BOLD)

        hline_partial = lambda y, x, length: safestr(self.scr, y, x, "─" * length, curses.A_DIM)
        hline_partial(4, left_x, CAL_W)

        # ── grid ──
        cal = calendar.monthcalendar(self.cal_year, self.cal_month)
        row = 5
        for week in cal:
            for col_i, day in enumerate(week):
                if day == 0:
                    continue
                ds      = date(self.cal_year, self.cal_month, day).isoformat()
                ratio   = completion_ratio(log, habits, ds)
                has_ev  = bool(self.events.get(ds))
                has_j   = bool(self.journal.get(ds, {}).get("text", "").strip())
                is_today   = date(self.cal_year, self.cal_month, day) == today
                is_sel     = (day - 1 == self.cal_cursor)

                if habits:
                    heat = ("○", "◑", "◕", "●")[
                        0 if ratio == 0 else 1 if ratio < 0.5 else 2 if ratio < 1.0 else 3
                    ]
                else:
                    heat = " "

                marker = ("♦" if has_ev and has_j else
                          "!" if has_ev else
                          "·" if has_j else " ")

                cell = f"{day:2} {heat}{marker}"   # e.g. " 5 ●!"
                cx   = left_x + col_i * CELL_W

                if is_sel:
                    safestr(self.scr, row, cx, f" {cell} ",
                            curses.color_pair(1) | curses.A_BOLD)
                elif is_today:
                    safestr(self.scr, row, cx, f"[{cell}]", curses.A_BOLD)
                else:
                    attr = curses.A_DIM if (ratio == 0 and not has_ev and not has_j) else 0
                    safestr(self.scr, row, cx, f" {cell} ", attr)

            row += 2   # double-spaced rows

        # ── legend ──
        legend_y = row + 1
        safestr(self.scr, legend_y, left_x,
                "○ 0%   ◑ <50%   ◕ <100%   ● done   ! event   · journal   ♦ both",
                curses.A_DIM)

        # ── right detail panel ──
        sel = self.cal_day_date()
        if sel and panel_x < w - 6:
            self._draw_day_panel(h, w, sel, log, habits, panel_x, panel_w)

        # ── keys ──
        hline(self.scr, h - 5, w)
        if self.status:
            safestr(self.scr, h - 4, 4, self.status, curses.A_BOLD)

        if self.cal_panel == "journal":
            keys = [("←→↑↓","navigate"), ("[","prev month"), ("]","next month"),
                    ("N","new event"), ("X","del event"), ("E","events panel"),
                    ("W","write journal"), ("TAB","habits"), ("Q","quit")]
        else:
            keys = [("←→↑↓","navigate"), ("[","prev month"), ("]","next month"),
                    ("N","new event"), ("X","del event"), ("J","journal panel"),
                    ("W","write journal"), ("TAB","habits"), ("Q","quit")]

        kx = 2
        for key, label in keys:
            if kx > w - 14:
                break
            safestr(self.scr, h - 3, kx, f"[{key}]", curses.A_REVERSE)
            kx += len(key) + 2
            safestr(self.scr, h - 3, kx, f" {label}  ", curses.A_DIM)
            kx += len(label) + 3
        hline(self.scr, h - 2, w, "▁")

        # ── overlays ──
        if self.mode == "event_add_title":
            self._prompt(h, w, "  NEW EVENT  —  Title  ",
                         f"  Title:  {self.input_buf}▌")
        elif self.mode == "event_add_time":
            self._prompt(h, w, "  NEW EVENT  —  Time  ",
                         f"  HH:MM or leave blank:  {self.input_buf}▌")
        elif self.mode == "event_add_notes":
            self._prompt(h, w, "  NEW EVENT  —  Notes  ",
                         f"  Notes (optional):  {self.input_buf}▌")
        elif self.mode == "journal_edit":
            self._draw_journal_editor(h, w)

    def _draw_day_panel(self, h, w, sel_date, log, habits, px, pw):
        ds = sel_date.isoformat()

        # heading
        heading = sel_date.strftime("%A  %d %B %Y")
        safestr(self.scr, 2, px, heading, curses.A_BOLD)
        safestr(self.scr, 3, px, "─" * min(pw - 1, w - px - 2), curses.A_DIM)

        # panel tab buttons
        ev_lbl  = " EVENTS "
        jnl_lbl = " JOURNAL "
        if self.cal_panel == "events":
            safestr(self.scr, 4, px, ev_lbl,  curses.A_REVERSE | curses.A_BOLD)
            safestr(self.scr, 4, px + len(ev_lbl) + 1, jnl_lbl, curses.A_DIM)
        else:
            safestr(self.scr, 4, px, ev_lbl,  curses.A_DIM)
            safestr(self.scr, 4, px + len(ev_lbl) + 1, jnl_lbl, curses.A_REVERSE | curses.A_BOLD)
        safestr(self.scr, 5, px, "─" * min(pw - 1, w - px - 2), curses.A_DIM)

        # habit summary
        day_log = log.get(ds, {})
        done_h  = [hb for hb in habits if day_log.get(hb)]
        row = 6
        if habits:
            safestr(self.scr, row, px,
                    f"{len(done_h)}/{len(habits)} habits", curses.A_DIM)
            row += 1
            if done_h:
                joined = "✓ " + "  ✓ ".join(done_h)
                safestr(self.scr, row, px, joined[:pw], curses.A_DIM)
                row += 1
        row += 1

        if self.cal_panel == "events":
            self._draw_events_panel(h, w, ds, row, px, pw)
        else:
            self._draw_journal_panel(h, w, ds, row, px, pw)

    def _draw_events_panel(self, h, w, ds, start_row, px, pw):
        day_events = self.events.get(ds, [])
        row = start_row
        if not day_events:
            safestr(self.scr, row,     px, "No events scheduled.", curses.A_DIM)
            safestr(self.scr, row + 2, px, "Press  N  to add one.", curses.A_DIM)
            return
        for i, ev in enumerate(day_events):
            if row >= h - 6:
                break
            t     = ev.get("time", "")
            title = ev.get("title", "")
            notes = ev.get("notes", "")
            tpart = f"{t}  " if t else ""
            line  = f"  {tpart}{title}"
            pad   = " " * max(0, pw - len(line) - 1)
            if i == self.ev_cursor:
                # selected: full reverse (white on black, bold)
                safestr(self.scr, row, px, (line + pad)[:pw], curses.color_pair(1) | curses.A_BOLD)
            else:
                # non-selected: draw a visible box-like row with reverse dim = grey feel
                safestr(self.scr, row, px, (line + pad)[:pw], curses.A_REVERSE | curses.A_DIM)
            if notes and row + 1 < h - 6:
                safestr(self.scr, row + 1, px + 6, notes[:pw - 6], curses.A_DIM)
                row += 1
            row += 2

    def _draw_journal_panel(self, h, w, ds, start_row, px, pw):
        entry = self.journal.get(ds, {})
        text  = entry.get("text", "").strip()
        row   = start_row
        if not text:
            safestr(self.scr, row,     px, "No journal entry.", curses.A_DIM)
            safestr(self.scr, row + 2, px, "Press  W  to write.", curses.A_DIM)
            return
        modified = entry.get("modified", "")
        if modified:
            safestr(self.scr, row, px, f"edited {modified}", curses.A_DIM)
            row += 2
        wrap_w = max(10, pw - 2)
        for para in text.split("\n"):
            for line in (textwrap.wrap(para, wrap_w) if para.strip() else [""]):
                if row >= h - 6:
                    safestr(self.scr, row, px, "…", curses.A_DIM)
                    return
                safestr(self.scr, row, px, line)
                row += 1

    def _draw_journal_editor(self, h, w):
        bh = h - 6
        bw = min(w - 6, 88)
        y  = 3
        x  = (w - bw) // 2
        box(self.scr, y, x, bh, bw)

        ds    = self.cal_date_str() or today_str()
        title = f"  JOURNAL  —  {date.fromisoformat(ds).strftime('%A  %d %B %Y')}  "
        safestr(self.scr, y + 1, x + max(0, (bw - len(title)) // 2), title,
                curses.A_REVERSE | curses.A_BOLD)
        safestr(self.scr, y + 2, x + 2, "─" * (bw - 4), curses.A_REVERSE)

        text_w = bw - 8
        lines  = []
        for para in self.input_buf.split("\n"):
            wrapped = textwrap.wrap(para, text_w) if para.strip() else [""]
            lines.extend(wrapped)

        max_rows = bh - 7
        visible  = lines[-max_rows:] if len(lines) > max_rows else lines
        for i, line in enumerate(visible):
            if i < len(visible) - 1:
                safestr(self.scr, y + 3 + i, x + 4, line, curses.A_REVERSE)
            else:
                # last line: cursor inline at end of text
                safestr(self.scr, y + 3 + i, x + 4, line + "▌",
                        curses.A_REVERSE | curses.A_BOLD)

        # empty buffer or just hit Enter — cursor on next blank line
        if not visible or self.input_buf.endswith("\n"):
            cur_row = y + 3 + len(visible)
            if cur_row < y + bh - 3:
                safestr(self.scr, cur_row, x + 4, "▌", curses.A_REVERSE | curses.A_BOLD)

        safestr(self.scr, y + bh - 2, x + 3,
                "  Type freely    ENTER = new line    ESC = save & close    CTRL+X = discard  ",
                curses.A_REVERSE | curses.A_DIM)

    def _handle_calendar(self, key):
        if   self.mode == "cal_browse":      self._h_cal_browse(key)
        elif self.mode == "event_add_title": self._h_text_input(key, self._finish_ev_title)
        elif self.mode == "event_add_time":  self._h_text_input(key, self._finish_ev_time)
        elif self.mode == "event_add_notes": self._h_text_input(key, self._finish_ev_notes)
        elif self.mode == "journal_edit":    self._h_journal_edit(key)

    def _h_cal_browse(self, key):
        days = calendar.monthrange(self.cal_year, self.cal_month)[1]
        if key in (ord('q'), ord('Q')):
            sys.exit(0)
        elif key == curses.KEY_RIGHT:
            if self.cal_cursor < days - 1:
                self.cal_cursor += 1
            else:
                self._cal_next(); self.cal_cursor = 0
        elif key == curses.KEY_LEFT:
            if self.cal_cursor > 0:
                self.cal_cursor -= 1
            else:
                self._cal_prev()
                self.cal_cursor = calendar.monthrange(self.cal_year, self.cal_month)[1] - 1
        elif key == curses.KEY_DOWN:
            self.cal_cursor = min(self.cal_cursor + 7, days - 1)
        elif key == curses.KEY_UP:
            self.cal_cursor = max(self.cal_cursor - 7, 0)
        elif key == ord(']'):
            self._cal_next(); self.cal_cursor = 0
        elif key == ord('['):
            self._cal_prev(); self.cal_cursor = 0
        elif key in (ord('n'), ord('N')):
            self._ev_title = self._ev_time = self._ev_notes = ""
            self.mode = "event_add_title"; self.input_buf = ""
        elif key in (ord('x'), ord('X')):
            self._delete_event()
        elif key in (ord('e'), ord('E')):
            self.cal_panel = "events"
        elif key in (ord('j'), ord('J')):
            self.cal_panel = "journal"
        elif key in (ord('w'), ord('W')):
            ds = self.cal_date_str() or today_str()
            self.input_buf = self.journal.get(ds, {}).get("text", "")
            self.mode = "journal_edit"
            self.cal_panel = "journal"

    def _cal_next(self):
        if self.cal_month == 12: self.cal_month = 1;  self.cal_year += 1
        else: self.cal_month += 1

    def _cal_prev(self):
        if self.cal_month == 1:  self.cal_month = 12; self.cal_year -= 1
        else: self.cal_month -= 1

    def _finish_ev_title(self):
        self._ev_title = self.input_buf.strip()
        self.input_buf = ""; self.mode = "event_add_time"

    def _finish_ev_time(self):
        t = self.input_buf.strip()
        if t:
            try: datetime.strptime(t, "%H:%M"); self._ev_time = t
            except ValueError: self.status = "Invalid time, skipped"; self._ev_time = ""
        else:
            self._ev_time = ""
        self.input_buf = ""; self.mode = "event_add_notes"

    def _finish_ev_notes(self):
        self._ev_notes = self.input_buf.strip()
        self.input_buf = ""
        if self._ev_title:
            ds = self.cal_date_str()
            if ds:
                evs = self.events.setdefault(ds, [])
                evs.append({"title": self._ev_title,
                             "time":  self._ev_time,
                             "notes": self._ev_notes})
                evs.sort(key=lambda e: e.get("time") or "99:99")
                save_events(self.events)
                self.status = f"Event added:  {self._ev_title}"
                self.cal_panel = "events"
        self.mode = "cal_browse"

    def _delete_event(self):
        ds = self.cal_date_str()
        if not ds: return
        evs = self.events.get(ds, [])
        if not evs: self.status = "No events to delete"; return
        if self.ev_cursor < len(evs):
            removed = evs.pop(self.ev_cursor)
            if not evs: del self.events[ds]
            self.ev_cursor = max(0, self.ev_cursor - 1)
            save_events(self.events); self.status = f"Deleted:  {removed.get('title','')}"

    def _h_journal_edit(self, key):
        if key == 27:                        # ESC = save
            self._save_journal()
        elif key == 24:                      # Ctrl+X = discard
            self.mode = "cal_browse"; self.input_buf = ""
            self.status = "Journal discarded"
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.input_buf = self.input_buf[:-1]
        elif key in (curses.KEY_ENTER, 10, 13):
            self.input_buf += "\n"
        elif 32 <= key <= 126:               # all printable chars including space
            self.input_buf += chr(key)

    def _save_journal(self):
        ds   = self.cal_date_str() or today_str()
        text = self.input_buf.strip()
        if text:
            self.journal[ds] = {
                "text":     text,
                "modified": datetime.now().strftime("%d %b %Y  %H:%M")
            }
            save_journal(self.journal)
            self.status = "Journal saved  ✓"
        else:
            if ds in self.journal:
                del self.journal[ds]
                save_journal(self.journal)
            self.status = "Journal cleared"
        self.mode = "cal_browse"; self.input_buf = ""
        self.cal_panel = "journal"

    # ── shared prompt overlay ─────────────────────────────────────────────────

    def _prompt(self, h, w, title, body):
        bw  = max(len(body) + 10, len(title) + 10, 60)
        bh  = 9
        y   = (h - bh) // 2
        x   = max(0, (w - bw) // 2)
        box(self.scr, y, x, bh, bw)
        safestr(self.scr, y + 1, x + max(0, (bw - len(title)) // 2), title,
                curses.A_REVERSE | curses.A_BOLD)
        safestr(self.scr, y + 2, x + 2, "─" * (bw - 4), curses.A_REVERSE)
        safestr(self.scr, y + 4, x + 4, body[:bw - 6], curses.A_REVERSE)
        safestr(self.scr, y + 7, x + 4,
                "  ENTER  confirm      ESC  cancel  ",
                curses.A_REVERSE | curses.A_DIM)

# ─── Entry ────────────────────────────────────────────────────────────────────

def main():
    launch_notification()
    threading.Thread(target=reminder_daemon, daemon=True).start()
    curses.wrapper(lambda scr: App(scr).run())

if __name__ == "__main__":
    main()
