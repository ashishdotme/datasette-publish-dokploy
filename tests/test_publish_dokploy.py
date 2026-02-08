from click.testing import CliRunner
from datasette import cli
from unittest import mock
import os
import pathlib
import pytest
import textwrap


@mock.patch("shutil.which")
def test_publish_dokploy_generate_dir(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            ["publish", "dokploy", "test.db", "--generate-dir", "app"],
        )
        assert result.exit_code == 0, result.output
        assert set(os.listdir("app")) >= {
            "Dockerfile",
            "index.py",
            "requirements.txt",
            "test.db",
        }


@mock.patch("shutil.which")
def test_publish_dokploy_generate_workflow(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            ["publish", "dokploy", "test.db", "--generate-github-actions"],
        )
        assert result.exit_code == 0
        assert "Deploy Datasette to Dokploy" in result.output
        assert "benbristow/dokploy-deploy-action@0.0.1" in result.output


@mock.patch("shutil.which")
@mock.patch("datasette_publish_dokploy.run")
def test_publish_dokploy_direct_api_trigger(mock_run, mock_which):
    mock_which.return_value = True
    # First two run() calls are docker build/push (check=True).
    # Third is curl with status/body capture (capture_output=True).
    def fake_run(args, **kwargs):
        if args[0] == "curl":
            # Return HTTP status code in stdout
            return mock.Mock(returncode=0, stdout="200", stderr="")
        return mock.Mock(0)

    mock_run.side_effect = fake_run
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            [
                "publish",
                "dokploy",
                "test.db",
                "--image",
                "ghcr.io/me/repo:latest",
                "--dokploy-url",
                "https://dokploy.example.com",
                "--application-id",
                "app-123",
                "--api-key",
                "secret",
            ],
        )
        assert result.exit_code == 0, result.output
        # Verify docker build/push happened
        assert mock_run.call_args_list[0] == mock.call(
            ["docker", "build", "-t", "ghcr.io/me/repo:latest", "."], check=True
        )
        assert mock_run.call_args_list[1] == mock.call(
            ["docker", "push", "ghcr.io/me/repo:latest"], check=True
        )
        # Verify curl was invoked against the correct endpoint with API key + payload
        curl_args = mock_run.call_args_list[2].args[0]
        assert curl_args[:5] == ["curl", "-sS", "-X", "POST", "https://dokploy.example.com/api/application.deploy"]
        assert "x-api-key: secret" in curl_args
        assert "content-type: application/json" in curl_args
        assert '{"applicationId": "app-123"}' in curl_args


@mock.patch("shutil.which")
@mock.patch("datasette_publish_dokploy.run")
def test_publish_dokploy_webhook_trigger(mock_run, mock_which):
    mock_which.return_value = True
    def fake_run(args, **kwargs):
        if args[0] == "curl":
            return mock.Mock(returncode=0, stdout="200", stderr="")
        return mock.Mock(0)

    mock_run.side_effect = fake_run
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            [
                "publish",
                "dokploy",
                "test.db",
                "--image",
                "ghcr.io/me/repo:latest",
                "--deploy-url",
                "https://dokploy.example.com/hook/deploy",
                "--token",
                "tok",
            ],
        )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args_list[0] == mock.call(
            ["docker", "build", "-t", "ghcr.io/me/repo:latest", "."], check=True
        )
        assert mock_run.call_args_list[1] == mock.call(
            ["docker", "push", "ghcr.io/me/repo:latest"], check=True
        )
        curl_args = mock_run.call_args_list[2].args[0]
        assert curl_args[:5] == ["curl", "-sS", "-X", "POST", "https://dokploy.example.com/hook/deploy"]
        assert "Authorization: Bearer tok" in curl_args


@mock.patch("shutil.which")
def test_publish_dokploy_requires_image_for_direct_deploy(mock_which):
    mock_which.return_value = True
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        result = runner.invoke(
            cli.cli,
            ["publish", "dokploy", "test.db", "--deploy-url", "https://dokploy.example.com/hook/deploy"],
        )
        assert result.exit_code == 1
        assert "--image is required for direct deployment" in result.output


@pytest.fixture(scope="session")
@mock.patch("shutil.which")
@mock.patch("datasette_publish_dokploy.run")
def generated_app_dir(mock_run, mock_which, tmp_path_factory):
    appdir = os.path.join(tmp_path_factory.mktemp("generated-app"), "app")
    mock_which.return_value = True
    mock_run.return_value = mock.Mock(0)
    runner = CliRunner()
    with runner.isolated_filesystem():
        open("test.db", "w").write("data")
        static_dir = pathlib.Path(".") / "static"
        static_dir.mkdir()
        (static_dir / "my.css").write_text("body { color: red }")
        result = runner.invoke(
            cli.cli,
            [
                "publish",
                "dokploy",
                "test.db",
                "--static",
                "static:static",
                "--setting",
                "default_page_size",
                "10",
                "--setting",
                "sql_time_limit_ms",
                "2000",
                "--setting",
                "allow_download",
                "0",
                "--crossdb",
                "--generate-dir",
                appdir,
            ],
        )
        assert result.exit_code == 0, result.output
        assert not mock_run.called
    return appdir


def test_publish_dokploy_generate(generated_app_dir):
    filenames = set(os.listdir(generated_app_dir))
    assert {
        "Dockerfile",
        "requirements.txt",
        "static",
        "index.py",
        "test.db",
    } <= filenames
    index_py = open(os.path.join(generated_app_dir, "index.py")).read()
    assert index_py.strip() == (
        textwrap.dedent(
            """
    from datasette.app import Datasette
    import json
    import pathlib
    import os

    static_mounts = [
        (static, str((pathlib.Path(".") / static).resolve()))
        for static in ["static"]
    ]

    metadata = dict()
    try:
        metadata = json.load(open("metadata.json"))
    except Exception:
        pass

    secret = os.environ.get("DATASETTE_SECRET")

    true, false = True, False

    ds = Datasette(
        [],
        ["test.db"],
        static_mounts=static_mounts,
        metadata=metadata,
        secret=secret,
        cors=True,
        settings={"default_page_size": 10, "sql_time_limit_ms": 2000, "allow_download": false},
        crossdb=True
    )
    app = ds.app()
    """
        ).strip()
    )
