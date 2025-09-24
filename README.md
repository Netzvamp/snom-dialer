# Snom dialer


A small tool for dialing with a snom desktop phone. Tested on Snom 320 and Snom D385. 

It provides a global hotkey, that opens a window with an input field to insert a number to dial with your desktop phone.

It's based on this: https://service.snom.com/display/wiki/Can+I+control+my+snom+phone+remotely

## Features

It provides a global hotkey, that opens a window with an input field to insert a number to dial on your desktop phone.

- Global hotkeys to show the dial window and to hang up (customizable).
- Compact main window with:
  - Editable drop-down for the last dialed numbers.
  - Minimal buttons (Dial, Hangup, Settings).
- Config file is stored at: ~/.snom-dialer.config.json
- Built-in Action URL callback server:
  - Auto-configures your Snom phone to send Action URLs to your PC.
  - Supports events: incoming, outgoing, connected, disconnected, onhook, offhook.
  - User-defined Action URLs per event with placeholders and optional “open in web browser”.

## Build an executable

Tested under Windows 10 with Python 3.12. It may also work under Linux and MacOS.

Run in command window:
```
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
pyinstaller snom-dialer.spec
```
This will create a dist\snom-dialer folder with a snom-dialer.exe


## Configuration and usage

First run:
- On first start the Settings dialog opens automatically.
- Enter IP/hostname, username and password of your Snom device.
- Click “Test” to verify connectivity. Only after a successful test, settings are saved to:
  - Windows/Linux/macOS: ~/.snom-dialer.config.json

Tray and window:
- A tray icon indicates the app is running (right-click for menu: Dial, Settings, Quit).
- Press the “Show Window” hotkey (default: <ctrl>+<alt>+s) to open the dial window.
- Type a number and press Enter to dial. Use the red button to hang up.
- The input is an editable drop-down that lists the 100 most recent numbers (MRU).

Hotkeys:
- Show Window: default <ctrl>+<alt>+s
- Hangup: default <ctrl>+<alt>+x

Action URLs – phone integration:
- The app starts a local Action URL callback server and configures your phone automatically.
- Default port is configurable (Settings → “Web server port”). If the port is in use, a nearby free port is chosen and saved.

### Advanced features

There is a power-user feature: Hold the Shift key while pressing Enter and the number will be entered on the phone but not dialed immediately. This lets you send key sequences (separated by semicolons) to the phone.

With that some advanced usages are possible, if you know this page: https://service.snom.com/display/wiki/Can+I+control+my+snom+phone+remotely

When you dial with shift the number you entered will be split and semicolons will be placed between them. 
If you add a semicolon by yourself, the numbers don't got split, and you can use key events as described on the Snom wiki page. 

#### Example
* <code>9;1;9;6;ENTER,,1000;*,1000,2000;#,,1000;9</code> Dial 9196, wait 1000ms and press * (and hold the key for 1000ms), #, 9 to generate DTMF tones inside a call.

## Action URLs – Overview & Examples

The app exposes HTTP endpoints that your Snom phone calls on events:
- /snom/incoming, /snom/outgoing, /snom/connected, /snom/disconnected, /snom/onhook, /snom/offhook

On startup, the app auto-updates the phone's Action URL settings to point to your PC (requires valid credentials in the Settings).

User-defined Action URLs
- In Settings → “Action URLs” you can configure per-event URLs that the app triggers when an event arrives from the phone.
- Each event offers a “Webbrowser” checkbox:
  - Enabled: open the final URL in a new browser tab
  - Disabled: perform an HTTP GET request to the final URL
- Placeholders are replaced with values from the phone and are URL-encoded automatically.

Available placeholders
- {remote}, {display_remote}, {local}, {call_id}, {display_local}
- {active_url}, {active_user}, {active_host}, {csta_id}
- {expansion_module}, {active_key}, {phone_ip}, {local_ip}
- {nr_ongoing_calls}, {context_url}, {cancel_reason}, {longpress_key}
- {timestamp}

Note: These correspond to Snom runtime variables. See Snom documentation for details:
- Snom Action URLs reference:
  https://service.snom.com/display/wiki/Action+URLs

Example: open a CRM record on incoming calls in the browser
- Settings → Action URLs → Incoming:
  - URL: https://crm.example.local/search?num={remote}&name={display_remote}&at={timestamp}
  - Webbrowser: checked

Example: call your own webhook on connected calls
- Settings → Action URLs → Connected:
  - URL: http://127.0.0.1:8080/calls/connected?from={remote}&to={local}&id={call_id}
  - Webbrowser: unchecked

Notes
- Placeholders are inserted after URL-encoding. Unknown placeholders are left empty.
- If a user-defined URL is empty for an event, nothing is triggered.


## Batchmode

    usage: snom-dialer.py [-h] command [parameter]
    
    Snom remote dialer
    
    positional arguments:
      command     One of dial, keyevent, hangup or hangup_all
      parameter   Optional parameter to command
    
    optional arguments:
      -h, --help  show this help message and exit
