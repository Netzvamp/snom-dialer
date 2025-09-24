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
import logging
import signal
import socket
from datetime import datetime
from flask import Flask, request
from werkzeug.serving import make_server
from PySide6.QtCore import Qt, QThread, QObject, Signal, QTimer, QEventLoop
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMainWindow, QWidget, QHBoxLayout,
    QLineEdit, QPushButton, QDialog, QFormLayout, QLabel, QDialogButtonBox,
    QMessageBox, QVBoxLayout, QComboBox, QSizePolicy, QGroupBox, QCheckBox
)
from PySide6.QtGui import QCursor, QIntValidator
from pynput import keyboard
import win32gui, win32con

import qtawesome as qta
from snom import Snom, SnomConnectionError
from urllib.parse import quote_plus
import threading
import requests
import re
import webbrowser

DEFAULT_WEB_PORT = 58231

logger = logging.getLogger(__name__)


def save_config_file(path: str, config: dict) -> None:
    """
    Persist the configuration to a JSON file.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


class IncomingCallDialog(QDialog):
    """
    Non-modal popup showing incoming call information with a hangup button.
    """

    def __init__(self, mainwindow: QMainWindow):
        super().__init__(mainwindow)
        self._mw = mainwindow
        self.setWindowTitle("Call info")
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.lbl_title = QLabel("Incoming call")
        self.lbl_remote_name = QLabel("-")
        self.lbl_remote_uri = QLabel("-")
        self.lbl_status = QLabel("Ringing")
        self.lbl_duration = QLabel("00:00")

        self.btn_hangup = QPushButton("Hang up")
        self.btn_close = QPushButton("Close")

        self.btn_answer = QPushButton("Answer")
        self.btn_answer.clicked.connect(self._mw.answer)

        layout = QVBoxLayout()
        layout.addWidget(self.lbl_title)
        layout.addWidget(QLabel("Caller:"))
        layout.addWidget(self.lbl_remote_name)
        layout.addWidget(QLabel("SIP/Remote:"))
        layout.addWidget(self.lbl_remote_uri)
        layout.addWidget(QLabel("Status:"))
        layout.addWidget(self.lbl_status)
        layout.addWidget(QLabel("Duration:"))
        layout.addWidget(self.lbl_duration)

        row = QHBoxLayout()
        row.addWidget(self.btn_answer)
        row.addWidget(self.btn_hangup)
        row.addWidget(self.btn_close)
        layout.addLayout(row)

        self.setLayout(layout)

        self.btn_hangup.clicked.connect(self._mw.hangup)
        self.btn_close.clicked.connect(self.close)

    def update_info(self, data: dict) -> None:
        """
        Update labels based on Action URL query parameters.
        """
        display_remote = data.get("display_remote") or ""
        remote = data.get("remote") or ""
        self.lbl_remote_name.setText(display_remote if display_remote else "(unknown)")
        self.lbl_remote_uri.setText(remote if remote else "(unknown)")

    def set_status(self, text: str) -> None:
        """
        Update call status label.
        """
        self.lbl_status.setText(text)

    def set_duration(self, text: str) -> None:
        """
        Update call duration label.
        """
        self.lbl_duration.setText(text)

    def set_title(self, text: str) -> None:
        """
        Update dialog title label.
        """
        self.lbl_title.setText(text)

    def show_answer_button(self, visible: bool) -> None:
        """
        Show or hide the answer button depending on call context.
        """
        self.btn_answer.setVisible(visible)


class DialWindow(QMainWindow):
    def __init__(self, configuration, config_path: str):
        super().__init__()
        self.config = configuration
        self.config_file = config_path

        self.snom = Snom(ip=self.config['ip'], username=self.config['username'], password=self.config['password'])

        self.window_title = "Snom Dialer"
        self.setWindowTitle(self.window_title)
        self.setFixedHeight(50)
        initial_width = int(self.config.get("window_width", 500) or 500)
        self.resize(max(300, initial_width), self.height())
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.shift_pressed = False

        self.number_input = QComboBox()
        self.number_input.setEditable(True)
        self.number_input.setInsertPolicy(QComboBox.NoInsert)
        self.number_input.setMaxCount(100)
        self.number_input.lineEdit().returnPressed.connect(self.dial)

        self.dial_button = QPushButton(
            qta.icon("fa5s.phone-square", options=[{'color': 'green', 'scale_factor': 1.3}]), "")
        self.dial_button.clicked.connect(self.dial)

        self.hangup_button = QPushButton(
            qta.icon("fa5s.phone-square", options=[{'color': 'red', 'scale_factor': 1.3}]), "")
        self.hangup_button.clicked.connect(self.hangup)

        self.settings_button = QPushButton(qta.icon("fa5s.cog"), "")
        self.settings_button.clicked.connect(self.open_settings_dialog)

        self.number_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dial_button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.hangup_button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.settings_button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        layout = QHBoxLayout()
        layout.addWidget(self.number_input)
        layout.addWidget(self.dial_button)
        layout.addWidget(self.hangup_button)
        layout.addWidget(self.settings_button)
        layout.setStretch(0, 1)  # number_input (index 0) takes all extra width
        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        self.hotkey_thread = None
        self.restart_hotkeys()

        self.action_events = ActionEventSignal()
        self.action_events.incoming.connect(self.on_incoming_event)
        self.action_events.ended.connect(self.on_call_ended)
        self.action_events.connected.connect(self.on_call_connected)
        self.action_events.outgoing.connect(self.on_outgoing_event)
        self.action_events.onhook.connect(self.on_onhook_event)
        self.action_events.offhook.connect(self.on_offhook_event)

        self.call_timer = QTimer(self)
        self.call_timer.setInterval(1000)
        self.call_timer.timeout.connect(self._update_call_duration)
        self.call_started_at = None

        self.incoming_dialog = None
        self.action_server = None
        self.recent_numbers = self.config.get("recent_numbers", [])
        if not isinstance(self.recent_numbers, list):
            self.recent_numbers = []
        self._reload_recent_numbers()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Shift:  # shift
            self.shift_pressed = True
        if event.key() == Qt.Key_Escape:  # Esc
            self.hide()

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Shift:
            self.shift_pressed = False

    def dial(self):
        logger.info("Dial requested")
        text = self.number_input.currentText()
        if self.shift_pressed:
            if ";" in text:
                self.snom.key_events(text.replace("#", "%23").replace("*", "%2A"))
            else:
                self.snom.key_events(";".join(text).replace("#", "%23").replace("*", "%2A"))
        else:
            self.snom.dial(text.replace("#", "%23").replace("*", "%2A"))
        self._add_recent_number(text)

    def hangup(self):
        logger.info("Hangup requested")
        self.snom.hangup_all()

    def answer(self) -> None:
        """
        Attempt to answer an incoming call using the Snom remote command.
        """
        logger.info("Answer requested")
        ok, msg = self.snom.answer()
        if not ok:
            logger.warning(f"Answer failed: {msg}")

    def restart_hotkeys(self) -> None:
        """
        Restart the global hotkeys thread to apply current configuration.
        """
        if self.hotkey_thread is not None:
            try:
                self.hotkey_thread.stop()
                self.hotkey_thread.wait(2000)
            except Exception as exc:
                logger.warning(f"Failed stopping hotkeys: {exc}")
        self.hotkey_thread = HotKeys()
        self.hotkey_thread.hotkey_show_main_window.sig.connect(self.show)
        self.hotkey_thread.hotkey_hangup.sig.connect(self.hangup)
        self.hotkey_thread.start()
        logger.info("Global hotkeys (re)started")

    def reconfigure_snom(self) -> None:
        """
        Recreate Snom client with the current configuration.
        Ensures dial and Action-URL operations use fresh credentials and host.
        """
        self.snom = Snom(
            ip=self.config["ip"],
            username=self.config["username"],
            password=self.config["password"],
        )
        logger.info("Reconfigured Snom client with updated settings")

    def open_settings_dialog(self) -> None:
        """
        Open the settings dialog from the main window.
        """
        dlg = SettingsDialog(self)
        dlg.exec()

    def _reload_recent_numbers(self) -> None:
        """
        Refresh the combo box items from the configuration's recent_numbers list.
        """
        items = [n for n in self.config.get("recent_numbers", []) if isinstance(n, str)]
        self.number_input.blockSignals(True)
        try:
            self.number_input.clear()
            for n in items:
                self.number_input.addItem(n)
        finally:
            self.number_input.blockSignals(False)

    def _add_recent_number(self, number: str) -> None:
        """
        Add a number to the recent list (MRU), de-duplicate, and limit to 100 entries.
        Persist the updated configuration.
        """
        num = (number or "").strip()
        if not num:
            return
        lst = [n for n in self.config.get("recent_numbers", []) if isinstance(n, str)]
        if num in lst:
            lst.remove(num)
        lst.insert(0, num)
        if len(lst) > 100:
            lst = lst[:100]
        self.config["recent_numbers"] = lst
        self._reload_recent_numbers()
        try:
            save_config_file(self.config_file, self.config)
            logger.info(f"Added number to recent list: {num}")
        except Exception as exc:
            logger.warning(f"Failed to persist recent numbers: {exc}")

    def _fill_placeholder_url(self, template: str, data: dict) -> str:
        """
        Replace placeholders in the template with URL-encoded values from data.

        Supported placeholders (from phone runtime data):
        {remote}, {display_remote}, {local}, {call_id}, {display_local},
        {active_url}, {active_user}, {active_host}, {csta_id},
        {expansion_module}, {active_key}, {phone_ip}, {local_ip},
        {nr_ongoing_calls}, {context_url}, {cancel_reason}, {longpress_key},
        plus {timestamp}. Unknown placeholders fall back to values from data.
        """
        values = {
            "remote": data.get("remote") or "",
            "display_remote": data.get("display_remote") or "",
            "local": data.get("local") or "",
            "call_id": data.get("call_id") or "",
            "display_local": data.get("display_local") or "",
            "active_url": data.get("active_url") or "",
            "active_user": data.get("active_user") or "",
            "active_host": data.get("active_host") or "",
            "csta_id": data.get("csta_id") or "",
            "expansion_module": data.get("expansion_module") or "",
            "active_key": data.get("active_key") or "",
            "phone_ip": data.get("phone_ip") or "",
            "local_ip": data.get("local_ip") or "",
            "nr_ongoing_calls": data.get("nr_ongoing_calls") or "",
            "context_url": data.get("context_url") or "",
            "cancel_reason": data.get("cancel_reason") or "",
            "longpress_key": data.get("longpress_key") or "",
            "timestamp": datetime.now().isoformat(),
        }

        def repl(match):
            key = match.group(1)
            raw = values.get(key, data.get(key, ""))
            return quote_plus(str(raw))

        return re.sub(r"{(\w+)}", repl, template)

    def _trigger_action_by_key(self, config_key: str, data: dict) -> None:
        template = (self.config.get(config_key) or "").strip()
        if not template:
            logger.info(f"Action URL template empty for key: {config_key}")
            return
        try:
            url = self._fill_placeholder_url(template, data)
        except Exception as exc:
            logger.warning(f"Failed to render action URL for {config_key}: {exc}")
            return

        open_in_browser = bool(self.config.get(f"{config_key}_open_browser", False))
        logger.info(f"Triggering [{config_key}] -> {url} (browser={open_in_browser})")

        def _do():
            try:
                if open_in_browser:
                    webbrowser.open_new_tab(url)
                    logger.info(f"Action URL [{config_key}] opened in browser")
                else:
                    resp = requests.get(url, timeout=3.0)
                    logger.info(f"Action URL [{config_key}] called: {resp.status_code}")
            except Exception as exc:
                logger.warning(f"Action URL [{config_key}] execution failed: {exc}")

        threading.Thread(target=_do, daemon=True).start()

    def _trigger_incoming_action(self, data: dict) -> None:
        """
        Call the configured Incoming Action URL in a background thread.
        """
        template = (self.config.get("incoming_action_url") or "").strip()
        if not template:
            return

        try:
            url = self._fill_placeholder_url(template, data)
        except Exception as exc:
            logger.warning(f"Failed to render incoming action URL: {exc}")
            return

        def _do_request():
            try:
                resp = requests.get(url, timeout=3.0)
                logger.info(f"Incoming Action URL called: {resp.status_code}")
            except Exception as exc:
                logger.warning(f"Incoming Action URL call failed: {exc}")

        threading.Thread(target=_do_request, daemon=True).start()

    def _local_ip_for_phone(self) -> str:
        """
        Determine the local source IP used to reach the phone's IP.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((self.config["ip"], 80))
                return s.getsockname()[0]
        except OSError as exc:
            logger.error(f"Could not determine local IP for phone {self.config['ip']}: {exc}")
            # Fallback: loopback (not useful for Snom), but avoid crash
            return "127.0.0.1"

    def start_action_server(self) -> None:
        """
        Start the Flask-based Action URL server and push URLs to the phone.
        """
        desired_port = int(self.config.get("web_port", DEFAULT_WEB_PORT))
        # Find a usable port by trying desired_port .. desired_port+20
        server = None
        for port in [desired_port] + list(range(desired_port + 1, desired_port + 21)):
            try:
                server = ActionUrlServer("0.0.0.0", port, self.action_events)
                actual_port = port
                break
            except OSError as exc:
                logger.warning(f"Port {port} unavailable for Action URL server: {exc}")
        if server is None:
            logger.error("Could not start Action URL server on any port")
            return

        self.action_server = server
        self.action_server.start()
        local_ip = self._local_ip_for_phone()
        base_url = f"http://{local_ip}:{actual_port}"

        ok, msg = self.snom.set_action_urls(base_url)
        if ok:
            logger.info(f"Action URLs configured to {base_url}")
            # Persist potentially changed port
            if self.config.get("web_port") != actual_port:
                self.config["web_port"] = actual_port
                try:
                    save_config_file(self.config_file, self.config)
                except Exception as exc:
                    logger.warning(f"Failed to persist web_port to config: {exc}")
            # Verify phone can reach our callback server
            self._verify_phone_reachability()
        else:
            logger.error(f"Failed to configure Action URLs: {msg}")

    def restart_action_server(self) -> None:
        """
        Restart the Action URL server and reconfigure Snom action URLs.
        """
        if self.action_server is not None:
            try:
                self.action_server.stop()
                self.action_server.wait(2000)
            except Exception as exc:
                logger.warning(f"Failed stopping Action URL server: {exc}")
            self.action_server = None
        self.start_action_server()

    def _verify_phone_reachability(self, timeout_ms: int = 3000) -> bool:
        """
        Trigger an Action URL event (OFFHOOK/ONHOOK) and wait for callback.
        """
        loop = QEventLoop()
        received = {"ok": False}

        def _on_reachable(_data: dict) -> None:
            received["ok"] = True
            if loop.isRunning():
                loop.quit()

        self.action_events.reachable.connect(_on_reachable)
        try:
            try:
                self.snom.send_request(f"{self.snom.cmd_url}key=OFFHOOK", timeout=2.0)
            except SnomConnectionError as exc:
                logger.warning(f"OFFHOOK trigger failed: {exc}")

            QTimer.singleShot(500, lambda: self.snom.send_request(
                f"{self.snom.cmd_url}key=ONHOOK", timeout=2.0))

            QTimer.singleShot(timeout_ms, loop.quit)
            loop.exec()
        finally:
            try:
                self.action_events.reachable.disconnect(_on_reachable)
            except Exception:
                pass

        if received["ok"]:
            logger.info("Phone reached local web server via Action URLs")
            return True

        logger.warning("No Action URL callback received from phone after OFFHOOK/ONHOOK test")
        return False

    def on_incoming_event(self, data: dict) -> None:
        """
        Show or update the incoming call popup with provided data.
        """
        logger.info("Handling incoming event -> triggering user Action URL")
        if self.incoming_dialog is None:
            self.incoming_dialog = IncomingCallDialog(self)
        self.incoming_dialog.set_title("Incoming call")
        self.incoming_dialog.show_answer_button(True)
        self.incoming_dialog.update_info(data)
        self.incoming_dialog.show()
        self.incoming_dialog.raise_()
        self.incoming_dialog.activateWindow()
        self.incoming_dialog.set_status("Ringing")
        self.incoming_dialog.set_duration("00:00")
        if self.call_timer.isActive():
            self.call_timer.stop()
        self.call_started_at = None
        self._trigger_action_by_key("action_url_incoming", data)

    def on_outgoing_event(self, data: dict) -> None:
        """
        Show or update the popup for outgoing calls (dialing).
        """
        logger.info("Handling outgoing event -> triggering user Action URL")
        if self.incoming_dialog is None:
            self.incoming_dialog = IncomingCallDialog(self)
        self.incoming_dialog.set_title("Outgoing call")
        self.incoming_dialog.show_answer_button(False)
        self.incoming_dialog.update_info(data)
        self.incoming_dialog.show()
        self.incoming_dialog.raise_()
        self.incoming_dialog.activateWindow()
        self.incoming_dialog.set_status("Dialing")
        self.incoming_dialog.set_duration("00:00")
        if self.call_timer.isActive():
            self.call_timer.stop()
        self.call_started_at = None
        self._trigger_action_by_key("action_url_outgoing", data)

    def on_call_connected(self, data: dict) -> None:
        """
        Handle 'connected' event: mark as connected and start duration timer.
        """
        logger.info("Handling connected event -> triggering user Action URL")
        logger.info(f"Call connected event: {data}")
        if self.incoming_dialog is None:
            self.incoming_dialog = IncomingCallDialog(self)
            self.incoming_dialog.update_info(data)
            self.incoming_dialog.show()
            self.incoming_dialog.raise_()
            self.incoming_dialog.activateWindow()
        self.incoming_dialog.set_status("Connected")
        self.incoming_dialog.show_answer_button(False)
        self.call_started_at = datetime.now()
        self.incoming_dialog.set_duration("00:00")
        if not self.call_timer.isActive():
            self.call_timer.start()
        self._trigger_action_by_key("action_url_connected", data)

    def _format_duration(self, seconds: int) -> str:
        """
        Format seconds as mm:ss or hh:mm:ss for long calls.
        """
        if seconds < 0:
            seconds = 0
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{m:02}:{s:02}" if h == 0 else f"{h}:{m:02}:{s:02}"

    def _update_call_duration(self) -> None:
        """
        Update duration label periodically while call is connected.
        """
        if self.incoming_dialog is None or self.call_started_at is None:
            return
        elapsed = int((datetime.now() - self.call_started_at).total_seconds())
        self.incoming_dialog.set_duration(self._format_duration(elapsed))

    def on_call_ended(self, data: dict) -> None:
        """
        Close the incoming call popup on end events.
        """
        logger.info("Handling disconnected event -> triggering user Action URL")
        self._trigger_action_by_key("action_url_disconnected", data)
        if self.call_timer.isActive():
            self.call_timer.stop()
        self.call_started_at = None
        if self.incoming_dialog is not None:
            try:
                self.incoming_dialog.close()
            finally:
                self.incoming_dialog = None

    def on_onhook_event(self, data: dict) -> None:
        self._trigger_action_by_key("action_url_onhook", data)

    def on_offhook_event(self, data: dict) -> None:
        self._trigger_action_by_key("action_url_offhook", data)

    def shutdown(self) -> None:
        """
        Gracefully stop background threads/resources before application quits.
        """
        logger.info("Shutting down dialer")
        if self.hotkey_thread is not None:
            try:
                self.hotkey_thread.stop()
                self.hotkey_thread.wait(2000)
            except Exception as exc:
                logger.warning(f"Failed to stop hotkeys on shutdown: {exc}")
        if self.action_server is not None:
            try:
                self.action_server.stop()
                self.action_server.wait(2000)
            except Exception as exc:
                logger.warning(f"Failed to stop Action URL server on shutdown: {exc}")

        if hasattr(self, "call_timer") and self.call_timer.isActive():
            self.call_timer.stop()

        try:
            self.config["window_width"] = int(self.width())
            save_config_file(self.config_file, self.config)
            logger.info(f"Persisted window width: {self.config['window_width']}")
        except Exception as exc:
            logger.warning(f"Failed to persist window width: {exc}")

    def show(self):
        logger.info("Show main window requested")
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
        le = self.number_input.lineEdit()
        if le:
            le.selectAll()


class TrayIcon(QSystemTrayIcon):

    def __init__(self, icon, dialwindow):
        super().__init__(icon)
        self.activated.connect(self.showMenuOnTrigger)

        self.mainwindow = dialwindow

        self.menu = QMenu()
        self.menu.addAction("Dial", self.mainwindow.show)
        self.menu.addAction("Settings", self.open_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.exit)
        self.setContextMenu(self.menu)

    def showMenuOnTrigger(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.contextMenu().popup(QCursor.pos())

    def open_settings(self):
        dlg = SettingsDialog(self.mainwindow)
        dlg.exec()

    def exit(self):
        self.mainwindow.shutdown()
        QApplication.quit()


class SettingsDialog(QDialog):
    """
    Dialog to edit and validate configuration, including live test against the phone.
    """

    def __init__(self, mainwindow: QMainWindow):
        super().__init__(mainwindow)
        self._mw = mainwindow
        self.setWindowTitle("Snom Dialer Settings")
        self.setModal(True)

        self.ip_edit = QLineEdit(self._mw.config.get("ip", ""))
        self.user_edit = QLineEdit(self._mw.config.get("username", ""))
        self.pw_edit = QLineEdit(self._mw.config.get("password", ""))
        self.pw_edit.setEchoMode(QLineEdit.Password)
        self.hk_show_edit = QLineEdit(self._mw.config.get("hotkey_show_window", ""))
        self.hk_hangup_edit = QLineEdit(self._mw.config.get("hotkey_hangup", ""))
        self.web_port_edit = QLineEdit(str(self._mw.config.get("web_port", DEFAULT_WEB_PORT)))
        self.web_port_edit.setValidator(QIntValidator(1, 65535))

        form = QFormLayout()
        form.addRow(QLabel("IP/Hostname:"), self.ip_edit)
        form.addRow(QLabel("Username:"), self.user_edit)
        form.addRow(QLabel("Password:"), self.pw_edit)
        form.addRow(QLabel("Hotkey Show Window:"), self.hk_show_edit)
        form.addRow(QLabel("Hotkey Hangup:"), self.hk_hangup_edit)
        form.addRow(QLabel("Web server port:"), self.web_port_edit)

        self.button_box = QDialogButtonBox()
        self.btn_save = self.button_box.addButton("Save", QDialogButtonBox.AcceptRole)
        self.btn_test = self.button_box.addButton("Test", QDialogButtonBox.ActionRole)
        self.btn_cancel = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)

        self.gb_actions = QGroupBox("Action URLs")
        gb_form = QFormLayout()

        self.au_incoming_edit = QLineEdit(self._mw.config.get(
            "action_url_incoming", self._mw.config.get("incoming_action_url", "")))
        self.au_connected_edit = QLineEdit(self._mw.config.get("action_url_connected", ""))
        self.au_outgoing_edit = QLineEdit(self._mw.config.get("action_url_outgoing", ""))
        self.au_disconnected_edit = QLineEdit(self._mw.config.get("action_url_disconnected", ""))
        self.au_onhook_edit = QLineEdit(self._mw.config.get("action_url_onhook", ""))
        self.au_offhook_edit = QLineEdit(self._mw.config.get("action_url_offhook", ""))

        # Incoming
        self.au_incoming_chk = QCheckBox("Webbrowser")
        self.au_incoming_chk.setChecked(bool(self._mw.config.get("action_url_incoming_open_browser", False)))
        row_incoming = QWidget()
        row_incoming_l = QHBoxLayout(row_incoming); row_incoming_l.setContentsMargins(0, 0, 0, 0)
        row_incoming_l.addWidget(self.au_incoming_edit, 1)
        row_incoming_l.addWidget(self.au_incoming_chk, 0)
        gb_form.addRow(QLabel("Incoming:"), row_incoming)

        # Connected
        self.au_connected_chk = QCheckBox("Webbrowser")
        self.au_connected_chk.setChecked(bool(self._mw.config.get("action_url_connected_open_browser", False)))
        row_connected = QWidget()
        row_connected_l = QHBoxLayout(row_connected); row_connected_l.setContentsMargins(0, 0, 0, 0)
        row_connected_l.addWidget(self.au_connected_edit, 1)
        row_connected_l.addWidget(self.au_connected_chk, 0)
        gb_form.addRow(QLabel("Connected:"), row_connected)

        # Outgoing
        self.au_outgoing_chk = QCheckBox("Webbrowser")
        self.au_outgoing_chk.setChecked(bool(self._mw.config.get("action_url_outgoing_open_browser", False)))
        row_outgoing = QWidget()
        row_outgoing_l = QHBoxLayout(row_outgoing); row_outgoing_l.setContentsMargins(0, 0, 0, 0)
        row_outgoing_l.addWidget(self.au_outgoing_edit, 1)
        row_outgoing_l.addWidget(self.au_outgoing_chk, 0)
        gb_form.addRow(QLabel("Outgoing:"), row_outgoing)

        # Disconnected
        self.au_disconnected_chk = QCheckBox("Webbrowser")
        self.au_disconnected_chk.setChecked(bool(self._mw.config.get("action_url_disconnected_open_browser", False)))
        row_disconnected = QWidget()
        row_disconnected_l = QHBoxLayout(row_disconnected); row_disconnected_l.setContentsMargins(0, 0, 0, 0)
        row_disconnected_l.addWidget(self.au_disconnected_edit, 1)
        row_disconnected_l.addWidget(self.au_disconnected_chk, 0)
        gb_form.addRow(QLabel("Disconnected:"), row_disconnected)

        # Onhook
        self.au_onhook_chk = QCheckBox("Webbrowser")
        self.au_onhook_chk.setChecked(bool(self._mw.config.get("action_url_onhook_open_browser", False)))
        row_onhook = QWidget()
        row_onhook_l = QHBoxLayout(row_onhook); row_onhook_l.setContentsMargins(0, 0, 0, 0)
        row_onhook_l.addWidget(self.au_onhook_edit, 1)
        row_onhook_l.addWidget(self.au_onhook_chk, 0)
        gb_form.addRow(QLabel("Onhook:"), row_onhook)

        # Offhook
        self.au_offhook_chk = QCheckBox("Webbrowser")
        self.au_offhook_chk.setChecked(bool(self._mw.config.get("action_url_offhook_open_browser", False)))
        row_offhook = QWidget()
        row_offhook_l = QHBoxLayout(row_offhook); row_offhook_l.setContentsMargins(0, 0, 0, 0)
        row_offhook_l.addWidget(self.au_offhook_edit, 1)
        row_offhook_l.addWidget(self.au_offhook_chk, 0)
        gb_form.addRow(QLabel("Offhook:"), row_offhook)

        placeholder_help = QLabel(
            "Available placeholders:\n"
            "  {remote}, {display_remote}, {local}, {call_id}, {display_local},\n"
            "  {active_url}, {active_user}, {active_host}, {csta_id},\n"
            "  {expansion_module}, {active_key}, {phone_ip}, {local_ip},\n"
            "  {nr_ongoing_calls}, {context_url}, {cancel_reason}, {longpress_key},\n"
            "  {timestamp}\n"
            "Note: Values are URL-encoded automatically."
        )
        placeholder_help.setWordWrap(True)

        link_label = QLabel(
            '<a href="https://service.snom.com/display/wiki/Action+URLs">'
            'Snom Action URLs – Placeholder reference</a>'
        )
        link_label.setOpenExternalLinks(True)
        link_label.setTextFormat(Qt.RichText)
        link_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link_label.setWordWrap(True)

        self.gb_actions.setLayout(gb_form)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.gb_actions)
        layout.addWidget(placeholder_help)
        layout.addWidget(link_label)
        layout.addWidget(self.button_box)
        self.setLayout(layout)

        self.btn_test.clicked.connect(self._on_test)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)

    def _collect_config(self) -> dict:
        return {
            "ip": self.ip_edit.text().strip(),
            "username": self.user_edit.text().strip(),
            "password": self.pw_edit.text(),
            "hotkey_show_window": self.hk_show_edit.text().strip(),
            "hotkey_hangup": self.hk_hangup_edit.text().strip(),
            "web_port": int(self.web_port_edit.text().strip() or str(DEFAULT_WEB_PORT)),
            "action_url_incoming": self.au_incoming_edit.text().strip(),
            "action_url_connected": self.au_connected_edit.text().strip(),
            "action_url_outgoing": self.au_outgoing_edit.text().strip(),
            "action_url_disconnected": self.au_disconnected_edit.text().strip(),
            "action_url_onhook": self.au_onhook_edit.text().strip(),
            "action_url_offhook": self.au_offhook_edit.text().strip(),
            "action_url_incoming_open_browser": self.au_incoming_chk.isChecked(),
            "action_url_connected_open_browser": self.au_connected_chk.isChecked(),
            "action_url_outgoing_open_browser": self.au_outgoing_chk.isChecked(),
            "action_url_disconnected_open_browser": self.au_disconnected_chk.isChecked(),
            "action_url_onhook_open_browser": self.au_onhook_chk.isChecked(),
            "action_url_offhook_open_browser": self.au_offhook_chk.isChecked(),
        }

    def _validate_inputs(self, cfg: dict) -> tuple[bool, str]:
        if not cfg["ip"]:
            return False, "IP/Hostname must not be empty"
        if not self._valid_host(cfg["ip"]):
            return False, "IP/Hostname is not valid"
        if not cfg["username"]:
            return False, "Username must not be empty"
        if not cfg["password"]:
            return False, "Password must not be empty"
        ok, msg = self._validate_hotkey(cfg["hotkey_show_window"])
        if not ok:
            return False, f"Hotkey Show Window invalid: {msg}"
        ok, msg = self._validate_hotkey(cfg["hotkey_hangup"])
        if not ok:
            return False, f"Hotkey Hangup invalid: {msg}"
        port = cfg.get("web_port", 5000)
        if not (1 <= int(port) <= 65535):
            return False, "Web server port must be between 1 and 65535"

        for key, label in [
            ("action_url_incoming", "Incoming Action URL"),
            ("action_url_connected", "Connected Action URL"),
            ("action_url_outgoing", "Outgoing Action URL"),
            ("action_url_disconnected", "Disconnected Action URL"),
            ("action_url_onhook", "Onhook Action URL"),
            ("action_url_offhook", "Offhook Action URL"),
        ]:
            v = (cfg.get(key) or "").strip()
            if v and not re.match(r"^https?://", v, re.IGNORECASE):
                return False, f"{label} must start with http:// or https://"

        return True, "OK"

    def _validate_hotkey(self, hk: str) -> tuple[bool, str]:
        try:
            list(keyboard.HotKey.parse(hk))
            return True, "OK"
        except Exception as exc:
            return False, str(exc)

    def _valid_host(self, host: str) -> bool:
        # Accept IPv4 or a simple hostname pattern
        import re
        ipv4 = r"^(?:\d{1,3}\.){3}\d{1,3}$"
        hostname = r"^(?=.{1,253}$)([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
        return bool(re.match(ipv4, host)) or bool(re.match(hostname, host))

    def _on_test(self) -> None:
        cfg = self._collect_config()
        ok, msg = self._validate_inputs(cfg)
        if not ok:
            QMessageBox.critical(self, "Invalid settings", msg)
            return
        snom = Snom(ip=cfg["ip"], username=cfg["username"], password=cfg["password"])
        success, result = snom.test_control()
        if success:
            QMessageBox.information(self, "Test successful", result)
        else:
            QMessageBox.critical(self, "Test failed", result)

    def _on_save(self) -> None:
        cfg = self._collect_config()
        ok, msg = self._validate_inputs(cfg)
        if not ok:
            QMessageBox.critical(self, "Invalid settings", msg)
            return

        # Ensure phone is reachable and controllable before saving
        snom = Snom(ip=cfg["ip"], username=cfg["username"], password=cfg["password"])
        success, result = snom.test_control()
        if not success:
            QMessageBox.critical(self, "Cannot save settings", f"Phone test failed: {result}")
            return

        try:
            merged = dict(self._mw.config)
            merged.update(cfg)
            save_config_file(self._mw.config_file, merged)
            self._mw.config.update(cfg)
            self._mw.reconfigure_snom()
            self._mw.restart_hotkeys()
            self._mw.restart_action_server()
            logger.info("Settings saved and hotkeys restarted")
            QMessageBox.information(self, "Settings saved", "Configuration updated successfully.")
            self.accept()
        except Exception as exc:
            logger.error(f"Saving settings failed: {exc}")
            QMessageBox.critical(self, "Error", f"Saving settings failed: {exc}")


class ShowWindowSignal(QObject):
    sig = Signal()


class HangupSignal(QObject):
    sig = Signal()


class ActionEventSignal(QObject):
    incoming = Signal(dict)
    connected = Signal(dict)
    ended = Signal(dict)
    reachable = Signal(dict)
    outgoing = Signal(dict)
    onhook = Signal(dict)
    offhook = Signal(dict)


class ActionUrlServer(QThread):
    """
    Threaded WSGI server hosting the Flask app to receive Snom Action URLs.
    """

    def __init__(self, host: str, port: int, event_signal: ActionEventSignal, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self._signal = event_signal
        self._server = None
        self._app = self._build_app()
        # Build the server at construction time to fail fast on port issues
        self._server = make_server(self.host, self.port, self._app)

    def _build_app(self) -> Flask:
        app = Flask("snom_dialer")

        @app.get("/snom/incoming")
        def incoming():
            data = request.args.to_dict(flat=True)
            logger.info(f"Received incoming call event: {data}")
            logger.info("Emitting 'incoming' to UI")
            self._signal.incoming.emit(data)
            return "OK", 200

        @app.get("/snom/connected")
        def connected():
            data = request.args.to_dict(flat=True)
            logger.info(f"Received connected event: {data}")
            logger.info("Emitting 'connected' to UI")
            self._signal.connected.emit(data)
            return "OK", 200

        @app.get("/snom/outgoing")
        def outgoing():
            data = request.args.to_dict(flat=True)
            logger.info(f"Received outgoing call event: {data}")
            logger.info("Emitting 'outgoing' to UI")
            self._signal.outgoing.emit(data)
            return "OK", 200

        @app.get("/snom/disconnected")
        def disconnected():
            data = request.args.to_dict(flat=True)
            logger.info(f"Received disconnected event: {data}")
            logger.info("Emitting 'ended' to UI")
            self._signal.ended.emit(data)
            return "OK", 200

        @app.get("/snom/onhook")
        def onhook():
            data = request.args.to_dict(flat=True)
            logger.info(f"Received onhook event: {data}")
            logger.info("Emitting 'onhook' and 'ended' to UI")
            self._signal.ended.emit(data)
            self._signal.onhook.emit(data)
            return "OK", 200

        @app.get("/snom/offhook")
        def offhook():
            data = request.args.to_dict(flat=True)
            logger.info(f"Received offhook event: {data}")
            logger.info("Emitting 'offhook' and 'reachable' to UI")
            self._signal.reachable.emit(data)
            self._signal.offhook.emit(data)
            return "OK", 200

        @app.get("/health")
        def health():
            return "ok", 200

        return app

    def run(self):
        try:
            logger.info(f"Action URL server listening on {self.host}:{self.port}")
            self._server.serve_forever()
        except Exception as exc:
            logger.error(f"Action URL server terminated with error: {exc}")

    def stop(self) -> None:
        try:
            if self._server is not None:
                logger.info("Stopping Action URL server")
                self._server.shutdown()
        except Exception as exc:
            logger.warning(f"Stopping Action URL server failed: {exc}")


class HotKeys(QThread):
    def __init__(self, parent=None):
        QThread.__init__(self, parent)
        self.hotkey_show_main_window = ShowWindowSignal()
        self.hotkey_hangup = HangupSignal()
        self.listener = None

    def run(self):
        mapping = {
            mainwindow.config["hotkey_show_window"]: self.show_mainwindow,
            mainwindow.config["hotkey_hangup"]: self.hangup,
        }
        self.listener = keyboard.GlobalHotKeys(mapping)
        self.listener.start()
        self.listener.join()

    def stop(self) -> None:
        """
        Stop the global hotkeys listener gracefully.
        """
        try:
            if self.listener is not None:
                self.listener.stop()
        except Exception as exc:
            logger.warning(f"Stopping hotkeys listener failed: {exc}")

    def show_mainwindow(self):
        self.hotkey_show_main_window.sig.emit()

    def hangup(self):
        self.hotkey_hangup.sig.emit()


if __name__ == '__main__':

    conf_file = os.path.join(os.path.expanduser("~"), ".snom-dialer.config.json")

    first_run = False
    if os.path.isfile(conf_file):
        with open(conf_file, encoding="utf-8") as f:
            config = json.load(f)

        if "incoming_action_url" in config and not config.get("action_url_incoming"):
            config["action_url_incoming"] = config.get("incoming_action_url", "")
    else:
        first_run = True
        # do not write a file yet; show setup dialog after UI is up
        config = {
            "ip": "",
            "username": "",
            "password": "",
            "hotkey_show_window": "<ctrl>+<alt>+s",
            "hotkey_hangup": "<ctrl>+<alt>+x",
            "web_port": DEFAULT_WEB_PORT,
            "action_url_incoming": "",
            "action_url_connected": "",
            "action_url_outgoing": "",
            "action_url_disconnected": "",
            "action_url_onhook": "",
            "action_url_offhook": "",
            "action_url_incoming_open_browser": False,
            "action_url_connected_open_browser": False,
            "action_url_outgoing_open_browser": False,
            "action_url_disconnected_open_browser": False,
            "action_url_onhook_open_browser": False,
            "action_url_offhook_open_browser": False,
            "recent_numbers": [],
            "window_width": 500,
        }

    # Configure logging (uses RichHandler if available)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    try:
        from rich.logging import RichHandler
        handler = RichHandler(rich_tracebacks=True, tracebacks_show_locals=True, markup=True, show_path=False)
    except Exception:
        handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(module)s.%(funcName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.propagate = False

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
    def _handle_sigint(*_):
        logger.info("SIGINT received, quitting application")
        app.quit()

    signal.signal(signal.SIGINT, _handle_sigint)

    _sigint_pump = QTimer()
    _sigint_pump.start(250)
    _sigint_pump.timeout.connect(lambda: None)

    app.setQuitOnLastWindowClosed(False)
    icon = qta.icon("fa5s.phone-square", options=[{'color': 'green', 'scale_factor': 1.3}])
    app.setWindowIcon(icon)

    mainwindow = DialWindow(config, conf_file)

    if first_run:
        logger.info(f"No configuration found at {conf_file}. Starting initial setup.")
        dlg = SettingsDialog(mainwindow)
        if dlg.exec() != QDialog.Accepted:
            logger.info("Initial setup canceled by user. Exiting.")
            sys.exit(0)
        else:
            logger.info(f"Initial setup completed. Configuration saved to {conf_file}.")
    else:
        mainwindow.start_action_server()

    tray = TrayIcon(icon, mainwindow)
    tray.show()

    app.aboutToQuit.connect(mainwindow.shutdown)

    sys.exit(app.exec())
