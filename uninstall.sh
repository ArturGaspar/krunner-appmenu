#!/bin/sh

set -eu

rm ~/.local/share/kservices5/plasma-runner-krunner_appmenu.desktop
rm ~/.config/autostart/krunner_appmenu_autostart.desktop

kquitapp5 krunner
