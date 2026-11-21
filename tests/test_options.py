

def test_windows_option_validates_length():
    import pytest
    from pydantic import ValidationError

    from reelwright.options import JobOptions

    ok = JobOptions(windows=[{"start": 10, "end": 40, "title": "x"}])
    assert ok.windows[0].end == 40
    with pytest.raises(ValidationError):
        JobOptions(windows=[{"start": 10, "end": 11}])
