import requests
from requests.auth import HTTPDigestAuth
requests.packages.urllib3.disable_warnings()

from typing import Tuple
from urllib.parse import quote
import logging

logger = logging.getLogger(__name__)


class SnomError(Exception):
    """Base exception for Snom related errors."""


class SnomConnectionError(SnomError):
    """Raised when a connection or request to the Snom phone fails."""


class Snom:
    def __init__(self, ip, username, password):
        self.ip = ip
        self.username = username
        self.password = password
        self.cmd_url = f"https://{self.ip}/command.htm?"

    def key_events(self, events: str):
        self.send_request(f"{self.cmd_url}key={events}")

    def dial(self, number: str):
        self.send_request(f"{self.cmd_url}number={number}")

    def hangup(self):
        self.send_request(f"{self.cmd_url}key=CANCEL")

    def hangup_all(self):
        self.send_request(f"{self.cmd_url}RELEASE_ALL_CALLS")

    def answer(self, timeout: float = 5.0) -> Tuple[bool, str]:
        """
        Attempt to answer the current incoming call on the phone.

        Returns
        -------
        tuple[bool, str]
            (True, message) on success, otherwise (False, error_message).
        """
        for key in ("ENTER", "OFFHOOK"):
            try:
                resp = self.send_request(f"{self.cmd_url}key={key}", timeout=timeout)
                if 200 <= resp.status_code < 400:
                    logger.info(f"Answered call via key {key}")
                    return True, f"Answered via {key}"
                logger.debug(f"Answer via key {key} failed: {resp.status_code}")
            except SnomConnectionError as exc:
                logger.debug(f"Answer via key {key} failed: {exc}")
        return False, "Failed to answer call using keys: ENTER, OFFHOOK"

    def test_control(self, timeout: float = 5.0) -> Tuple[bool, str]:
        """
        Try to reach the phone and issue a harmless command (CANCEL).

        Returns
        -------
        tuple[bool, str]
            (True, message) on success, otherwise (False, error_message).
        """
        try:
            resp = self.send_request(f"{self.cmd_url}key=CANCEL", timeout=timeout)
            if 200 <= resp.status_code < 400:
                return True, "Connection and control test succeeded"
            return False, f"Unexpected HTTP status: {resp.status_code}"
        except SnomConnectionError as exc:
            return False, f"Connection failed: {exc}"

    def set_action_urls(self, base_url: str, timeout: float = 5.0) -> Tuple[bool, str]:
        """
        Configure the phone's Action URLs to point at our callback server.

        Parameters
        ----------
        base_url
            Base URL of the callback server, e.g. "http://192.168.1.23:5000"
        timeout
            HTTP timeout in seconds.

        Returns
        -------
        tuple[bool, str]
            (True, message) on success, otherwise (False, error_message).
        """
        # Map Snom settings to callback endpoints; keep '$' variables unescaped
        common_params = (
            "remote=$remote"
            "&display_remote=$display_remote"
            "&local=$local"
            "&call_id=$call-id"
            "&active_url=$active_url"
            "&active_user=$active_user"
            "&active_host=$active_host"
            "&csta_id=$csta_id"
            "&display_local=$display_local"
            "&expansion_module=$expansion_module"
            "&active_key=$active_key"
            "&phone_ip=$phone_ip"
            "&local_ip=$local_ip"
            "&nr_ongoing_calls=$nr_ongoing_calls"
            "&context_url=$context_url"
            "&cancel_reason=$cancel_reason"
            "&longpress_key=$longpress_key"
        )

        urls = {
            "action_incoming_url": f"{base_url}/snom/incoming?{common_params}",
            "action_connected_url": f"{base_url}/snom/connected?{common_params}",
            "action_outgoing_url": f"{base_url}/snom/outgoing?{common_params}",
            "action_disconnected_url": f"{base_url}/snom/disconnected?{common_params}",
            "action_onhook_url": f"{base_url}/snom/onhook?{common_params}",
            "action_offhook_url": f"{base_url}/snom/offhook?{common_params}",
        }

        # Build URL for settings update (keep $ and reserved chars)
        def _q(v: str) -> str:
            # Encode '&' so that inner query remains part of the value
            return quote(v, safe="$:/?=,+-_.~")

        params = "&".join(f"{k}={_q(v)}" for k, v in urls.items())
        url = f"https://{self.ip}/settings.htm?settings=save&{params}"
        logger.debug(f"Updating Action URLs via: {url}")

        try:
            resp = self.send_request(url, timeout=timeout)
            if 200 <= resp.status_code < 400:
                logger.info("Successfully updated Snom Action URLs")
                return True, "Action URLs updated"
            msg = f"Unexpected HTTP status while updating Action URLs: {resp.status_code}"
            logger.error(msg)
            return False, msg
        except SnomConnectionError as exc:
            return False, f"Failed to update Action URLs: {exc}"

    def send_request(self, url: str, timeout: float = 5.0) -> requests.Response:
        """
        Send a POST request to the phone.

        Returns
        -------
        requests.Response
            The HTTP response from the phone.

        Raises
        ------
        SnomConnectionError
            If the request fails due to connectivity/auth issues.
        """
        try:
            safe_url = url.replace('#', '%23').replace('*', '%2A')
            response = requests.post(
                safe_url,
                auth=HTTPDigestAuth(self.username, self.password),
                verify=False,
                timeout=timeout
            )
            logger.debug(f"POST {safe_url} -> {response.status_code}")
            return response
        except requests.RequestException as exc:
            logger.error(f"Request to Snom failed: {exc}")
            raise SnomConnectionError(str(exc)) from exc
