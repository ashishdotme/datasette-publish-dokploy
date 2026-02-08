from datasette import hookimpl
from datasette.publish.common import (
    add_common_publish_arguments_and_options,
    fail_if_publish_binary_not_installed,
)
from datasette.utils import (
    temporary_docker_directory,
    value_as_boolean,
    ValueAsBooleanError,
)
import click
from click.types import CompositeParamType
from subprocess import run, CalledProcessError
import json
import os
import pathlib
import shutil
import tempfile

INDEX_PY = """
from datasette.app import Datasette
import json
import pathlib
import os

static_mounts = [
    (static, str((pathlib.Path(".") / static).resolve()))
    for static in {statics}
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
    {database_files},
    static_mounts=static_mounts,
    metadata=metadata{extras},
    secret=secret,
    cors=True,
    settings={settings}{crossdb}
)
app = ds.app()
""".strip()

DOCKERFILE = """
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8001
EXPOSE 8001

CMD ["uvicorn", "index:app", "--host", "0.0.0.0", "--port", "8001"]
""".strip() + "\n"


class Setting(CompositeParamType):
    name = "setting"
    arity = 2

    def convert(self, config, param, ctx):
        from datasette.app import DEFAULT_SETTINGS

        name, value = config
        if name not in DEFAULT_SETTINGS:
            self.fail(
                f"{name} is not a valid option (--help-config to see all)",
                param,
                ctx,
            )
            return
        default = DEFAULT_SETTINGS[name]
        if isinstance(default, bool):
            try:
                return name, value_as_boolean(value)
            except ValueAsBooleanError:
                self.fail(f'"{name}" should be on/off/true/false/1/0', param, ctx)
                return
        elif isinstance(default, int):
            if not value.isdigit():
                self.fail(f'"{name}" should be an integer', param, ctx)
                return
            return name, int(value)
        elif isinstance(default, str):
            return name, value
        else:
            self.fail("Invalid option")


def add_dokploy_options(cmd):
    for decorator in reversed(
        (
            click.option(
                "--image",
                help="Container image name with tag, e.g. ghcr.io/owner/repo:latest",
            ),
            click.option(
                "--generate-dir",
                type=click.Path(dir_okay=True, file_okay=False),
                help="Output generated application files and stop without deploying",
            ),
            click.option(
                "--generate-github-actions",
                is_flag=True,
                help="Output GitHub Actions workflow YAML and stop",
            ),
            click.option(
                "--dokploy-url",
                help="Dokploy base URL, e.g. https://dokploy.example.com",
            ),
            click.option(
                "--application-id",
                help="Dokploy application ID for API-triggered deploy",
            ),
            click.option(
                "--api-key",
                help="Dokploy API key for API-triggered deploy",
            ),
            click.option(
                "--deploy-url",
                help="Dokploy deploy webhook URL",
            ),
            click.option(
                "--token",
                help="Optional bearer token for webhook-triggered deployments",
            ),
            click.option(
                "--setting",
                "settings",
                type=Setting(),
                help="Setting, see docs.datasette.io/en/stable/settings.html",
                multiple=True,
            ),
            click.option(
                "--crossdb", is_flag=True, help="Enable cross-database SQL queries"
            ),
        )
    ):
        cmd = decorator(cmd)
    return cmd


def github_actions_workflow():
    return """name: Deploy Datasette to Dokploy

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push image
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository }}:latest

      - name: Trigger Dokploy deployment
        uses: benbristow/dokploy-deploy-action@0.0.1
        with:
          auth_token: ${{ secrets.DOKPLOY_AUTH_TOKEN }}
          application_id: ${{ secrets.DOKPLOY_APPLICATION_ID }}
          dokploy_url: ${{ secrets.DOKPLOY_URL }}
"""


def _curl_check(url, method, headers, data=None):
    # curl exits 0 for HTTP 401/403/etc., so we capture the status code and body
    # and raise a ClickException for non-2xx responses.
    body_file = tempfile.NamedTemporaryFile(delete=False)
    body_file.close()
    try:
        cmd = [
            "curl",
            "-sS",
            "-X",
            method,
            url,
            "-o",
            body_file.name,
            "-w",
            "%{http_code}",
        ]
        for header in headers:
            cmd.extend(["-H", header])
        if data is not None:
            cmd.extend(["-d", data])

        result = run(cmd, capture_output=True, text=True)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        try:
            status = int(stdout) if stdout else 0
        except ValueError:
            status = 0

        try:
            body = open(body_file.name, "r", encoding="utf-8", errors="replace").read()
        except Exception:
            body = ""

        if result.returncode != 0:
            raise click.ClickException(stderr or f"curl failed with code {result.returncode}")
        if status < 200 or status >= 300:
            msg = body.strip() or "(empty response body)"
            raise click.ClickException(f"HTTP {status}: {msg}")
        return body
    finally:
        try:
            os.remove(body_file.name)
        except OSError:
            pass


def _looks_like_datasette_requirement(req):
    req = (req or "").strip().lower()
    if not req.startswith("datasette"):
        return False
    if req == "datasette":
        return True
    # datasette==0.65.2, datasette>=..., datasette[extra]==..., etc.
    next_ch = req[len("datasette") : len("datasette") + 1]
    return next_ch in ("=", "<", ">", "!", "~", "[", " ")


def _trigger_dokploy(dokploy_url, application_id, api_key):
    url = dokploy_url.rstrip("/") + "/api/application.deploy"
    payload = json.dumps({"applicationId": application_id})
    _curl_check(
        url,
        "POST",
        headers=[
            f"x-api-key: {api_key}",
            "accept: application/json",
            "content-type: application/json",
        ],
        data=payload,
    )


def _trigger_webhook(deploy_url, token):
    headers = []
    if token:
        headers.append(f"Authorization: Bearer {token}")
    _curl_check(deploy_url, "POST", headers=headers)


def _publish(
    files,
    metadata,
    extra_options,
    branch,
    template_dir,
    plugins_dir,
    static,
    install,
    plugin_secret,
    version_note,
    secret,
    title,
    license,
    license_url,
    source,
    source_url,
    about,
    about_url,
    image,
    generate_dir,
    generate_github_actions,
    dokploy_url,
    application_id,
    api_key,
    deploy_url,
    token,
    settings,
    crossdb,
):
    if generate_dir:
        generate_dir = str(pathlib.Path(generate_dir).resolve())

    if generate_github_actions:
        click.echo(github_actions_workflow().rstrip())
        return

    extra_metadata = {
        "title": title,
        "license": license,
        "license_url": license_url,
        "source": source,
        "source_url": source_url,
        "about": about,
        "about_url": about_url,
    }

    with temporary_docker_directory(
        files,
        "datasette-dokploy",
        metadata,
        extra_options,
        branch,
        template_dir,
        plugins_dir,
        static,
        install,
        False,
        version_note,
        secret,
        extra_metadata,
        port=8001,
    ):
        if os.path.exists("Dockerfile"):
            os.remove("Dockerfile")
        open("Dockerfile", "w").write(DOCKERFILE)

        extras = []
        if template_dir:
            extras.append('template_dir="templates"')
        if plugins_dir:
            extras.append('plugins_dir="plugins"')

        statics = [item[0] for item in static]
        open("index.py", "w").write(
            INDEX_PY.format(
                database_files=json.dumps([os.path.split(f)[-1] for f in files]),
                extras=", {}".format(", ".join(extras)) if extras else "",
                statics=json.dumps(statics),
                settings=json.dumps(dict(settings) or {}),
                crossdb=",\n    crossdb=True" if crossdb else "",
            )
        )

        install = list(install)
        datasette_from_install = next(
            (req for req in install if _looks_like_datasette_requirement(req)), None
        )
        if datasette_from_install and branch:
            raise click.ClickException(
                "Cannot use --branch and --install datasette... at the same time"
            )

        datasette_install = datasette_from_install or "datasette"
        if branch and not datasette_from_install:
            datasette_install = (
                "https://github.com/simonw/datasette/archive/{}.zip".format(branch)
            )
        if datasette_from_install:
            install = [req for req in install if not _looks_like_datasette_requirement(req)]

        open("requirements.txt", "w").write(
            "\n".join([datasette_install, "pysqlite3-binary", "uvicorn"] + install)
        )

        if generate_dir:
            shutil.copytree(".", generate_dir)
            click.echo("Your generated application files have been written to:", err=True)
            click.echo(f"    {generate_dir}\n", err=True)
            click.echo("To deploy from GitHub Actions:", err=True)
            click.echo("1. Commit and push these files", err=True)
            click.echo(
                "2. Run: datasette publish dokploy --generate-github-actions > .github/workflows/deploy-datasette.yml",
                err=True,
            )
            return

        if not image:
            raise click.ClickException(
                "--image is required for direct deployment. Use --generate-dir to export files instead."
            )

        fail_if_publish_binary_not_installed(
            "docker", "Docker", "https://docs.docker.com/get-docker/"
        )
        fail_if_publish_binary_not_installed("curl", "curl", "https://curl.se/")

        try:
            run(["docker", "build", "-t", image, "."], check=True)
            run(["docker", "push", image], check=True)
        except CalledProcessError as ex:
            raise click.ClickException(str(ex))

        if dokploy_url and application_id and api_key:
            _trigger_dokploy(dokploy_url, application_id, api_key)
        elif deploy_url:
            _trigger_webhook(deploy_url, token)


@hookimpl
def publish_subcommand(publish):
    @publish.command()
    @add_common_publish_arguments_and_options
    @add_dokploy_options
    def dokploy(*args, **kwargs):
        "Publish to self-hosted Dokploy"
        _publish(*args, **kwargs)
