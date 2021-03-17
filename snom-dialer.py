"""
Copyright (C) 2021  Robert Lieback <info@zetabyte.de>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import sys
import os
import json
from PySide2.QtCore import Qt, QThread, QObject, Signal
from PySide2.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMainWindow, QWidget, QHBoxLayout, QLineEdit, QPushButton
from PySide2.QtGui import QCursor
from pynput import keyboard
import win32gui, win32con, win32api

import qtawesome as qta
from snom import Snom


class DialWindow(QMainWindow):
    def __init__(self, configuration):
        super().__init__()
        self.config = configuration

        self.snom = Snom(ip=self.config['ip'], username=self.config['username'], password=self.config['password'])

        self.window_title = "Snom Dialer"
        self.setWindowTitle(self.window_title)
        self.setFixedSize(400, 50)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.shift_pressed = False

        self.number_input = QLineEdit()
        self.number_input.returnPressed.connect(self.dial)

        self.dial_button = QPushButton(
            qta.icon("fa.phone-square", options=[{'color': 'green', 'scale_factor': 1.3}]), "")
        self.dial_button.clicked.connect(self.dial)

        self.hangup_button = QPushButton(
            qta.icon("fa.phone-square", options=[{'color': 'red', 'scale_factor': 1.3}]), "")
        self.hangup_button.clicked.connect(self.hangup)

        layout = QHBoxLayout()
        layout.addWidget(self.number_input)
        layout.addWidget(self.dial_button)
        layout.addWidget(self.hangup_button)
        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        self.hotkey_thread = HotKeys()
        self.hotkey_thread.start()

        self.hotkey_thread.hotkey_show_main_window.sig.connect(self.show)
        self.hotkey_thread.hotkey_hangup.sig.connect(self.hangup)

    def keyPressEvent(self, event) -> None:
        if event.key() == 16777248:  # shift
            self.shift_pressed = True
        if event.key() == 16777216:  # Esc
            self.hide()

    def keyReleaseEvent(self, event) -> None:
        if event.key() == 16777248:
            self.shift_pressed = False

    def dial(self):
        if self.shift_pressed:
            if ";" in self.number_input.text():  # send raw keyevent
                self.snom.key_events(self.number_input.text().replace("#", "%23").replace("*", "%2A"))
            else:
                self.snom.key_events(";".join(self.number_input.text()).replace("#", "%23").replace("*", "%2A"))
        else:
            self.snom.dial(self.number_input.text().replace("#", "%23").replace("*", "%2A"))

    def hangup(self):
        self.snom.hangup_all()

    def show(self):
        super().show()

        def windowEnumerationHandler(hwnd, top_windows):
            top_windows.append((hwnd, win32gui.GetWindowText(hwnd)))

        top_windows = []
        win32gui.EnumWindows(windowEnumerationHandler, top_windows)
        for i in top_windows:
            if self.window_title in i[1]:
                win32gui.ShowWindow(i[0], win32con.SW_MINIMIZE)
                win32gui.ShowWindow(i[0], win32con.SW_RESTORE)
                break

        self.number_input.setFocus()
        self.number_input.selectAll()


class TrayIcon(QSystemTrayIcon):

    def __init__(self, icon, dialwindow):
        super().__init__(icon)
        self.activated.connect(self.showMenuOnTrigger)

        self.mainwindow = dialwindow

        self.menu = QMenu()
        self.menu.addAction("Dial", self.mainwindow.show)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.exit)
        self.setContextMenu(self.menu)

    def showMenuOnTrigger(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.contextMenu().popup(QCursor.pos())

    def exit(self):
        self.mainwindow.hotkey_thread.terminate()
        sys.exit()


class ShowWindowSignal(QObject):
    sig = Signal()


class HangupSignal(QObject):
    sig = Signal()


class HotKeys(QThread):
    def __init__(self, parent=None):
        QThread.__init__(self, parent)
        self.hotkey_show_main_window = ShowWindowSignal()
        self.hotkey_hangup = HangupSignal()

    def run(self):
        with keyboard.GlobalHotKeys(
                {mainwindow.config["hotkey_show_window"]: self.show_mainwindow,
                 mainwindow.config["hotkey_hangup"]: self.hangup}
        ) as h:
            h.join()

    def show_mainwindow(self):
        self.hotkey_show_main_window.sig.emit()

    def hangup(self):
        self.hotkey_hangup.sig.emit()


if __name__ == '__main__':

    conf_file = os.path.join(os.getcwd(), "config.json")

    if os.path.isfile(conf_file):
        with open(conf_file) as f:
            config = json.load(f)

    else:
        # write a json file as a template and exit
        config = {
            "ip": "192.168.188.221",
            "username": "admin",
            "password": "tester",
            "hotkey_show_window": "<ctrl>+<alt>+s",
            "hotkey_hangup": "<ctrl>+<alt>+x"
        }
        with open(conf_file, "w") as f:
            json.dump(config, f, indent=4)
        win32api.MessageBox(
            0,
            f"Couldn't find config, so i've written an empty template to {conf_file}. "
            f"Please fill in your configuration and start this application again.",
            "Error"
        )
        sys.exit(-1)

    if len(sys.argv) > 1:  # batchmode
        import argparse

        parser = argparse.ArgumentParser(description="Snom remote dialer")
        parser.add_argument("command", type=str, help="One of dial, keyevent, hangup or hangup_all")
        parser.add_argument("parameter", type=str, help="Optional parameter to command", nargs='?')
        args = parser.parse_args()

        snom = Snom(ip=config["ip"], username=config["username"], password=config["password"])

        if args.command == "dial" and args.parameter:
            snom.dial(args.parameter)
        elif args.command == "keyevent" and args.parameter:
            snom.key_events(args.parameter)
        elif args.command == "hangup":
            snom.hangup()
        elif args.command == "hangup_all":
            snom.hangup_all()
        else:
            print("ERROR: Couldn't parse parameters.\n")
            parser.parse_args(['-h'])


        sys.exit()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    icon = qta.icon("fa.phone-square", options=[{'color': 'green', 'scale_factor': 1.3}])
    app.setWindowIcon(icon)

    mainwindow = DialWindow(config)
    tray = TrayIcon(icon, mainwindow)
    tray.show()

    sys.exit(app.exec_())
