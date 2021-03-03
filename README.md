# krunner-appmenu
A KRunner plugin that shows the menu of the current application.

Requires Python 3.4, dbus-python and python-xlib.

KDE Store page: https://store.kde.org/p/1487175/

To configure a key binding (e.g. Alt) to open KRunner with only this plugin, do

    kwriteconfig5 --file kwinrc --group ModifierOnlyShortcuts --key Alt org.kde.krunner,/App,,displaySingleRunner,krunner_appmenu
