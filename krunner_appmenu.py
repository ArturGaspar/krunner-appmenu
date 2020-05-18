#!/usr/bin/env python3

import difflib
import logging
import re
import threading
from enum import Enum
from functools import lru_cache
from itertools import chain
from operator import itemgetter, methodcaller

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
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.lock = threading.Lock()
        self._active_window_id = None
        self.active_appmenu = (None, None)

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
                logger.debug("active window is krunner")
                return
            appmenu = self._get_appmenu_names(window)
            logger.debug("active window has appmenu %r", appmenu)

        with self.lock:
            self._active_window_id = winid
            self.active_appmenu = appmenu

    def _main(self):
        self._display = Xlib.display.Display()
        self._root = self._display.screen().root
        self._root.change_attributes(event_mask=Xlib.X.PropertyChangeMask)
        _NET_ACTIVE_WINDOW = self._display.get_atom('_NET_ACTIVE_WINDOW')
        while True:
            try:
                self._update_active_appmenu()
                while True:
                    event = self._display.next_event()
                    if (event.type == Xlib.X.PropertyNotify and
                            event.atom == _NET_ACTIVE_WINDOW):
                        break
            except Xlib.error.XError:
                pass


@lru_cache(maxsize=10000)
def _sequencematcher_ratio(j, a, b):
    return difflib.SequenceMatcher(j, a, b).ratio()


@lru_cache(maxsize=10000)
def _sequencematcher_blocks_filter_sum(j, a, b):
    blocks = difflib.SequenceMatcher(j, a, b).get_matching_blocks()
    return sum(filter(lambda n: n > 3, map(itemgetter(2), blocks)))


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
        self._window_info.thread.start()
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

    def _get_dbusmenu_entries(self, dbusmenu, id_=0, props=None, children=None):
        if id_ == 0 or (props.get('children-display') and not children):
            if id_ != 0:
                dbusmenu.AboutToShow(id_)
            props_wanted = ["label", "icon-name", "children-display",
                            "enabled", "shortcut"]
            rev, (id_, props, children) = dbusmenu.GetLayout(id_, -1,
                                                             props_wanted)

        entry = {
            "id": int(id_),
            "label": str(props.get("label", ""))
        }

        icon_name = props.get("icon-name")
        if icon_name:
            entry["icon_name"] = icon_name

        shortcut = props.get("shortcut")
        if shortcut:
            entry["shortcut"] = '+'.join(key.upper() if len(key) == 1 else key
                                         for key in shortcut[0])

        if not children and props.get('enabled', True) and entry["label"]:
            entry["ancestors"] = []
            yield entry

        for child in children:
            for item in self._get_dbusmenu_entries(dbusmenu, *child):
                if id_ != 0:
                    item["ancestors"].insert(0, entry)
                yield item

    @staticmethod
    def _format_label(label):
        return label.replace("_", "")

    def _load_menu(self):
        service, objpath = self._active_appmenu
        if service is None or objpath is None:
            logger.debug("no active appmenu")
            self._menu_entries = None
            return

        logger.debug("loading appmenu from %s %s", service, objpath)
        obj = self.connection.get_object(service, objpath)
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
        ancestor_labels = list(self._remove_special_chars(label.lower())
                               for label in ancestor_labels)
        ancestor_labels_words = set(chain(*map(methodcaller('split'),
                                               ancestor_labels)))
        label = self._remove_special_chars(label.lower())
        label_words = set(label.split())
        ancestor_labels_words -= label_words
        return {
            "ancestor_labels": ancestor_labels,
            "ancestor_labels_words": list(ancestor_labels_words),
            "label": label,
            "label_words": list(label_words)
        }

    @staticmethod
    def _remove_special_chars(s):
        return ' '.join(re.sub(r'[^\w]', ' ', s).split())

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

    def _match(self, query):
        query = self._remove_special_chars(query.lower())
        query_words = query.split()

        for entry in self._menu_entries:
            md = entry["match_data"]
            scores = []
            for type_, score in self._match_one(query, query_words,
                                                md["label"], md["label_words"],
                                                md["ancestor_labels_words"]):
                scores.append((score, type_))
                if score == 1:
                    break
            if scores:
                scores.sort()
                score, type_ = scores[0]
                yield self._make_action(entry, type_, score * 0.9)

    def _match_one(self, query, query_words, label, label_words,
                   ancestor_labels_words):
        # Total length of matching blocks between the query and the target,
        # as a ratio of the query length.
        score = _sequencematcher_blocks_filter_sum(None, label, query)
        score /= len(query)
        if score >= 0.8:
            if score == 1:
                type_ = self.QueryMatchType.ExactMatch
            else:
                type_ = self.QueryMatchType.PossibleMatch
            logger.debug("whole query match %r %r, score=%r",
                         query, label, score)
            yield type_, score

        # Average of the best match for each individual word in the query,
        # including words of ancestor labels.
        score = 0
        for qword in query_words:
            word_score = 0
            for lword in chain(label_words, ancestor_labels_words):
                ratio = _sequencematcher_ratio(None, lword, qword)
                word_score = max(word_score, ratio)
                if word_score == 1:
                    break
            if word_score < 0.7:
                return
            score += word_score

        score /= len(query_words)
        if score == 1:
            type_ = self.QueryMatchType.ExactMatch
        else:
            type_ = self.QueryMatchType.PossibleMatch
        logger.debug("words query match %r %r/%r, score=%r",
                     query, ancestor_labels_words, label_words, score)
        # Penalise this score in relation to that of whole query match.
        yield type_, score - 0.3

    @dbus.service.method(KRUNNER1_IFACE, out_signature='a(sss)')
    def Actions(self, msg):
        return []

    @dbus.service.method(KRUNNER1_IFACE, in_signature='s',
                         out_signature='a(sssida{sv})')
    def Match(self, query):
        try:
            with self._window_info.lock:
                if self._active_appmenu != self._window_info.active_appmenu:
                    logger.debug("active window has changed, resetting appmenu")
                    self._active_appmenu = self._window_info.active_appmenu
                    self._menu_entries = None
            if self._menu_entries is None:
                logger.debug("loading appmenu contents")
                self._load_menu()
            if not self._menu_entries:
                logger.debug("appmenu not available")
                return []
            if len(query) < 3:
                return []

            results = list(self._match(query))
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
            obj = self.connection.get_object(service, objpath)
            dbusmenu = dbus.Interface(obj, DBUSMENU_IFACE)
            for ancestor in ancestors:
                dbusmenu.Event(ancestor, "opened", "", 0)
            dbusmenu.Event(int(entry_id), "clicked", "", 0)
            for ancestor in ancestors:
                dbusmenu.Event(ancestor, "closed", "", 0)
        except Exception:
            logger.exception("Error in Run()")
            raise


if __name__ == "__main__":
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)

    logging.basicConfig(level=logging.DEBUG)

    bus = dbus.SessionBus()
    busname = dbus.service.BusName("org.krunner_appmenu", bus)
    runner = Runner(busname, "/krunner_appmenu")
    loop = GLib.MainLoop()
    loop.run()
