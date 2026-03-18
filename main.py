import tkinter as tk
import subprocess
import threading
import queue
import re
import collections

TARGET_HOST      = "8.8.8.8"
PING_INTERVAL    = 1.0        # seconds between pings
HISTORY_SIZE     = 30
WIN_W, WIN_H     = 140, 65
MARGIN           = 20

PING_GOOD        = 80
PING_WARN        = 200

COLOR_BG         = "#1e1e2e"
COLOR_GOOD       = "#a6e3a1"
COLOR_WARN       = "#f9e2af"
COLOR_BAD        = "#f38ba8"
COLOR_DIM        = "#45475a"
COLOR_TEXT       = "#cdd6f4"

SPARK_H          = 36


class PingWorker:
    def __init__(self, result_queue: queue.Queue):
        self._queue  = result_queue
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            result = self._do_ping()
            self._queue.put(result)
            self._stop.wait(PING_INTERVAL)

    def _do_ping(self) -> dict:
        try:
            proc = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", TARGET_HOST],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return self._parse(proc.stdout, proc.returncode)
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "latency_ms": None}
        except Exception:
            return {"status": "offline", "latency_ms": None}

    def _parse(self, stdout: str, returncode: int) -> dict:
        if re.search(r"time<1ms", stdout, re.IGNORECASE):
            return {"status": "online", "latency_ms": 1}
        m = re.search(r"time[=<](\d+)ms", stdout, re.IGNORECASE)
        if m:
            return {"status": "online", "latency_ms": int(m.group(1))}
        if re.search(r"Request timed out", stdout, re.IGNORECASE):
            return {"status": "timeout", "latency_ms": None}
        if re.search(r"Destination host unreachable", stdout, re.IGNORECASE):
            return {"status": "offline", "latency_ms": None}
        if returncode == 0:
            # Non-English locale fallback
            m2 = re.search(r"(\d{1,4})\s*ms", stdout)
            if m2:
                return {"status": "online", "latency_ms": int(m2.group(1))}
        return {"status": "offline", "latency_ms": None}


class Sparkline:
    def __init__(self, canvas: tk.Canvas):
        self._canvas  = canvas
        self._history = collections.deque([None] * HISTORY_SIZE, maxlen=HISTORY_SIZE)
        self._w = WIN_W
        self._h = SPARK_H

    def push(self, value):
        self._history.append(value)

    def redraw(self):
        c = self._canvas
        c.delete("all")

        # Baseline
        c.create_line(0, self._h - 1, self._w, self._h - 1,
                      fill=COLOR_DIM, width=1)

        valid = [v for v in self._history if v is not None]
        if not valid:
            return

        max_val = max(max(valid), PING_WARN)
        pts = self._scale(max_val)

        for i in range(1, len(pts)):
            if pts[i - 1] is None or pts[i] is None:
                continue
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            color = self._color(self._history[i])
            c.create_line(x0, y0, x1, y1, fill=color, width=1.5)

        if pts[-1] is not None:
            x, y = pts[-1]
            r = 2
            c.create_oval(x - r, y - r, x + r, y + r,
                          fill=self._color(self._history[-1]), outline="")

    def _scale(self, max_val: int):
        pts  = []
        step = self._w / (HISTORY_SIZE - 1)
        pad  = 3
        for i, val in enumerate(self._history):
            x = i * step
            if val is None:
                pts.append(None)
            else:
                norm = val / max_val
                y = self._h - pad - norm * (self._h - pad * 2)
                pts.append((x, y))
        return pts

    @staticmethod
    def _color(val):
        if val is None:
            return COLOR_BAD
        if val <= PING_GOOD:
            return COLOR_GOOD
        if val <= PING_WARN:
            return COLOR_WARN
        return COLOR_BAD


class OverlayApp:
    def __init__(self):
        self._queue  = queue.Queue()
        self._worker = PingWorker(self._queue)
        self._drag_x = 0
        self._drag_y = 0

        self.root = tk.Tk()
        self._build_window()
        self._build_widgets()
        self._setup_drag()
        self._setup_menu()

    def _build_window(self):
        root = self.root
        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-alpha", 0.50)
        root.configure(bg=COLOR_BG)

        sw = root.winfo_screenwidth()
        x  = sw - WIN_W - MARGIN
        y  = MARGIN
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    def _build_widgets(self):
        self.frame = tk.Frame(self.root, bg=COLOR_BG)
        self.frame.pack(fill=tk.BOTH, expand=True)

        self.ping_label = tk.Label(
            self.frame,
            text="●  -- ms",
            font=("Consolas", 11, "bold"),
            fg=COLOR_DIM,
            bg=COLOR_BG,
            anchor="center",
        )
        self.ping_label.pack(fill=tk.X, padx=6, pady=(5, 2))

        self.canvas = tk.Canvas(
            self.frame,
            width=WIN_W,
            height=SPARK_H,
            bg=COLOR_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill=tk.X)

        self.sparkline = Sparkline(self.canvas)

    def _setup_drag(self):
        for widget in (self.frame, self.ping_label, self.canvas):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>",     self._drag_motion)

    def _setup_menu(self):
        self._menu = tk.Menu(self.root, tearoff=0, bg=COLOR_BG, fg=COLOR_TEXT)
        self._menu.add_command(label="Quit", command=self._on_close)
        for widget in (self.frame, self.ping_label, self.canvas):
            widget.bind("<Button-3>", self._show_menu)

    def _show_menu(self, event):
        self._menu.tk_popup(event.x_root, event.y_root)

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_motion(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _poll(self):
        result = None
        try:
            while True:
                result = self._queue.get_nowait()
        except queue.Empty:
            pass

        if result is not None:
            self._update(result)

        self.root.after(200, self._poll)

    def _update(self, result: dict):
        latency = result["latency_ms"]
        status  = result["status"]
        color   = self._color(latency, status)

        if status == "online" and latency is not None:
            self.ping_label.config(text=f"●  {latency} ms", fg=color)
        else:
            self.ping_label.config(text="●  -- ms", fg=COLOR_BAD)

        self.sparkline.push(latency)
        self.sparkline.redraw()

    @staticmethod
    def _color(latency, status):
        if status != "online" or latency is None:
            return COLOR_BAD
        if latency <= PING_GOOD:
            return COLOR_GOOD
        if latency <= PING_WARN:
            return COLOR_WARN
        return COLOR_BAD

    def _on_close(self):
        self._worker.stop()
        self.root.destroy()

    def run(self):
        self._worker.start()
        self.root.after(200, self._poll)
        self.root.mainloop()


if __name__ == "__main__":
    app = OverlayApp()
    app.run()
