
import re
import pathlib


# Release version scheme - special case of PEP-440.
# Forms allowed: N.N.N, N.N.NaN, N.N.NbN, N.N.NrcN
RELEASE_TAG_PATTERN = re.compile(r'^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$')

# Development version scheme.
# Forms allowed: dev.XXXX, where `XXXX` - any non-empty alphanumerical suffix
DEVELOPMENT_TAG_PATTERN = re.compile(r'^dev\.[a-zA-Z0-9.]+$')


RELEASES_REMOTE_ROOT = pathlib.PurePosixPath('~/releases')
PROJECTS_REMOTE_ROOT = pathlib.PurePosixPath('~/projects')
SERVICE_INDEX_FILENAME = 'services.index.yaml'


CODONS_TEMPLATE = """
project:
  tag: {project_tag}

tag_policy: ribosome.default_tag_policy

slack:
  # incoming_webhook_url: https://hooks.slack.com/services/...

meta:
  format: python

codestyle:
  commands:
    # - make codestyle

build:
  commands:
    # - make build

test:
  commands:
    # - make test

release:
  include:
    - meta.py
  publish:
    # s3bucket: <bucket-name>
    # localdir: ..

setup:
  commands:
    # - make setup

cleanup:
  # will run with sudo
  commands:
    # - rm -rf $(pipenv --venv)

service:
  load:
    # will run with sudo
    commands:
      # - $(pipenv --py) ./service.py install {{service}} {{config}}
      # - $(pipenv --py) ./service.py start {{service}} {{config}}
  unload:
    # will run with sudo
    commands:
      # - $(pipenv --py) ./service.py uninstall {{service}} {{config}}
  do:
    commands:
      # - $(pipenv --py) ./service.py do {{service}} {{config}} {{action}} {{args}}

services:
  # <service_name>:
  #   configs:
  #     - <config1>
  #     - <config2>
"""


META_PYTHON = """
# This file is generated. Do not edit or store under version control.

project = {project}
version = {version}

revision = {revision}
branch = {branch}
tag = {tag}
distance = {distance}
dirty = {dirty}

generated = {generated}
"""
