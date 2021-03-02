#!/bin/sh

set -eu

mkdir -p ~/.config/autostart/
mkdir -p ~/.local/share/kservices5/

cp plasma-runner-krunner_appmenu.desktop ~/.local/share/kservices5/
sed "s|@PROJECTDIR@|${PWD}|" krunner_appmenu_autostart.desktop.in > ~/.config/autostart/krunner_appmenu_autostart.desktop

chmod +x ./krunner_appmenu.py

nohup ./krunner_appmenu.py >/dev/null 2>&1 &

kquitapp5 krunner
