from solarsystem.cli import main


def test_missing_file_exits_nonzero(tmp_path, capsys):
    code = main([str(tmp_path / "missing.pdf"), "--output", str(tmp_path), "--service", "echo"])
    assert code != 0
    assert "找不到文件" in capsys.readouterr().err
