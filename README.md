# krunner-appmenu
A KRunner plugin that shows the menu of the current application.

To install

* Copy `appmenurunner.desktop` to `~/.local/share/kservices5/` 

* Copy `krunner_appmenu.py` to `~/.config/autostart-scripts/` and ensure it has executable permissions. (autostart-scripts is not ideal but it works)

* Copy `appmenurunner_globalshortcut.desktop` to `~/.local/share/kglobalaccel`. This will allow you to configure a keyboard shortcut that will launch krunner with only this runner enabled. By default, the shortcut is Meta+M.

This plugin depends on Python 3.4, dbus-python and python-xlib.


