"""Functionality of this modules adopted from [setuptools_scm] package."""

import pathlib

from ribosome.scminfo import git
from ribosome.scminfo import hg


HANDLERS = (git, hg)


def describe(root=None):

    if root is None:
        rootpath = pathlib.Path.cwd()
    else:
        rootpath = pathlib.Path(root)

    if not rootpath.exists():
        return None, 'Root path does not exists: {}'.format(rootpath)
    if not rootpath.is_dir():
        return None, 'Root path is not directory: {}'.format(rootpath)

    for module in HANDLERS:
        if module.detect(rootpath):
            return module.describe(rootpath)

    return None, 'No supported SCM found in directory: {}'.format(rootpath)
