import pytest

from face_unlock.voting import FrameVote, Verdict


def test_early_pass() -> None:
    v = FrameVote(required=3, window=5)
    assert v.add(True) is Verdict.PENDING
    assert v.add(True) is Verdict.PENDING
    assert v.add(True) is Verdict.PASS


def test_early_fail_when_pass_impossible() -> None:
    v = FrameVote(required=3, window=5)
    v.add(False)
    v.add(False)
    assert v.add(False) is Verdict.FAIL  # 0 passes + 2 remaining < 3


def test_mixed_reaches_pass_on_last_frame() -> None:
    v = FrameVote(required=3, window=5)
    for r in [True, False, True, False]:
        assert v.add(r) is Verdict.PENDING
    assert v.add(True) is Verdict.PASS


def test_full_window_rejects_more() -> None:
    v = FrameVote(required=1, window=1)
    v.add(False)
    with pytest.raises(RuntimeError):
        v.add(True)


@pytest.mark.parametrize("required,window", [(0, 5), (6, 5)])
def test_invalid_params(required: int, window: int) -> None:
    with pytest.raises(ValueError):
        FrameVote(required, window)
