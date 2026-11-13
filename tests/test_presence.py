from face_unlock.config import PresenceConfig
from face_unlock.presence import Action, PresenceEnv, PresenceMonitor, Seen

CFG = PresenceConfig(idle_minutes=3.0, check_interval_s=20.0, absent_checks_to_lock=3)


class Env:
    def __init__(self, idle: float, seen: list[Seen], paused: bool = False) -> None:
        self.idle = idle
        self.seen = list(seen)
        self.is_paused = paused
        self.checks = 0
        self.locks = 0

    def build(self) -> PresenceEnv:
        def check() -> Seen:
            self.checks += 1
            return self.seen.pop(0)

        def lock() -> None:
            self.locks += 1

        return PresenceEnv(lambda: self.idle, lambda: self.is_paused, check, lock)


def test_no_camera_while_active() -> None:
    env = Env(idle=30, seen=[])
    m = PresenceMonitor(CFG, env.build())
    action, sleep = m.tick()
    assert action is Action.NONE and env.checks == 0
    assert sleep == min(60.0, 180 - 30 + 1)


def test_locks_after_consecutive_absent() -> None:
    env = Env(idle=400, seen=[Seen.ABSENT] * 3)
    m = PresenceMonitor(CFG, env.build())
    actions = [m.tick()[0] for _ in range(3)]
    assert actions == [Action.CHECKED, Action.CHECKED, Action.LOCKED] and env.locks == 1


def test_presence_resets_streak() -> None:
    env = Env(idle=400, seen=[Seen.ABSENT, Seen.ABSENT, Seen.PRESENT, Seen.ABSENT, Seen.ABSENT])
    m = PresenceMonitor(CFG, env.build())
    for _ in range(5):
        m.tick()
    assert env.locks == 0


def test_unknown_never_locks() -> None:
    env = Env(idle=400, seen=[Seen.UNKNOWN] * 10)
    m = PresenceMonitor(CFG, env.build())
    for _ in range(10):
        m.tick()
    assert env.locks == 0


def test_paused_never_checks() -> None:
    env = Env(idle=400, seen=[], paused=True)
    m = PresenceMonitor(CFG, env.build())
    assert m.tick()[0] is Action.PAUSED and env.checks == 0


def test_user_returns_during_last_check() -> None:
    env = Env(idle=400, seen=[Seen.ABSENT] * 3)
    pe = env.build()
    calls = {"n": 0}

    def idle() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] > 3 else 400.0  # input arrives during the 3rd camera check

    m = PresenceMonitor(CFG, PresenceEnv(idle, pe.paused, pe.check, pe.lock))
    for _ in range(3):
        m.tick()
    assert env.locks == 0
