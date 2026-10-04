# -*- coding: utf-8 -*-
"""Shared fake for the gist-creation race (`auto_sync.acquire_gist_id`).

A manual Upload and the auto-sync loop can both read an empty `gist_id` and
each create a gist, leaving an orphan. The tests below need two racers that are
guaranteed to be mid-flight together, so this fake makes both of them
rendezvous at their *first* `gist_id` read before either is allowed to create.
No sleeps: if the single-flight protocol breaks, the test fails instead of
flaking or hanging.
"""

import threading

import mock


class SingleFlightRace:
    """Fake gist API plus a mutable `gist_id` setting shared by both racers.

    Patch the settings seam with `settings_get`/`settings_set` and the client
    with `api`, then call `race(target)` with the zero-argument call under test.
    `created` and `updated` record what actually reached the (fake) API.
    """

    def __init__(self, gid='g-new', revision='v1', racers=2):
        self.gid = gid
        self.revision = revision
        self._racers = racers
        self.store = {'gist_id': ''}
        self._rendezvous = threading.Barrier(racers)
        self._first_reads = set()
        self._armed = False
        self.created = []
        self.updated = []
        self.api = mock.MagicMock()
        self.api.create.side_effect = self._create
        self.api.update.side_effect = self._update

    def _gist(self, gid):
        return {'id': gid,
                'history': [{'version': self.revision, 'committed_at': 't'}]}

    def _create(self, data):
        self.created.append(data)
        return self._gist(self.gid)

    def _update(self, gid, data=None):
        self.updated.append(gid)
        return self._gist(gid)

    def settings_get(self, key):
        # Snapshot the value *before* meeting the other racer: the point of the
        # rendezvous is that both observe the same empty setting, and a winner
        # must not be able to write `gist_id` in the gap after its release.
        value = self.store.get(key, '')
        if self._armed and key == 'gist_id':
            name = threading.current_thread().name
            if name not in self._first_reads:
                self._first_reads.add(name)
                # Every racer observes the empty setting before any of them may
                # create. The timeout turns a broken protocol into a failure.
                self._rendezvous.wait(timeout=5)
        return value

    def settings_set(self, key, value):
        self.store[key] = value

    def race(self, target):
        # Only `race()` arms the rendezvous: a direct call from the main thread
        # has no partner to meet.
        self._armed = True
        threads = [threading.Thread(target=target, name='racer-{}'.format(i))
                   for i in range(self._racers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        stuck = [t.name for t in threads if t.is_alive()]
        if stuck:
            raise AssertionError('racers did not finish: {}'.format(stuck))
