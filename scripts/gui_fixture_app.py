"""
Sythion (Synth) GUI fixture application (Phase D, TODO #82).

A minimal native Win32 window used to exercise the real UIA driver end to end.
It exposes, through standard Win32 controls and their built-in UIA providers:

  - a top-level window (Window pattern -> SetWindowVisualState),
  - a BUTTON child (Invoke pattern),
  - an EDIT child (Value pattern),
  - a STATIC status child (observable Name change).

On button click the fixture updates the status control to "clicked" so tests
can prove a UIA invoke had a real effect.

Usage:
  python gui_fixture_app.py --hwnds-path <json-file> --title <title>

The fixture writes the created window/child HWNDs as JSON to --hwnds-path and
then runs a message loop until the window is destroyed or the process is
terminated.
"""
import argparse
import json
import sys

import win32con
import win32gui

CLASS_NAME = "SythionGuiFixtureWindow"
STATIC_ID = 100
BUTTON_ID = 101
EDIT_ID = 102


class FixtureApp:
    def __init__(self, title: str, hwnds_path: str):
        self.title = title
        self.hwnds_path = hwnds_path
        self.hwnd = None
        self.button_hwnd = None
        self.edit_hwnd = None
        self.status_hwnd = None

    def wndproc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_COMMAND:
            ctrl_id = wparam & 0xFFFF
            notify = (wparam >> 16) & 0xFFFF
            if ctrl_id == BUTTON_ID and notify == win32con.BN_CLICKED:
                win32gui.SetWindowText(self.status_hwnd, "clicked")
                return 0
        elif msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def create(self) -> int:
        hinst = win32gui.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = CLASS_NAME
        wc.lpfnWndProc = self.wndproc
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass  # already registered in this process

        self.hwnd = win32gui.CreateWindow(
            CLASS_NAME,
            self.title,
            win32con.WS_OVERLAPPEDWINDOW,
            win32con.CW_USEDEFAULT,
            win32con.CW_USEDEFAULT,
            420,
            240,
            0,
            0,
            hinst,
            None,
        )

        self.status_hwnd = win32gui.CreateWindow(
            "STATIC", "idle", win32con.WS_CHILD | win32con.WS_VISIBLE,
            20, 20, 380, 24, self.hwnd, STATIC_ID, hinst, None)
        self.button_hwnd = win32gui.CreateWindow(
            "BUTTON", "Fixture Button",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
            20, 60, 140, 32, self.hwnd, BUTTON_ID, hinst, None)
        self.edit_hwnd = win32gui.CreateWindow(
            "EDIT", "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER | win32con.ES_AUTOHSCROLL,
            20, 110, 380, 28, self.hwnd, EDIT_ID, hinst, None)

        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
        win32gui.UpdateWindow(self.hwnd)

        with open(self.hwnds_path, "w", encoding="utf-8") as fh:
            json.dump({
                "window": int(self.hwnd),
                "button": int(self.button_hwnd),
                "edit": int(self.edit_hwnd),
                "status": int(self.status_hwnd),
            }, fh)
        return int(self.hwnd)

    def run(self) -> None:
        win32gui.PumpMessages()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sythion (Synth) GUI fixture app")
    parser.add_argument("--hwnds-path", required=True)
    parser.add_argument("--title", default="Sythion (Synth) GUI Fixture")
    args = parser.parse_args()

    app = FixtureApp(args.title, args.hwnds_path)
    app.create()
    print(f"fixture window {app.hwnd} ready", flush=True)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
