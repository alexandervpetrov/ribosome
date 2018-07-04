"""Functionality of this package partially adopted from [setuptools_scm] package."""

import pathlib

from . import git
from . import hg


HANDLERS = (git, hg)


def _detect_scm_handler(rootpath):

    if not rootpath.exists():
        return None, 'Root path does not exists: {}'.format(rootpath)
    if not rootpath.is_dir():
        return None, 'Root path is not directory: {}'.format(rootpath)

    for handler in HANDLERS:
        if handler.detect(rootpath):
            return handler, None

    return None, None


def describe(root):
    rootpath = pathlib.Path(root)

    handler, error = _detect_scm_handler(rootpath)
    if error is not None:
        return None, error
    if handler is None:
        return None, 'No supported SCM found in directory: {}'.format(rootpath)

    return handler.describe(rootpath)


def archive(root, targetdir):
    rootpath = pathlib.Path(root)
    targetdir = pathlib.Path(targetdir)

    handler, error = _detect_scm_handler(rootpath)
    if error is not None:
        return None, error
    if handler is None:
        return None, 'No supported SCM found in directory: {}'.format(rootpath)

    return handler.archive(rootpath, targetdir)
