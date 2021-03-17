import requests
from requests.auth import HTTPDigestAuth
requests.packages.urllib3.disable_warnings()


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

    def send_request(self, url):
        requests.post(
            url.replace('#', '%23').replace('*', '%2A'),
            auth=HTTPDigestAuth(self.username, self.password),
            verify=False
        )
