import json
import os
import tempfile
import unittest

from bus.core import BusRoute


class BusRouteTest(unittest.TestCase):
    def setUp(self):
        self.game = BusRoute()

    def _archive_path(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    # 1. Arriving at a stop must release the previous stop.
    def test_arrive_releases_previous_stop(self):
        self.assertEqual(self.game.depart(), "departed")
        self.assertTrue(self.game.occupied["depot"])
        self.assertEqual(self.game.arrive(), "north")
        self.assertFalse(self.game.occupied["depot"])
        self.assertTrue(self.game.occupied["north"])
        self.assertEqual(self.game.arrive(), "central")
        self.assertFalse(self.game.occupied["north"])
        self.assertTrue(self.game.occupied["central"])

    # 2. The capacity check must count transfer passengers too.
    def test_full_capacity_includes_transfer_passengers(self):
        game = BusRoute(capacity=2)
        self.assertEqual(game.board("p1", "c1"), "boarded")
        self.assertEqual(game.board("t1", "c2", transfer=True), "boarded")
        self.assertEqual(game.passenger_count(), 2)
        self.assertEqual(game.board("p2", "c3"), "full")
        self.assertEqual(game.board("t2", "c4", transfer=True), "full")
        self.assertEqual(game.passenger_count(), 2)

    # 3. A detour must rewrite the remaining stops of the trip.
    def test_detour_updates_remaining_stops(self):
        self.game.depart()
        self.assertEqual(self.game.arrive(), "north")
        self.assertEqual(self.game.detour("riverside", "harbor"), "ok")
        self.assertEqual(self.game.stops, ["depot", "north", "riverside", "harbor"])
        self.assertEqual(self.game.arrive(), "riverside")
        self.assertFalse(self.game.occupied["north"])
        self.assertTrue(self.game.occupied["riverside"])
        self.assertEqual(self.game.arrive(), "harbor")
        self.assertEqual(self.game.arrive(), "terminus")

    # 4. Shifts must not advance while the driver is resting.
    def test_shifts_halt_while_driver_rests(self):
        self.assertEqual(self.game.tick(), "ok")
        self.assertEqual(self.game.schedule, 1)
        self.game.rest()
        self.assertEqual(self.game.tick(), "resting")
        self.assertEqual(self.game.tick(), "resting")
        self.assertEqual(self.game.schedule, 1)
        self.game.resume()
        self.assertEqual(self.game.tick(), "ok")
        self.assertEqual(self.game.schedule, 2)

    # 5. Cancelling a departure must refund the trip's fares.
    def test_cancel_departure_refunds_fares(self):
        self.game.depart()
        self.assertEqual(self.game.swipe("c1", 5), "charged")
        self.assertEqual(self.game.swipe("c2", 3), "charged")
        self.assertEqual(self.game.revenue, 8)
        self.assertEqual(self.game.cancel(), "refunded")
        self.assertEqual(self.game.revenue, 0)
        self.assertEqual(self.game.fares, [])
        self.assertEqual(self.game.passenger_count(), 0)
        self.assertEqual(self.game.cancel(), "noop")
        self.assertEqual(self.game.revenue, 0)

    # 6. Swiping the same card twice must charge only once.
    def test_duplicate_swipe_charges_once(self):
        self.game.depart()
        self.assertEqual(self.game.swipe("c1", 5), "charged")
        self.assertEqual(self.game.swipe("c1", 5), "duplicate")
        self.assertEqual(self.game.swipe("c1", 5), "duplicate")
        self.assertEqual(self.game.revenue, 5)
        self.assertEqual(len(self.game.fares), 1)
        self.game.cancel()
        self.assertEqual(self.game.revenue, 0)
        self.game.depart()
        self.assertEqual(self.game.swipe("c1", 5), "charged")
        self.assertEqual(self.game.revenue, 5)

    # 7. Delay events must not leak into the next day.
    def test_delay_events_cleared_on_new_day(self):
        self.game.delay(12)
        self.game.delay(8)
        self.assertEqual(self.game.delay_minutes, 20)
        self.assertEqual(len(self.game.delays), 2)
        self.game.next_day()
        self.assertEqual(self.game.day, 2)
        self.assertEqual(self.game.delays, [])
        self.assertEqual(self.game.delay_minutes, 0)

    # 8. Loading an archive must not double the passengers.
    def test_load_does_not_double_passengers(self):
        path = self._archive_path()
        self.game.board("p1", "c1")
        self.game.board("p2", "c2", transfer=True)
        self.game.save(path)
        restored = BusRoute().load(path)
        self.assertEqual(restored.passenger_count(), 2)
        restored.load(path)
        self.assertEqual(restored.passenger_count(), 2)
        self.assertEqual([p["id"] for p in restored.onboard], ["p1", "p2"])

    # Boundary: empty and unknown commands are rejected.
    def test_empty_command_rejected(self):
        with self.assertRaises(ValueError):
            self.game.cmd("")
        with self.assertRaises(ValueError):
            self.game.cmd("   ")

    def test_unknown_command_rejected(self):
        with self.assertRaises(ValueError):
            self.game.cmd("fly")
        with self.assertRaises(ValueError):
            self.game.cmd("swipe c1")
        with self.assertRaises(ValueError):
            self.game.cmd("detour")
        with self.assertRaises(ValueError):
            self.game.cmd("tick now")

    # Boundary: at exactly full load the next boarding is refused.
    def test_capacity_boundary_exact(self):
        game = BusRoute(capacity=1)
        self.assertEqual(game.board("p1", "c1"), "boarded")
        self.assertEqual(game.passenger_count(), 1)
        self.assertEqual(game.board("p2", "c2"), "full")
        self.assertEqual(game.passenger_count(), 1)

    # Boundary: boarding the same passenger twice is idempotent.
    def test_duplicate_board_is_idempotent(self):
        self.assertEqual(self.game.board("p1", "c1"), "boarded")
        self.assertEqual(self.game.board("p1", "c1"), "duplicate")
        self.assertEqual(self.game.passenger_count(), 1)

    # Boundary: repeated depart is idempotent, cancel without depart is a noop.
    def test_depart_and_cancel_idempotent(self):
        self.assertEqual(self.game.depart(), "departed")
        self.assertEqual(self.game.depart(), "duplicate")
        self.assertEqual(self.game.trip, 1)
        fresh = BusRoute()
        self.assertEqual(fresh.cancel(), "noop")
        self.assertEqual(fresh.revenue, 0)

    # Boundary: the bus cannot move before departure or past the terminus.
    def test_arrive_requires_departure_and_stops_at_terminus(self):
        self.assertEqual(self.game.arrive(), "not_departed")
        self.game.depart()
        self.assertEqual(self.game.arrive(), "north")
        self.assertEqual(self.game.arrive(), "central")
        self.assertEqual(self.game.arrive(), "harbor")
        self.assertEqual(self.game.arrive(), "terminus")
        self.assertTrue(self.game.occupied["harbor"])
        self.assertEqual(self.game.position, 3)

    # Boundary: a resting driver cannot drive the bus.
    def test_bus_waits_while_driver_rests(self):
        self.game.depart()
        self.game.rest()
        self.assertEqual(self.game.arrive(), "resting")
        self.assertTrue(self.game.occupied["depot"])
        self.assertEqual(self.game.depart(), "duplicate")
        self.game.resume()
        self.assertEqual(self.game.arrive(), "north")

    # Archive: save/load round-trips the full state including the event sequence.
    def test_save_load_roundtrip_preserves_state(self):
        path = self._archive_path()
        reference = BusRoute()
        self.game.depart()
        self.game.board("p1", "c1")
        self.game.swipe("c1", 5)
        self.game.arrive()
        self.game.delay(7)
        expected = [self.game.roll_event() for _ in range(3)]
        for _ in range(3):
            reference.depart()
            reference.board("p1", "c1")
            reference.swipe("c1", 5)
            reference.arrive()
            reference.delay(7)
            reference.roll_event()
        self.game.save(path)
        with open(path, "r", encoding="utf-8") as handle:
            archive = json.load(handle)
        self.assertEqual(archive["events"], expected)
        self.assertEqual(archive["revenue"], 5)
        restored = BusRoute().load(path)
        self.assertEqual(restored.events, expected)
        self.assertEqual(restored.revenue, 5)
        self.assertEqual(restored.position, 1)
        self.assertTrue(restored.occupied["north"])
        self.assertEqual(restored.roll_event(), reference.roll_event())

    # Archive: CMD dispatch drives save/load and stays idempotent.
    def test_cmd_save_load_roundtrip(self):
        path = self._archive_path()
        self.assertEqual(self.game.cmd("depart"), "departed")
        self.assertEqual(self.game.cmd("board p1 c1"), "boarded")
        self.assertEqual(self.game.cmd("swipe c1 4"), "charged")
        self.assertEqual(self.game.cmd("save %s" % path), "saved")
        restored = BusRoute()
        self.assertEqual(restored.cmd("load %s" % path), "loaded")
        self.assertEqual(restored.cmd("load %s" % path), "loaded")
        self.assertEqual(restored.revenue, 4)
        self.assertEqual(restored.passenger_count(), 1)
        self.assertEqual(restored.swipe("c1", 4), "duplicate")


if __name__ == "__main__":
    unittest.main()
