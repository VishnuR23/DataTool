from datatool.telemetry.cursor import FileCursor


def test_cursor_roundtrips_watermarks(tmp_path):
    cursor = FileCursor(tmp_path / "cur.json")
    assert cursor.load() == {}
    cursor.save({"action": "2026-06-22T00:00:00+00:00"})
    assert FileCursor(tmp_path / "cur.json").load() == {"action": "2026-06-22T00:00:00+00:00"}
