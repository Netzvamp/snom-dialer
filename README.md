# Snom dialer


A small tool for speed dialing with a snom desktop phone. Tested on Snom 320 and Snom D385. Only Windows for now.

It provides a global hotkey, that opens a window with an input field to insert a number to dial on your desktop phone.

It's based on this: https://service.snom.com/display/wiki/Can+I+control+my+snom+phone+remotely

Made quick and dirty in one night, with some quick refinement the day later. Don't expect perfection.

## Build an executable

Tested under Windows 10 with Python 3.9 and 3.8.

Run in command window:
```
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
pyinstaller dialer.spec
```
This will create a dist\snom-dialer folder with an snom-dialer.exe


## Configuration and usage

On first run, it creates an config.json and exits. 
You need to edit this file and fill in IP, username and password of your snom device.
It's also possible to change the global hotkeys.

Then start it again. You'll see a trayicon ... this is only show you it's running.

* Press the global hotkey combination (defaults to \<ctrl\> + \<alt\> + s)
* A window with an input field pops up.
* Insert the phone number you want to dial and press the enter key. The window will disappear.
* Your phone will call the number.
* Press the hangup hotkey (default to \<ctrl\> + \<alt\> + x) to hangup the call 

### Advanced features

There is a "hidden" feature: Hold the shift key while pressing the enter key, and the number will be inserted but not dialed. 

With that some advanced usages are possible, if you know this page: https://service.snom.com/display/wiki/Can+I+control+my+snom+phone+remotely

When you dial with shift the number you entered will be split and semicolons will be placed between them. 
If you add a semicolon by yourself, the numbers don't got split, and you can use key events as described on the Snom wiki page. 

#### Example
* <code>9;1;9;6;ENTER,,1000;*,1000,2000;#,,1000;9</code> Dial 9196, wait 1000ms and press * (and hold the key for 1000ms), #, 9 to generate DTMF tones inside a call.

## Batchmode

    usage: snom-dialer.py [-h] command [parameter]
    
    Snom remote dialer
    
    positional arguments:
      command     One of dial, keyevent, hangup or hangup_all
      parameter   Optional parameter to command
    
    optional arguments:
      -h, --help  show this help message and exit