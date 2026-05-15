import datetime
import os
import sqlite3
from tempfile import TemporaryDirectory

from pytest_mock.plugin import MockerFixture

from findmy_traccar_bridge import bridge, time


class FakeClock(time.Clock):
    called = 0

    def now(self) -> datetime.datetime:
        if(self.called < 5):
            res = datetime.datetime(2025,1,1,0,0,0)
        elif(self.called < 10):
            # 2 hours after first api call, should make another
            res = datetime.datetime(2025,1,1,2,0,0)
        elif(self.called < 50):
            # should not do any calls beacause of rate limiting
            res = datetime.datetime(2025,1,1,2,0,3)
        else:
            # stop condition reached
            res = datetime.datetime(2025,1,1,2,0,6)
        self.called += 1
        return res

    def sleep(self, seconds: float):
        pass


def test_main_loop(mocker: MockerFixture):
    mocker.patch.dict(os.environ, {"BRIDGE_PRIVATE_KEYS": "ab12,cd34"})

    apple_account_mocker = mocker.Mock()
    def fake_account(manager):
        manager.apple_account = apple_account_mocker
        # todo: return real LocationReport objects
        manager.apple_account.fetch_location_history.return_value = dict()
    mocker.patch(
        "findmy_traccar_bridge.device_utilities.AppleAccountManager.load_login_token",
        new=fake_account
    )

    fake_clock = FakeClock()

    with TemporaryDirectory() as tmp_dir:

        bridge.db_path = f"{tmp_dir}/db.sqlite" # ":memory:" if data doesn't matter
        bridge.Bridge(clock=fake_clock).run(until=datetime.datetime(2025,1,1,2,0,5))

        # initial call, then wait for rate-limit, then call again, then break
        assert apple_account_mocker.fetch_location_history.call_count == 2

        db = sqlite3.connect(bridge.db_path)
        # for now nothing is returned
        assert db.cursor().execute("select count(*) from locations").fetchone() == (0,)
