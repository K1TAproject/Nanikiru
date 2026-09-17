"""Small Tk debugging view; all mutations go through Recorder."""

from dataclasses import asdict
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

if __package__ in (None, ""):
    # IDE script execution does not establish the package's parent directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nanikiru import Discard, Draw, Phase, Recorder, Tile
else:
    from . import Discard, Draw, Phase, Recorder, Tile


PROJECT = Path(__file__).resolve().parents[1]
SPECIAL_ACTIONS = ("吃", "碰", "明杠", "暗杠", "加杠", "立直", "自摸", "荣和", "流局")
PHASES = {Phase.DISCARD: "待弃牌", Phase.RESPONSE: "待响应 / 下家摸牌", Phase.EXHAUSTED: "牌山耗尽"}


class DebugWindow:
    def __init__(self, root, recorder):
        self.root, self.recorder = root, recorder
        self.preview = None
        self.dirty = False
        self.action_builders = {
            "摸牌": lambda p, t, mode: Draw(p, Tile.parse(t) if t else None),
            "弃牌": lambda p, t, mode: Discard(p, Tile.parse(t), mode),
        }
        self.messages = Queue()
        self.testing = False
        root.title("Nanikiru 牌局测试工具")
        root.geometry("1100x800")
        root.minsize(850, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = ttk.Frame(root, padding=6)
        toolbar.pack(fill="x")
        for label, command in (("重置示例", self.reset), ("加载 JSON", self.load),
                               ("保存 JSON", self.save), ("暂停 / 恢复", self.pause),
                               ("撤销最近动作", self.undo)):
            ttk.Button(toolbar, text=label, command=command).pack(side="left", padx=3)
        self.test_button = ttk.Button(toolbar, text="运行自动化测试", command=self.run_tests)
        self.test_button.pack(side="left", padx=3)
        self.status = ttk.Label(root, padding=6)
        self.status.pack(fill="x")

        form = ttk.LabelFrame(root, text="手动动作（牌：1m～9m / p / s；1z～7z；赤五：0m / 0p / 0s）", padding=6)
        form.pack(fill="x", padx=6)
        self.action = tk.StringVar(value="弃牌")
        self.player = tk.StringVar(value="0")
        self.tile = tk.StringVar()
        self.mode = tk.StringVar(value="摸切")
        self.action_box = ttk.Combobox(form, textvariable=self.action, state="readonly", width=12)
        self.action_box.pack(side="left", padx=3)
        for title, variable, values in (("玩家", self.player, ("0", "1", "2", "3")),
                                        ("弃牌方式", self.mode, ("摸切", "手切", "未知"))):
            ttk.Label(form, text=title).pack(side="left", padx=3)
            ttk.Combobox(form, textvariable=variable, values=values, state="readonly", width=7).pack(side="left")
        ttk.Label(form, text="牌（对手摸牌留空）").pack(side="left", padx=3)
        ttk.Entry(form, textvariable=self.tile, width=7).pack(side="left")
        ttk.Button(form, text="提交动作", command=self.submit).pack(side="left", padx=6)

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.state_text = ScrolledText(notebook, wrap="word", font=("Microsoft YaHei", 10))
        self.json_text = ScrolledText(notebook, wrap="none")
        self.log_text = ScrolledText(notebook, wrap="word", height=12)
        for widget, title in ((self.state_text, "四家当前数据"), (self.json_text, "完整状态 JSON"),
                              (self.log_text, "操作 / 错误 / 测试输出")):
            notebook.add(widget, text=title)
        self.notebook = notebook
        history_frame = ttk.LabelFrame(root, text="接入后的动作历史（初始快照以前的动作不补造）", padding=4)
        history_frame.pack(fill="x", padx=6, pady=4)
        self.history = ttk.Combobox(history_frame, state="readonly", width=65)
        self.history.pack(side="left", fill="x", expand=True)
        for title, callback in (("查看此步", self.show_step), ("返回当前", self.live), ("回退并删除后续", self.rewind)):
            ttk.Button(history_frame, text=title, command=callback).pack(side="left", padx=3)
        self.register_actions()
        self.refresh()
        self.log("已加载人工示例。特殊动作尚未实现；可加载记录 JSON 测试其他局面。")
        self.poll_id = root.after(100, self.poll_tests)

    def register_action(self, name, builder):
        """builder(player, tile_text, tsumogiri) returns a core action, or None on cancel.

        A future builder may open a parameter dialog. Recorder must independently
        support and validate the returned action; this hook never changes state directly.
        """
        self.action_builders[name] = builder
        self.register_actions()

    def register_actions(self):
        self.action_box["values"] = list(dict.fromkeys((*self.action_builders, *SPECIAL_ACTIONS)))

    @staticmethod
    def put(widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", content)
        widget.configure(state="disabled")

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def report_error(self, exc):
        self.log(f"失败：{exc}")
        self.notebook.select(self.log_text)

    def refresh(self):
        state = self.recorder.state if self.preview is None else self.recorder.replay(self.preview)
        tag = "当前" if self.preview is None else f"只读回放：第 {self.preview} 步"
        self.status.configure(text=f"{tag} | 东 {state.hand_number} 局 / {state.honba} 本场 / 供托 {state.riichi_sticks} | "
                              f"{PHASES[state.phase]} | 玩家 {state.actor} | 剩余可摸 {state.remaining_draws} | "
                              f"{'已暂停' if self.recorder.paused else '记录中'} | {'未保存' if self.dirty else '无未保存修改'}")
        lines = [f"庄家：{state.dealer_seat}  观察者：{state.observer_seat}  "
                 f"宝牌指示牌：{' '.join(map(str, state.dora_indicators))}"]
        for p in state.players:
            drawn = p.hand.drawn_tile
            lines.extend(["", f"玩家 {p.seat} {'（自家）' if p.seat == state.observer_seat else ''} | "
                          f"点数 {p.score} | 自风 {state.seat_wind(p.seat)} | 立直 {'是' if p.riichi else '否'}",
                          f"暗手：{' '.join(map(str, p.hand.known_tiles)) or '无已知牌'}；未知 {p.hand.unknown_count} 张；"
                          f"摸入：{str(drawn.tile) if drawn and drawn.tile else '未知' if drawn else '无'}；总数 {p.hand.count}",
                          "副露：" + (" / ".join(f"{m.kind.value}: {' '.join(map(str, m.tiles))} 来源 {m.source_discard_id or '无'}"
                                               for m in p.melds) or "无"),
                          "牌河：" + ("  ".join(f"{i}:{d.tile}"
                                               + (f"[被{d.claimed_by}鸣走]" if d.claimed_by is not None else "")
                                               + ("[立直]" if d.is_riichi_declaration else "")
                                               + ({True: "[摸切]", False: "[手切]", None: "[未知]"}[d.is_tsumogiri])
                                               for i, d in enumerate(p.discards, 1)) or "空")])
        self.put(self.state_text, "\n".join(lines))
        self.put(self.json_text, json.dumps(asdict(state), ensure_ascii=False, indent=2))
        self.history["values"] = ["0：初始快照"] + [f"{i}：{a}" for i, a in enumerate(self.recorder.events, 1)]
        self.history.current(len(self.recorder.events) if self.preview is None else self.preview)
        if self.preview is None:
            player = (state.actor + 1) % 4 if state.phase == Phase.RESPONSE else state.actor
            self.player.set(str(player))
            self.action.set("摸牌" if state.phase == Phase.RESPONSE else "弃牌")
            drawn = state.players[player].hand.drawn_tile
            self.tile.set(str(drawn.tile) if drawn and drawn.tile else "")

    def submit(self):
        try:
            if self.preview is not None:
                raise ValueError("当前为只读回放，请先返回当前，或回退并删除后续动作。")
            name = self.action.get()
            if name not in self.action_builders:
                raise ValueError(f"{name}尚未实现，记录未改变。")
            action = self.action_builders[name](int(self.player.get()), self.tile.get().strip(),
                                                {"摸切": True, "手切": False, "未知": None}[self.mode.get()])
            if action is None:
                return
            self.recorder.submit(action)
            self.dirty = True
            self.log(f"成功 #{len(self.recorder.events)}：{action}")
            self.refresh()
        except (ValueError, TypeError) as exc:
            self.report_error(exc)

    def confirm_replace(self):
        return not self.dirty or messagebox.askyesno("未保存修改", "放弃未保存的修改？", parent=self.root)

    def reset(self):
        if self.confirm_replace():
            from examples.record_round import sample_snapshot
            self.recorder = Recorder(sample_snapshot()[0])
            self.preview, self.dirty = None, False
            self.log("已重置为人工示例。")
            self.refresh()

    def load(self):
        path = filedialog.askopenfilename(parent=self.root, initialdir=PROJECT / "examples", filetypes=[("记录 JSON", "*.json")])
        if path and self.confirm_replace():
            try:
                recorder = Recorder.load(path)
                self.recorder = recorder
                self.preview, self.dirty = None, False
                self.log(f"已加载：{path}")
                self.refresh()
            except (ValueError, OSError) as exc:
                self.report_error(exc)

    def save(self):
        path = filedialog.asksaveasfilename(parent=self.root, initialdir=PROJECT / "examples",
                                          defaultextension=".json", filetypes=[("记录 JSON", "*.json")])
        if path:
            try:
                self.recorder.save(path)
                self.dirty = False
                self.log(f"已保存当前完整记录（不是回放预览）：{path}")
                self.refresh()
            except OSError as exc:
                self.report_error(exc)

    def pause(self):
        self.recorder.resume() if self.recorder.paused else self.recorder.stop()
        self.dirty = True
        self.log("已暂停。" if self.recorder.paused else "已恢复。")
        self.refresh()

    def undo(self):
        try:
            if self.preview is not None:
                raise ValueError("先返回当前记录再撤销。")
            self.recorder.undo()
            self.dirty = True
            self.log("已撤销最近动作。")
            self.refresh()
        except ValueError as exc:
            self.report_error(exc)

    def show_step(self):
        self.preview = self.history.current()
        self.refresh()

    def live(self):
        self.preview = None
        self.refresh()

    def rewind(self):
        count = self.history.current()
        if count < len(self.recorder.events) and messagebox.askyesno("回退", f"删除第 {count} 步以后的所有动作？", parent=self.root):
            self.recorder.rewind(count)
            self.preview, self.dirty = None, True
            self.log(f"已回退到第 {count} 步。")
            self.refresh()

    def run_tests(self):
        if self.testing:
            return
        self.testing = True
        self.test_button.configure(state="disabled")
        self.notebook.select(self.log_text)
        self.log("开始自动化测试（独立进程，不改变当前牌局）…")

        def worker():
            try:
                result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                        cwd=PROJECT, capture_output=True, text=True, encoding="utf-8",
                                        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.messages.put(result.stdout + result.stderr + f"\n测试进程退出码：{result.returncode}")
            except OSError as exc:
                self.messages.put(f"无法运行测试：{exc}")
        Thread(target=worker, daemon=True).start()

    def poll_tests(self):
        try:
            self.log(self.messages.get_nowait())
            self.testing = False
            self.test_button.configure(state="normal")
        except Empty:
            pass
        self.poll_id = self.root.after(100, self.poll_tests)

    def close(self):
        if self.confirm_replace():
            self.root.after_cancel(self.poll_id)
            self.root.destroy()


def main():
    from examples.record_round import sample_snapshot
    root = tk.Tk()
    DebugWindow(root, Recorder(sample_snapshot()[0]))
    root.mainloop()


if __name__ == "__main__":
    main()
