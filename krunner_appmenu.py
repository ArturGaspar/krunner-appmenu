#!/usr/bin/env python3

import logging
import re
import threading
import unicodedata
from enum import Enum
from itertools import filterfalse
from operator import itemgetter

import dbus
import dbus.exceptions
import dbus.service
import Xlib.X
import Xlib.Xatom
import Xlib.display


logger = logging.getLogger(__name__)


DBUSMENU_IFACE = "com.canonical.dbusmenu"
KRUNNER1_IFACE = "org.kde.krunner1"


class AppmenuXWindowInfo(object):
    """

    Keep track of active window in a separate thread.

    When the runner is activated, KRunner is the active window, so it is
    necessary to know what was the active window before that.

    """

    def __init__(self):
        super().__init__()
        self._active_window_id = None
        self.active_appmenu = (None, None)
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()

    def _get_property(self, window, property, property_type, offset=0,
                      length=None):
        atom = self._display.get_atom(property)
        if length is not None:
            r = window.get_property(atom, property_type, offset, length)
        else:
            assert offset == 0
            r = window.get_full_property(atom, property_type)

        if r is None:
            return None
        else:
            return r.value

    def _get_active_window_id(self):
        r = self._get_property(self._root,
                               '_NET_ACTIVE_WINDOW',
                               Xlib.Xatom.WINDOW,
                               length=1)
        if r:
            return r[0]
        else:
            return None

    def _get_appmenu_names(self, window):
        service_name = self._get_property(window,
                                          '_KDE_NET_WM_APPMENU_SERVICE_NAME',
                                          Xlib.Xatom.STRING)
        if service_name is not None:
            service_name = service_name.decode()
        objpath = self._get_property(window,
                                     '_KDE_NET_WM_APPMENU_OBJECT_PATH',
                                     Xlib.Xatom.STRING)
        if objpath is not None:
            objpath = objpath.decode()
        return service_name, objpath

    def _update_active_appmenu(self):
        winid = self._get_active_window_id()
        if winid == self._active_window_id:
            return

        if not winid:
            logger.debug("no active window id")
            appmenu = (None, None)
        else:
            logger.debug("active window id: %r", winid)
            window = self._display.create_resource_object('window', winid)
            if window.get_wm_class() == ('krunner', 'krunner'):
                logger.debug("active window is krunner, ignoring")
                return
            appmenu = self._get_appmenu_names(window)
            logger.debug("active window has appmenu %r", appmenu)

        self._active_window_id = winid
        self.active_appmenu = appmenu

    def _main(self):
        self._display = Xlib.display.Display()
        self._root = self._display.screen().root
        self._root.change_attributes(event_mask=Xlib.X.PropertyChangeMask)

        try:
            self._update_active_appmenu()
        except Xlib.error.XError:
            pass

        PropertyNotify = Xlib.X.PropertyNotify
        _NET_ACTIVE_WINDOW = self._display.get_atom('_NET_ACTIVE_WINDOW')
        while True:
            try:
                ev = self._display.next_event()
                if ev.type == PropertyNotify and ev.atom == _NET_ACTIVE_WINDOW:
                    self._update_active_appmenu()
            except Xlib.error.XError:
                pass


class Runner(dbus.service.Object):
    class QueryMatchType(Enum):
        NoMatch = 0
        CompletionMatch = 10
        PossibleMatch = 30
        InformationalMatch = 50
        HelperMatch = 70
        ExactMatch = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._window_info = AppmenuXWindowInfo()
        self._active_appmenu = (None, None)
        self._menu_entries = None
        for signal_name in 'ItemsPropertiesUpdated', 'LayoutUpdated':
            self.connection.add_signal_receiver(self._reset_appmenu,
                                                dbus_interface=DBUSMENU_IFACE,
                                                signal_name=signal_name,
                                                member_keyword='signal',
                                                sender_keyword='sender',
                                                path_keyword='path')

    def _reset_appmenu(self, *args, sender, path, signal):
        if (sender, path) == self._active_appmenu:
            logger.debug("resetting appmenu: %s", signal)
            self._menu_entries = None

    @staticmethod
    def _format_shortcut_key(key):
        if len(key) == 1:
            key = key.upper()
        elif key == "Control":
            key = "Ctrl"
        return key

    def _make_menu_entry(self, id_, props):
        entry = {
            "id": int(id_),
            "label": str(props.get("label", ""))
        }
        icon_name = props.get("icon-name")
        if icon_name:
            entry["icon_name"] = str(icon_name)
        shortcut = props.get("shortcut")
        if shortcut:
            entry["shortcut"] = '+'.join(map(self._format_shortcut_key,
                                             shortcut[0]))
        return entry

    def _get_dbusmenu_entries(self, dbusmenu, id_=0, props=None, children=None,
                              ancestors=None):
        if id_ == 0 or (props.get('children-display') and not children):
            if id_ != 0:
                dbusmenu.AboutToShow(id_)
            props_wanted = ["label", "icon-name", "children-display",
                            "shortcut"]
            rev, (id_, props, children) = dbusmenu.GetLayout(id_, -1,
                                                             props_wanted)
        if ancestors is None:
            ancestors = []
        entry = self._make_menu_entry(id_, props)
        if children:
            if id_ != 0:
                ancestors = ancestors[:]
                ancestors.append(entry)
            for child in children:
                yield from self._get_dbusmenu_entries(dbusmenu, *child,
                                                      ancestors)
        elif entry["label"]:
            entry["ancestors"] = ancestors
            yield entry

    @staticmethod
    def _format_label(label):
        return label.replace("_", "")

    def load_menu(self):
        service, objpath = self._active_appmenu
        if service is None or objpath is None:
            logger.debug("no active appmenu")
            self._menu_entries = None
            return

        logger.debug("loading appmenu from %s %s", service, objpath)
        obj = self.connection.get_object(service, objpath, introspect=False)
        dbusmenu = dbus.Interface(obj, DBUSMENU_IFACE)

        self._menu_entries = []
        for entry in self._get_dbusmenu_entries(dbusmenu):
            label = self._format_label(entry["label"])
            ancestor_labels = list(map(self._format_label,
                                       map(itemgetter('label'),
                                           entry["ancestors"])))

            ancestor_ids = list(map(itemgetter('id'), entry["ancestors"]))
            action_id = "{}|{}|{}|{}".format(service,
                                             objpath,
                                             ','.join(map(str, ancestor_ids)),
                                             entry["id"])
            entry.update({
                "action_id": action_id,
                "action_text": " » ".join(ancestor_labels + [label]),
                "match_data": self._create_match_data(ancestor_labels, label)
            })
            self._menu_entries.append(entry)
        logger.debug("appmenu has %s entries", len(self._menu_entries))

    def _create_match_data(self, ancestor_labels, label):
        labels = ancestor_labels + [label]
        words = set(self._prepare_match_text(' '.join(labels)).split())
        return {
            "words": words
        }

    @staticmethod
    def _prepare_match_text(s):
        s = "".join(filterfalse(unicodedata.combining,
                                unicodedata.normalize('NFKD', s)))
        s = s.lower()
        s = re.sub(r'\W+', ' ', s)
        s = s.strip()
        return s

    def _make_action(self, entry, type_, relevance):
        properties = {}
        if "shortcut" in entry:
            properties["subtext"] = entry["shortcut"]
        return (entry["action_id"],
                entry["action_text"],
                entry.get("icon_name", ""),
                type_.value,
                relevance,
                properties)

    @staticmethod
    def _match_words(query_words, label_words):
        scores = 0
        for qword in query_words:
            score = 0
            for lword in label_words:
                if qword in lword:
                    new_score = len(qword) / len(lword)
                    score = max(score, new_score)
                    if score == 1:
                        break
            # If a query word fails to match any label words, give this match a
            # final score 0.
            if score == 0:
                return 0

            scores += score
        return scores / len(query_words)

    def is_enabled(self, entry, cache=None):
        if not entry["ancestors"]:
            return True

        id_ = entry["id"]
        if cache is not None:
            try:
                return cache[id_]
            except KeyError:
                pass

        obj = self.connection.get_object(*self._active_appmenu,
                                         introspect=False)
        dbusmenu = dbus.Interface(obj, DBUSMENU_IFACE)

        parent_id = entry["ancestors"][-1]["id"]
        props_wanted = ["enabled"]
        dbusmenu.AboutToShow(parent_id)
        siblings = dbusmenu.GetLayout(parent_id, -1, props_wanted)[1][2]

        enabled = True
        for s_id, props, children in siblings:
            s_enabled = props.get("enabled", True)
            if cache is not None:
                cache[s_id] = s_enabled
            if s_id == id_:
                enabled = s_enabled
                if cache is None:
                    break

        return enabled

    def match(self, query):
        if not self._menu_entries:
            logger.debug("appmenu not available")
            return []

        query = self._prepare_match_text(query)
        query_words = query.split()

        enabled_cache = {}
        for entry in self._menu_entries:
            md = entry["match_data"]
            score = self._match_words(query_words, md["words"])
            logger.debug("query match %r on %r, score=%r",
                         query, md["words"], score)
            if score > 0:
                if score == 1:
                    type_ = self.QueryMatchType.ExactMatch
                else:
                    type_ = self.QueryMatchType.PossibleMatch

                if self.is_enabled(entry, enabled_cache):
                    yield self._make_action(entry, type_, score)

    @dbus.service.method(KRUNNER1_IFACE, out_signature='a(sss)')
    def Actions(self, msg):
        return []

    @dbus.service.method(KRUNNER1_IFACE, in_signature='s',
                         out_signature='a(sssida{sv})')
    def Match(self, query):
        try:
            active_appmenu = self._window_info.active_appmenu
            if self._active_appmenu != active_appmenu:
                logger.debug("active window has changed, resetting appmenu")
                self._active_appmenu = active_appmenu
                self._menu_entries = None
            if self._menu_entries is None:
                logger.debug("loading appmenu contents")
                self.load_menu()
            if len(query) < 3:
                return []

            results = list(self.match(query))
            results.sort(key=itemgetter(4, 3), reverse=True)
            results = results[:10]
            return results
        except Exception:
            logger.exception("Error in Match()")
            raise

    @dbus.service.method(KRUNNER1_IFACE, in_signature='ss')
    def Run(self, matchId, actionId):
        try:
            service, objpath, ancestors, entry_id = matchId.split('|')
            ancestors = list(map(int, ancestors.split(',')))
            obj = self.connection.get_object(service, objpath, introspect=False)
            dbusmenu = dbus.Interface(obj, DBUSMENU_IFACE)
            # for ancestor in ancestors:
            #     dbusmenu.Event(ancestor, "opened", "", 0)
            # for ancestor in ancestors[::-1]:
            #     dbusmenu.Event(ancestor, "closed", "", 0)
            dbusmenu.Event(int(entry_id), "clicked", "", 0,
                           signature='isvu')
        except Exception:
            logger.exception("Error in Run()")
            raise


if __name__ == "__main__":
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)

    logging.basicConfig()

    bus = dbus.SessionBus()
    busname = dbus.service.BusName("org.krunner_appmenu", bus)
    runner = Runner(busname, "/krunner_appmenu")
    loop = GLib.MainLoop()
    loop.run()
