# krunner-appmenu
A KRunner plugin that shows the menu of the current application.

Requires Python 3.4, dbus-python and python-xlib.

KDE Store page: https://store.kde.org/p/1487409/

To configure a key binding (e.g. Alt) to open KRunner with only this plugin, do

    kwriteconfig5 --file kwinrc --group ModifierOnlyShortcuts --key Alt org.kde.krunner,/App,,displaySingleRunner,krunner_appmenu

If you just want your plugin to show up when using the key binding you can run

    kwriteconfig5 --file ~/.local/share/kservices5/plasma-runner-krunner_appmenu.desktop  --group "Desktop Entry" --key "X-Plasma-Runner-Match-Regex" '^$'; kquitapp5 krunner
See [this KDE Bug report](https://bugs.kde.org/show_bug.cgi?id=435050) for details