import os
import pexpect
import threading
from collections import namedtuple
import time
import Pyro4

import logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger('player_piano.midi')


TrackPosition = namedtuple('TrackPosition', ['measure', 'beat', 'tick'])

class MidishException(Exception):
    pass

class MidiPlayThread(threading.Thread):
    def __init__(self, midi):
        self.midi = midi 
        self.__stop_requested = False
        threading.Thread.__init__(self)

    def stop(self):
        self.__stop_requested = True
        if threading.current_thread() != self:
            self.join()

    def run(self):
        # Play until the end, unless interrupted:
        while not self.__stop_requested:
            pos = self.midi._update_position()
            if pos.measure >= self.midi.track_length:
                self.midi._track_end()
                break
        log.debug("MidiPlayThread terminated")

class MidiPlaylist(object):
    def __init__(self, name="untitled"):
        self.midi = Midi(self.next_track)
        self.clear()
        self.name = name

    def clear(self):
        self.midi.stop()
        self.name = "untitled"
        self.repeat = False
        self.current_track_num = -1
        self.playlist = []
        self.state = "stopped"

    def add(self, track_id, position=None):
        if position is None:
            self.playlist.append(track_id)
        else:
            self.playlist.insert(position, track_id)

    def remove(self, position):
        if self.current_track_num == position:
            self.next_track()
        elif self.current_track_num > position:
            self.current_track_num -= 1
        self.playlist.pop(position)

    def get_current_track(self):
        if self.current_track_num < 0:
            track_id = None
        else:
            track_id = self.playlist[self.current_track_num] 
        data = {"id": track_id,
                "length": self.midi.track_length,
                "current_pos": dict(self.midi.current_pos._asdict())}
        return data

    def get_playlist(self):
        return {'current_track_num': self.current_track_num,
                'playlist': self.playlist}

    def next_track(self, force_play=False, **kw):
        time.sleep(2)
        if self.current_track_num >= len(self.playlist)-1:
            if self.repeat:
                log.info("Playlist finished, looping back to the beginning (repeat==True)")
                self.current_track_num = 0
            else:
                log.info("Playlist finished")
                return

        log.info("Loading next track...")
        self.current_track_num += 1
        self.midi.load_track("{}.mid".format(self.playlist[self.current_track_num]))
        if self.state in ("playing",) or force_play:
            self.midi.play()
        
    def play(self):
        if self.state in ("paused", "stopped"):
            self.next_track(force_play=True)
        self.state = "playing"

    def stop(self):
        self.midi.stop()
        self.state = "stopped"

    def pause(self):
        self.midi.pause()
        self.state = "paused"

class Midi(object):
    """Low level midi interface via midish"""
    def __init__(self, track_end_callback=None, library_path="midi_store"):
        self.library_path = library_path
        self.current_track = None
        self.current_pos = TrackPosition(0,0,0)
        self.track_length = 0
        self.play_thread = None
        self._playing_state = "stopped"
        self.track_end_callback = track_end_callback
        self._startup()

    def _startup(self):
        cwd = os.getcwd()
        os.chdir(self.library_path)
        self.midish = pexpect.spawn('midish -v')
        os.chdir(cwd)
        self._update_position()
        self.midish.expect("\+ready")
        log.info("midish initialized")

    def load_track(self, name):
        self.stop()
        self.midish.sendline('import "{}"'.format(name))
        # Tracks that load properly show the initialized position:
        try:
            self._update_position()
        except Exception:
            raise AssertionError("Could not load track: {}".format(name))
        self.midish.expect("\+ready")
        # Get the track length in measures
        self.midish.sendline("print [mend]")
        self.midish.expect('[0-9]+')
        self.track_length = int(self.midish.match.group())
        self.midish.expect("\+ready")
        self.current_track = name
        log.info("Track loaded: {} - {} measures".format(name, self.track_length))

    def stop(self):
        if self.play_thread:
            self.play_thread.stop()
        self.midish.sendline("s")
        self.midish.expect("\+ready")
        self._playing_state = "stopped"
        log.info("Playback stopped")

    def pause(self):
        if self.play_thread:
            self.play_thread.stop()
        self.midish.sendline("i")
        self.midish.expect("\+ready")
        self._playing_state = "paused"
        log.info("Playback Paused")

    def _track_end(self):
        """Track end event trigger when track finished playing through to the end"""
        log.info("Track end")
        self.stop()
        if self.track_end_callback:
            self.track_end_callback(current_track=self.current_track)

    def play(self):
        if self._playing_state == "playing":
            return
        self.midish.sendline("p")
        self.midish.expect("\+ready")
        self.play_thread = MidiPlayThread(self)
        self.play_thread.start()
        self._playing_state = "playing"
        log.info("Playback started for {}".format(self.current_track))
        
    def _update_position(self, catch_exception=True):
        pats = ['\+pos ([0-9]+) ([0-9]+) ([0-9]+)']
        if catch_exception:
            pats.append('\+ready')
        index = self.midish.expect(pats)
        if index == 0:
            self.current_pos = TrackPosition(*[int(x) for x in self.midish.match.groups()])
        else:
            raise MidishException('expecting a track position, but got +ready instead')
        log.debug(self.current_pos)
        return self.current_pos

def server():
    midi = MidiPlaylist()
    daemon = Pyro4.Daemon()
    ns = Pyro4.locateNS()
    uri=daemon.register(midi)
    ns.register("midi", uri)
    print("Ready. Registered midi server with Pyro4 nameserver.")
    daemon.requestLoop()

if __name__ == "__main__":
    server()
