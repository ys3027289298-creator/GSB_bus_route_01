"""Offline bus route dispatch game core.

Fixed seed, CMD instruction dispatch, JSON save/load archives.
"""

import json
import random

DEFAULT_SEED = 67
DEFAULT_CAPACITY = 3
DEFAULT_STOPS = ("depot", "north", "central", "harbor")


def _rng_state_to_json(state):
    version, internal, gauss = state
    return {"version": version, "internal": list(internal), "gauss": gauss}


def _rng_state_from_json(data):
    return (data["version"], tuple(data["internal"]), data["gauss"])


class BusRoute:
    def __init__(self, seed=DEFAULT_SEED, capacity=DEFAULT_CAPACITY, stops=DEFAULT_STOPS):
        self.seed = seed
        self.day = 1
        self.schedule = 0
        self.trip = 0
        self.capacity = capacity
        self.base_stops = list(stops)
        self.stops = list(stops)
        self.position = 0
        self.occupied = {stop: False for stop in self.base_stops}
        self.occupied[self.stops[0]] = True
        self.departed = False
        self.driver_resting = False
        self.onboard = []
        self.revenue = 0
        self.trip_fares = 0
        self.fares = []
        self.swiped = set()
        self.delays = []
        self.delay_minutes = 0
        self.events = []
        self.audit = []
        self._rng = random.Random(seed)

    # -- random event sequence ------------------------------------------
    def roll_event(self):
        value = self._rng.randrange(1, 1000000)
        self.events.append(value)
        return value

    # -- driver shifts ----------------------------------------------------
    def rest(self):
        self.driver_resting = True
        self.audit.append(["rest", self.day, self.schedule])
        return "ok"

    def resume(self):
        self.driver_resting = False
        self.audit.append(["resume", self.day, self.schedule])
        return "ok"

    # -- one schedule slot per tick; frozen while the driver rests --------
    def tick(self):
        if self.driver_resting:
            self.audit.append(["resting", self.day, self.schedule])
            return "resting"
        self.schedule += 1
        self.audit.append(["tick", self.day, self.schedule])
        return "ok"

    # -- departure lifecycle ------------------------------------------------
    def depart(self):
        if self.departed:
            self.audit.append(["depart_duplicate", self.trip])
            return "duplicate"
        if self.driver_resting:
            self.audit.append(["depart_refused_resting", self.trip])
            return "resting"
        self.trip += 1
        self.departed = True
        self.trip_fares = 0
        self.swiped = set()
        self.audit.append(["depart", self.trip])
        return "departed"

    def cancel(self):
        if not self.departed:
            self.audit.append(["cancel_noop", self.trip])
            return "noop"
        self.revenue -= self.trip_fares
        self.fares = [fare for fare in self.fares if fare[0] != self.trip]
        self.audit.append(["refund", self.trip, self.trip_fares])
        self.trip_fares = 0
        self.swiped = set()
        self.departed = False
        self.onboard = []
        return "refunded"

    # -- movement: arriving releases the previous stop ---------------------
    def arrive(self):
        if not self.departed:
            self.audit.append(["arrive_refused_not_departed", self.position])
            return "not_departed"
        if self.driver_resting:
            self.audit.append(["arrive_refused_resting", self.position])
            return "resting"
        if self.position + 1 >= len(self.stops):
            self.audit.append(["terminus", self.stops[self.position]])
            return "terminus"
        self.occupied[self.stops[self.position]] = False
        self.position += 1
        stop = self.stops[self.position]
        self.occupied.setdefault(stop, False)
        self.occupied[stop] = True
        self.audit.append(["arrive", stop, self.position])
        return stop

    # -- detour rewrites the remaining stops of the active trip ------------
    def detour(self, *new_stops):
        if not new_stops:
            raise ValueError("detour requires at least one stop")
        self.stops = self.stops[: self.position + 1] + list(new_stops)
        for stop in new_stops:
            self.occupied.setdefault(stop, False)
        self.audit.append(["detour", list(new_stops), self.position])
        return "ok"

    # -- boarding: transfers count toward the load like everyone else ------
    def board(self, passenger_id, card, transfer=False):
        if any(p["id"] == passenger_id for p in self.onboard):
            self.audit.append(["board_duplicate", passenger_id])
            return "duplicate"
        if len(self.onboard) >= self.capacity:
            self.audit.append(["board_full", passenger_id, transfer])
            return "full"
        self.onboard.append({"id": passenger_id, "card": card, "transfer": bool(transfer)})
        self.audit.append(["board", passenger_id, bool(transfer)])
        return "boarded"

    def passenger_count(self):
        return len(self.onboard)

    # -- fares: one swipe per card per trip ---------------------------------
    def swipe(self, card, fare):
        if not self.departed:
            self.audit.append(["swipe_refused_not_departed", card])
            return "not_departed"
        if card in self.swiped:
            self.audit.append(["swipe_duplicate", card, self.trip])
            return "duplicate"
        fare = int(fare)
        self.swiped.add(card)
        self.fares.append([self.trip, card, fare])
        self.revenue += fare
        self.trip_fares += fare
        self.audit.append(["swipe", card, fare, self.trip])
        return "charged"

    # -- delays live and die within a single day -----------------------------
    def delay(self, minutes):
        minutes = int(minutes)
        self.delays.append({"day": self.day, "minutes": minutes})
        self.delay_minutes += minutes
        self.audit.append(["delay", self.day, minutes])
        return "ok"

    def next_day(self):
        self.day += 1
        self.delays = []
        self.delay_minutes = 0
        self.audit.append(["next_day", self.day])
        return "ok"

    # -- CMD instruction dispatch -----------------------------------------
    def cmd(self, line):
        parts = line.split()
        if not parts:
            raise ValueError("empty command")
        name = parts[0]
        args = parts[1:]
        if name == "tick":
            self._require_args(name, args, 0)
            return self.tick()
        if name == "rest":
            self._require_args(name, args, 0)
            return self.rest()
        if name == "resume":
            self._require_args(name, args, 0)
            return self.resume()
        if name == "depart":
            self._require_args(name, args, 0)
            return self.depart()
        if name == "cancel":
            self._require_args(name, args, 0)
            return self.cancel()
        if name == "arrive":
            self._require_args(name, args, 0)
            return self.arrive()
        if name == "detour":
            if not args:
                raise ValueError("usage: detour <stop> [stop...]")
            return self.detour(*args)
        if name == "board":
            self._require_args(name, args, 2, 3)
            transfer = len(args) == 3 and args[2] == "transfer"
            return self.board(args[0], args[1], transfer=transfer)
        if name == "swipe":
            self._require_args(name, args, 2)
            return self.swipe(args[0], int(args[1]))
        if name == "delay":
            self._require_args(name, args, 1)
            return self.delay(int(args[0]))
        if name == "nextday":
            self._require_args(name, args, 0)
            return self.next_day()
        if name == "event":
            self._require_args(name, args, 0)
            return str(self.roll_event())
        if name == "save":
            self._require_args(name, args, 1)
            self.save(args[0])
            return "saved"
        if name == "load":
            self._require_args(name, args, 1)
            self.load(args[0])
            return "loaded"
        raise ValueError("unknown command: %s" % name)

    @staticmethod
    def _require_args(name, args, minimum, maximum=None):
        if maximum is None:
            maximum = minimum
        if not minimum <= len(args) <= maximum:
            raise ValueError("bad argument count for command: %s" % name)

    # -- JSON archive -------------------------------------------------------
    def save(self, path):
        state = {
            "seed": self.seed,
            "day": self.day,
            "schedule": self.schedule,
            "trip": self.trip,
            "capacity": self.capacity,
            "base_stops": list(self.base_stops),
            "stops": list(self.stops),
            "position": self.position,
            "occupied": dict(self.occupied),
            "departed": self.departed,
            "driver_resting": self.driver_resting,
            "onboard": [dict(p) for p in self.onboard],
            "revenue": self.revenue,
            "trip_fares": self.trip_fares,
            "fares": [list(fare) for fare in self.fares],
            "swiped": sorted(self.swiped),
            "delays": [dict(d) for d in self.delays],
            "delay_minutes": self.delay_minutes,
            "events": list(self.events),
            "audit": [list(event) for event in self.audit],
            "rng_state": _rng_state_to_json(self._rng.getstate()),
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)

    def load(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        self.seed = state["seed"]
        self.day = state["day"]
        self.schedule = state["schedule"]
        self.trip = state["trip"]
        self.capacity = state["capacity"]
        self.base_stops = list(state["base_stops"])
        self.stops = list(state["stops"])
        self.position = state["position"]
        self.occupied = dict(state["occupied"])
        self.departed = state["departed"]
        self.driver_resting = state["driver_resting"]
        self.onboard = [dict(p) for p in state["onboard"]]
        self.revenue = state["revenue"]
        self.trip_fares = state["trip_fares"]
        self.fares = [list(fare) for fare in state["fares"]]
        self.swiped = set(state["swiped"])
        self.delays = [dict(d) for d in state["delays"]]
        self.delay_minutes = state["delay_minutes"]
        self.events = list(state["events"])
        self.audit = [list(event) for event in state["audit"]]
        self._rng = random.Random(self.seed)
        self._rng.setstate(_rng_state_from_json(state["rng_state"]))
        return self


def demo():
    game = BusRoute()
    script = [
        "depart",
        "board p1 c1",
        "board p2 c2 transfer",
        "swipe c1 5",
        "swipe c1 5",
        "swipe c2 3",
        "arrive",
        "detour riverside harbor",
        "arrive",
        "delay 12",
        "tick",
        "rest",
        "tick",
        "resume",
        "tick",
        "nextday",
        "event",
        "cancel",
    ]
    for line in script:
        print("%-28s -> %s" % (line, game.cmd(line)))
    print("day=%d schedule=%d trip=%d revenue=%d passengers=%d delays=%s"
          % (game.day, game.schedule, game.trip, game.revenue,
             game.passenger_count(), game.delays))
    return game


if __name__ == "__main__":
    demo()
