
import logging
import pathlib
import tempfile

from . import utils

log = logging.getLogger('scmtools.git')


def detect(rootpath):
    rootpath = pathlib.Path(rootpath)
    scmdir = rootpath.joinpath('.git')
    return scmdir.exists() and scmdir.is_dir()


def is_git_command_available(rootpath):
    __, error = utils.run('git help'.split(), cwd=rootpath)
    return error is None


def describe(rootpath):
    if not is_git_command_available(rootpath):
        return None, 'Command [git] is not available'

    output, error = utils.run(
        'git rev-parse --show-toplevel'.split(),
        cwd=rootpath,
        errormsg='Failed to identify repository',
    )
    if error is not None:
        return None, error

    gitroot = output
    if not gitroot:
        return None, 'Failed to identify repository'
    if not rootpath.samefile(pathlib.Path(gitroot)):
        return None, 'Failed to identify repository: root paths detected not match'

    shallow_file_path = rootpath.joinpath('.git').joinpath('shallow')
    if shallow_file_path.is_file():
        return None, 'Repository is shallow clone. Cannot reliably work on shallow'

    branch, error = get_branch(rootpath)
    if error is not None:
        return None, error

    description, error = get_latest_tag_description(rootpath)
    if error is None:
        latest_tag, distance, revision, dirty = description
    else:
        # probably latest tag does not exist
        latest_tag = None
        revision, error = get_revision(rootpath)
        if error is not None:
            return None, error
        distance, error = get_all_commits(rootpath)
        if error is not None:
            return None, error
        dirty, error = get_dirty_status(rootpath)
        if error is not None:
            return None, error

    scminfo = dict(
        scm='git',
        revision=revision,
        branch=branch,
        tag=latest_tag,
        distance=distance,
        dirty=dirty,
    )
    return scminfo, None


def archive(rootpath, targetdir):
    if not is_git_command_available(rootpath):
        return None, 'Command [git] is not available'

    log.debug('Making archive of repo [%s] to [%s]...', rootpath, targetdir)
    with tempfile.TemporaryDirectory() as tempdir:
        archive = pathlib.Path(tempdir).joinpath('archive.tar')

        __, error = utils.run(
            ['git', 'archive', '--format=tar', '--output={}'.format(archive), 'HEAD'],
            cwd=rootpath,
            errormsg='Failed to make repository archive',
        )
        if error is not None:
            return None, error

        __, error = utils.run(
            ['tar', '--extract', '--file={}'.format(archive), '--directory={}'.format(targetdir)],
            cwd=rootpath,
            errormsg='Failed to extract repository archive to target directory',
        )
        if error is not None:
            return None, error

    return None, None


def get_branch(rootpath):
    output, error = utils.run(
        'git rev-parse --abbrev-ref HEAD'.split(),
        cwd=rootpath,
        errormsg='Failed to get branch',
    )
    if error is not None:
        return None, error
    branch = output
    return branch, None


def get_revision(rootpath):
    output, error = utils.run(
        'git rev-parse --verify --quiet HEAD'.split(),
        cwd=rootpath,
        errormsg='Failed to get revision',
    )
    if error is not None:
        return None, error
    revision = output[:7]
    return revision, None


def get_latest_tag_description(rootpath):
    output, error = utils.run(
        # 'git describe --dirty --tags --long --match *.*'.split(),  # only tags with dots inside allowed
        'git describe --dirty --tags --long --match *'.split(),  # any tags allowed
        cwd=rootpath,
        errormsg='Failed to describe repository',
    )
    if error is not None:
        return None, error

    repo_description = output

    dirty = False
    if repo_description.endswith('-dirty'):
        dirty = True
        repo_description = repo_description[:-6]

    tag, distance, revision = repo_description.rsplit("-", 2)
    distance = int(distance)
    revision = revision[1:]

    return (tag, distance, revision, dirty), None


def get_all_commits(rootpath):
    output, error = utils.run(
        'git rev-list HEAD'.split(),
        cwd=rootpath,
        errormsg='Failed to get commit history',
    )
    if error is not None:
        return None, error
    commits_count = len(output.splitlines())
    return commits_count, None


def get_dirty_status(rootpath):
    output, error = utils.run(
        'git status --porcelain --untracked-files=no'.split(),
        cwd=rootpath,
        errormsg='Failed to get dirty status',
    )
    if error is not None:
        return None, error
    is_dirty = bool(output)
    return is_dirty, None
